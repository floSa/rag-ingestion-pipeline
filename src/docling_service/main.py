from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import re, hashlib, json, io, os, time
from pathlib import Path
from collections import deque
from typing import Dict, List, Optional, Tuple

from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode, TableStructureOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

import fitz  # PyMuPDF
from minio import Minio
import chromadb
from nebula3.gclient.net import ConnectionPool
from nebula3.Config import Config
from sentence_transformers import SentenceTransformer

app = FastAPI(title="Docling Streaming Extraction API")

# Configuration via variables d'environnement (voir .env.example)
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "")
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "documents")

NEBULA_HOST = os.getenv("NEBULA_HOST", "graphd")
NEBULA_PORT = int(os.getenv("NEBULA_PORT", "9669"))
CHROMA_HOST = os.getenv("CHROMA_HOST", "chromadb")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))

# Clients globaux (chargés UNE SEULE FOIS)
print("Loading IA Models (Layout + Embeddings)...")
pipeline_options = PdfPipelineOptions(do_ocr=False, do_table_structure=False)
converter = DocumentConverter(format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)})
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

minio_client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=False)

def compute_id(filename: str, page_no: int, order: int, text: str) -> str:
    return hashlib.sha256(f"{filename}|{page_no}|{order}|{text[:50]}".encode()).hexdigest()[:10]

def extract_bbox(bbox) -> Dict[str, float]:
    if not bbox: return {}
    return {"l": round(bbox.l, 2), "t": round(bbox.t, 2), "r": round(bbox.r, 2), "b": round(bbox.b, 2)}

# Configuration des tags Nebula (Mapping Docling -> Nebula)
TAG_MAP = {
    "text": "Paragraph",
    "paragraph": "Paragraph",
    "heading": "SectionHeader",
    "section_header": "SectionHeader",
    "list_item": "ListItem",
    "table": "Table",
    "picture": "Picture",
    "formula": "Formula",
    "code": "Code",
    "caption": "Caption",
    "footnote": "Footnote",
    "page_header": "PageHeader",
    "page_footer": "PageFooter",
    "title": "SectionHeader"
}

def init_nebula():
    """Initialise le schéma sémantique NebulaGraph avec une résilience maximale."""
    print("Initializing Semantic NebulaGraph Schema...")
    config = Config()
    
    attempts = 0
    max_attempts = 15
    connected = False
    
    while attempts < max_attempts and not connected:
        pool = ConnectionPool() # On recrée le pool à chaque tentative pour éviter les états corrompus
        try:
            if pool.init([(NEBULA_HOST, NEBULA_PORT)], config):
                connected = True
                print("Connected to NebulaGraph!")
            else:
                attempts += 1
                print(f"Nebula Connection Attempt {attempts}/{max_attempts} returned False. Retrying...")
                time.sleep(10)
        except Exception as e:
            attempts += 1
            print(f"Nebula not ready yet ({e}). Attempt {attempts}/{max_attempts}. Waiting 10s...")
            time.sleep(10)
        finally:
            if not connected:
                pool.close()

    if not connected:
        print("CRITICAL: Could not connect to NebulaGraph after 15 attempts.")
        return

    try:
        session = pool.get_session('root', 'nebula')
        # On ne supprime plus l'espace au démarrage pour éviter de tout perdre
        # session.execute('DROP SPACE IF EXISTS rag_space;')
        session.execute('CREATE SPACE IF NOT EXISTS rag_space(partition_num=10, replica_factor=1, vid_type=FIXED_STRING(64));')
        time.sleep(5)
        session.execute('USE rag_space;')
        
        session.execute('CREATE TAG IF NOT EXISTS Document(filename string, type_file string);')
        all_tags = set(TAG_MAP.values())
        for tag in all_tags:
            session.execute(f'CREATE TAG IF NOT EXISTS {tag}(label string, page_no int, text string, minio_url string);')
        
        session.execute('CREATE EDGE IF NOT EXISTS PARENT_OF(sequence int);')
        session.execute('CREATE EDGE IF NOT EXISTS LINKED_TO(relation string);')
        session.execute('CREATE TAG INDEX IF NOT EXISTS doc_index ON Document(filename(20));')
        session.release()
        print("NebulaGraph Semantic Schema Ready.")
    except Exception as e:
        print(f"Nebula Schema Init Error: {e}")
    finally:
        pool.close()

