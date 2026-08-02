"""Conversion Docling d'un document en elements, puis persistance.

Deux regimes selon le format :

- **PDF** : conversion par batchs de pages pour borner la memoire, avec crop
  des elements visuels vers MinIO. Les batchs ne se chevauchent plus : les ids
  etant deterministes, le chevauchement ne faisait que re-convertir les memes
  pages, soit environ 40 % de temps GPU perdu.
- **HTML et Markdown** : conversion d'un seul tenant, sans pagination ni crop.
  Les images des captures HTML ont deja ete exportees vers MinIO par le
  pipeline Dagster, on se contente de propager leur URL.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from src.docling_service import images, storage
from src.docling_service.elements import VISUAL_LABELS, DocumentAccumulator
from src.docling_service.markdown import (
    IMAGE_MARKER,
    ImageReference,
    extract_image_references,
    normalize_markdown,
)
from src.docling_service.settings import get_settings

logger = logging.getLogger(__name__)

# Formats convertis d'un seul tenant, et type_file associe.
FLAT_SUFFIXES: dict[str, str] = {
    ".html": "html",
    ".htm": "html",
    ".md": "md",
    ".markdown": "md",
}
PDF_SUFFIXES: set[str] = {".pdf"}

SUPPORTED_SUFFIXES: set[str] = PDF_SUFFIXES | set(FLAT_SUFFIXES)

# Signature du rapporteur d'avancement (``Job.report``).
Reporter = Callable[..., None]


class UnsupportedFormatError(ValueError):
    """Le format du fichier n'est pas pris en charge."""


class BatchExtractionError(RuntimeError):
    """Au moins un batch de pages n'a pas pu etre converti."""


def _noop(**_: Any) -> None:
    """Rapporteur par defaut, sans effet."""


@lru_cache(maxsize=1)
def get_converter() -> DocumentConverter:
    """Retourne le convertisseur Docling, construit au premier appel.

    Construit paresseusement pour que le module reste importable (et le service
    demarrable) meme si le chargement des modeles est lent.
    """
    logger.info("Chargement des modeles Docling...")
    # Docling active l'OCR et la reconstruction de structure des tables par
    # defaut : sur un livre de plusieurs centaines de pages, cela multiplie le
    # temps de conversion. Les tables sont de toute facon croppees en image.
    # La cle est InputFormat.PDF et non "pdf" : les deux fonctionnent
    # aujourd'hui, mais une cle non reconnue ferait retomber silencieusement
    # sur les defauts, sans autre symptome qu'une lenteur inexpliquee.
    options = PdfPipelineOptions(do_ocr=False, do_table_structure=False)
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


def extract(path: Path, report: Reporter = _noop) -> dict[str, Any]:
    """Extrait un document et le persiste dans les stores.

    Args:
        path: Chemin du fichier a extraire.
        report: Rapporteur d'avancement, appele avec des mots-cles.

    Returns:
        Bilan de l'extraction (elements, chunks, pages).

    Raises:
        UnsupportedFormatError: Si l'extension n'est pas prise en charge.
        BatchExtractionError: Si au moins un batch de pages a echoue.
    """
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        return _extract_pdf(path, report)
    type_file = FLAT_SUFFIXES.get(suffix)
    if type_file is None:
        raise UnsupportedFormatError(
            f"Format non pris en charge : {suffix or path.name} "
            f"(attendus : {', '.join(sorted(SUPPORTED_SUFFIXES))})"
        )
    return _extract_flat(path, type_file, report)


def _index_attachments(directory: Path) -> dict[str, Path]:
    """Recense les fichiers presents autour d'une note, par nom.

    Obsidian ne met que le nom du fichier dans ``![[image.jpg]]`` et resout le
    reste lui-meme. On reconstitue cette resolution en indexant une fois pour
    toutes ce qui vit a cote de la note.
    """
    index: dict[str, Path] = {}
    for candidate in directory.rglob("*"):
        if candidate.is_file():
            index.setdefault(candidate.name, candidate)
    return index


