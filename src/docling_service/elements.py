"""Taxonomie des labels Docling et construction des elements de document.

Un « element » est le dict pivot du service : produit a partir d'un item
Docling, il alimente ensuite NebulaGraph (structure) et ChromaDB (recherche).
Sa forme est decrite par :class:`src.pipeline.schemas.DocumentElement`.

Le module n'importe ni Docling ni torch — les items sont lus par ``getattr`` —
afin de rester testable sans l'image d'extraction.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.docling_service.hierarchy import HeadingStack

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

# Dossier ou le pipeline depose les HTML nettoyes ; il double l'arborescence
# d'origine et ne fait pas partie de l'identite du document.
#
# **C'EST UNE CONSTANTE, ET C'ETAIT UN REGLAGE.** `PipelineSettings` portait un
# champ `cleaned_subdir` expose par `CLEANED_SUBDIR`, annonce a l'operateur dans
# `.env.example`, et il decidait a lui seul de deux choses qui n'ont pas le meme
# proprietaire : ou le nettoyage ECRIT, et ce que `wipe_stores` SUPPRIME. Trois
# consequences mesurees, aucune souhaitable (registre 4.29.a) :
#
# - `CLEANED_SUBDIR=htms` est strictement contenu dans la racine, passait donc le
#   containment livre par le lot 4, et faisait supprimer `Datas/htms/` — 24 des
#   25 fichiers du corpus versionne. `=database` supprimait les cinq stores ;
# - toute valeur autre que celle-ci CASSE L'IDENTITE DES DOCUMENTS, en silence.
#   `document_identity` retire ce segment du chemin pour retrouver la source ; il
#   le lisait ICI tandis que le nettoyage ecrivait selon le REGLAGE. Les deux
#   sites pouvaient donc diverger. `mesure` avec `CLEANED_SUBDIR=.propre`, sur le
#   chemin nettoye d'un chapitre reel : `key` passe de
#   `htms/MLOps with Databricks/Preface` a `.propre/htms/...`, `collection` passe
#   de l'ouvrage au dossier de source, et l'`element_id` passe de `fab608f4eb` a
#   `9d6460cded`. C'est l'exigence 2 du contrat rompue, et l'exigence 3 avec
#   elle, sans qu'aucune erreur ne soit levee ;
# - personne ne configure ou une etape intermediaire depose ses fichiers.
#
# Le containment de `wipe_stores.purge_cleaned` est CONSERVE : il ne coute rien
# et il protege desormais contre un `source_dir` mal regle, qui reste un reglage
# legitime.
CLEANED_SUBDIR = ".cleaned"


def cleaned_root(source_dir: str | Path) -> Path:
    """Racine des copies nettoyees, sous la racine des donnees.

    SEUL site de cette derivation avec :func:`cleaned_path`, et c'est le point :
    `wipe_stores` SUPPRIME ce repertoire, l'asset `cleaned_html` y ECRIT, et
    :func:`document_identity` retire son nom du chemin. Trois lecteurs, une
    definition.

    Args:
        source_dir: Racine des donnees, ``PipelineSettings.source_dir``.

    Returns:
        Le repertoire des copies nettoyees.
    """
    return Path(source_dir) / CLEANED_SUBDIR


def cleaned_path(source_dir: str | Path, source_path: str) -> Path:
    """Chemin de la copie nettoyee d'un document.

    L'arborescence d'origine est reproduite telle quelle sous
    :func:`cleaned_root`, de sorte que retirer ce seul segment rende le chemin
    source — ce que fait :func:`document_identity`. C'est cet aller-retour qui
    doit tenir, et un test l'asserte : la copie nettoyee d'un document doit
    rendre l'identite du document, sans quoi ses `element_id` changent en
    silence.

    Args:
        source_dir: Racine des donnees, ``PipelineSettings.source_dir``.
        source_path: Chemin relatif a la racine — la cle de partition Dagster.

    Returns:
        Le chemin de la copie nettoyee.
    """
    return cleaned_root(source_dir) / source_path


def tag_for_label(label: str) -> str:
    """Retourne le tag NebulaGraph d'un label Docling (Paragraph par defaut)."""
    return TAG_MAP.get(label, "Paragraph")