def init_minio():
    """S'assure que le bucket MinIO existe, avec retry."""
    import time
    attempts = 0
    max_attempts = 15
    while attempts < max_attempts:
        try:
            if not minio_client.bucket_exists(MINIO_BUCKET):
                minio_client.make_bucket(MINIO_BUCKET)
                print(f"MinIO Bucket '{MINIO_BUCKET}' created.")
            else:
                print(f"MinIO Bucket '{MINIO_BUCKET}' ready.")
            return
        except Exception as e:
            attempts += 1
            print(f"MinIO not ready yet ({e}). Attempt {attempts}/{max_attempts}. Waiting 5s...")
            time.sleep(5)
    print("CRITICAL: Could not connect to MinIO after 15 attempts.")

@app.on_event("startup")
async def startup_event():
    """Lancer l'initialisation en arrière-plan pour ne pas bloquer le port 8000."""
    import asyncio
    asyncio.create_task(asyncio.to_thread(init_nebula))
    asyncio.create_task(asyncio.to_thread(init_minio))

def crop_and_upload_image(pdf_path: str, page_no: int, bbox: dict, image_id: str, element_type: str) -> str:
    try:
        if not bbox or not all(k in bbox for k in ["l", "t", "r", "b"]): return None
        doc_fitz = fitz.open(pdf_path)
        page = doc_fitz[page_no - 1]
        
        # Docling uses BOTTOMLEFT origin (t > b). PyMuPDF expects TOPLEFT origin.
        y0 = bbox["t"]
        y1 = bbox["b"]
        if y0 > y1: # Convert from BOTTOMLEFT to TOPLEFT
            page_h = page.rect.height
            y0, y1 = page_h - y0, page_h - y1
            
        rect = fitz.Rect(bbox["l"], min(y0, y1), bbox["r"], max(y0, y1)) & page.rect
        if rect.is_empty or rect.width < 1 or rect.height < 1:
            doc_fitz.close()
            return None
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), clip=rect)
        image_bytes = pix.tobytes("png")
        object_name = f"images/{Path(pdf_path).stem}/{image_id}_{element_type}.png"
        
        try:
            minio_client.put_object(MINIO_BUCKET, object_name, io.BytesIO(image_bytes), length=len(image_bytes), content_type="image/png")
            print(f"Uploaded to MinIO: {object_name}")
        except Exception as upload_err:
            print(f"MinIO Upload FAIL: {upload_err}")
            doc_fitz.close()
            return None
            
        doc_fitz.close()
        return f"http://{MINIO_ENDPOINT}/{MINIO_BUCKET}/{object_name}"
    except Exception as e:
        print(f"Crop Error: {e}")
        return None

