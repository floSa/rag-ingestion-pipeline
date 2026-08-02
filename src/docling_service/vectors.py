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

from src.docling_service.blocks import build_blocks, has_content
from src.docling_service.chunking import chunk_ids, chunk_text, contextualize
from src.docling_service.elements import DocumentFacts, DocumentIdentity
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
    elements: Sequence[dict[str, Any]],
    identity: DocumentIdentity,
    facts: DocumentFacts | None = None,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Regroupe puis decoupe les elements en chunks prets pour ChromaDB.

    Les elements sont d'abord fusionnes en blocs coherents (voir
    :mod:`src.docling_service.blocks`) : l'analyse de layout produit quantite de
    fragments isoles qui n'ont aucun sens une fois vectorises. Les blocs encore
    trop longs sont ensuite decoupes en fenetres recouvrantes.

    Args:
        elements: Elements produits par ``DocumentAccumulator``.
        identity: Identite du document.
        facts: Format, langue et empreinte du document. La langue est reportee
            sur chaque chunk pour que l'agent puisse filtrer sans repasser par
            le graphe.

    Returns:
        Triplet (ids, textes, metadonnees), aligne index par index. Les
        elements ecartes — sans texte, ou trop courts pour porter du sens —
        restent presents dans le graphe.
    """
    settings = get_settings()
    language = facts.language if facts else ""
    ids: list[str] = []
    texts: list[str] = []
    metadatas: list[dict[str, Any]] = []

    blocks = build_blocks(
        list(elements),
        target_chars=settings.chunk_size,
        min_chars=settings.min_chunk_chars,
    )

    for block in blocks:
        chunks = chunk_text(
            block.text,
            size=settings.chunk_size,
            overlap=settings.chunk_overlap,
        )
        if not chunks:
            continue

        anchor = block.anchor
        element_id = str(anchor["id"])
        for index, (chunk_id, chunk) in enumerate(
            zip(chunk_ids(element_id, len(chunks)), chunks, strict=True)
        ):
            # Le decoupage en fenetres peut faire tomber une fenetre entiere
            # sur une suite de ponctuation ou un filet de tableau : le bloc
            # avait du contenu, cette fenetre-la n'en a pas.
            if not has_content(chunk):
                continue
            ids.append(chunk_id)
            texts.append(chunk)
            metadatas.append(
                ChunkMetadata(
                    element_id=element_id,
                    graph_node_id=element_id,
                    filename=identity.filename,
                    collection=identity.collection,
                    source_path=identity.source_path,
                    language=language,
                    label=str(anchor.get("label") or ""),
                    page_no=int(anchor.get("page_no") or 0),
                    minio_url=str(anchor.get("minio_url") or ""),
                    reference_id=str(anchor.get("reference_id") or "DOC"),
                    depth=int(anchor.get("depth") or 0),
                    section_title=str(anchor.get("section_title") or ""),
                    page_position=int(anchor.get("page_position") or 0),
                    ref_position=int(anchor.get("ref_position") or 0),
                    chunk_index=index,
                    chunk_count=len(chunks),
                    block_size=block.size,
                ).model_dump()
            )

    return ids, texts, metadatas


def write_elements(
    elements: Sequence[dict[str, Any]],
    identity: DocumentIdentity,
    facts: DocumentFacts | None = None,
) -> int:
    """Encode et enregistre les elements dans ChromaDB.

    Args:
        elements: Elements produits par ``DocumentAccumulator``.
        identity: Identite du document.
        facts: Format, langue et empreinte du document.

    Returns:
        Nombre de chunks ecrits.

    Raises:
        Exception: Toute erreur d'encodage ou d'ecriture est propagee, pour
            faire echouer le job plutot que de laisser l'index incomplet.
    """
    ids, texts, metadatas = build_chunks(elements, identity, facts)
    if not ids:
        return 0

    settings = get_settings()
    # Le vecteur est calcule sur le texte contextualise, le document stocke
    # reste le texte brut : le passage s'affiche tel quel cote agent.
    if settings.embed_section_context:
        embed_texts = [
            contextualize(text, str(meta.get("section_title") or ""))
            for text, meta in zip(texts, metadatas, strict=True)
        ]
    else:
        embed_texts = texts

    vectors = get_embedding_model().encode(
        embed_texts,
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

    logger.info("ChromaDB: %d chunks ecrits pour %s", len(ids), identity.key)
    return len(ids)
