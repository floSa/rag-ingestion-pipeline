"""Controle avant-vol : etat des trois stores apres (ou avant) une ingestion.

A lancer depuis le reseau Docker, les stores etant adresses par leur nom de
service :

    docker compose exec docling-service python src/verify_data.py

Les identifiants viennent de la configuration du service (donc de ``.env``) et
ne sont plus ecrits en dur.
"""

from __future__ import annotations

import sys

import chromadb
from minio import Minio
from nebula3.Config import Config
from nebula3.gclient.net import ConnectionPool

from src.docling_service.settings import get_settings

settings = get_settings()
failures: list[str] = []


def report(label: str, message: str, ok: bool = True) -> None:
    """Affiche une ligne de bilan et memorise les echecs."""
    print(f"{'  OK ' if ok else 'ECHEC'}  {label} : {message}")
    if not ok:
        failures.append(label)


print("--- ChromaDB ---")
try:
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    collection = client.get_collection("rag_documents")
    report("chunks indexes", str(collection.count()))
except Exception as exc:
    report("connexion", str(exc), ok=False)

print("\n--- MinIO ---")
try:
    minio_client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_root_user,
        secret_key=settings.minio_root_password,
        secure=False,
    )
    objects = list(minio_client.list_objects(settings.minio_bucket, recursive=True))
    report(f"objets dans '{settings.minio_bucket}'", str(len(objects)))
except Exception as exc:
    report("connexion", str(exc), ok=False)

print("\n--- NebulaGraph ---")
pool = ConnectionPool()
try:
    if not pool.init([(settings.nebula_host, settings.nebula_port)], Config()):
        report("connexion", "init a renvoye False", ok=False)
    else:
        session = pool.get_session("root", "nebula")
        try:
            for label, query in (
                ("noeuds", "USE rag_space; MATCH (v) RETURN count(v) AS cnt;"),
                ("aretes", "USE rag_space; MATCH ()-[e]->() RETURN count(e) AS cnt;"),
                (
                    "documents",
                    "USE rag_space; MATCH (d:Document) RETURN count(d) AS cnt;",
                ),
            ):
                result = session.execute(query)
                if result.is_succeeded():
                    report(label, str(result.rows()[0].values[0].get_iVal()))
                else:
                    report(label, result.error_msg(), ok=False)
        finally:
            session.release()
except Exception as exc:
    report("connexion", str(exc), ok=False)
finally:
    pool.close()

print()
if failures:
    print(f"{len(failures)} controle(s) en echec : {', '.join(failures)}")
    sys.exit(1)
print("Les trois stores repondent.")