@dataclass(frozen=True)
class DocumentIdentity:
    """Identite d'un document ingere.

    Le nom de fichier seul ne suffit pas. Un livre decoupe en chapitres donne
    des noms qui se repetent d'un ouvrage a l'autre — « Preface », « Index »,
    « Appendix ». Deux chapitres homonymes produiraient les memes identifiants
    d'elements et fusionneraient silencieusement en un seul document.

    C'est donc le **chemin** qui porte l'identite, et le dossier parent qui
    porte l'ouvrage — sans quoi une citation ne peut pas dire de quel livre
    elle vient.

    Attributes:
        source_path: Chemin relatif a ``Datas/``, avec son extension.
        key: Le meme, sans extension. Base des identifiants d'elements.
        filename: Nom du fichier seul — le chapitre.
        collection: Dossier de premier niveau sous la racine de la source,
            c'est-a-dire l'ouvrage. Vide pour un fichier depose a plat.
    """

    source_path: str
    key: str
    filename: str
    collection: str


@dataclass(frozen=True)
class DocumentFacts:
    """Ce qu'on sait du document au-dela de son chemin.

    Regroupe en un objet ce qui accompagnait deja l'identite jusqu'aux deux
    stores, et ce qui s'y ajoute : la langue, pour que l'agent sache dans
    quelle langue il interroge, et l'empreinte du fichier, qui permet de
    reconnaitre un ouvrage deja ingere sous un autre nom.

    Attributes:
        type_file: ``pdf``, ``html`` ou ``md``.
        total_pages: Nombre de pages (1 pour les formats non pagines).
        language: Code ISO 639-1, ou chaine vide si indeterminee.
        content_hash: SHA-256 du fichier source, en hexadecimal.
    """

    type_file: str
    total_pages: int = 0
    language: str = ""
    content_hash: str = ""


def document_identity(source_path: str) -> DocumentIdentity:
    """Construit l'identite d'un document a partir de son chemin relatif.

    Args:
        source_path: Chemin relatif a ``Datas/``, par exemple
            ``htms/Practical MLOps/1. Introduction to MLOps.html``.

    Returns:
        L'identite correspondante.
    """
    normalise = source_path.replace("\\", "/").strip("/")
    # Les HTML sont convertis depuis leur copie nettoyee, qui reproduit
    # l'arborescence sous un dossier dedie : on revient au chemin d'origine.
    segments = [s for s in normalise.split("/") if s and s != CLEANED_SUBDIR]

    key = "/".join(segments)
    if "." in segments[-1]:
        segments[-1] = segments[-1].rsplit(".", 1)[0]
        key = "/".join(segments)

    return DocumentIdentity(
        source_path=normalise,
        key=key,
        filename=segments[-1],
        # segments[0] est le dossier de la source (pdfs, htms, mds) ; l'ouvrage
        # est le niveau suivant, quand il existe.
        collection=segments[1] if len(segments) >= 3 else "",
    )


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


def item_page_span(item: Any) -> tuple[int, int]:
    """Premiere et derniere page couvertes par un item Docling.

    **La seconde etait JETEE, et c'est le registre 4.22.** Six pages du PDF du
    corpus — 8, 18, 19, 25, 68, 69 sur 71 — n'ont aucun element dans le graphe,
    alors que PyMuPDF y lit 1 181 a 1 472 caracteres. Le texte n'est pas perdu :
    il est attribue a la page PRECEDENTE, Docling fusionnant un paragraphe qui
    enjambe une page. Toute citation « page 7 » couvre donc en realite 7 ET 8,
    et rien ne le disait.

    L'information existait : `mesure` le 1er septembre 2026, conversion reelle du
    PDF du corpus en `page_range=(7, 8)`, l'item `#/texts/3` porte **deux
    provenances, pages [7, 8]**. Seule la premiere etait lue.

    Args:
        item: Item Docling.

    Returns:
        `(premiere, derniere)`. `(1, 1)` sans provenance — les formats non
        pagines. La derniere n'est jamais inferieure a la premiere : des
        provenances en desordre rendraient une plage vide, donc une page
        d'entree comptee comme perdue.
    """
    prov = list(getattr(item, "prov", None) or [])
    if not prov:
        return 1, 1
    pages = [int(p.page_no) for p in prov]
    return pages[0], max(pages[0], pages[-1])


def pages_sans_element(
    elements: Sequence[dict[str, Any]],
    total_pages: int,
    ecartees: set[int] | None = None,
) -> list[int]:
    """Pages qu'AUCUN element ne couvre — le compteur la ou il y a perte.

    Une page enjambee n'est PAS une page perdue : elle est couverte par un
    element qui commence avant elle, et `page_no_end` le dit desormais. Ce qui
    reste apres ce changement est la vraie perte — une page que personne ne
    couvre, ni comme page d'entree ni comme page de fin.

    Les pages ECARTEES ne comptent pas : le front/back matter est saute
    volontairement, et le compter comme une perte rendrait le compteur bavard sur
    chaque PDF. Un compteur qu'on n'ecoute plus ne compte rien.

    Args:
        elements: Elements produits par `DocumentAccumulator`.
        total_pages: Pagination du document.
        ecartees: Pages volontairement non converties.

    Returns:
        Les numeros de page non couverts, tries.
    """
    couvertes: set[int] = set()
    for element in elements:
        debut = int(element.get("page_no") or 1)
        fin = max(debut, int(element.get("page_no_end") or debut))
        couvertes.update(range(debut, fin + 1))
    sautees = ecartees or set()
    return [
        page for page in range(1, total_pages + 1) if page not in couvertes and page not in sautees
    ]


