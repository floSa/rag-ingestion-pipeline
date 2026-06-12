"""Service FastAPI d'extraction structuree de documents via Docling."""

from __future__ import annotations

import asyncio
import hashlib
import io
import time
from collections import deque
from pathlib import Path
from typing import Any

import chromadb
import fitz  # PyMuPDF
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from fastapi import FastAPI, HTTPException
from minio import Minio
from nebula3.Config import Config
from nebula3.gclient.net import ConnectionPool
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

from src.docling_service.settings import get_settings

# ---------------------------------------------------------------------------
# Configuration centralisee via pydantic-settings
# ---------------------------------------------------------------------------
_settings = get_settings()

# ---------------------------------------------------------------------------
# Mapping Docling labels -> NebulaGraph tags
# ---------------------------------------------------------------------------
TAG_MAP: dict[str, str] = {
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
    "title": "SectionHeader",
}

VISUAL_LABELS: set[str] = {"picture", "table", "figure", "graphic"}

# Labels Docling correspondant à des en-têtes de section (cf. TAG_MAP).
# Sert à construire la hiérarchie Document > SectionHeader > Éléments :
# chaque élément est rattaché au dernier en-tête rencontré.
SECTION_LABELS: set[str] = {lbl for lbl, tag in TAG_MAP.items() if tag == "SectionHeader"}

# ---------------------------------------------------------------------------
# Clients globaux (chargés UNE SEULE FOIS au démarrage du process)
# ---------------------------------------------------------------------------
print("Loading IA Models (Layout + Embeddings)...")
pipeline_options = PdfPipelineOptions(do_ocr=False, do_table_structure=False)
converter = DocumentConverter(
    format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
)
embedding_model = SentenceTransformer(_settings.embedding_model_name)
minio_client = Minio(
    _settings.minio_endpoint,
    access_key=_settings.minio_root_user,
    secret_key=_settings.minio_root_password,
    secure=False,
)

app = FastAPI(title="Docling Streaming Extraction API")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def compute_id(filename: str, page_no: int, order: int, text: str) -> str:
    """Génère un identifiant court déterministe pour un élément."""
    raw = f"{filename}|{page_no}|{order}|{text[:50]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:10]


def extract_bbox(bbox: Any) -> dict[str, float]:
    """Convertit un objet bbox Docling en dict sérialisable."""
    if not bbox:
        return {}
    return {
        "l": round(bbox.l, 2),
        "t": round(bbox.t, 2),
        "r": round(bbox.r, 2),
        "b": round(bbox.b, 2),
    }


def _connect_nebula(max_attempts: int = 15, wait_seconds: int = 10) -> ConnectionPool | None:
    """Tente de se connecter à NebulaGraph avec retry."""
    config = Config()
    for attempt in range(1, max_attempts + 1):
        pool = ConnectionPool()
        try:
            if pool.init([(_settings.nebula_host, _settings.nebula_port)], config):
                return pool
            print(f"Nebula attempt {attempt}/{max_attempts} returned False. Retrying...")
        except Exception as exc:
            print(
                f"Nebula not ready ({exc}). Attempt {attempt}/{max_attempts}. "
                f"Waiting {wait_seconds}s..."
            )
        pool.close()
        time.sleep(wait_seconds)
    return None


