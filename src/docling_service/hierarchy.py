"""Reconstruction de la hierarchie des titres d'un document.

Jusqu'ici, tout titre etait rattache au document : la chaine s'arretait a
``element -> titre -> document``, quelle que soit la source. Un sous-titre et
le chapitre qui le contient etaient freres.

**Une seule regle, tous les formats.** Le parent d'un titre est le titre
precedent de *rang superieur*. Le rang est un petit entier ou 0 designe le
niveau le plus haut. Ce qui change d'un format a l'autre, ce n'est pas la
regle, c'est seulement d'ou vient le rang :

- **HTML** : Docling declare le parent de chaque titre. Le rang est le nombre
  de titres au-dessus dans son arbre.
- **Markdown** : Docling expose ``level`` (1 pour ``##``, 2 pour ``###``).
- **PDF** : Docling ne declare rien et met tout au meme niveau. Le rang vient
  alors de la **taille de police**, classee au sein du document.

Aucune valeur n'est ecrite en dur : les tailles sont classees document par
document. Un ouvrage compose en 24/22/20 points produit exactement les memes
niveaux qu'un ouvrage en 20/18/16.

Quand aucun signal n'est disponible, tous les titres recoivent le meme rang et
l'on retombe sur le comportement anterieur — tout sous le document. Jamais de
hierarchie inventee.
"""

from __future__ import annotations

from dataclasses import dataclass

# Profondeur maximale d'imbrication des titres. Au-dela, un RAG n'y gagne
# rien : l'objectif est de reconstruire un bloc avec ses titres parents, pas
# de reproduire une arborescence complete.
MAX_DEPTH = 3


def dense_ranks(values: list[float]) -> dict[float, int]:
    """Classe des valeurs de la plus grande a la plus petite, sans trou.

    La plus grande valeur recoit le rang 0. Les rangs sont consecutifs, de
    sorte qu'un document n'utilisant que deux tailles donne les rangs 0 et 1,
    et non 0 et 4.

    Args:
        values: Valeurs observees dans le document (tailles de police...).

    Returns:
        Le rang de chaque valeur distincte.
    """
    return {valeur: rang for rang, valeur in enumerate(sorted(set(values), reverse=True))}


@dataclass(frozen=True)
class Placement:
    """Ou rattacher un titre, et a quelle profondeur."""

    parent_id: str | None
    depth: int


class HeadingStack:
    """Suit les titres ouverts pour rattacher chaque nouveau titre au bon parent.

    Le principe est celui d'une pile : un titre de rang superieur ou egal a
    celui du sommet ferme ce sommet. Ce qui reste dessous est son parent.

    La profondeur est toujours ``parent + 1``, jamais le rang brut. Sans cela,
    un faux titre de tres petite taille tomberait directement au niveau 4 sous
    un titre de niveau 2, creant un trou dans l'arbre.
    """

    def __init__(self, max_depth: int = MAX_DEPTH) -> None:
        self._max_depth = max_depth
        self._ouverts: list[tuple[str, int, int]] = []  # (id, rang, profondeur)

    def place(self, element_id: str, rank: int) -> Placement:
        """Rattache un titre et l'ouvre a son tour.

        Args:
            element_id: Identifiant du titre.
            rank: Son rang, 0 etant le niveau le plus haut.

        Returns:
            Le parent et la profondeur retenus.
        """
        while self._ouverts and self._ouverts[-1][1] >= rank:
            self._ouverts.pop()

        if self._ouverts:
            parent_id, _, parent_depth = self._ouverts[-1]
            depth = min(parent_depth + 1, self._max_depth)
        else:
            parent_id, depth = None, 0

        self._ouverts.append((element_id, rank, depth))
        return Placement(parent_id, depth)

    @property
    def current_id(self) -> str | None:
        """Identifiant du titre courant, auquel rattacher les elements suivants."""
        return self._ouverts[-1][0] if self._ouverts else None

    def reset(self) -> None:
        """Vide la pile — nouveau document."""
        self._ouverts.clear()


def is_inside(
    box: tuple[float, float, float, float],
    others: list[tuple[float, float, float, float]],
    tolerance: float = 2.0,
) -> bool:
    """Indique si une boite est contenue dans l'une des autres.

    Sert a ecarter un titre detecte a l'interieur d'une image ou d'un tableau :
    le texte d'une figure peut etre grand sans etre un titre de section.

    Args:
        box: Boite testee, en ``(gauche, bas, droite, haut)``.
        others: Boites des images et tableaux de la meme page.
        tolerance: Marge admise, en points.

    Returns:
        ``True`` si la boite est contenue dans l'une des autres.
    """
    left, bottom, right, top = box
    for o_left, o_bottom, o_right, o_top in others:
        if (
            left >= o_left - tolerance
            and right <= o_right + tolerance
            and bottom >= o_bottom - tolerance
            and top <= o_top + tolerance
        ):
            return True
    return False