def _resolve_image(target: str, note_dir: Path, attachments: dict[str, Path]) -> Path | None:
    """Retrouve le fichier image designe par un lien Markdown."""
    direct = note_dir / target
    if direct.is_file():
        return direct
    return attachments.get(Path(target).name)


def _upload_markdown_images(
    references: list[ImageReference], note_dir: Path, doc_key: str
) -> dict[int, str]:
    """Envoie sur MinIO les images referencees par une note.

    Returns:
        Les URL obtenues, indexees par rang de l'image. Une image introuvable
        ou refusee est simplement absente : l'element restera dans le graphe,
        sans URL.
    """
    if not references:
        return {}

    attachments = _index_attachments(note_dir)
    urls: dict[int, str] = {}
    introuvables = 0

    for reference in references:
        source = _resolve_image(reference.target, note_dir, attachments)
        if source is None:
            introuvables += 1
            continue
        url = images.upload_file(source, doc_key, reference.index)
        if url:
            urls[reference.index] = url

    logger.info(
        "[%s] images : %d referencees, %d envoyees, %d introuvables",
        doc_key,
        len(references),
        len(urls),
        introuvables,
    )
    return urls


@contextmanager
def _prepared_source(path: Path, type_file: str) -> Iterator[tuple[Path, dict[int, str]]]:
    """Fournit le fichier a convertir, prepare si besoin, et ses images.

    Deux traitements pour le Markdown, dans cet ordre :

    1. **Les images sont sorties du texte** et remplacees par une balise a leur
       position exacte, puis envoyees sur MinIO. Docling ne reconnait ni la
       syntaxe Obsidian ``![[fichier.jpg]]`` ni ``![](chemin)`` : sans cela,
       les images seraient rendues en texte brut et perdues.
    2. **Les paragraphes sont recolles**, Docling convertissant le Markdown
       ligne a ligne.

    Le fichier source n'est jamais modifie ; la version preparee vit dans un
    repertoire temporaire, supprime a la sortie.
    """
    if type_file != "md":
        yield path, {}
        return

    original = path.read_text(encoding="utf-8", errors="replace")
    balise, references = extract_image_references(original)
    prepared = normalize_markdown(balise)
    urls = _upload_markdown_images(references, path.parent, path.stem)

    if prepared == original:
        yield path, urls
        return

    # Repertoire temporaire plutot que fichier temporaire : le nom d'origine est
    # conserve, ce qui garde des logs lisibles cote Docling.
    directory = Path(tempfile.mkdtemp(prefix="md-prepare-"))
    try:
        target = directory / path.name
        target.write_text(prepared, encoding="utf-8")
        logger.info("[%s] Markdown prepare (images extraites, paragraphes recolles)", path.stem)
        yield target, urls
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _extract_flat(path: Path, type_file: str, report: Reporter) -> dict[str, Any]:
    """Convertit un document non pagine (HTML, Markdown) d'un seul tenant."""
    stem = path.stem
    logger.info("[%s] conversion %s...", stem, type_file)
    report(pages_total=1, pages_done=0, elements=0, chunks=0)

    with _prepared_source(path, type_file) as (source_path, image_urls):
        result = get_converter().convert(str(source_path))

    document = result.document
    accumulator = DocumentAccumulator(stem)
    elements: list[dict[str, Any]] = []

    for item, _ in document.iterate_items():
        element = accumulator.add_item(item, document)

        # Balise laissee par la preparation du Markdown : cet element est une
        # image. On lui rend sa nature et son URL, en place — donc rattache a
        # la meme section, avec sa legende toujours adjacente.
        marker = IMAGE_MARKER.match(element["text"])
        if marker is not None:
            element["label"] = "picture"
            element["text"] = marker.group(2).strip()
            url = image_urls.get(int(marker.group(1)))
            if url:
                element["minio_url"] = url

        # Les images des captures HTML sont deja sur MinIO (src reecrit par le
        # nettoyage) : on propage l'URL sur le noeud Picture.
        uri = getattr(getattr(item, "image", None), "uri", None)
        if uri and str(uri).startswith("http"):
            element["minio_url"] = str(uri)
        elements.append(element)

    chunks = storage.persist(elements, stem, type_file, total_pages=1)
    report(pages_done=1, elements=len(elements), chunks=chunks)
    logger.info("[%s] termine : %d elements, %d chunks", stem, len(elements), chunks)
    return {"elements": len(elements), "chunks": chunks, "pages": 1, "type_file": type_file}


