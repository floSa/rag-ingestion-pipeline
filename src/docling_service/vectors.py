"""Ecriture des chunks et de leurs embeddings dans ChromaDB.

Deux corrections par rapport a la version initiale :

- **plus de troncature a 1000 caracteres** : les textes longs sont decoupes en
  fenetres recouvrantes au lieu d'etre coupes, dans l'embedding comme dans le
  document stocke. Cette ligne disait « plus de troncature », sans borne : une
  phrase d'exhaustivite, et elle est fausse. `mesure` le 31 aout 2026 sur
  4 365 chunks : **137 (3,1 %) depassent la fenetre de 128 tokens** du modele,
  qui les tronque lui-meme. Voir :func:`get_chunker` pour la cause, qui est
  structurelle et non un reglage ;
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
from functools import lru_cache
from typing import Any

import chromadb

from src.docling_service import chunking
from src.docling_service.anchoring import block_size, resolve_anchors
from src.docling_service.blocks import has_content
from src.docling_service.elements import DocumentFacts, DocumentIdentity
from src.docling_service.embedding import (
    EmbeddingContractError,
    canonical_name,
    get_embedding_model,
)
from src.docling_service.settings import get_settings
from src.pipeline.schemas import ChunkMetadata

logger = logging.getLogger(__name__)

COLLECTION_NAME = "rag_documents"

_collection: Any = None
_collection_lock = threading.Lock()


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
            _inscrire_le_modele(_collection, settings.embedding_model_name)
        return _collection


def _inscrire_le_modele(collection: Any, modele: str) -> None:
    """Inscrit sur la collection le modele qui produit ses vecteurs, et refuse d'en melanger deux.

    L'exigence 1 du contrat n'etait verifiable par personne apres coup : rien
    n'enregistrait quel modele avait ecrit l'index. Un ``.env`` change entre
    deux ingestions laissait une collection qui portait des vecteurs de deux
    modeles, tous deux en 384 dimensions, sans qu'aucune erreur ne le signale.

    Trois cas, et un seul refuse :

    - la collection ne porte rien : on l'inscrit. C'est le cas de tout index
      ecrit avant ce garde ;
    - elle porte le meme modele : rien a faire ;
    - **elle porte un AUTRE modele : on leve.** Ecrire par-dessus melangerait
      deux espaces vectoriels dans une meme collection, et c'est la panne la
      plus couteuse du systeme.

    Args:
        collection: Collection ChromaDB ouverte.
        modele: ``settings.embedding_model_name``.

    Raises:
        EmbeddingContractError: Si la collection a ete produite par un autre
            modele. Le job echoue plutot que d'ecrire un index mixte.
    """
    enregistre = (getattr(collection, "metadata", None) or {}).get("embedding_model")
    if enregistre and canonical_name(str(enregistre)) != canonical_name(modele):
        raise EmbeddingContractError(
            f"la collection {COLLECTION_NAME} a ete produite par "
            f"« {enregistre} » et l'ingestion tourne avec « {modele} ». Ecrire "
            "par-dessus melangerait deux espaces vectoriels dans une meme "
            "collection, sans qu'aucune erreur ne le signale a l'usage. Purger "
            "et reingerer, ou corriger EMBEDDING_MODEL_NAME."
        )
    if not enregistre:
        collection.modify(metadata={"embedding_model": canonical_name(modele)})
        logger.info("ChromaDB: collection tracee au modele %s", canonical_name(modele))


@lru_cache(maxsize=1)
def get_chunker() -> Any:
    """Retourne le decoupeur Docling, construit au premier appel.

    ``HybridChunker`` decoupe en respectant la structure du document *et* la
    fenetre du modele d'embedding. Il recoit le tokenizer du modele lui-meme,
    et non une approximation.

    Ce docstring affirmait que « c'est ce qui garantit qu'aucun chunk ne sera
    tronque a l'encodage ». **C'est faux, et de deux facons distinctes**, toutes
    deux mesurees le 31 aout 2026 sur les 4 365 chunks du corpus :

    1. **Le decoupeur ne peut pas fractionner une table.** Une table serialisee
       en Markdown est un bloc indivisible pour lui : il la rend telle quelle,
       plus longue que la fenetre. Les **65** chunks qui depassent deja sur le
       texte stocke sont **65 sur 65 des tables** — aucun autre label. Ce n'est
       pas un reglage a corriger ici : reduire la fenetre ne fractionne pas
       davantage, et refaire le decoupage des tables est un chantier a part
       (registre 7.1). Ce qui manquait etait de le MESURER et de l'ecrire ;
    2. **le titre de section est prefixe APRES le decoupage.** ``HybridChunker``
       compte ses tokens sur sa propre serialisation ; ``write_elements``
       prepose ensuite le titre pour l'encodage. **72** chunks franchissent la
       fenetre par ce seul prefixe, et le decoupeur ne pouvait pas le prevoir.

    Le nombre reel de chunks tronques par le modele est donc **137 (3,1 %)**, et
    ``index_report`` le dit desormais — il en annoncait 65.
    """
    from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer

    modele = get_embedding_model()
    limite = int(modele.max_seq_length)
    logger.info("Decoupeur Docling : fenetre de %d tokens", limite)
    return HybridChunker(
        tokenizer=HuggingFaceTokenizer(tokenizer=modele.tokenizer, max_tokens=limite)
    )


def build_chunks(
    elements: Sequence[dict[str, Any]],
    identity: DocumentIdentity,
    facts: DocumentFacts | None = None,
    document: Any = None,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Decoupe le document en chunks prets pour ChromaDB.

    Le decoupage est confie a ``HybridChunker`` : il regroupe ce qui va
    ensemble, respecte la structure du document, et remplit la fenetre du
    modele sans jamais la depasser. Nos identifiants restent les notres —
    chaque chunk est rattache a l'element d'ou part sa lecture, via la
    reference interne Docling.

    Args:
        elements: Elements produits par ``DocumentAccumulator``.
        identity: Identite du document.
        facts: Format, langue et empreinte du document. La langue est reportee
            sur chaque chunk pour que l'agent puisse filtrer sans repasser par
            le graphe.
        document: Document Docling converti. Sans lui, rien n'est indexe : le
            decoupage a besoin de la structure, pas seulement du texte.

    Returns:
        Triplet (ids, textes, metadonnees), aligne index par index. Les
        elements ecartes — sans texte, ou trop courts pour porter du sens —
        restent presents dans le graphe.
    """
    if document is None:
        logger.warning("[%s] aucun document Docling fourni : rien a indexer", identity.key)
        return [], [], []

    settings = get_settings()
    language = facts.language if facts else ""
    liste = list(elements)

    morceaux = list(get_chunker().chunk(document))
    refs = [[str(item.self_ref) for item in c.meta.doc_items] for c in morceaux]
    ancres = resolve_anchors(refs, liste)

    ids: list[str] = []
    texts: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for morceau, ancre, refs_du_chunk in zip(morceaux, ancres, refs, strict=True):
        texte = morceau.text.strip()
        # Un chunk sans ancre connue serait rattache au hasard ; un chunk sans
        # caractere alphanumerique n'a rien a apporter a une recherche.
        if ancre is None or not has_content(texte) or len(texte) < settings.min_chunk_chars:
            continue

        element = ancre.element
        element_id = str(element["id"])
        chunk_id = element_id if ancre.count == 1 else f"{element_id}#{ancre.index}"

        ids.append(chunk_id)
        texts.append(texte)
        metadatas.append(
            ChunkMetadata(
                element_id=element_id,
                graph_node_id=element_id,
                filename=identity.filename,
                collection=identity.collection,
                source_path=identity.source_path,
                language=language,
                label=str(element.get("label") or ""),
                page_no=int(element.get("page_no") or 0),
                minio_url=str(element.get("minio_url") or ""),
                reference_id=str(element.get("reference_id") or "DOC"),
                depth=int(element.get("depth") or 0),
                section_title=str(element.get("section_title") or ""),
                page_position=int(element.get("page_position") or 0),
                ref_position=int(element.get("ref_position") or 0),
                chunk_index=ancre.index,
                chunk_count=ancre.count,
                block_size=block_size(refs_du_chunk, liste),
            ).model_dump()
        )

    return ids, texts, metadatas


def write_elements(
    elements: Sequence[dict[str, Any]],
    identity: DocumentIdentity,
    facts: DocumentFacts | None = None,
    document: Any = None,
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
    ids, texts, metadatas = build_chunks(elements, identity, facts, document)
    if not ids:
        return 0

    settings = get_settings()
    # Le vecteur est calcule sur le texte contextualise, le document stocke
    # reste le texte brut : le passage s'affiche tel quel cote agent.
    #
    # La construction vit dans chunking.embedding_inputs et non ici, parce que
    # index_report doit tokeniser EXACTEMENT le meme texte pour compter les
    # troncatures. Quand les deux decidaient chacun de leur cote, l'instrument
    # en annoncait la moitie (registre 3.4).
    embed_texts = chunking.embedding_inputs(texts, metadatas, settings.embed_section_context)

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
