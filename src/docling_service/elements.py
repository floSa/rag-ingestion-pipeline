"""Taxonomie des labels Docling et construction des elements de document.

Un « element » est le dict pivot du service : produit a partir d'un item
Docling, il alimente ensuite NebulaGraph (structure) et ChromaDB (recherche).
Sa forme est decrite par :class:`src.pipeline.schemas.DocumentElement`.

Le module n'importe ni Docling ni torch — les items sont lus par ``getattr`` —
afin de rester testable sans l'image d'extraction.
"""

from __future__ import annotations

import hashlib
from typing import Any

# Mapping labels Docling -> tags NebulaGraph.
TAG_MAP: dict[str, str] = {
    "text": "Paragraph",
    "paragraph": "Paragraph",
    "heading": "SectionHeader",
    "section_header": "SectionHeader",
    "list_item": "ListItem",
    "table": "Table",
    "picture": "Picture",
    "formula": "Formula",
    "code": "Code",
    "caption": "Caption",
    "footnote": "Footnote",
    "page_header": "PageHeader",
    "page_footer": "PageFooter",
    "title": "SectionHeader",
}

# Elements dont on exporte un crop image vers MinIO.
VISUAL_LABELS: set[str] = {"picture", "table", "figure", "graphic"}

# Labels correspondant a des en-tetes de section (cf. TAG_MAP). Sert a batir la
# hierarchie Document > SectionHeader > Elements : chaque element est rattache
# au dernier en-tete rencontre.
SECTION_LABELS: set[str] = {lbl for lbl, tag in TAG_MAP.items() if tag == "SectionHeader"}

# Parent par defaut d'un element sans section : le document lui-meme.
ROOT_REFERENCE = "DOC"


def tag_for_label(label: str) -> str:
    """Retourne le tag NebulaGraph d'un label Docling (Paragraph par defaut)."""
    return TAG_MAP.get(label, "Paragraph")


def compute_id(filename: str, page_no: int, position_in_page: int, text: str) -> str:
    """Genere un identifiant court deterministe pour un element.

    La position DANS LA PAGE (et non l'ordre global de lecture) rend l'id
    stable d'une ingestion a l'autre : une page reconvertie produit les memes
    ids, et les upserts ecrasent au lieu de dupliquer.

    Args:
        filename: Nom du document sans extension.
        page_no: Numero de page (1 pour les formats non pagines).
        position_in_page: Rang de l'element dans sa page.
        text: Texte de l'element (seuls les 50 premiers caracteres comptent).

    Returns:
        Hash de 10 caracteres hexadecimaux.
    """
    raw = f"{filename}|{page_no}|{position_in_page}|{text[:50]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:10]


def extract_bbox(bbox: Any) -> dict[str, float] | None:
    """Convertit un objet bbox Docling en dict serialisable, None s'il n'y en a pas.

    None plutot qu'un dict vide : c'est la forme attendue par
    :class:`src.pipeline.schemas.DocumentElement`, contre lequel les elements
    sont valides avant persistance.
    """
    if not bbox:
        return None
    return {
        "l": round(bbox.l, 2),
        "t": round(bbox.t, 2),
        "r": round(bbox.r, 2),
        "b": round(bbox.b, 2),
    }


def item_label(item: Any) -> str:
    """Label normalise en minuscules d'un item Docling."""
    return str(getattr(item, "label", "text")).lower()


def item_text(item: Any, document: Any = None) -> str:
    """Texte d'un item Docling, chaine vide s'il n'en porte pas.

    Les tables font exception : leur ``text`` vaut ``None``, le contenu vivant
    dans une structure dediee. Sans export explicite, elles ressortaient vides
    de l'extraction — presentes dans le graphe, mais introuvables par la
    recherche vectorielle.

    Args:
        item: Item issu de ``document.iterate_items()``.
        document: Document Docling parent, requis pour exporter les tables.

    Returns:
        Le texte de l'element, ou une chaine vide.
    """
    text = getattr(item, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    exporter = getattr(item, "export_to_markdown", None)
    if exporter is None or document is None:
        return ""
    try:
        return str(exporter(document)).strip()
    except Exception:
        # Export indisponible sur ce type d'item : pas de texte, pas d'echec.
        return ""


def item_provenance(item: Any) -> Any:
    """Premiere provenance d'un item Docling, ou None."""
    prov = getattr(item, "prov", None)
    return prov[0] if prov else None


class DocumentAccumulator:
    """Construit les elements d'un document en suivant hierarchie et positions.

    Porte l'etat qui doit survivre aux batchs de pages d'un meme document :
    section courante, ordre de lecture global, rang dans la page et rang sous
    le parent. Ces trois positions sont celles attendues par ``rag-agent-chat``
    (``page_position``, ``ref_position``, ``reference_id``).
    """

    def __init__(self, filename_stem: str) -> None:
        self.filename_stem = filename_stem
        self._global_order = 0
        self._current_section_id: str | None = None
        self._page_counters: dict[int, int] = {}
        self._reference_counters: dict[str, int] = {}

    @property
    def count(self) -> int:
        """Nombre d'elements produits jusqu'ici."""
        return self._global_order

    def add_item(self, item: Any, document: Any = None) -> dict[str, Any]:
        """Construit l'element correspondant a un item Docling.

        Args:
            item: Item issu de ``document.iterate_items()``.
            document: Document Docling parent, necessaire pour exporter le
                contenu des tables.

        Returns:
            Le dict element, positions et rattachement hierarchique renseignes.
        """
        prov = item_provenance(item)
        page_no = int(prov.page_no) if prov else 1
        label = item_label(item)
        text = item_text(item, document)

        position_in_page = self._page_counters.get(page_no, 0)
        self._page_counters[page_no] = position_in_page + 1

        element_id = compute_id(self.filename_stem, page_no, position_in_page, text)

        # Un en-tete ouvre une nouvelle section et reste rattache au document ;
        # les autres elements se rattachent au dernier en-tete rencontre.
        if label in SECTION_LABELS:
            self._current_section_id = element_id
            reference_id = ROOT_REFERENCE
        else:
            reference_id = self._current_section_id or ROOT_REFERENCE

        ref_position = self._reference_counters.get(reference_id, 0)
        self._reference_counters[reference_id] = ref_position + 1

        element: dict[str, Any] = {
            "id": element_id,
            "label": label,
            "page_no": page_no,
            "bbox": extract_bbox(prov.bbox if prov else None),
            "text": text,
            "order": self._global_order,
            "reference_id": reference_id,
            "page_position": position_in_page,
            "ref_position": ref_position,
        }

        self._global_order += 1
        return element
