"""Normalisation du Markdown avant conversion Docling.

Docling convertit le Markdown **ligne a ligne** : un fichier dont les
paragraphes sont coupes a 80 colonnes — la forme la plus courante des exports
et des notes ecrites a la main — produit un element par ligne source. La
recherche vectorielle porte alors sur des fragments de 75 caracteres au lieu de
paragraphes, ce qui degrade fortement la pertinence.

On recolle donc les lignes d'un meme paragraphe avant de passer le fichier a
Docling. Un fichier dont les paragraphes tiennent deja sur une ligne est rendu
inchange : la normalisation est sans effet la ou elle n'a rien a faire.

Tout ce qui n'est pas de la prose est laisse intact — blocs de code (clotures
ou indentes), tableaux, titres, listes, citations, filets horizontaux, HTML
inline — et les retours a la ligne explicites du Markdown (deux espaces en fin
de ligne, ou antislash final) sont respectes.

Module sans dependance externe : testable sans Docling.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import unquote

# Ouverture ou fermeture d'un bloc de code cloture (``` ou ~~~).
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
# Constructions qui ouvrent un bloc : elles ne se recollent jamais.
_HEADING = re.compile(r"^\s{0,3}#{1,6}(\s|$)")
_LIST = re.compile(r"^\s{0,3}([-*+]\s|\d+[.)]\s)")
_QUOTE = re.compile(r"^\s{0,3}>")
_TABLE = re.compile(r"^\s{0,3}\|")
_HORIZONTAL_RULE = re.compile(r"^\s{0,3}([-*_]\s*){3,}$")
_INDENTED_CODE = re.compile(r"^(\t| {4,})\S")
_HTML_BLOCK = re.compile(r"^\s{0,3}<")
# Soulignement d'un titre « setext » : la ligne au-dessus est un titre, pas de
# la prose, et ne doit donc pas etre recollee a ce qui la precede.
_SETEXT_UNDERLINE = re.compile(r"^\s{0,3}(=+|-+)\s*$")
# Retour a la ligne explicite : deux espaces finaux, ou antislash final.
_HARD_BREAK = re.compile(r"(\s{2,}|\\)$")

# Liens image. Obsidian utilise sa propre syntaxe ``![[fichier.jpg|1000]]`` ;
# le Markdown standard ``![legende](chemin)``. Docling ne reconnait ni l'une ni
# l'autre : il les rend en texte brut, et les images sont perdues.
_WIKILINK_IMAGE = re.compile(r"!\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]")
_STANDARD_IMAGE = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)>\s]+)>?(?:\s+\"[^\"]*\")?\s*\)")

# Balise substituee a un lien image avant la conversion. Elle traverse Docling
# comme un element de texte ordinaire, ce qui garantit que l'image conserve sa
# **place exacte** dans l'ordre de lecture : la legende qui la suit reste
# adjacente, et la section qui la contient reste la sienne.
IMAGE_MARKER = re.compile(r"^⟦IMG:(\d{4})⟧\s*(.*)$")

_BLOCK_STARTERS = (
    _HEADING,
    _LIST,
    _QUOTE,
    _TABLE,
    _HORIZONTAL_RULE,
    _INDENTED_CODE,
    _HTML_BLOCK,
    IMAGE_MARKER,
)


@dataclass(frozen=True)
class ImageReference:
    """Un lien image trouve dans un document Markdown.

    Attributes:
        index: Rang de l'image dans le document, base de sa balise.
        target: Cible du lien — nom de fichier (Obsidian) ou chemin relatif.
        caption: Legende eventuelle. Vide pour un wikilink Obsidian.
    """

    index: int
    target: str
    caption: str

    @property
    def marker(self) -> str:
        """Balise substituee a ce lien dans le document."""
        return f"⟦IMG:{self.index:04d}⟧"


def _walk_lines(text: str) -> Iterator[tuple[str, bool]]:
    """Parcourt les lignes en signalant celles qui sont dans un bloc de code.

    Yields:
        Couples (ligne, dans_un_bloc_de_code). Les delimiteurs de bloc sont
        signales comme etant dans le bloc : on n'y touche pas non plus.
    """
    in_fence = False
    fence_marker = ""
    for line in text.splitlines():
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)[0]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence, fence_marker = False, ""
            yield line, True
            continue
        yield line, in_fence


def _starts_block(line: str) -> bool:
    """Indique si la ligne ouvre un bloc non recollable."""
    return any(pattern.match(line) for pattern in _BLOCK_STARTERS)


def _is_prose(line: str) -> bool:
    """Indique si la ligne est de la prose ordinaire, recollable."""
    return bool(line.strip()) and not _starts_block(line)


def _image_label(target: str, caption: str) -> str:
    """Texte porte par l'element image : la legende, sinon le nom du fichier."""
    if caption:
        return caption
    filename = target.replace("\\", "/").rsplit("/", 1)[-1]
    return filename.rsplit(".", 1)[0] or filename


