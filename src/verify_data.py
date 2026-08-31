"""Controle avant-vol : etat des trois stores apres (ou avant) une ingestion.

A lancer depuis le reseau Docker, les stores etant adresses par leur nom de
service :

    docker compose exec docling-service python -m src.verify_data

Les identifiants viennent de la configuration du service (donc de ``.env``) et
ne sont plus ecrits en dur.

**Ce module faisait ses entrees-sorties a l'IMPORT.** Il n'avait pas de
``main`` : ouvrir une connexion ChromaDB, lister un bucket MinIO et interroger
NebulaGraph etaient des instructions de niveau module, executees par le seul
fait d'importer ``src.verify_data``. Deux consequences, et la seconde est la
plus couteuse :

- un ``import`` accidentel — un outil qui parcourt le paquet, une completion,
  un ``pytest --collect-only`` — declenchait les trois controles et pouvait
  appeler ``sys.exit(1)`` ;
- **rien n'etait testable.** Un test qui importe le module aurait exige les
  trois stores debout. Le module n'etait donc garde par aucun test, exactement
  comme ``index_report`` et ``verify_contract`` (registre 4.5).

Les clients de stores sont importes DANS ``main`` pour la meme raison : le
module doit rester importable sans eux.
"""

from __future__ import annotations

import sys
from typing import Any


def report(label: str, message: str, echecs: list[str], ok: bool = True) -> None:
    """Affiche une ligne de bilan et memorise l'echec.

    La liste des echecs est passee en argument plutot que tenue au niveau du
    module : un etat de module survit a l'appel et s'accumule d'une execution a
    l'autre dans un meme processus.

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


def verifier_minio(settings: Any, echecs: list[str]) -> None:
    """Compte les objets du bucket."""
    from minio import Minio

    print("\n--- MinIO ---")
    try:
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_root_user,
            secret_key=settings.minio_root_password,
            secure=False,
        )
        objets = list(client.list_objects(settings.minio_bucket, recursive=True))
        report(f"objets dans '{settings.minio_bucket}'", str(len(objets)), echecs)
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
        session = pool.get_session("root", "nebula")
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
    verifier_minio(settings, echecs)
    verifier_nebula(settings, echecs)

    print()
    if echecs:
        print(f"{len(echecs)} controle(s) en echec : {', '.join(echecs)}")
        sys.exit(1)
    print("Les trois stores repondent.")


if __name__ == "__main__":
    main()
