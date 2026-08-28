"""Purge des stores avant une re-ingestion propre.

A lancer depuis le reseau Docker, les stores etant adresses par leur nom de
service :

    docker compose exec docling-service python -m src.wipe_stores
    docker compose restart docling-service   # recree le schema NebulaGraph

Utile notamment quand la chaine d'extraction change : les identifiants
d'elements derivent de leur texte, si bien qu'une extraction modifiee produit
de nouveaux identifiants et laisse les anciens en orphelins.

**Les trois stores, pas deux.** Le bucket MinIO etait laisse intact : les crops
d'images des ingestions precedentes y survivaient a toute purge. Ce n'est pas
une fuite — l'agent ne sert que les objets references par le graphe
(``RESTRICT_MEDIA_TO_GRAPH=true``), donc un objet dont le noeud a disparu est
deja inaccessible — mais c'est de la place perdue qui grossit a chaque
re-ingestion, et un bucket qu'on ne peut plus lire pour se rassurer.
"""

from __future__ import annotations

import sys
from typing import Any

from src.docling_service.settings import get_settings

# Les noms de collection et de space vivent dans nebula.py et vectors.py, qui
# tirent nebula3 et chromadb. Ils sont importes dans main() : ce module doit
# rester importable sans les clients, pour que la purge soit testable hors de
# l'image d'extraction.


def purge_collection(client: Any, nom: str) -> None:
    """Supprime la collection ChromaDB.

    Args:
        client: Client ChromaDB.
        nom: Nom de la collection.
    """
    client.delete_collection(nom)


def purge_bucket(client: Any, bucket: str) -> int:
    """Vide un bucket MinIO de tous ses objets.

    ``recursive=True`` n'est pas un detail : sans lui, ``list_objects`` ne rend
    que les prefixes de premier niveau et la purge laisse derriere elle tout le
    contenu de ``images/{stem}/``, sans rien signaler.

    Les objets sont supprimes un a un plutot que par ``remove_objects`` : ce
    dernier rend un iterateur d'erreurs qu'il faut penser a consommer, et une
    erreur non consommee est une suppression qu'on croit faite.

    Args:
        client: Client MinIO.
        bucket: Nom du bucket a vider.

    Returns:
        Le nombre d'objets supprimes.
    """
    if not client.bucket_exists(bucket):
        return 0

    supprimes = 0
    for objet in client.list_objects(bucket, recursive=True):
        client.remove_object(bucket, objet.object_name)
        supprimes += 1
    return supprimes


def purge_space(session: Any, space: str) -> str:
    """Supprime le space NebulaGraph.

    Le schema n'evolue pas en place : c'est un DROP puis une recreation au
    redemarrage du service, jamais une migration.

    Args:
        session: Session NebulaGraph.
        space: Nom du space.

    Returns:
        Un message decrivant le resultat.
    """
    result = session.execute(f"DROP SPACE IF EXISTS {space};")
    if result.is_succeeded():
        return f"space {space} supprime"
    return f"DROP SPACE : {result.error_msg()}"


def main() -> None:
    """Purge les trois stores et rend compte de chacun."""
    import chromadb

    from src.docling_service import images
    from src.docling_service.nebula import SPACE, get_writer
    from src.docling_service.vectors import COLLECTION_NAME

    settings = get_settings()
    echecs: list[str] = []

    print("--- ChromaDB ---")
    try:
        client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        purge_collection(client, COLLECTION_NAME)
        print(f"collection {COLLECTION_NAME} supprimee")
    except Exception as exc:
        print(f"ChromaDB : {exc}")
        echecs.append("ChromaDB")

    print("\n--- MinIO ---")
    try:
        supprimes = purge_bucket(images.get_client(), settings.minio_bucket)
        print(f"{supprimes} objets supprimes du bucket {settings.minio_bucket}")
    except Exception as exc:
        print(f"MinIO : {exc}")
        echecs.append("MinIO")

    print("\n--- NebulaGraph ---")
    writer = get_writer()
    try:
        # use_space=False : on ne se place pas dans le space qu'on s'apprete a
        # supprimer, et le DROP doit rester possible meme s'il n'existe plus.
        with writer.session(use_space=False) as session:
            print(purge_space(session, SPACE))
    except Exception as exc:
        print(f"NebulaGraph : {exc}")
        echecs.append("NebulaGraph")
    finally:
        writer.close()

    print("\nRedemarrer docling-service pour recreer le schema.")
    if echecs:
        # Une purge partielle est pire qu'une purge absente : on croit repartir
        # propre et on re-ingere par-dessus des restes.
        print(f"PURGE INCOMPLETE : {', '.join(echecs)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
