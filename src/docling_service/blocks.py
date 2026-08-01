"""Regroupement des elements en blocs vectorisables.

L'analyse de layout produit un element par fragment detecte. Sur un PDF, cela
descend jusqu'a la variable isolee : sur le corpus de reference, 36 % des
entrees indexees etaient des fragments comme ``x``, ``and``, ``Note``, ``n``,
``-`` ou ``.``. Vectorises tels quels, ils polluent la recherche, diluent le
travail du reranker et gonflent l'index d'un tiers pour rien.

La reponse retenue suit l'etat de l'art du decoupage pour RAG : **fusionner
plutot que jeter**, avec un plancher minimal sous lequel un bloc n'a plus
d'interet. C'est exactement la limite connue du ``HybridChunker`` de Docling,
qui fusionne les pairs de meme metadonnee (``merge_peers``) mais n'a pas de
``min_tokens`` : les fragments isoles y survivent.

Deux garde-fous encadrent la fusion :

- on ne franchit **jamais une frontiere de section** : un bloc melangeant deux
  sections repondrait a des questions qui ne le concernent pas ;
- on ne fusionne que des elements de **meme nature** : une table ou un bloc de
  code sont autonomes, les noyer dans un paragraphe deforme leur sens.

Rien n'est perdu : tous les elements restent dans NebulaGraph, qui porte la
structure du document. Seul l'index vectoriel est nettoye.

Limite connue : sur un PDF, la persistance se fait par lot de pages, si bien
que la fusion ne franchit pas une frontiere de lot. Un paragraphe a cheval sur
deux lots produit donc deux blocs. Avec des lots de cinq pages et une fusion
deja bornee par les sections, l'effet est marginal — le corriger demanderait de
retenir un bloc en attente d'un lot a l'autre, pour un gain sans rapport avec
la complexite ajoutee.

Module sans dependance externe : testable sans Docling ni ChromaDB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Elements de prose : ils se fusionnent entre eux.
PROSE_LABELS: frozenset[str] = frozenset({"text", "paragraph", "list_item", "caption", "footnote"})
# Le code se fusionne avec du code, jamais avec de la prose.
CODE_LABELS: frozenset[str] = frozenset({"code"})

# Tout le reste (table, formula, section_header, title, picture, en-tetes et
# pieds de page, index) forme des blocs autonomes.


@dataclass
class Block:
    """Un bloc de texte destine a l'index vectoriel.

    Attributes:
        text: Texte du bloc, concatenation des elements fusionnes.
        anchor: Premier element du bloc. Il fournit l'identifiant et les
            metadonnees ; c'est lui qui fait le lien avec le graphe.
        element_ids: Identifiants de tous les elements fusionnes.
    """

    text: str
    anchor: dict[str, Any]
    element_ids: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        """Nombre d'elements fusionnes dans ce bloc."""
        return len(self.element_ids)


def has_content(text: str) -> bool:
    """Indique si un texte porte au moins un caractere alphanumerique.

    Un texte qui n'en contient aucun est un artefact de mise en page — filet de
    tableau, puce, ponctuation isolee — et n'a rien a faire dans un index
    vectoriel.
    """
    return any(character.isalnum() for character in text)


def _family(label: str) -> str:
    """Retourne la famille de fusion d'un label."""
    if label in PROSE_LABELS:
        return "prose"
    if label in CODE_LABELS:
        return "code"
    return "standalone"


def build_blocks(
    elements: list[dict[str, Any]],
    target_chars: int,
    min_chars: int,
) -> list[Block]:
    """Regroupe des elements en blocs vectorisables.

    Args:
        elements: Elements produits par ``DocumentAccumulator``, dans l'ordre
            de lecture.
        target_chars: Taille visee d'un bloc fusionne, en caracteres. La fusion
            s'arrete avant de la depasser.
        min_chars: Plancher sous lequel un bloc est ecarte de l'index. Les
            elements ecartes restent presents dans le graphe.

    Returns:
        Les blocs retenus, dans l'ordre de lecture.
    """
    blocks: list[Block] = []
    current: Block | None = None
    current_family = ""
    current_section = ""

    def flush() -> None:
        nonlocal current, current_family, current_section
        if current is not None and len(current.text.strip()) >= min_chars:
            blocks.append(current)
        current, current_family, current_section = None, "", ""

    for element in elements:
        text = str(element.get("text") or "").strip()
        if not has_content(text):
            continue

        family = _family(str(element.get("label") or ""))
        section = str(element.get("reference_id") or "")

        if family == "standalone":
            flush()
            if len(text) >= min_chars:
                blocks.append(Block(text=text, anchor=element, element_ids=[str(element["id"])]))
            continue

        fits = (
            current is not None
            and family == current_family
            and section == current_section
            and len(current.text) + 1 + len(text) <= target_chars
        )
        if fits and current is not None:
            current.text = f"{current.text}\n{text}"
            current.element_ids.append(str(element["id"]))
        else:
            flush()
            current = Block(text=text, anchor=element, element_ids=[str(element["id"])])
            current_family, current_section = family, section

    flush()
    return blocks
