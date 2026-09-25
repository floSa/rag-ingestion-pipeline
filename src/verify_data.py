"""Controle avant-vol : etat des trois stores apres (ou avant) une ingestion.

A lancer dans un conteneur jetable du service d'extraction :

    docker compose run --rm --no-deps -T -e PYTHONPATH=/app -w /app \\
      docling-service python -m src.verify_data

Les adresses et identifiants viennent des reglages du service (donc de
``.env`` et de ``docker-compose.yml``).

Toutes les entrees-sorties sont dans des fonctions, et les clients de stores
sont importes dans ces fonctions : importer le module n'ouvre aucune connexion
et n'appelle pas ``sys.exit``, ce qui le rend testable sans les stores
(registre 4.5).
"""

from __future__ import annotations

import sys
from typing import Any


def report(label: str, message: str, echecs: list[str], ok: bool = True) -> None:
    """Affiche une ligne de bilan et memorise l'echec.

    La liste des echecs est passee en argument plutot que tenue au niveau du
    module : un etat de module s'accumulerait d'un appel a l'autre dans un meme
    processus.

    Args:
        label: Nom du controle.
        message: Ce qu'il a constate.
        echecs: Liste ou noter le label en cas d'echec, modifiee en place.
        ok: Faux si le controle a echoue.
    """
    print(f"{'  OK ' if ok else 'ECHEC'}  {label} : {message}")
    if not ok:
        echecs.append(label)


def verifier_chromadb(settings: Any, echecs: list[str]) -> None:
    """Compte les chunks indexes."""
    import chromadb

    print("--- ChromaDB ---")
    try:
        client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        collection = client.get_collection("rag_documents")
        report("chunks indexes", str(collection.count()), echecs)
    except Exception as exc:
        report("connexion", str(exc), echecs, ok=False)


def verifier_le_stockage_objet(settings: Any, echecs: list[str]) -> None:
    """Compte les objets du bucket, et affiche le serveur interroge.

    L'en-tete affiche la valeur de ``S3_ENDPOINT``, et non un nom de produit
    ecrit dans le code : il montre le serveur reellement interroge.

    Le client est construit par ``images.build_client``, seul site de
    construction du depot. L'import reste dans la fonction (voir l'en-tete du
    module).
    """
    from src.docling_service.images import build_client

    print(f"\n--- Stockage objet ({settings.s3_endpoint}) ---")
    try:
        client = build_client(
            settings.s3_endpoint,
            settings.s3_access_key,
            settings.s3_secret_key,
        )
        objets = list(client.list_objects(settings.s3_bucket, recursive=True))
        report(f"objets dans '{settings.s3_bucket}'", str(len(objets)), echecs)
    except Exception as exc:
        report("connexion", str(exc), echecs, ok=False)


def verifier_nebula(settings: Any, echecs: list[str]) -> None:
    """Compte noeuds, aretes et documents du graphe."""
    from nebula3.Config import Config
    from nebula3.gclient.net import ConnectionPool

    print("\n--- NebulaGraph ---")
    pool = ConnectionPool()
    try:
        if not pool.init([(settings.nebula_host, settings.nebula_port)], Config()):
            report("connexion", "init a renvoye False", echecs, ok=False)
            return
        session = pool.get_session(settings.nebula_user, settings.nebula_password)
        try:
            for label, query in (
                ("noeuds", "USE rag_space; MATCH (v) RETURN count(v) AS cnt;"),
                ("aretes", "USE rag_space; MATCH ()-[e]->() RETURN count(e) AS cnt;"),
                ("documents", "USE rag_space; MATCH (d:Document) RETURN count(d) AS cnt;"),
            ):
                result = session.execute(query)
                if result.is_succeeded():
                    report(label, str(result.rows()[0].values[0].get_iVal()), echecs)
                else:
                    report(label, result.error_msg(), echecs, ok=False)
        finally:
            session.release()
    except Exception as exc:
        report("connexion", str(exc), echecs, ok=False)
    finally:
        pool.close()


def main() -> None:
    """Interroge les trois stores et sort en erreur si l'un ne repond pas."""
    from src.docling_service.settings import get_settings

    settings = get_settings()
    echecs: list[str] = []
    verifier_chromadb(settings, echecs)
    verifier_le_stockage_objet(settings, echecs)
    verifier_nebula(settings, echecs)

    print()
    if echecs:
        print(f"{len(echecs)} controle(s) en echec : {', '.join(echecs)}")
        sys.exit(1)
    print("Les trois stores repondent.")


if __name__ == "__main__":
    main()