def _extract_from_line(line: str, start_index: int) -> tuple[str, list[ImageReference]]:
    """Retire les liens image d'une ligne et retourne la ligne nettoyee."""
    references: list[ImageReference] = []
    cleaned = line

    for pattern, is_wikilink in ((_WIKILINK_IMAGE, True), (_STANDARD_IMAGE, False)):
        pieces: list[str] = []
        position = 0
        for match in pattern.finditer(cleaned):
            pieces.append(cleaned[position : match.start()])
            position = match.end()
            raw_target = match.group(1) if is_wikilink else match.group(2)
            caption = "" if is_wikilink else match.group(1).strip()
            references.append(
                ImageReference(
                    index=start_index + len(references),
                    target=unquote(raw_target.strip()),
                    caption=caption,
                )
            )
        pieces.append(cleaned[position:])
        cleaned = "".join(pieces)

    return cleaned, references


def extract_image_references(text: str) -> tuple[str, list[ImageReference]]:
    """Remplace les liens image par des balises et recense les images.

    Chaque lien devient une ligne autonome ``⟦IMG:0001⟧ legende``, placee **la
    ou etait l'image**. C'est ce qui preserve l'ancrage : apres conversion,
    l'element image occupe la meme position dans l'ordre de lecture, donc la
    legende qui la suit lui reste adjacente et la section qui la contient reste
    la sienne.

    Les liens situes dans un bloc de code sont laisses intacts : ils
    documentent une syntaxe, ils ne designent pas une image a ingerer.

    Args:
        text: Contenu Markdown brut.

    Returns:
        Le contenu avec balises, et la liste des images referencees dans
        l'ordre de lecture.
    """
    references: list[ImageReference] = []
    output: list[str] = []

    for line, in_fence in _walk_lines(text):
        if in_fence:
            output.append(line)
            continue

        cleaned, found = _extract_from_line(line, len(references))
        if not found:
            output.append(line)
            continue

        references.extend(found)
        if cleaned.strip():
            output.append(cleaned)
        output.extend(
            f"{reference.marker} {_image_label(reference.target, reference.caption)}"
            for reference in found
        )

    rendu = "\n".join(output)
    if text.endswith("\n") and not rendu.endswith("\n"):
        rendu += "\n"
    return rendu, references


def normalize_markdown(text: str) -> str:
    """Recolle les lignes d'un meme paragraphe dans un document Markdown.

    Args:
        text: Contenu Markdown brut.

    Returns:
        Le contenu avec les paragraphes sur une seule ligne. Identique a
        l'entree si aucun paragraphe n'etait coupe.
    """
    lines = text.splitlines()
    output: list[str] = []
    # Suit si la derniere ligne emise peut accueillir une suite de paragraphe.
    previous_is_open_prose = False
    in_fence = False
    fence_marker = ""

    for index, line in enumerate(lines):
        fence_match = _FENCE.match(line)
        if fence_match:
            marker = fence_match.group(1)[0]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence, fence_marker = False, ""
            output.append(line)
            previous_is_open_prose = False
            continue

        if in_fence:
            output.append(line)
            continue

        # Une ligne suivie d'un soulignement setext est un titre : on la laisse
        # seule, sans quoi le titre engloberait le paragraphe precedent.
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        followed_by_setext = bool(next_line) and bool(_SETEXT_UNDERLINE.match(next_line))

        if not _is_prose(line) or followed_by_setext:
            output.append(line)
            previous_is_open_prose = False
            continue

        if previous_is_open_prose:
            output[-1] = f"{output[-1].rstrip()} {line.strip()}"
        else:
            output.append(line)

        # Un retour a la ligne explicite ferme la suite : la ligne suivante
        # doit rester distincte.
        previous_is_open_prose = not _HARD_BREAK.search(line)

    normalized = "\n".join(output)
    # Preserve la presence (ou l'absence) d'un saut de ligne final.
    if text.endswith("\n") and not normalized.endswith("\n"):
        normalized += "\n"
    return normalized
