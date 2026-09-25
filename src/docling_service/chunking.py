"""Ce que le modele d'embedding recoit, et sous quel identifiant il est ecrit.

Ce module ne decoupe rien. Le decoupage est fait par
`HybridChunker(tokenizer=..., max_tokens=modele.max_seq_length)`
(`vectors.get_chunker`), qui suit la structure du document et non un compte de
caracteres. Il n'existe donc pas de taille de chunk ni de recouvrement en
caracteres a regler (registre 5.1).

Le module expose ce que la production appelle :

- :func:`contextualize` et :func:`embedding_inputs` — le texte tel que le modele
  le recoit, construit a un seul endroit, partage par `vectors` et par
  `index_report` ;
- :func:`chunk_id` — la forme de l'identifiant ChromaDB, qui est une clause du
  contrat avec `rag-agent-chat` ;
- :func:`has_content` — le filtre qui decide si un texte merite un vecteur
  (anciennement dans `blocks.py`, registre 5.2).

Le module ne depend que de la bibliotheque standard : il reste testable sans
sentence-transformers ni ChromaDB.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def contextualize(text: str, section_title: str) -> str:
    """Prepose le titre de sa section au texte destine au modele d'embedding.

    Un passage isole de son titre perd une part de son sens : « la moyenne est
    sensible aux valeurs extremes » ne dit pas de quoi elle est la moyenne. Le
    titre de section restitue ce contexte au vecteur, sans cout de calcul.
    C'est la technique ``contextualize()`` de Docling, et le principe du
    *contextual retrieval*.

    Le texte **stocke** reste le texte brut : l'utilisateur voit le passage tel
    qu'il figure dans le document, seul le vecteur porte le prefixe.

    Args:
        text: Texte du chunk.
        section_title: Titre de la section a laquelle il appartient.

    Returns:
        Le texte prefixe, ou le texte inchange si le titre est vide ou deja
        present en tete (cas du chunk qui *est* le titre).
    """
    title = section_title.strip()
    if not title:
        return text
    if text.lstrip().lower().startswith(title.lower()):
        return text
    return f"{title}\n\n{text}"


def embedding_inputs(
    texts: Sequence[str],
    metadatas: Sequence[Mapping[str, Any]],
    embed_section_context: bool,
) -> list[str]:
    """Construit exactement ce que le modele d'embedding recoit.

    C'est le seul endroit qui en decide. ``vectors.write_elements`` (qui
    encode) et ``index_report`` (qui compte les troncatures) l'appellent tous
    les deux. Avec deux constructions separees, l'instrument mesurait un autre
    texte que celui encode et sous-comptait les troncatures d'un facteur 2,1
    (mesure sur le corpus complet ; les deux comptes sont documentes a
    :func:`~src.docling_service.vectors.get_chunker`).

    Args:
        texts: Textes stockes, dans l'ordre.
        metadatas: Metadonnees alignees sur ``texts``, dont ``section_title``.
        embed_section_context: Reglage ``settings.embed_section_context``. A
            faux, aucun prefixe n'est ajoute — et l'instrument doit dire vrai
            dans les deux positions.

    Returns:
        Les textes tels que le modele les recoit, alignes sur l'entree.

    Raises:
        ValueError: Si les deux suites n'ont pas la meme longueur. Un decalage
            d'un rang prefixerait chaque chunk du titre de son voisin, sans que
            rien ne le signale.
    """
    if len(texts) != len(metadatas):
        raise ValueError(
            f"{len(texts)} textes pour {len(metadatas)} metadonnees : "
            "le prefixe serait pris sur le mauvais chunk"
        )
    if not embed_section_context:
        return list(texts)
    return [
        contextualize(text, str(meta.get("section_title") or ""))
        for text, meta in zip(texts, metadatas, strict=True)
    ]


def chunk_id(element_id: str, index: int, count: int) -> str:
    """Derive l'id ChromaDB d'un chunk. C'est le seul endroit qui fixe cette forme.

    Un element tenant en un seul chunk conserve son id nu ; les elements
    multi-chunks recoivent un suffixe ``#n``. C'est une clause du contrat, que
    `verify_contract` compte (« ids de chunk suffixes en #n ») : 974 sur 4 365
    sur l'index vivant, mesure le 2 septembre 2026. Un suffixe inconditionnel
    porterait ce compte a 4 365 sur 4 365.

    Le contrat avec ``rag-agent-chat`` est preserve : le consommateur lit
    ``chunk_id`` (l'id ChromaDB) et ``element_id`` (le hash 10 hexa) dans deux
    champs distincts, et ne valide le format ``^[a-f0-9]{10}$`` que sur le
    second.

    `vectors.build_chunks` appelle cette fonction pour chaque chunk, ce qui
    place la forme de l'id sous test (registre 5.1). Une forme erronee ne
    duplique pas les chunks a la reingestion : `extraction.extract` appelle
    `storage.forget_document(identity)` avant la conversion, et
    `vectors.delete_document` supprime par ``where={"source_path": ...}``,
    jamais par id. Elle casse en revanche la clause du contrat.

    Args:
        element_id: Identifiant de l'element dans NebulaGraph.
        index: Rang du chunk dans son element, a partir de 0.
        count: Nombre de chunks produits pour cet element.

    Returns:
        L'identifiant du chunk.
    """
    return element_id if count == 1 else f"{element_id}#{index}"


def has_content(text: str) -> bool:
    """Indique si un texte porte au moins un caractere alphanumerique.

    Un texte qui n'en contient aucun est un artefact de mise en page — filet de
    tableau, puce, ponctuation isolee — et n'a rien a faire dans un index
    vectoriel.

    Usage en production, dans `vectors.build_chunks` :

        autonome = ancre.count == 1
        if autonome and (not has_content(texte) or len(texte) < min_chunk_chars):
            continue

    Le filtre ne s'applique qu'a un chunk qui est le seul de son element
    (registre 4.28.a). Une fenetre au milieu d'un texte continu est conservee
    meme courte, sans quoi l'agent concatenerait un texte troue. L'element
    ecarte reste dans NebulaGraph : seul l'index vectoriel est nettoye.

    Args:
        text: Texte a examiner.

    Returns:
        Vrai si le texte porte au moins un caractere alphanumerique.
    """
    return any(character.isalnum() for character in text)
