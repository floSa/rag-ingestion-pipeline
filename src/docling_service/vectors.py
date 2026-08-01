"""Ecriture des chunks et de leurs embeddings dans ChromaDB.

Deux corrections par rapport a la version initiale :

- **plus de troncature** : les textes longs sont decoupes en fenetres
  recouvrantes au lieu d'etre coupes a 1000 caracteres, dans l'embedding comme
  dans le document stocke ;
- **encodage par lots** : ``SentenceTransformer.encode`` recoit toute la liste
  d'un coup au lieu d'un appel par element, ce qui exploite reellement le GPU.

Les echecs ne sont plus avales : ils remontent, comme ceux de NebulaGraph. Une
exception avalee d'un cote et levee de l'autre laissait graphe et vecteurs se
desynchroniser sans bruit.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import Any

import chromadb
from sentence_transformers import SentenceTransformer

from src.docling_service.chunking import chunk_ids, chunk_text
from src.docling_service.settings import get_settings
from src.pipeline.schemas import ChunkMetadata

logger = logging.getLogger(__name__)

COLLECTION_NAME = "rag_documents"

_model: SentenceTransformer | None = None
_model_lock = threading.Lock()
_collection: Any = None
_collection_lock = threading.Lock()


def get_embedding_model() -> SentenceTransformer:
    """Retourne le modele d'embedding, charge au premier appel."""
    global _model
    with _model_lock:
        if _model is None:
            name = get_settings().embedding_model_name
            logger.info("Chargement du modele d'embedding %s...", name)
            _model = SentenceTransformer(name)
        return _model


def get_collection() -> Any:
    """Retourne la collection ChromaDB, ouverte au premier appel.

    Le client est conserve : en ouvrir un par lot d'ecriture rouvrait une
    connexion HTTP toutes les quelques pages.
    """
    global _collection
    with _collection_lock:
        if _collection is None:
            settings = get_settings()
            client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
            _collection = client.get_or_create_collection(name=COLLECTION_NAME)
        return _collection


def build_chunks(
    elements: Sequence[dict[str, Any]], filename: str
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Decoupe les elements en chunks prets pour ChromaDB.

    Args:
        elements: Elements produits par ``DocumentAccumulator``.
        filename: Nom du document sans extension.

    Returns:
        Triplet (ids, textes, metadonnees), aligne index par index. Les
        elements sans texte (images sans legende) sont ignores : ils vivent
        dans le graphe, pas dans l'index vectoriel.
    """
    settings = get_settings()
    ids: list[str] = []
    texts: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for element in elements:
        chunks = chunk_text(
            str(element.get("text") or ""),
            size=settings.chunk_size,
            overlap=settings.chunk_overlap,
        )
        if not chunks:
            continue

        element_id = str(element["id"])
        for index, (chunk_id, chunk) in enumerate(
            zip(chunk_ids(element_id, len(chunks)), chunks, strict=True)
        ):
            ids.append(chunk_id)
            texts.append(chunk)
            metadatas.append(
                ChunkMetadata(
                    element_id=element_id,
                    graph_node_id=element_id,
                    filename=filename,
                    label=str(element.get("label") or ""),
                    page_no=int(element.get("page_no") or 0),
                    minio_url=str(element.get("minio_url") or ""),
                    reference_id=str(element.get("reference_id") or "DOC"),
                    page_position=int(element.get("page_position") or 0),
                    ref_position=int(element.get("ref_position") or 0),
                    chunk_index=index,
                    chunk_count=len(chunks),
                ).model_dump()
            )

    return ids, texts, metadatas


def write_elements(elements: Sequence[dict[str, Any]], filename: str) -> int:
    """Encode et enregistre les elements dans ChromaDB.

    Args:
        elements: Elements produits par ``DocumentAccumulator``.
        filename: Nom du document sans extension.

    Returns:
        Nombre de chunks ecrits.

    Raises:
        Exception: Toute erreur d'encodage ou d'ecriture est propagee, pour
            faire echouer le job plutot que de laisser l'index incomplet.
    """
    ids, texts, metadatas = build_chunks(elements, filename)
    if not ids:
        return 0

    settings = get_settings()
    vectors = get_embedding_model().encode(
        texts,
        batch_size=settings.embedding_batch_size,
        show_progress_bar=False,
    )
    embeddings: list[list[float]] = [vector.tolist() for vector in vectors]

    collection = get_collection()
    step = settings.chroma_upsert_batch
    for start in range(0, len(ids), step):
        stop = start + step
        collection.upsert(
            ids=ids[start:stop],
            embeddings=embeddings[start:stop],
            documents=texts[start:stop],
            metadatas=metadatas[start:stop],
        )

    logger.info("ChromaDB: %d chunks ecrits pour %s", len(ids), filename)
    return len(ids)
