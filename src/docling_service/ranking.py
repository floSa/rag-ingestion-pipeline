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
