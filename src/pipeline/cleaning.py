"""Nettoyage HTML universel : pre-passe SingleFile puis extraction de contenu.

Techniques alignees sur le projet ``html_to_md`` (meme auteur) :

1. **Pre-passe d'hygiene** : suppression deterministe du bruit non ambigu —
   scripts, styles, elements caches (``sf-hidden``, ``display:none``, attribut
   ``hidden``), chrome de page (nav, aside, roles ARIA), commentaires,
   attributs inline et grosses images ``data:`` issues de SingleFile.
   ``header``/``footer`` sont conserves a l'interieur d'un ``<article>``/
   ``<main>`` (ils portent souvent le titre).
2. **Extraction par comparaison de candidats** : conteneurs semantiques HTML5
   (``<article>``, ``<main>``, ``[role=main]``), trafilatura et
   readability-lxml sont compares ; le candidat qui conserve le plus de texte
   gagne (le plus complet est le moins risque pour l'ingestion).
3. **Garde-fou** : si aucun candidat ne passe les seuils, on conserve le HTML
   pre-nettoye plutot que de perdre du contenu.

Chaque strategie respecte le protocole :class:`CleaningStrategy` ; en ajouter
une (ex: nettoyage par LLM) revient a ajouter une classe dans ``_STRATEGIES``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import trafilatura
from bs4 import BeautifulSoup, Comment, Tag
from readability import Document

from src.pipeline.sources import CleaningOptions

# Balises qui ne portent jamais de contenu a ingerer.
NOISE_TAGS: list[str] = [
    "script",
    "style",
    "link",
    "meta",
    "noscript",
    "iframe",
    "template",
    "object",
    "embed",
    "form",
    "button",
]

# Chrome de page toujours supprime. header/footer sont traites a part :
# a l'interieur d'un <article>/<main> ils portent souvent le titre.
CHROME_TAGS: list[str] = ["nav", "aside"]

# Classes marquees par SingleFile lui-meme.
NOISE_CLASSES: list[str] = ["sf-hidden"]

NOISE_SELECTORS: list[str] = [
    "[role=navigation]",
    "[role=banner]",
    "[role=complementary]",
    "[role=contentinfo]",
    "[role=dialog]",
    "[aria-modal=true]",
    # Widgets « articles lies » des plugins WordPress courants.
    ".crp_related",
    ".yarpp-related",
    ".jp-relatedposts",
]

# Attributs susceptibles de porter des URI data: volumineuses (SingleFile).
URI_ATTRS: tuple[str, ...] = ("src", "srcset", "href", "poster", "data-src")

# Conteneurs semantiques HTML5 essayes du plus precis au plus large.
SEMANTIC_SELECTORS: tuple[str, ...] = ("article", "main", "[role=main]")

PRECLEANED_FALLBACK = "precleaned"


@dataclass
class CleaningReport:
    """Bilan d'un nettoyage, expose dans les metadonnees Dagster."""

    strategy: str
    raw_bytes: int
    precleaned_bytes: int
    cleaned_bytes: int
    text_chars: int


@dataclass
class ExtractionCandidate:
    """Contenu propose par une strategie d'extraction."""

    strategy: str
    html: str


class CleaningStrategy(Protocol):
    """Une strategie d'extraction du contenu principal."""

    def extract(self, html: str) -> ExtractionCandidate | None:
        """Retourne un candidat (nom + HTML), ou None si echec."""
        ...


def _visible_text_length(html: str) -> int:
    """Longueur du texte visible d'un fragment HTML."""
    return len(BeautifulSoup(html, "lxml").get_text(" ", strip=True))


def _decompose_all(tags: Iterable[Any]) -> None:
    """Decompose une liste de tags en ignorant ceux deja decomposes.

    Un tag dont l'ancetre a ete supprime par une boucle precedente a ses
    attributs a ``None`` : le toucher leverait une AttributeError.
    """
    for tag in tags:
        if isinstance(tag, Tag) and not tag.decomposed:
            tag.decompose()


def _top_level_only(tags: list[Tag]) -> list[Tag]:
    """Ne garde que les elements dont aucun ancetre n'est lui-meme selectionne."""
    selected = {id(t) for t in tags}
    return [t for t in tags if not any(id(p) in selected for p in t.parents)]


class SemanticContainerStrategy:
    """Extraction par conteneurs semantiques HTML5 (<article>, <main>, [role=main])."""

    def __init__(self, min_chars: int) -> None:
        self.min_chars = min_chars

    def extract(self, html: str) -> ExtractionCandidate | None:
        soup = BeautifulSoup(html, "lxml")
        for selector in SEMANTIC_SELECTORS:
            tags = _top_level_only(soup.select(selector))
            text_len = sum(len(t.get_text(strip=True)) for t in tags)
            if text_len >= self.min_chars:
                fragment = "\n".join(str(t) for t in tags)
                return ExtractionCandidate(strategy=selector, html=fragment)
        return None


class TrafilaturaStrategy:
    """Extraction de contenu via trafilatura (heuristiques de boilerplate)."""

    def extract(self, html: str) -> ExtractionCandidate | None:
        result: str | None = trafilatura.extract(
            html,
            output_format="html",
            include_tables=True,
            include_images=True,
            include_formatting=True,
            include_links=False,
            favor_recall=True,
        )
        if not result:
            return None
        return ExtractionCandidate(strategy="trafilatura", html=result)


