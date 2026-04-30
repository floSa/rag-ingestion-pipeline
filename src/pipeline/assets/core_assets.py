"""Assets Dagster principaux : extraction, knowledge graph, vectorisation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb
import requests
from dagster import AssetExecutionContext, DynamicPartitionsDefinition, asset
from nebula3.Config import Config
from nebula3.gclient.net import ConnectionPool

from src.pipeline.resources import EmbeddingsResource  # type: ignore[attr-defined]
from src.pipeline.settings import get_settings

pdf_partitions = DynamicPartitionsDefinition(name="pdf_partitions")


@asset(group_name="extraction", partitions_def=pdf_partitions)
def extract_structured_json(context) -> dict[str, Any]:
    """Appelle le microservice Docling pour le fichier de la partition actuelle.

    Args:
        context: Contexte d'exécution Dagster contenant la partition key.

    Returns:
        Dictionnaire JSON avec les clés ``metadata`` et ``elements``.
    """
    # Reconstruire le chemin absolu à partir de la clé de partition relative
    base_dir = "/opt/dagster/app/Datas"
    file_path = str(Path(base_dir) / context.partition_key)
    if not Path(file_path).exists():
        context.log.warning(f"File not found for partition: {file_path}")
        return {}

    settings = get_settings()
    docling_url = settings.docling_service_url + "/extract"
    context.log.info(f"Requesting extraction for: {file_path}")
    resp = requests.post(docling_url, json={"filepath": file_path}, timeout=1200)
    resp.raise_for_status()
    context.log.info(f"Successfully extracted: {file_path}")
    result: dict[str, Any] = resp.json()
    return result


@asset(group_name="knowledge_graph", partitions_def=pdf_partitions)
def build_knowledge_graph(
    context,
    extract_structured_json: dict[str, Any],
) -> bool:
    """Construit les noeuds et relations dans NebulaGraph pour un document.

    Args:
        context: Contexte d'exécution Dagster.
        extract_structured_json: Résultat de l'extraction Docling.

    Returns:
        ``True`` si le graphe a été construit, ``False`` sinon.
    """
    if not extract_structured_json:
        context.log.warning("No data extracted, skipping graph build.")
        return False

    settings = get_settings()
    nebula_host = settings.nebula_host
    nebula_port = settings.nebula_port

    config = Config()
    config.max_connection_pool_size = 10
    pool = ConnectionPool()
    if not pool.init([(nebula_host, nebula_port)], config):
        raise RuntimeError("NebulaGraph init failed")

    try:
        session = pool.get_session("root", "nebula")
        session.execute(
            "CREATE SPACE IF NOT EXISTS rag_space"
            "(partition_num=10, replica_factor=1, vid_type=FIXED_STRING(64));"
        )
        session.execute("USE rag_space;")

        session.execute("CREATE TAG IF NOT EXISTS Document(filename string, type_file string);")
        session.execute(
            "CREATE TAG IF NOT EXISTS Element"
            "(label string, type string, page_no int, text string, minio_url string);"
        )
        session.execute("CREATE EDGE IF NOT EXISTS HAS_PARENT();")

        metadata: dict[str, Any] = extract_structured_json.get("metadata", {})
        elements: list[dict[str, Any]] = extract_structured_json.get("elements", [])
        filename: str = metadata.get("filename", "unknown")
        type_file: str = metadata.get("type_file", "unknown")

        doc_vid = f"doc_{filename}"
        session.execute(
            f"INSERT VERTEX Document(filename, type_file) "
            f'VALUES "{doc_vid}":("{filename}", "{type_file}");'
        )

        for elem in elements:
            vid: str = elem["id"]
            label: str = elem.get("label", "text")
            elem_type: str = elem.get("type", "text")
            page_no: int = elem.get("page_no", 1)
            text_clean = (elem.get("text") or "").replace('"', '\\"').replace("'", "\\'")[:1000]
            minio_url = (elem.get("minio_url") or "").replace('"', '\\"')

            session.execute(
                f"INSERT VERTEX Element(label, type, page_no, text, minio_url) "
                f'VALUES "{vid}":("{label}", "{elem_type}", {page_no}, '
                f'"{text_clean}", "{minio_url}");'
            )

            parent_id = elem.get("reference_id")
            target_vid = doc_vid if parent_id == "DOC" else parent_id
            if target_vid:
                session.execute(f'INSERT EDGE HAS_PARENT() VALUES "{vid}" -> "{target_vid}":();')

        session.release()
    except Exception as exc:
        context.log.error(f"Error building knowledge graph: {exc}")
        return False
    finally:
        pool.close()

    return True


@asset(group_name="vector_db", partitions_def=pdf_partitions)
def vectorize_content(
    context,
    embeddings: EmbeddingsResource,
    extract_structured_json: dict[str, Any],
) -> bool:
    """Génère les embeddings et les insère dans ChromaDB.

    Args:
        context: Contexte d'exécution Dagster.
        embeddings: Ressource fournissant le modèle d'embeddings.
        extract_structured_json: Résultat de l'extraction Docling.

    Returns:
        ``True`` si la vectorisation a réussi, ``False`` sinon.
    """
    if not extract_structured_json:
        return False

    settings = get_settings()
    chroma_host = settings.chroma_host
    chroma_port = settings.chroma_port

    chroma_client = chromadb.HttpClient(host=chroma_host, port=chroma_port)
    collection = chroma_client.get_or_create_collection(name="rag_documents")

    model = embeddings.get_model()
    elements: list[dict[str, Any]] = extract_structured_json.get("elements", [])

    for elem in elements:
        text: str | None = elem.get("text") or elem.get("content")
        if not text:
            continue

        chunks = [text[i : i + 500] for i in range(0, len(text), 500)]

        for i, chunk in enumerate(chunks):
            chunk_id = f"{elem['id']}_part{i}"
            vector: list[float] = model.encode(chunk).tolist()

            metadata: dict[str, Any] = {
                "element_id": elem["id"],
                "graph_node_id": elem["id"],
                "page_position": elem.get("page_position", 0),
                "ref_position": elem.get("ref_position", 0),
                "minio_url": elem.get("minio_url", ""),
            }

            collection.upsert(
                ids=[chunk_id],
                embeddings=[vector],
                metadatas=[metadata],
                documents=[chunk],
            )

    return True