# ---------------------------------------------------------------------------
# Initialisation au startup
# ---------------------------------------------------------------------------
def init_nebula() -> None:
    """Initialise le schéma sémantique NebulaGraph."""
    print("Initializing Semantic NebulaGraph Schema...")
    pool = _connect_nebula()
    if pool is None:
        print("CRITICAL: Could not connect to NebulaGraph after 15 attempts.")
        return

    try:
        session = pool.get_session("root", "nebula")
        # Add storage hosts manually, as required by NebulaGraph when not using an orchestrator
        session.execute('ADD HOSTS "storaged":9779;')
        time.sleep(3)
        session.execute(
            "CREATE SPACE IF NOT EXISTS rag_space"
            "(partition_num=10, replica_factor=1, vid_type=FIXED_STRING(64));"
        )
        time.sleep(5)
        session.execute("USE rag_space;")

        session.execute("CREATE TAG IF NOT EXISTS Document(filename string, type_file string);")
        for tag in set(TAG_MAP.values()):
            session.execute(
                f"CREATE TAG IF NOT EXISTS {tag}"
                "(label string, page_no int, text string, minio_url string);"
            )

        session.execute("CREATE EDGE IF NOT EXISTS PARENT_OF(sequence int);")
        session.execute("CREATE EDGE IF NOT EXISTS LINKED_TO(relation string);")
        session.execute("CREATE TAG INDEX IF NOT EXISTS doc_index ON Document(filename(20));")
        session.release()
        print("NebulaGraph Semantic Schema Ready.")
    except Exception as exc:
        print(f"Nebula Schema Init Error: {exc}")
    finally:
        pool.close()


def init_minio() -> None:
    """S'assure que le bucket MinIO existe, avec retry."""
    max_attempts = 15
    for attempt in range(1, max_attempts + 1):
        try:
            if not minio_client.bucket_exists(_settings.minio_bucket):
                minio_client.make_bucket(_settings.minio_bucket)
                print(f"MinIO Bucket '{_settings.minio_bucket}' created.")
            else:
                print(f"MinIO Bucket '{_settings.minio_bucket}' ready.")
            return
        except Exception as exc:
            print(f"MinIO not ready ({exc}). Attempt {attempt}/{max_attempts}. Waiting 5s...")
            time.sleep(5)
    print("CRITICAL: Could not connect to MinIO after 15 attempts.")


@app.on_event("startup")
async def startup_event() -> None:
    """Lance l'initialisation en arrière-plan."""
    asyncio.create_task(asyncio.to_thread(init_nebula))
    asyncio.create_task(asyncio.to_thread(init_minio))


