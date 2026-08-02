"""Decoupage des textes longs destines a la base vectorielle.

L'ingestion tronquait les textes a 1000 caracteres, dans l'embedding comme dans
le document stocke : un paragraphe long etait ampute en silence et sa fin
devenait inatteignable par la recherche. On decoupe desormais en fenetres
recouvrantes, sans rien perdre.

Le module ne depend que de la bibliotheque standard : il reste testable sans
sentence-transformers ni ChromaDB.
"""

from __future__ import annotations

# ~450 caracteres : le modele multilingue encode 128 tokens, soit environ 500
# caracteres de prose francaise ou anglaise. Au-dela il tronque de lui-meme, et
# le vecteur ne represente plus que le debut du texte sans que rien ne le
# signale. Le recouvrement evite de couper une idee en deux.
DEFAULT_CHUNK_SIZE = 450
DEFAULT_CHUNK_OVERLAP = 75


def chunk_text(
    text: str,
    size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Decoupe un texte en fenetres recouvrantes alignees sur les mots.

    Args:
        text: Texte a decouper.
        size: Taille maximale d'une fenetre, en caracteres.
        overlap: Recouvrement entre deux fenetres consecutives, en caracteres.

    Returns:
        Liste des fenetres, vide si le texte est vide. Un texte plus court que
        ``size`` est retourne tel quel, en un seul element.

    Raises:
        ValueError: Si ``size`` est nul ou negatif, ou si ``overlap`` n'est pas
            strictement inferieur a ``size`` (la progression ne serait pas garantie).
    """
    if size <= 0:
        raise ValueError("size doit etre strictement positif")
    if not 0 <= overlap < size:
        raise ValueError("overlap doit etre compris entre 0 et size (exclu)")

    stripped = text.strip()
    if not stripped:
        return []
    if len(stripped) <= size:
        return [stripped]

    chunks: list[str] = []
    start = 0
    while start < len(stripped):
        end = min(start + size, len(stripped))
        if end < len(stripped):
            # Reculer jusqu'a la derniere frontiere de mot, sans jamais rogner
            # plus que le recouvrement (sinon un texte sans espace boucle).
            boundary = stripped.rfind(" ", start + size - overlap, end)
            if boundary > start:
                end = boundary

        chunk = stripped[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(stripped):
            break
        # max(..., start + 1) : garantit la progression meme si end - overlap
        # retombe avant start (fenetre courte apres recul sur un espace).
        start = max(end - overlap, start + 1)

    return chunks


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


def chunk_ids(element_id: str, count: int) -> list[str]:
    """Derive les ids ChromaDB des chunks d'un element.

    Un element tenant en un seul chunk conserve son id nu : les documents deja
    ingeres gardent leur identifiant et l'upsert les met a jour au lieu de les
    dupliquer. Les elements multi-chunks recoivent un suffixe ``#n``.

    Le contrat avec ``rag-agent-chat`` est preserve : le consommateur lit
    ``chunk_id`` (l'id ChromaDB) et ``element_id`` (le hash 10 hexa) dans deux
    champs distincts, et ne valide le format ``^[a-f0-9]{10}$`` que sur le
    second.

    Args:
        element_id: Identifiant de l'element dans NebulaGraph.
        count: Nombre de chunks produits pour cet element.

    Returns:
        Liste de ``count`` identifiants.
    """
    if count == 1:
        return [element_id]
    return [f"{element_id}#{index}" for index in range(count)]
