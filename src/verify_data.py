
import chromadb
from minio import Minio
from nebula3.Config import Config
from nebula3.gclient.net import ConnectionPool

print("--- Checking ChromaDB ---")
try:
    chroma_client = chromadb.HttpClient(host='chromadb', port=8000)
    collection = chroma_client.get_collection('rag_documents')
    print("ChromaDB Document Count:", collection.count())
except Exception as e:
    print("ChromaDB Error:", e)

print("\n--- Checking MinIO ---")
try:
    minio_client = Minio(
        "minio:9000",
        access_key="admin",
        secret_key="miniopassword",
        secure=False
    )
    objects = list(minio_client.list_objects("documents", recursive=True))
    print("MinIO Object Count in 'documents' bucket:", len(objects))
except Exception as e:
    print("MinIO Error:", e)

print("\n--- Checking NebulaGraph ---")
try:
    config = Config()
    pool = ConnectionPool()
    if pool.init([('graphd', 9669)], config):
        session = pool.get_session('root', 'nebula')
        res = session.execute('USE rag_space; MATCH (v) RETURN count(v) as cnt;')
        if res.is_succeeded():
            print("NebulaGraph Nodes Count:", res.rows()[0].values[0].get_iVal())
        else:
            print("NebulaGraph Query Failed:", res.error_msg())

        res_edges = session.execute('USE rag_space; MATCH ()-[e]->() RETURN count(e) as cnt;')
        if res_edges.is_succeeded():
            print("NebulaGraph Edges Count:", res_edges.rows()[0].values[0].get_iVal())
        else:
            print("NebulaGraph Edges Query Failed:", res_edges.error_msg())
        pool.close()
    else:
        print("Failed to connect to NebulaGraph")
except Exception as e:
    print("NebulaGraph Error:", e)