def _extract_pdf(path: Path, report: Reporter) -> dict[str, Any]:
    """Convertit un PDF par batchs de pages, avec crop des elements visuels."""
    import fitz  # import local : PyMuPDF n'est present que dans l'image d'extraction

    settings = get_settings()
    pdf_path = str(path)
    stem = path.stem

    with fitz.open(pdf_path) as document:
        total_pages: int = len(document)

    logger.info("[%s] PDF de %d pages", stem, total_pages)
    report(pages_total=total_pages, pages_done=0, elements=0, chunks=0)

    accumulator = DocumentAccumulator(stem)
    converter = get_converter()
    total_chunks = 0
    failed_batches: list[str] = []

    # Le PDF est ouvert UNE fois pour tous les crops du document.
    with fitz.open(pdf_path) as document:
        start_page = 1
        while start_page <= total_pages:
            end_page = min(start_page + settings.pdf_batch_pages - 1, total_pages)
            logger.info("[%s] batch %d-%d/%d", stem, start_page, end_page, total_pages)

            try:
                batch_elements = _convert_batch(
                    converter, pdf_path, stem, document, accumulator, start_page, end_page
                )
            except Exception as exc:
                # Une page illisible ne doit pas condamner les 399 autres : on
                # note l'echec, on continue, et le job echouera a la fin avec
                # la liste des pages manquantes. Jamais un run vert sur un trou.
                logger.exception("[%s] batch %d-%d en echec", stem, start_page, end_page)
                failed_batches.append(f"{start_page}-{end_page} ({type(exc).__name__}: {exc})")
            else:
                # L'ecriture, elle, est bloquante : si un store refuse le lot,
                # continuer n'aurait aucun sens.
                total_chunks += storage.persist(batch_elements, stem, "pdf", total_pages)

            report(
                pages_done=end_page,
                elements=accumulator.count,
                chunks=total_chunks,
                failed_batches=list(failed_batches),
            )
            start_page = end_page + 1

    if failed_batches:
        raise BatchExtractionError(
            f"{len(failed_batches)} batch(s) non convertis pour {stem} : "
            f"{'; '.join(failed_batches)}"
        )

    logger.info("[%s] termine : %d elements, %d chunks", stem, accumulator.count, total_chunks)
    return {
        "elements": accumulator.count,
        "chunks": total_chunks,
        "pages": total_pages,
        "type_file": "pdf",
    }


def _convert_batch(
    converter: DocumentConverter,
    pdf_path: str,
    stem: str,
    document: Any,
    accumulator: DocumentAccumulator,
    start_page: int,
    end_page: int,
) -> list[dict[str, Any]]:
    """Convertit une plage de pages et construit ses elements."""
    result = converter.convert(pdf_path, page_range=(start_page, end_page))
    elements: list[dict[str, Any]] = []

    for item, _ in result.document.iterate_items():
        element = accumulator.add_item(item, result.document)
        if element["label"] in VISUAL_LABELS and element["bbox"]:
            element["minio_url"] = images.crop_and_upload(
                document,
                stem,
                element["page_no"],
                element["bbox"],
                element["id"],
                element["label"],
            )
        elements.append(element)

    # Libere le backend Docling entre deux batchs (API privee, d'ou le garde-fou).
    backend = getattr(getattr(result, "input", None), "_backend", None)
    if backend is not None:
        backend.unload()

    return elements
