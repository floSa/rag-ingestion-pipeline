"""Rattachement des chunks Docling a nos propres elements.

``HybridChunker`` decoupe le document en respectant sa structure et la fenetre
du modele d'embedding. Mais il rend ses chunks avec **ses** references internes
(``#/texts/18``), alors que le contrat avec ``rag-agent-chat`` impose **nos**
identifiants — un hash de dix hexadecimaux, valide par l'agent sur
``^[a-f0-9]{10}$`` et servant de pivot vers le graphe.

Ce module fait le pont. Il ne connait ni Docling ni ChromaDB : il recoit des
references et des elements, et rend des ancres. C'est ce qui le rend testable
sans lancer de conversion.

Deux cas a traiter, et ils se produisent tous les deux :

- **un chunk couvre plusieurs elements** — c'est le but de HybridChunker, qui
  regroupe ce qui va ensemble. L'ancre est alors le **premier** element du
  chunk, celui d'ou part la lecture ;
- **plusieurs chunks partagent la meme ancre** — un element trop long pour la
  fenetre est reparti sur plusieurs chunks. Ils recoivent alors un suffixe
  ``#0``, ``#1``, comme le prevoit deja le contrat.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Anchor:
    """L'element auquel un chunk est rattache, et sa place parmi ses freres."""

    element: dict[str, Any]
    index: int
    count: int


def index_by_self_ref(elements: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Indexe les elements par leur reference Docling.

    Args:
        elements: Elements produits par ``DocumentAccumulator``.

    Returns:
        Les elements, par reference. Ceux qui n'en portent pas sont ignores.
    """
    return {str(e["self_ref"]): e for e in elements if e.get("self_ref")}


def resolve_anchors(
    chunk_refs: list[list[str]], elements: list[dict[str, Any]]
) -> list[Anchor | None]:
    """Determine l'element d'ancrage de chaque chunk, et sa position.

    Args:
        chunk_refs: Pour chaque chunk, dans l'ordre, les references Docling des
            elements qu'il couvre.
        elements: Elements produits par ``DocumentAccumulator``.

    Returns:
        Une ancre par chunk, ou ``None`` si aucune reference n'est connue —
        auquel cas le chunk est ecarte plutot que rattache au hasard.
    """
    par_ref = index_by_self_ref(elements)

    # Premiere passe : trouver l'element d'ancrage de chaque chunk.
    ancres: list[dict[str, Any] | None] = []
    for refs in chunk_refs:
        trouve = next((par_ref[r] for r in refs if r in par_ref), None)
        ancres.append(trouve)

    # Seconde passe : numeroter les chunks qui partagent une meme ancre.
    total = Counter(str(a["id"]) for a in ancres if a is not None)
    vus: Counter[str] = Counter()

    resultat: list[Anchor | None] = []
    for element in ancres:
        if element is None:
            resultat.append(None)
            continue
        cle = str(element["id"])
        resultat.append(Anchor(element=element, index=vus[cle], count=total[cle]))
        vus[cle] += 1
    return resultat


def block_size(refs: list[str], elements: list[dict[str, Any]]) -> int:
    """Nombre d'elements du document reellement couverts par un chunk.

    Args:
        refs: References Docling portees par le chunk.
        elements: Elements produits par ``DocumentAccumulator``.

    Returns:
        Le compte, au minimum 1.
    """
    par_ref = index_by_self_ref(elements)
    return max(1, sum(1 for r in refs if r in par_ref))
