"""Ecriture des chunks et de leurs embeddings dans ChromaDB.

Deux choix de conception :

- **pas de coupe arbitraire du texte** : le document est decoupe par
  ``HybridChunker`` (:func:`get_chunker`), et le texte stocke est integral.
  Une part des chunks depasse toutefois la fenetre du modele, qui les tronque
  a l'encodage ; les chiffres et leurs causes sont a :func:`get_chunker` ;
- **encodage par lots** : ``SentenceTransformer.encode`` recoit toute la liste
  d'un coup au lieu d'un appel par element, ce qui exploite reellement le GPU.

Les echecs remontent, comme ceux de NebulaGraph. Une exception avalee d'un
cote et levee de l'autre laisserait graphe et vecteurs se desynchroniser sans
bruit.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from src.docling_service import chunking
from src.docling_service.anchoring import block_size, resolve_anchors
from src.docling_service.chunking import has_content
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

    Le client est conserve : en ouvrir un par lot d'ecriture rouvrirait une
    connexion HTTP toutes les quelques pages.

    `chromadb` est importe ici et non au niveau du module : il n'est pas dans
    le venv du depot (les dependances lourdes d'extraction sont dans
    ``Dockerfile.docling``). Un import de module rendrait
    ``src.docling_service.vectors`` inimportable cote hote, et
    ``_inscrire_le_modele`` intestable (registre §3.4, §4.4, §4.5 pour le meme
    cas dans ``index_report``, ``verify_contract`` et ``verify_data``).
    """
    import chromadb

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

    Rend l'exigence 1 du contrat verifiable apres coup : sans cette
    inscription, rien n'enregistre quel modele a ecrit l'index. Un ``.env``
    change entre deux ingestions laisserait une collection portant des vecteurs
    de deux modeles, tous deux en 384 dimensions, sans aucune erreur.

    Trois cas, et un seul refuse :

    - la collection ne porte rien : le modele y est inscrit (cas de tout index
      ecrit avant cette inscription) ;
    - elle porte le meme modele : rien a faire ;
    - **elle porte un autre modele : leve.** Ecrire par-dessus melangerait deux
      espaces vectoriels dans une meme collection.

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

    Ce n'est pas une garantie contre la troncature a l'encodage. Mesure le
    31 aout 2026 sur les 4 365 chunks du corpus, deux causes :

    1. **Le decoupeur ne peut pas fractionner une table.** Une table serialisee
       en Markdown est un bloc indivisible : il la rend telle quelle, plus
       longue que la fenetre. Les **65** chunks qui depassent sur le texte
       stocke sont tous des tables. Reduire la fenetre n'y change rien ; le
       decoupage des tables est un chantier a part (registre 7.1) ;
    2. **le titre de section est prefixe apres le decoupage.** ``HybridChunker``
       compte ses tokens sur sa propre serialisation ; ``write_elements``
       prepose ensuite le titre pour l'encodage. **72** chunks franchissent la
       fenetre par ce seul prefixe.

    Le nombre reel de chunks tronques par le modele est donc **137 (3,1 %)**,
    celui que rapporte ``index_report``. Les maxima suivent le meme ecart :
    **140** tokens sur le texte stocke contre **149** sur le texte encode.

    Ce docstring est le site de reference de ces cinq nombres (65, 72, 137,
    140, 149) et de la fenetre de **128** tokens ; les autres documents y
    renvoient.

    La fenetre n'est pas un reglage : c'est ``modele.max_seq_length``, lu a
    l'execution sur le modele du contrat (registre 5.1).

    Remesure le 2 septembre 2026 sur l'index vivant, avec ce code monte dans
    l'image d'extraction (registre 4.27) : ``python -m src.index_report`` rend
    « limite : 128 tokens », « chunks tronques par le modele : 137 (3.1 %) »,
    « tokens : mediane 95, maximum 149 ».
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
    ensemble et respecte la structure du document.

    Les chunks peuvent depasser la fenetre du modele (voir :func:`get_chunker`).
    Chaque chunk est rattache, via la reference interne Docling, a l'element
    du service d'ou part sa lecture.

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
        # Un chunk sans ancre connue serait rattache au hasard. Il ne troue aucun
        # compte : `resolve_anchors` ne compte que les chunks dont l'ancre est
        # connue, donc `chunk_count` l'ignore aussi.
        if ancre is None:
            continue

        # Le filtre ne vaut que pour un chunk autonome (registre 4.28.a).
        # `resolve_anchors` fixe `chunk_count` avant ce filtrage : jeter un chunk
        # qui a des freres laisserait un trou dans le jeu, avec un compte qui
        # annonce le morceau manquant, et l'agent concatenerait un texte troue.
        # Cas mesure sur l'index vivant : deux blocs de code dont une fenetre du
        # milieu etait courte.
        #
        # Recalculer le compte apres filtrage rendrait la perte indetectable
        # (le controle `jeux_de_chunks_incomplets` ne la verrait plus). Garder
        # la fenetre ne coute que quelques vecteurs de faible valeur : 2 chunks
        # sur 4 365 sur l'index mesure.
        autonome = ancre.count == 1
        if autonome and (not has_content(texte) or len(texte) < settings.min_chunk_chars):
            continue

        element = ancre.element
        element_id = str(element["id"])
        # La forme de l'id est une clause du contrat, fixee par
        # `chunking.chunk_id` seul (registre 5.1) ; `verify_contract` compte les
        # ids suffixes (974 sur 4 365, mesure le 2 septembre 2026).
        ids.append(chunking.chunk_id(element_id, ancre.index, ancre.count))
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
                page_no_end=int(element.get("page_no_end") or element.get("page_no") or 0),
                media_url=str(element.get("media_url") or ""),
                object_key=str(element.get("object_key") or ""),
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


def delete_document(identity: DocumentIdentity, collection: Any = None) -> int:
    """Retire de l'index vectoriel tous les chunks d'un document.

    Pendant de ``NebulaWriter.delete_document``. Les identifiants derivent du
    texte (``elements.py``) : un texte modifie produit de nouveaux
    identifiants, et `upsert` ecrit les nouveaux sans toucher aux anciens, qui
    survivraient en orphelins. Le capteur Dagster declenchant sur ``mtime``,
    mettre a jour un document est le cas nominal (registre 4.2).

    La suppression vise ``source_path`` et jamais ``filename`` : ``source_path``
    est l'identite d'un document (contrat, exigence 3), et le corpus porte deux
    ``Preface.html``. Une purge par nom emporterait les deux.

    Args:
        identity: Identite du document a purger.
        collection: Collection ChromaDB. Ouverte au besoin ; l'argument existe
            pour que la decision soit eprouvable sans ChromaDB.

    Returns:
        Nombre de chunks retires : une purge muette ne dirait pas si elle a
        retire trois chunks ou trois mille.
    """
    cible = get_collection() if collection is None else collection
    clause = {"source_path": identity.source_path}
    presents = cible.get(where=clause, include=[])
    nombre = len(presents.get("ids") or [])
    if nombre:
        cible.delete(where=clause)
        logger.info("ChromaDB: %d chunks retires pour %s", nombre, identity.source_path)
    return nombre


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
    # index_report doit tokeniser exactement le meme texte pour compter les
    # troncatures (registre 3.4).
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