class ReadabilityStrategy:
    """Extraction de contenu via readability-lxml (algorithme Arc90)."""

    def extract(self, html: str) -> ExtractionCandidate | None:
        summary: str = Document(html).summary(html_partial=True)
        if not summary:
            return None
        return ExtractionCandidate(strategy="readability", html=summary)


def preclean_html(raw: str, options: CleaningOptions) -> str:
    """Pre-passe d'hygiene : retire le bruit non ambigu sans toucher au contenu.

    Concu pour les captures SingleFile : elements caches, chrome de page,
    attributs ``style``, handlers ``on*`` et images ``data:`` au-dela d'un
    seuil sont supprimes, ce qui divise la taille du fichier avant extraction.

    Args:
        raw: HTML brut.
        options: Options de nettoyage de la source.

    Returns:
        HTML allege, structure du contenu intacte.
    """
    soup = BeautifulSoup(raw, "lxml")

    _decompose_all(soup.find_all(NOISE_TAGS))
    _decompose_all(soup.find_all(CHROME_TAGS))

    # header/footer : supprimes sauf a l'interieur d'un article/main.
    _decompose_all(
        tag
        for tag in soup.find_all(["header", "footer"])
        if not tag.decomposed and not tag.find_parent(["article", "main"])
    )

    for class_name in NOISE_CLASSES:
        _decompose_all(soup.find_all(class_=class_name))

    for selector in NOISE_SELECTORS + options.extra_remove_selectors:
        _decompose_all(soup.select(selector))

    # Elements caches en dur (attribut hidden / display:none / visibility:hidden),
    # AVANT la suppression des attributs style qui porte l'information.
    _decompose_all(soup.find_all(hidden=True))
    _decompose_all(
        tag
        for tag in soup.find_all(style=True)
        if not tag.decomposed
        and (
            "display:none" in (style := str(tag.get("style", "")).replace(" ", "").lower())
            or "visibility:hidden" in style
        )
    )

    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    for element in soup.find_all(True):
        if not isinstance(element, Tag):
            continue
        element.attrs.pop("style", None)
        for attr in [a for a in element.attrs if a.startswith("on")]:
            del element.attrs[attr]
        for attr in URI_ATTRS:
            value = element.attrs.get(attr)
            if (
                isinstance(value, str)
                and value.startswith("data:")
                and len(value) > options.max_data_uri_bytes
            ):
                del element.attrs[attr]

    return str(soup)


def clean_html(raw: str, options: CleaningOptions) -> tuple[str, CleaningReport]:
    """Nettoie un HTML quelconque et retourne (html_nettoye, bilan).

    Un conteneur semantique HTML5 (<article>, <main>) qui passe les seuils fait
    autorite : c'est la delimitation posee par l'auteur de la page. A defaut,
    trafilatura et readability sont compares et le candidat qui conserve le
    plus de texte gagne. En dernier recours, le HTML pre-nettoye est conserve
    tel quel.

    Args:
        raw: HTML brut (capture SingleFile, export editeur, etc.).
        options: Options de nettoyage de la source.

    Returns:
        Le HTML nettoye et le :class:`CleaningReport` associe.
    """
    precleaned = preclean_html(raw, options)
    reference_chars = _visible_text_length(precleaned)

    strategies: tuple[CleaningStrategy, ...] = (
        SemanticContainerStrategy(min_chars=options.min_text_chars),
        TrafilaturaStrategy(),
        ReadabilityStrategy(),
    )

    accepted: list[tuple[int, ExtractionCandidate]] = []
    for strategy in strategies:
        try:
            candidate = strategy.extract(precleaned)
        except Exception:
            candidate = None
        if candidate is None:
            continue

        text_chars = _visible_text_length(candidate.html)
        ratio_ok = reference_chars == 0 or text_chars / reference_chars >= options.min_text_ratio
        if text_chars >= options.min_text_chars and ratio_ok:
            accepted.append((text_chars, candidate))

    # Priorite au conteneur semantique : delimitation explicite de l'auteur.
    semantic = [c for c in accepted if c[1].strategy in SEMANTIC_SELECTORS]
    if semantic:
        accepted = semantic

    if accepted:
        text_chars, best = max(accepted, key=lambda c: c[0])
        report = CleaningReport(
            strategy=best.strategy,
            raw_bytes=len(raw.encode("utf-8")),
            precleaned_bytes=len(precleaned.encode("utf-8")),
            cleaned_bytes=len(best.html.encode("utf-8")),
            text_chars=text_chars,
        )
        return best.html, report

    report = CleaningReport(
        strategy=PRECLEANED_FALLBACK,
        raw_bytes=len(raw.encode("utf-8")),
        precleaned_bytes=len(precleaned.encode("utf-8")),
        cleaned_bytes=len(precleaned.encode("utf-8")),
        text_chars=reference_chars,
    )
    return precleaned, report


def clean_html_file(source_path: Path, dest_path: Path, options: CleaningOptions) -> CleaningReport:
    """Nettoie un fichier HTML et ecrit le resultat.

    Args:
        source_path: Fichier HTML source.
        dest_path: Destination du HTML nettoye (les dossiers sont crees).
        options: Options de nettoyage de la source.

    Returns:
        Le :class:`CleaningReport` du nettoyage.
    """
    raw = source_path.read_text(encoding="utf-8", errors="ignore")
    cleaned, report = clean_html(raw, options)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(cleaned, encoding="utf-8")
    return report