class DocumentAccumulator:
    """Construit les elements d'un document en suivant hierarchie et positions.

    Porte l'etat qui doit survivre aux batchs de pages d'un meme document :
    section courante, ordre de lecture global, rang dans la page et rang sous
    le parent. Ces trois positions sont celles attendues par ``rag-agent-chat``
    (``page_position``, ``ref_position``, ``reference_id``).
    """

    def __init__(self, identity: DocumentIdentity) -> None:
        self.identity = identity
        self._global_order = 0
        self._current_section_title = ""
        self._page_counters: dict[int, int] = {}
        self._reference_counters: dict[str, int] = {}
        # Suit les titres ouverts pour rattacher chaque nouveau titre au bon
        # parent. Sans rang fourni, tous les titres partagent le rang 0 et se
        # retrouvent freres sous le document — comportement anterieur.
        self._headings = HeadingStack()
        self._depths: dict[str, int] = {}

    @property
    def count(self) -> int:
        """Nombre d'elements produits jusqu'ici."""
        return self._global_order

    def add_item(
        self, item: Any, document: Any = None, heading_rank: int | None = None
    ) -> dict[str, Any]:
        """Construit l'element correspondant a un item Docling.

        Args:
            item: Item issu de ``document.iterate_items()``.
            document: Document Docling parent, necessaire pour exporter le
                contenu des tables.
            heading_rank: Rang du titre, 0 designant le niveau le plus haut.
                Ignore pour les elements qui ne sont pas des titres. Absent,
                tous les titres sont freres sous le document.

        Returns:
            Le dict element, positions et rattachement hierarchique renseignes.
        """
        prov = item_provenance(item)
        # `page_no` reste la PREMIERE page, et ce n'est pas un detail :
        # `compute_id` en derive l'identifiant de l'element. Le deplacer
        # changerait tous les `element_id` du corpus, donc le jeu de questions de
        # l'agent (contrat, exigence 2). `page_no_end` est ADDITIF, et c'est ce
        # qui rend le registre 4.22 corrigible sans reecrire les identifiants.
        page_no, page_no_end = item_page_span(item)
        label = item_label(item)
        text = item_text(item, document)

        position_in_page = self._page_counters.get(page_no, 0)
        self._page_counters[page_no] = position_in_page + 1

        # La cle du document, et non son seul nom de fichier : deux chapitres
        # homonymes dans deux ouvrages differents doivent donner des ids
        # distincts, sinon ils se recouvrent en silence.
        element_id = compute_id(self.identity.key, page_no, position_in_page, text)

        # Un titre se rattache au titre precedent de rang superieur ; les
        # autres elements se rattachent au dernier titre rencontre.
        if label in SECTION_LABELS:
            placement = self._headings.place(element_id, heading_rank or 0)
            self._depths[element_id] = placement.depth
            # Le titre est retenu au-dela du lot de pages courant : il sert a
            # contextualiser les embeddings des elements de la section.
            self._current_section_title = text
            reference_id = placement.parent_id or ROOT_REFERENCE
            depth = placement.depth
        else:
            current = self._headings.current_id
            reference_id = current or ROOT_REFERENCE
            depth = self._depths.get(current, -1) + 1 if current else 0

        ref_position = self._reference_counters.get(reference_id, 0)
        self._reference_counters[reference_id] = ref_position + 1

        element: dict[str, Any] = {
            "id": element_id,
            # Reference interne Docling (« #/texts/18 »). Sert a rattacher les
            # chunks produits par HybridChunker a nos propres elements ; elle
            # ne part dans aucun store.
            "self_ref": str(getattr(item, "self_ref", "")),
            "label": label,
            "page_no": page_no,
            # Derniere page couverte. Egale a `page_no` sauf pour un element que
            # Docling a fusionne par-dessus une frontiere de page : la, une
            # citation « page N » couvre en realite N a `page_no_end` (4.22).
            "page_no_end": page_no_end,
            "bbox": extract_bbox(prov.bbox if prov else None),
            "text": text,
            "order": self._global_order,
            "reference_id": reference_id,
            "depth": depth,
            "section_title": self._current_section_title,
            "page_position": position_in_page,
            "ref_position": ref_position,
        }

        self._global_order += 1
        return element
