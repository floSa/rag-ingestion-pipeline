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

import base64
import binascii
import html as html_module
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import trafilatura
from bs4 import BeautifulSoup, Comment, Tag
from bs4.element import NavigableString
from readability import Document

from src.pipeline.sources import CleaningOptions, ExtractionProfile

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

# Formules : source LaTeX laissee dans le DOM par les moteurs de rendu web.
_MATH_TEX_TYPE = re.compile(r"^\s*math/tex", re.I)

# Restes de rendu MathJax v2 a purger une fois la source recuperee.
_MATHJAX_RENDER_SELECTORS: list[str] = [
    ".MathJax",
    ".MathJax_Display",
    ".MathJax_Preview",
    ".MathJax_SVG",
    ".MathJax_SVG_Display",
    ".MathJax_CHTML",
]

# Decorations d'ancres frequentes a l'interieur des titres (liens « # », « ¶ »).
_ANCHOR_DECORATIONS: set[str] = {"#", "##", "¶", "§"}

# Separateurs usuels entre titre d'article et nom de site dans <title>.
_TITLE_SEP = re.compile(r"\s+[-–—|·»]\s+")


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


class ImageExporter(Protocol):
    """Exporte une image decodee et retourne son URL, ou None si echec."""

    def __call__(self, payload: bytes, mime: str, index: int) -> str | None:
        """Upload l'image ``payload`` (type ``mime``) et retourne son URL."""
        ...


def _decode_data_uri(value: str) -> tuple[str, bytes] | None:
    """Decode une URI ``data:...;base64,...`` en (mime, octets), ou None."""
    header, _, payload = value.partition(",")
    if not payload or ";base64" not in header:
        return None
    mime = header[5:].split(";")[0] or "application/octet-stream"
    payload = "".join(payload.split())  # certains generateurs inserent des sauts de ligne
    try:
        return mime, base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None


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


class ProfileStrategy:
    """Extraction par profil dedie a un site (selecteurs declares en YAML)."""

    def __init__(self, profile: ExtractionProfile, min_chars: int) -> None:
        self.profile = profile
        self.min_chars = min_chars

    def extract(self, html: str) -> ExtractionCandidate | None:
        soup = BeautifulSoup(html, "lxml")
        if not soup.select_one(self.profile.detect):
            return None
        tags = _top_level_only(soup.select(self.profile.content))
        for selector in self.profile.strip:
            for tag in tags:
                _decompose_all(tag.select(selector))
        text_len = sum(len(t.get_text(strip=True)) for t in tags)
        if text_len < self.min_chars:
            return None
        fragment = "\n".join(str(t) for t in tags)
        return ExtractionCandidate(strategy=self.profile.name, html=fragment)


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


def _convert_math_to_latex(soup: BeautifulSoup) -> int:
    """Remplace in-place les formules rendues par leur source LaTeX en texte.

    Les moteurs de rendu web gardent presque toujours la source LaTeX dans le
    DOM : KaTeX (``<annotation encoding="application/x-tex">``), MathJax v2
    (``<script type="math/tex">``), MathJax v3 (``<mjx-container>``), MathML
    natif (``<math>``). Chaque formule devient ``$latex$`` (inline) ou
    ``$$latex$$`` (bloc) — texte simple qui traverse l'extraction et que
    Docling indexera tel quel.

    A appeler AVANT la suppression du bruit, qui supprime les <script>.

    Returns:
        Nombre de formules converties.
    """
    converted = 0

    def replace(element: Tag, latex: str, display: bool) -> None:
        nonlocal converted
        latex = latex.strip()
        if not latex:
            element.decompose()
            return
        element.replace_with(NavigableString(f"$${latex}$$" if display else f"${latex}$"))
        converted += 1

    # MathJax v2 : la source est dans un <script type="math/tex[; mode=display]">.
    for script in soup.find_all("script", type=_MATH_TEX_TYPE):
        display = "mode=display" in (script.get("type") or "")
        replace(script, script.get_text(), display)
    for selector in _MATHJAX_RENDER_SELECTORS:
        _decompose_all(soup.select(selector))

    # KaTeX : on remplace .katex-display entier si present, sinon .katex.
    for katex in soup.select(".katex"):
        if katex.decomposed:
            continue
        annotation = katex.select_one('annotation[encoding="application/x-tex"]')
        latex = annotation.get_text() if annotation else katex.get_text(" ", strip=True)
        wrapper = katex.find_parent(class_="katex-display")
        replace(wrapper or katex, latex, display=wrapper is not None)

    # MathJax v3 : <mjx-container display="true|false">.
    for container in soup.find_all("mjx-container"):
        annotation = container.select_one('annotation[encoding="application/x-tex"]')
        latex = (
            annotation.get_text()
            if annotation
            else str(container.get("aria-label") or container.get_text(" ", strip=True))
        )
        replace(container, latex, display=container.get("display") == "true")

    # MathML natif restant.
    for math in soup.find_all("math"):
        if math.decomposed:
            continue
        annotation = math.select_one('annotation[encoding="application/x-tex"]')
        latex = annotation.get_text() if annotation else math.get_text(" ", strip=True)
        replace(math, latex, display=math.get("display") == "block")

    return converted


def _tidy_headings(soup: BeautifulSoup) -> None:
    """Retire les decorations d'ancres dans les titres (ex. « ## # Intro »)."""
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        for child in list(heading.children):
            if isinstance(child, Tag) and child.get_text(strip=True) in _ANCHOR_DECORATIONS:
                child.decompose()
            elif isinstance(child, NavigableString) and child.strip() in _ANCHOR_DECORATIONS:
                child.extract()


