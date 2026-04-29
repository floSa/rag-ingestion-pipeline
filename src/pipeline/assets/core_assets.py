from dagster import asset, DynamicPartitionsDefinition, AssetExecutionContext
import requests
import chromadb
from nebula3.gclient.net import ConnectionPool
from nebula3.Config import Config
import os

DOCLING_API_URL = "http://docling-service:8000/extract"

# Définition des partitions pour les PDFs (un dynamique par nom de fichier)
pdf_partitions = DynamicPartitionsDefinition(name="pdf_partitions")

@asset(group_name="extraction", partitions_def=pdf_partitions)
def extract_structured_json(context: AssetExecutionContext) -> dict:
    """Appelle le microservice Docling pour le fichier de la partition actuelle."""
    file_path = context.partition_key
    if not os.path.exists(file_path):
        # On pourrait être dans le cas d'un fichier supprimé mais dont la partition existe encore
        print(f"File not found for partition: {file_path}")
        return {}
        
    print(f"Requesting extraction for: {file_path}")
    resp = requests.post(DOCLING_API_URL, json={"filepath": file_path}, timeout=1200)
    resp.raise_for_status()
    print(f"Successfully extracted: {file_path}")
    return resp.json()

@asset(group_name="knowledge_graph", partitions_def=pdf_partitions)
def build_knowledge_graph(context: AssetExecutionContext, extract_structured_json: dict):
    """Construit les noeuds et les relations dans NebulaGraph pour UN document."""
    if not extract_structured_json:
        print("No data extracted, skipping graph build.")
        return False
        
    config = Config()
    config.max_connection_pool_size = 10
    pool = ConnectionPool()
    if not pool.init([('graphd', 9669)], config):
        raise Exception("NebulaGraph init failed")
        
    try:
        session = pool.get_session('root', 'nebula')
        session.execute('CREATE SPACE IF NOT EXISTS rag_space(partition_num=10, replica_factor=1, vid_type=FIXED_STRING(64));')
        
        import time
        # Propagation de l'espace (Standard Nebula)
        session.execute('USE rag_space;')
        
        # Création du schéma (Tags et Edges)
        session.execute('CREATE TAG IF NOT EXISTS Document(filename string, type_file string);')
        session.execute('CREATE TAG IF NOT EXISTS Element(label string, type string, page_no int, text string, minio_url string);')
        session.execute('CREATE EDGE IF NOT EXISTS HAS_PARENT();')
        
        doc = extract_structured_json
        metadata = doc.get("metadata", {})
        elements = doc.get("elements", [])
        filename = metadata.get("filename", "unknown")
        type_file = metadata.get("type_file", "unknown")
        
        # Insertion du noeud Document racine
        doc_vid = f"doc_{filename}"
        session.execute(f'INSERT VERTEX Document(filename, type_file) VALUES "{doc_vid}":("{filename}", "{type_file}");')

        for elem in elements:
            vid = elem["id"]
            label = elem.get("label", "text")
            elem_type = elem.get("type", "text")
            page_no = elem.get("page_no", 1)
            text_clean = (elem.get("text") or "").replace('"', '\\"').replace("'", "\\'")[:1000]
            minio_url = (elem.get("minio_url") or "").replace('"', '\\"')
            
            # Insertion du noeud Element
            query_node = (
                f'INSERT VERTEX Element(label, type, page_no, text, minio_url) '
                f'VALUES "{vid}":("{label}", "{elem_type}", {page_no}, "{text_clean}", "{minio_url}");'
            )
            session.execute(query_node)
            
            # Création de la relation parentale
            parent_id = elem.get("reference_id")
            target_vid = doc_vid if parent_id == "DOC" else parent_id
                
            if target_vid:
                query_edge = f'INSERT EDGE HAS_PARENT() VALUES "{vid}" -> "{target_vid}":();'
                session.execute(query_edge)

    except Exception as e:
        print(f"Error building knowledge graph: {e}")
        return False
    finally:
        pool.close()
        
    return True

from src.pipeline.resources import EmbeddingsResource

@asset(group_name="vector_db", partitions_def=pdf_partitions)
def vectorize_content(context: AssetExecutionContext, embeddings: EmbeddingsResource, extract_structured_json: dict):
    """Génère les embeddings pour la partition actuelle (un document)."""
    if not extract_structured_json:
        return False

    chroma_client = chromadb.HttpClient(host='chromadb', port=8000)
    collection = chroma_client.get_or_create_collection(name="rag_documents")
    
    model = embeddings.get_model()
    doc = extract_structured_json
    elements = doc.get("elements", [])
    
    for elem in elements:
        text = elem.get("text") or elem.get("content")
        if not text:
            continue
            
        chunks = [text[i:i+500] for i in range(0, len(text), 500)]
        
        for i, chunk in enumerate(chunks):
            chunk_id = f"{elem['id']}_part{i}"
            vector = model.encode(chunk).tolist()
            
            metadata = {
                "element_id": elem["id"],
                "graph_node_id": elem["id"],
                "page_position": elem.get("page_position", 0),
                "ref_position": elem.get("ref_position", 0),
                "minio_url": elem.get("minio_url", "")
            }
            
            collection.upsert(
                ids=[chunk_id],
                embeddings=[vector],
                metadatas=[metadata],
                documents=[chunk]
            )
                
    return True
