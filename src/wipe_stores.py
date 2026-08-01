"""Purge des stores avant une re-ingestion propre.

A lancer depuis le reseau Docker, les stores etant adresses par leur nom de
service :

    docker compose exec docling-service python -m src.wipe_stores
    docker compose restart docling-service   # recree le schema NebulaGraph

Utile notamment quand la chaine d'extraction change : les identifiants
d'elements derivent de leur texte, si bien qu'une extraction modifiee produit
de nouveaux identifiants et laisse les anciens en orphelins.
"""

from __future__ import annotations

import chromadb
from nebula3.Config import Config
from nebula3.gclient.net import ConnectionPool

from src.docling_service.settings import get_settings

settings = get_settings()

print("--- ChromaDB ---")
try:
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    client.delete_collection("rag_documents")
    print("collection rag_documents supprimee")
except Exception as exc:
    print(f"ChromaDB : {exc}")

print("\n--- NebulaGraph ---")
pool = ConnectionPool()
try:
    if not pool.init([(settings.nebula_host, settings.nebula_port)], Config()):
        print("connexion impossible")
    else:
        session = pool.get_session("root", "nebula")
        try:
            result = session.execute("DROP SPACE IF EXISTS rag_space;")
            if result.is_succeeded():
                print("space rag_space supprime")
            else:
                print(f"DROP SPACE : {result.error_msg()}")
        finally:
            session.release()
finally:
    pool.close()

print("\nRedemarrer docling-service pour recreer le schema.")