def _page_title(html: str) -> str:
    """Titre d'article du <title>, sans le suffixe du site (« X - MonSite »)."""
    match = re.search(r"<title[^>]*>([^<]{1,300})</title>", html, re.IGNORECASE)
    if not match:
        return ""
    title = html_module.unescape(match.group(1)).strip()
    parts = _TITLE_SEP.split(title)
    return " ".join(parts[:-1]).strip() if len(parts) >= 2 else title


def _ensure_heading(fragment: str, title: str) -> str:
    """Prefixe un <h1> si aucun n'a survecu a l'extraction (structure Docling)."""
    if not title or re.search(r"<h1[\s>]", fragment, re.IGNORECASE):
        return fragment
    return f"<h1>{html_module.escape(title)}</h1>\n{fragment}"


def preclean_html(
    raw: str,
    options: CleaningOptions,
    image_exporter: ImageExporter | None = None,
) -> str:
    """Pre-passe d'hygiene : retire le bruit non ambigu sans toucher au contenu.

    Concu pour les captures SingleFile : elements caches, chrome de page,
    attributs ``style``, handlers ``on*`` sont supprimes, ce qui divise la
    taille du fichier avant extraction. Les images ``data:`` au-dela d'un
    seuil sont exportees via ``image_exporter`` (src reecrit avec l'URL),
    ou supprimees si aucun exporteur n'est fourni / si l'export echoue.

    Args:
        raw: HTML brut.
        options: Options de nettoyage de la source.
        image_exporter: Destination des images base64 volumineuses (MinIO).

    Returns:
        HTML allege, structure du contenu intacte.
    """
    soup = BeautifulSoup(raw, "lxml")

    # Formules d'abord : leur source LaTeX vit dans des <script>/<svg>/MathML
    # que la suppression du bruit detruirait.
    _convert_math_to_latex(soup)

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

    image_index = 0
    for element in soup.find_all(True):
        if not isinstance(element, Tag) or element.decomposed:
            continue
        element.attrs.pop("style", None)
        for attr in [a for a in element.attrs if a.startswith("on")]:
            del element.attrs[attr]
        for attr in URI_ATTRS:
            value = element.attrs.get(attr)
            if not (isinstance(value, str) and value.startswith("data:")):
                continue

            if len(value) <= options.max_data_uri_bytes:
                # Petite image inline = icone d'interface : on la jette entiere.
                if element.name == "img" and attr == "src":
                    element.decompose()
                    break
                continue

            exported_url: str | None = None
            if image_exporter is not None and element.name == "img" and attr == "src":
                decoded = _decode_data_uri(value)
                if decoded is not None:
                    mime, payload = decoded
                    exported_url = image_exporter(payload, mime, image_index)
                    image_index += 1

            if exported_url:
                element.attrs[attr] = exported_url
            else:
                del element.attrs[attr]

    _tidy_headings(soup)

    return str(soup)


def _build_report(raw: str, precleaned: str, html: str, strategy: str) -> CleaningReport:
    """Construit le bilan d'un nettoyage."""
    return CleaningReport(
        strategy=strategy,
        raw_bytes=len(raw.encode("utf-8")),
        precleaned_bytes=len(precleaned.encode("utf-8")),
        cleaned_bytes=len(html.encode("utf-8")),
        text_chars=_visible_text_length(html),
    )


def clean_html(
    raw: str,
    options: CleaningOptions,
    image_exporter: ImageExporter | None = None,
) -> tuple[str, CleaningReport]:
    """Nettoie un HTML quelconque et retourne (html_nettoye, bilan).

    Ordre de priorite :

    1. Un **profil par site** (``options.profiles``) qui matche : choix
       explicite de l'utilisateur, il gagne directement.
    2. Un **conteneur semantique HTML5** (<article>, <main>) qui passe les
       seuils : delimitation posee par l'auteur de la page.
    3. **trafilatura** et **readability** compares : le candidat qui conserve
       le plus de texte gagne.
    4. En dernier recours, le HTML pre-nettoye est conserve tel quel.

    Args:
        raw: HTML brut (capture SingleFile, export editeur, etc.).
        options: Options de nettoyage de la source.
        image_exporter: Destination des images base64 volumineuses (MinIO).

    Returns:
        Le HTML nettoye et le :class:`CleaningReport` associe.
    """
    precleaned = preclean_html(raw, options, image_exporter)
    reference_chars = _visible_text_length(precleaned)
    title = _page_title(precleaned)

    for profile in options.profiles:
        profiled = ProfileStrategy(profile, options.min_text_chars).extract(precleaned)
        if profiled is not None:
            fragment = _ensure_heading(profiled.html, title)
            return fragment, _build_report(raw, precleaned, fragment, profiled.strategy)

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
        _, best = max(accepted, key=lambda c: c[0])
        fragment = _ensure_heading(best.html, title)
        return fragment, _build_report(raw, precleaned, fragment, best.strategy)

    # Le repli est le document entier (avec son <head>) : pas de h1 a prefixer.
    return precleaned, _build_report(raw, precleaned, precleaned, PRECLEANED_FALLBACK)


def clean_html_file(
    source_path: Path,
    dest_path: Path,
    options: CleaningOptions,
    image_exporter: ImageExporter | None = None,
) -> CleaningReport:
    """Nettoie un fichier HTML et ecrit le resultat.

    Args:
        source_path: Fichier HTML source.
        dest_path: Destination du HTML nettoye (les dossiers sont crees).
        options: Options de nettoyage de la source.
        image_exporter: Destination des images base64 volumineuses (MinIO).

    Returns:
        Le :class:`CleaningReport` du nettoyage.
    """
    raw = source_path.read_text(encoding="utf-8", errors="ignore")
    cleaned, report = clean_html(raw, options, image_exporter)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(cleaned, encoding="utf-8")
    return report
