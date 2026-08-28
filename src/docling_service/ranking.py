"""Determination du rang d'un titre, quelle que soit la source.

Le rang est un petit entier ou 0 designe le niveau le plus haut. La regle
d'assemblage de l'arbre, elle, vit dans :mod:`src.docling_service.hierarchy`
et ne connait que ce nombre.

Trois signaux, essayes dans cet ordre, du plus fiable au plus indirect :

1. **Le parent declare par Docling.** Sur les captures HTML, Docling rattache
   chaque titre a celui qui le domine. Le rang est alors le nombre de titres
   au-dessus dans son arbre. C'est une donnee, pas une deduction.
2. **L'attribut ``level``.** Sur le Markdown, Docling le renseigne fidelement
   d'apres les dieses : 1 pour ``##``, 2 pour ``###``.
3. **La taille de police.** Sur les PDF, Docling ne declare aucun parent et
   met tous les titres au meme niveau. La taille, elle, est ecrite en clair
   dans le fichier. On classe les tailles **du document courant**, sans
   aucune valeur en dur : un ouvrage compose en 24/22/20 points se segmente
   exactement comme un ouvrage en 20/18/16.

Deux garde-fous sur le troisieme signal, qui est le seul indirect :

- un titre dont la boite est contenue dans une image ou un tableau n'est pas
  un titre de section — le texte d'une figure peut etre grand ;
- un titre qui n'est pas plus grand que le corps du texte n'ouvre pas de
  niveau. Sans cela, un faux titre detecte en pleine page creerait une
  branche parasite.

Quand aucun signal ne repond, tous les titres recoivent le rang 0 et l'on
retombe sur le comportement anterieur : tout sous le document.
"""

from __future__ import annotations

from typing import Any

from src.docling_service.hierarchy import dense_ranks, is_inside

# Labels que Docling attribue aux titres.
HEADING_LABELS: frozenset[str] = frozenset({"title", "section_header", "heading"})


def docling_parent_rank(item: Any, document: Any) -> int | None:
    """Rang deduit du parent que Docling declare.

    On remonte la chaine des parents en comptant les titres traverses. Les
    conteneurs anonymes (listes, groupes de mise en page) sont franchis sans
    etre comptes : ils n'ont pas de sens editorial.

    Args:
        item: Item Docling.
        document: Document Docling, pour resoudre les references.

    Returns:
        Le rang, ou ``None`` si Docling ne declare aucun parent exploitable.
    """
    rang = 0
    parent = getattr(item, "parent", None)
    vus = 0
    while parent is not None and vus < 32:  # garde-fou contre une reference circulaire
        vus += 1
        cref = getattr(parent, "cref", "")
        if not cref or cref == "#/body":
            return rang
        try:
            cible = parent.resolve(document)
        except Exception:
            return None
        if str(getattr(cible, "label", "")) in HEADING_LABELS:
            rang += 1
        parent = getattr(cible, "parent", None)
    return None


def docling_level_rank(item: Any) -> int | None:
    """Rang deduit de l'attribut ``level``, quand Docling le renseigne.

    Args:
        item: Item Docling.

    Returns:
        Le rang, ou ``None`` si l'attribut est absent.
    """
    level = getattr(item, "level", None)
    return int(level) if isinstance(level, int) else None


def font_size_ranks(sizes: list[float]) -> dict[float, int]:
    """Classe les tailles de police d'un document, de la plus grande a la plus petite.

    Args:
        sizes: Tailles relevees sur les titres du document.

    Returns:
        Le rang de chaque taille distincte.
    """
    return dense_ranks(sizes)


def is_heading_candidate(
    box: tuple[float, float, float, float] | None,
    figure_boxes: list[tuple[float, float, float, float]],
) -> bool:
    """Indique si un titre detecte merite d'ouvrir un niveau.

    Args:
        box: Boite du titre, ou ``None`` si elle est inconnue.
        figure_boxes: Boites des images et tableaux de la meme page.

    Returns:
        ``False`` si le titre se trouve dans une figure.
    """
    if box is None:
        return True
    return not is_inside(box, figure_boxes)


def exceeds_body_size(size: float, body_size: float) -> bool:
    """Indique si un titre est visuellement plus grand que le corps du texte.

    Args:
        size: Taille du titre.
        body_size: Taille dominante du corps du texte dans le document.

    Returns:
        ``True`` si le titre depasse le corps du texte.
    """
    return bool(body_size) and size > body_size


def flat_rank(item: Any, document: Any) -> int | None:
    """Rang d'un titre dans un document non pagine (HTML, Markdown).

    Le parent declare par Docling prime : sur les captures HTML, il rattache
    chaque titre a celui qui le domine. A defaut, l'attribut ``level``, que
    Docling renseigne fidelement sur le Markdown d'apres les dieses.

    Args:
        item: Item Docling.
        document: Document Docling, pour resoudre les references.

    Returns:
        Le rang, ou ``None`` si l'element n'est pas un titre ou si aucun signal
        ne repond.
    """
    if str(getattr(item, "label", "")) not in HEADING_LABELS:
        return None
    rang = docling_parent_rank(item, document)
    return rang if rang is not None else docling_level_rank(item)


def pdf_heading_rank(
    label: str,
    bbox: dict[str, float] | None,
    heading_size: float,
    body_size: float,
    size_ranks: dict[float, int],
    figure_boxes: list[tuple[float, float, float, float]],
) -> int | None:
    """Rang d'un titre dans un PDF, deduit de sa taille de police.

    Docling ne declare aucun parent sur les PDF et met tous les titres au meme
    niveau. La taille, elle, est ecrite en clair dans le fichier.

    La fonction ne lit ni le PDF ni l'item Docling : elle recoit des mesures
    deja prises et ne fait que decider. C'est ce qui la rend verifiable sans
    PyMuPDF, et c'est la decision — pas la mesure — qui determine si le graphe
    est plat ou hierarchique.

    Deux titres sont ecartes du classement : celui dont la boite est contenue
    dans une image ou un tableau — le texte d'une figure peut etre grand sans
    etre un titre — et celui qui n'est pas plus grand que le corps du texte,
    qui est presque toujours un faux positif de detection.

    Args:
        label: Label Docling de l'element.
        bbox: Boite de l'element, ``None`` si inconnue.
        heading_size: Taille de police relevee dans la boite du titre.
        body_size: Taille dominante du corps du texte.
        size_ranks: Rang de chaque taille de titre du document.
        figure_boxes: Boites des images et tableaux de la meme page.

    Returns:
        Le rang du titre. ``None`` uniquement quand l'element n'est pas un
        titre, ou quand le document n'offre aucun classement — auquel cas tous
        ses titres restent freres sous le document.
    """
    if label not in HEADING_LABELS:
        return None
    if not size_ranks:
        return None

    # Un titre que l'on ne sait pas classer se range sous le titre courant.
    # Lui donner le rang 0 en ferait un chapitre et remettrait l'arbre a zero :
    # c'est ce que faisait « Then: », faux titre detecte en pleine page.
    inclassable = max(size_ranks.values()) + 1

    if not bbox:
        return inclassable

    boite = (bbox["l"], bbox["b"], bbox["r"], bbox["t"])
    if not is_heading_candidate(boite, figure_boxes):
        return inclassable
    if not exceeds_body_size(heading_size, body_size):
        return inclassable
    return size_ranks.get(heading_size, inclassable)