# ---------------------------------------------------------------------------
# Crop & upload d'images
# ---------------------------------------------------------------------------
def crop_and_upload_image(
    pdf_path: str,
    page_no: int,
    bbox: dict[str, float],
    image_id: str,
    element_type: str,
) -> str | None:
    """Crop une zone d'une page PDF et l'upload sur MinIO."""
    try:
        if not bbox or not all(k in bbox for k in ("l", "t", "r", "b")):
            return None

        doc_fitz = fitz.open(pdf_path)
        page = doc_fitz[page_no - 1]

        # Docling uses BOTTOMLEFT origin (t > b). PyMuPDF expects TOPLEFT.
        y0 = bbox["t"]
        y1 = bbox["b"]
        if y0 > y1:
            page_h = page.rect.height
            y0, y1 = page_h - y0, page_h - y1

        rect = fitz.Rect(bbox["l"], min(y0, y1), bbox["r"], max(y0, y1)) & page.rect
        if rect.is_empty or rect.width < 1 or rect.height < 1:
            doc_fitz.close()
            return None

        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), clip=rect)
        image_bytes: bytes = pix.tobytes("png")
        object_name = f"images/{Path(pdf_path).stem}/{image_id}_{element_type}.png"

        try:
            minio_client.put_object(
                _settings.minio_bucket,
                object_name,
                io.BytesIO(image_bytes),
                length=len(image_bytes),
                content_type="image/png",
            )
            print(f"Uploaded to MinIO: {object_name}")
        except Exception as upload_err:
            print(f"MinIO Upload FAIL: {upload_err}")
            doc_fitz.close()
            return None

        doc_fitz.close()
        return f"http://{_settings.minio_endpoint}/{_settings.minio_bucket}/{object_name}"
    except Exception as exc:
        print(f"Crop Error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Flush vers NebulaGraph + ChromaDB
# ---------------------------------------------------------------------------
def flush_chunk_to_storage(elements: list[dict[str, Any]], filename: str, type_file: str) -> None:
    """Envoie un batch d'éléments vers NebulaGraph et ChromaDB."""
    _flush_to_nebula(elements, filename, type_file)
    _flush_to_chroma(elements, filename)


def _flush_to_nebula(elements: list[dict[str, Any]], filename: str, type_file: str) -> None:
    pool = _connect_nebula(max_attempts=10, wait_seconds=5)
    if pool is None:
        print("Nebula Flush Error: could not connect")
        return

    try:
        session = pool.get_session("root", "nebula")
        session.execute("USE rag_space;")
        doc_vid = f"doc_{filename}"
        session.execute(
            f"INSERT VERTEX Document(filename, type_file) "
            f'VALUES "{doc_vid}":("{filename}", "{type_file}");'
        )

        last_visual_id: str | None = None

        for elem in elements:
            vid: str = elem["id"]
            lbl: str = elem["label"]
            tag = TAG_MAP.get(lbl, "Paragraph")
            text_clean = (elem.get("text") or "").replace('"', '\\"').replace("'", "\\'")[:1000]
            m_url = (elem.get("minio_url") or "").replace('"', '\\"')

            session.execute(
                f"INSERT VERTEX {tag}(label, page_no, text, minio_url) "
                f'VALUES "{vid}":("{lbl}", {elem["page_no"]}, '
                f'"{text_clean}", "{m_url}");'
            )
            # Hiérarchie : un élément est rattaché à sa section parente si elle
            # existe, sinon directement au Document (en-têtes et orphelins).
            parent_vid = elem.get("parent_id") or doc_vid
            session.execute(
                f'INSERT EDGE PARENT_OF(sequence) VALUES "{parent_vid}" -> "{vid}":({elem["order"]});'
            )

            if tag == "Caption" and last_visual_id:
                session.execute(
                    f"INSERT EDGE LINKED_TO(relation) "
                    f'VALUES "{vid}" -> "{last_visual_id}":("describes");'
                )

            if tag in ("Table", "Picture"):
                last_visual_id = vid

        session.release()
    except Exception as err:
        print(f"Nebula Flush Error: {err}")
    finally:
        pool.close()


def _flush_to_chroma(elements: list[dict[str, Any]], filename: str) -> None:
    try:
        chroma_client = chromadb.HttpClient(host=_settings.chroma_host, port=_settings.chroma_port)
        collection = chroma_client.get_or_create_collection(name="rag_documents")
        for elem in elements:
            text = elem.get("text")
            if text:
                vector: list[float] = embedding_model.encode(text[:1000]).tolist()
                collection.upsert(
                    ids=[elem["id"]],
                    embeddings=[vector],
                    documents=[text[:1000]],
                    # Contrat d'interface avec rag-agent-chat : graph_node_id
                    # fait le lien avec NebulaGraph, minio_url permet d'afficher
                    # les images, page_no/label alimentent les citations.
                    metadatas=[{
                        "element_id": elem["id"],
                        "graph_node_id": elem["id"],
                        "filename": filename,
                        "label": elem.get("label") or "",
                        "page_no": int(elem.get("page_no") or 0),
                        "minio_url": elem.get("minio_url") or "",
                    }],
                )
    except Exception as err:
        print(f"Chroma Flush Error: {err}")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class ExtractRequest(BaseModel):
    """Requête d'extraction."""

    filepath: str


# ---------------------------------------------------------------------------
# Endpoint principal
# ---------------------------------------------------------------------------
BATCH_PAGE_SIZE: int = 5
BATCH_OVERLAP: int = 2
FLUSH_BUFFER_SIZE: int = 1

HTML_SUFFIXES: set[str] = {".html", ".htm"}


def _element_from_item(item: Any, filename_stem: str, global_order: int) -> dict[str, Any]:
    """Construit le dict element commun a partir d'un item Docling."""
    lbl = str(getattr(item, "label", "text")).lower()
    prov = item.prov[0] if getattr(item, "prov", None) else None
    text: str = getattr(item, "text", "").strip() if hasattr(item, "text") else ""

    return {
        "id": compute_id(
            filename_stem,
            prov.page_no if prov else 1,
            global_order,
            text,
        ),
        "label": lbl,
        "page_no": prov.page_no if prov else 1,
        "bbox": extract_bbox(prov.bbox if prov else None),
        "text": text,
        "order": global_order,
    }


def _extract_html(path_obj: Path) -> None:
    """Convertit un fichier HTML d'un seul tenant (pas de pagination ni de crop)."""
    filename_stem = path_obj.stem
    print(f"[{filename_stem}] HTML conversion...")

    conv_res = converter.convert(str(path_obj))
    elements: list[dict[str, Any]] = []
    current_section_id: str | None = None
    for global_order, (item, _) in enumerate(conv_res.document.iterate_items()):
        elem = _element_from_item(item, filename_stem, global_order)

        # Hiérarchie : les en-têtes restent rattachés au Document, les autres
        # éléments à la dernière section rencontrée.
        if elem["label"] in SECTION_LABELS:
            current_section_id = elem["id"]
        else:
            elem["parent_id"] = current_section_id

        # Les images des captures HTML ont deja ete exportees vers MinIO par le
        # pipeline (src reecrit) : on propage l'URL sur le noeud Picture.
        image_uri = getattr(getattr(item, "image", None), "uri", None)
        if image_uri and str(image_uri).startswith("http"):
            elem["minio_url"] = str(image_uri)

        elements.append(elem)

    flush_chunk_to_storage(elements, filename_stem, "html")


def _extract_pdf(path_obj: Path) -> None:
    """Convertit un PDF par batchs de pages, avec crop des elements visuels."""
    pdf_path = str(path_obj)
    filename_stem = path_obj.stem

    with fitz.open(pdf_path) as doc:
        total_pages: int = len(doc)

    chunk_buffer: deque[list[dict[str, Any]]] = deque()
    start_page = 1
    global_order = 0
    # Suivi de section global au document (persiste entre les batchs de pages)
    current_section_id: str | None = None

    while start_page <= total_pages:
        end_page = min(start_page + BATCH_PAGE_SIZE - 1, total_pages)
        print(f"[{filename_stem}] Batch {start_page}-{end_page}...")

        try:
            conv_res = converter.convert(pdf_path, page_range=(start_page, end_page))
            chunk_elements: list[dict[str, Any]] = []

            for item, _ in conv_res.document.iterate_items():
                elem = _element_from_item(item, filename_stem, global_order)

                # Hiérarchie : les en-têtes restent rattachés au Document,
                # les autres éléments à la dernière section rencontrée.
                if elem["label"] in SECTION_LABELS:
                    current_section_id = elem["id"]
                else:
                    elem["parent_id"] = current_section_id

                if elem["label"] in VISUAL_LABELS and elem["bbox"]:
                    elem["minio_url"] = crop_and_upload_image(
                        pdf_path,
                        elem["page_no"],
                        elem["bbox"],
                        elem["id"],
                        elem["label"],
                    )

                chunk_elements.append(elem)
                global_order += 1

            chunk_buffer.append(chunk_elements)
            if len(chunk_buffer) >= FLUSH_BUFFER_SIZE:
                flush_chunk_to_storage(chunk_buffer.popleft(), filename_stem, "pdf")

            if hasattr(conv_res.input, "_backend") and conv_res.input._backend:
                conv_res.input._backend.unload()

        except Exception as exc:
            print(f"Batch Error: {exc}")

        if end_page == total_pages:
            break
        start_page = end_page - BATCH_OVERLAP + 1

    while chunk_buffer:
        flush_chunk_to_storage(chunk_buffer.popleft(), filename_stem, "pdf")


@app.post("/extract")
async def extract_document(req: ExtractRequest) -> dict[str, str]:
    """Extrait le contenu structuré d'un document et le persiste dans les stores."""
    path_obj = Path(req.filepath)
    if not path_obj.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if path_obj.suffix.lower() in HTML_SUFFIXES:
        _extract_html(path_obj)
    else:
        _extract_pdf(path_obj)

    return {"status": "success"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