def flush_chunk_to_storage(elements: List[dict], filename: str, type_file: str):
    # 1. NEBULA
    config = Config()
    attempts = 0
    max_attempts = 10
    connected = False
    
    while attempts < max_attempts and not connected:
        pool = ConnectionPool()
        try:
            if pool.init([(NEBULA_HOST, NEBULA_PORT)], config):
                connected = True
            else:
                attempts += 1
                time.sleep(5)
        except Exception:
            attempts += 1
            time.sleep(5)
        finally:
            if not connected:
                pool.close()

    if connected:
        try:
            session = pool.get_session('root', 'nebula')
            session.execute('USE rag_space;')
            doc_vid = f"doc_{filename}"
            session.execute(f'INSERT VERTEX Document(filename, type_file) VALUES "{doc_vid}":("{filename}", "{type_file}");')
            
            last_visual_id = None # Pour lier les Captions au dernier Picture/Table
            
            for e in elements:
                vid = e["id"]
                lbl = e["label"]
                tag = TAG_MAP.get(lbl, "Paragraph")
                text_clean = (e.get("text") or "").replace('"', '\\"').replace("'", "\\'")[:1000]
                m_url = (e.get("minio_url") or "").replace('"', '\\"')
                
                # Insertion du Vertex avec son tag spécifique
                session.execute(f'INSERT VERTEX {tag}(label, page_no, text, minio_url) VALUES "{vid}":("{lbl}", {e["page_no"]}, "{text_clean}", "{m_url}");')
                
                # Relation hiérarchique avec ordre
                session.execute(f'INSERT EDGE PARENT_OF(sequence) VALUES "{doc_vid}" -> "{vid}":({e["order"]});')
                
                # Relation sémantique pour les légendes (Captions)
                if tag == "Caption" and last_visual_id:
                    session.execute(f'INSERT EDGE LINKED_TO(relation) VALUES "{vid}" -> "{last_visual_id}":("describes");')
                
                # Mise à jour du dernier objet visuel rencontré
                if tag in ["Table", "Picture"]:
                    last_visual_id = vid
                    
            session.release()
        except Exception as err: print(f"Nebula Flush Error: {err}")
        finally: pool.close()

    # 2. CHROMA
    try:
        chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        collection = chroma_client.get_or_create_collection(name="rag_documents")
        for e in elements:
            if e.get("text"):
                vector = embedding_model.encode(e["text"][:1000]).tolist()
                collection.upsert(ids=[e["id"]], embeddings=[vector], documents=[e["text"][:1000]], metadatas=[{"element_id": e["id"], "filename": filename}])
    except Exception as err: print(f"Chroma Flush Error: {err}")

class ExtractRequest(BaseModel):
    filepath: str

@app.post("/extract")
async def extract_document(req: ExtractRequest):
    pdf_path = req.filepath
    if not os.path.exists(pdf_path): raise HTTPException(status_code=404, detail="File not found")
        
    path_obj = Path(pdf_path)
    filename_stem = path_obj.stem
    type_file = path_obj.suffix.lstrip('.')

    with fitz.open(pdf_path) as d: total_pages = len(d)

    CHUNK_SIZE = 5
    OVERLAP = 2
    BUFFER_SIZE = 1 
    
    chunk_buffer = deque()
    start_page = 1
    global_order = 0
    
    while start_page <= total_pages:
        end_page = min(start_page + CHUNK_SIZE - 1, total_pages)
        print(f"[{filename_stem}] Batch {start_page}-{end_page}...")
        
        try:
            conv_res = converter.convert(pdf_path, page_range=(start_page, end_page))
            chunk_elements = []
            for item, _ in conv_res.document.iterate_items():
                lbl = str(getattr(item, 'label', 'text')).lower()
                prov = item.prov[0] if item.prov else None
                text = getattr(item, 'text', '').strip() if hasattr(item, 'text') else ""
                
                elem = {
                    "id": compute_id(filename_stem, prov.page_no if prov else 1, global_order, text),
                    "label": lbl, 
                    "page_no": prov.page_no if prov else 1,
                    "bbox": extract_bbox(prov.bbox if prov else None),
                    "text": text,
                    "order": global_order
                }
                
                if type_file == "pdf" and lbl in ["picture", "table", "figure", "graphic"] and elem["bbox"]:
                    elem["minio_url"] = crop_and_upload_image(pdf_path, elem["page_no"], elem["bbox"], elem["id"], lbl)
                
                chunk_elements.append(elem)
                global_order += 1

            chunk_buffer.append(chunk_elements)
            if len(chunk_buffer) >= BUFFER_SIZE:
                flush_chunk_to_storage(chunk_buffer.popleft(), filename_stem, type_file)

            if hasattr(conv_res.input, "_backend") and conv_res.input._backend:
                conv_res.input._backend.unload()
                
        except Exception as e: print(f"Batch Error: {e}")
        
        if end_page == total_pages: break
        start_page = end_page - OVERLAP + 1

    while chunk_buffer:
        flush_chunk_to_storage(chunk_buffer.popleft(), filename_stem, type_file)

    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
