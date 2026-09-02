"""Ce que le modele d'embedding recoit, et sous quel identifiant il est ecrit.

**CE MODULE NE DECOUPE PLUS RIEN, ET SON EN-TETE L'AFFIRMAIT ENCORE.** Il
portait « on decoupe desormais en fenetres recouvrantes, sans rien perdre » et
exposait `chunk_text`, `DEFAULT_CHUNK_SIZE` et `DEFAULT_CHUNK_OVERLAP` : trois
symboles **sans aucun appelant en production**, seuls les tests de ce module les
exercaient (registre 5.1). Le decoupage reel est
`HybridChunker(tokenizer=..., max_tokens=modele.max_seq_length)`
(`vectors.get_chunker`), qui decoupe sur la STRUCTURE du document et non sur un
compte de caracteres. Le debat « 900 contre 450 » qui a occupe la documentation
de ce depot etait donc vide : les deux valeurs etaient fausses, parce qu'aucune
n'etait lue.

Ce qui reste ici est ce que la production appelle, et rien d'autre :

- :func:`contextualize` et :func:`embedding_inputs` — le texte tel que le modele
  le recoit, a un seul site, partage par `vectors` et par `index_report` ;
- :func:`chunk_id` — la forme de l'identifiant ChromaDB, qui est une clause du
  contrat avec `rag-agent-chat` ;
- :func:`has_content` — le filtre qui decide si un texte merite un vecteur. Il
  vivait dans `blocks.py`, dont il etait le SEUL symbole encore appele : le
  module portait par ailleurs une doctrine de regroupement que la production
  n'applique plus (registre 5.2), et un module nomme « blocs » qui ne contient
  aucune notion de bloc est un nom qui ment.

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

    C'est le SEUL site qui en decide, et c'en est le point. Il y en avait deux :
    ``vectors.write_elements`` prefixait le titre de section avant d'encoder, et
    ``index_report`` tokenisait le texte stocke pour compter les troncatures.
    L'instrument mesurait donc un autre texte que celui qu'il pretendait
    surveiller, et sous-comptait d'un facteur 2 — 65 chunks annonces au-dela de
    la fenetre contre 137 reels (`mesure`, 31 aout 2026, 4 365 chunks).

    Corriger le calcul de l'instrument n'aurait ferme que l'ecart du jour : deux
    endroits qui decident du meme texte finissent par diverger a nouveau. Il n'y
    en a plus qu'un, et les deux appelants le partagent.

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
    """Derive l'id ChromaDB d'un chunk, et c'est le SEUL site de cette forme.

    Un element tenant en un seul chunk conserve son id nu : les documents deja
    ingeres gardent leur identifiant et l'upsert les met a jour au lieu de les
    dupliquer. Les elements multi-chunks recoivent un suffixe ``#n``.

    Le contrat avec ``rag-agent-chat`` est preserve : le consommateur lit
    ``chunk_id`` (l'id ChromaDB) et ``element_id`` (le hash 10 hexa) dans deux
    champs distincts, et ne valide le format ``^[a-f0-9]{10}$`` que sur le
    second.

    **CETTE FONCTION S'APPELAIT `chunk_ids`, RENDAIT UNE LISTE, ET N'AVAIT AUCUN
    APPELANT** (registre 5.1). Elle n'etait pas pour autant du code mort a
    amputer : `vectors.build_chunks` reconstruisait la MEME forme par une
    seconde expression en ligne, et **cette expression-la n'etait gardee par
    rien**. `mesure` le 2 septembre 2026 sur le code livre par le lot 4 :
    remplacer la ligne de `vectors.py` par un suffixe inconditionnel
    (`f"{element_id}#{ancre.index}"`) laisse la suite ENTIEREMENT VERTE, 862
    tests — alors que cette mutation fait qu'une reingestion DUPLIQUE chaque
    element au lieu de le mettre a jour, l'id nu ayant disparu.

    Retirer la fonction aurait donc retire les seuls tests d'une clause du
    contrat dont le site de production n'a aucun garde. Elle est rendue
    UNITAIRE — c'est ce que l'appelant demande, un chunk a la fois — et
    l'appelant la traverse.

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

    **CE FILTRE JETTE, ET LE MODULE D'OU IL VIENT AFFIRMAIT L'INVERSE.**
    `blocks.py` ouvrait sur « la reponse retenue suit l'etat de l'art du
    decoupage pour RAG : **fusionner plutot que jeter** », doctrine de 33 lignes
    qui decrivait `build_blocks` — sans appelant depuis que `HybridChunker` l'a
    remplace. Ce que la production fait, `mesure` a `vectors.py:230` :

        autonome = ancre.count == 1
        if autonome and (not has_content(texte) or len(texte) < min_chunk_chars):
            continue

    Elle JETTE donc, et la borne compte : depuis le lot 4 (registre 4.28.a), le
    filtre ne s'applique qu'a un chunk qui est le SEUL de son element. Une
    fenetre du MILIEU d'un texte continu est conservee meme courte, sans quoi
    l'agent concatenerait un texte troue. L'element ecarte, lui, reste dans
    NebulaGraph : c'est l'index vectoriel qui est nettoye, pas le document.

    Args:
        text: Texte a examiner.

    Returns:
        Vrai si le texte porte au moins un caractere alphanumerique.
    """
    return any(character.isalnum() for character in text)
