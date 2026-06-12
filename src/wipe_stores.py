"""Purge des stores avant ré-ingestion propre (doublons historiques)."""
import chromadb
from nebula3.Config import Config
from nebula3.gclient.net import ConnectionPool

client = chromadb.HttpClient(host="chromadb", port=8000)
try:
    client.delete_collection("rag_documents")
    print("CHROMA: collection rag_documents supprimée")
except Exception as exc:
    print(f"CHROMA: {exc}")

pool = ConnectionPool()
pool.init([("graphd", 9669)], Config())
s = pool.get_session("root", "nebula")
r = s.execute("DROP SPACE IF EXISTS rag_space;")
print(f"NEBULA: DROP SPACE -> {r.is_succeeded()} {r.error_msg() if not r.is_succeeded() else ''}")
s.release()
pool.close()
