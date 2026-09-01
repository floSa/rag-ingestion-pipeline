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

import hashlib
import logging
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter

from src.docling_service import images, language, matter, ranking, storage
from src.docling_service.elements import (
    VISUAL_LABELS,
    DocumentAccumulator,
    DocumentFacts,
    DocumentIdentity,
    document_identity,
    extract_bbox,
    item_provenance,
    pages_sans_element,
)
from src.docling_service.markdown import (
    IMAGE_MARKER,
    ImageReference,
    extract_image_references,
    normalize_markdown,
)
from src.docling_service.nebula import get_writer
from src.docling_service.ngql import document_vid
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


@lru_cache(maxsize=2)
def get_converter(ocr: bool = False) -> DocumentConverter:
    """Retourne le convertisseur Docling, construit au premier appel.

    Construit paresseusement pour que le module reste importable (et le service
    demarrable) meme si le chargement des modeles est lent.

    **`docling` est importe ICI et non au niveau du module.** Il n'est pas dans
    le venv du depot — les deps lourdes d'extraction vivent dans
    `Dockerfile.docling` — donc un import de module rendait
    `src.docling_service.extraction` INIMPORTABLE cote hote, et tout ce qu'il
    decide intestable : c'est ce qui laissait le contrat de `page_batches` sans
    garde (registre 4.14), et c'est ce qui laisserait sans garde les deux appels
    a `storage.forget_document` (4.1 et 4.2). C'est le meme geste que
    `vectors.get_collection`, `nebula._connect` et le `import fitz` local de ce
    fichier meme — sur le sixieme et dernier module dans ce cas.
    *Ce qu'un test n'importe pas, il ne teste pas.*

    Args:
        ocr: Activer la reconnaissance de caracteres. Reserve aux documents
            scannes : elle multiplie le temps de conversion, et un PDF normal
            n'en a aucun besoin puisque son texte est deja lisible.

    Returns:
        Le convertisseur correspondant. Les deux variantes sont conservees,
        pour ne pas recharger les modeles a chaque bascule.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    logger.info("Chargement des modeles Docling (ocr=%s)...", ocr)
    # Docling active l'OCR et la reconstruction de structure des tables par
    # defaut : sur un livre de plusieurs centaines de pages, cela multiplie le
    # temps de conversion. Les tables sont de toute facon croppees en image.
    # La cle est InputFormat.PDF et non "pdf" : les deux fonctionnent
    # aujourd'hui, mais une cle non reconnue ferait retomber silencieusement
    # sur les defauts, sans autre symptome qu'une lenteur inexpliquee.
    options = PdfPipelineOptions(do_ocr=ocr, do_table_structure=False)
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


def extract(path: Path, source_path: str = "", report: Reporter = _noop) -> dict[str, Any]:
    """Extrait un document et le persiste dans les stores.

    Args:
        path: Chemin du fichier a extraire.
        source_path: Chemin relatif a ``Datas/``, porteur de l'identite du
            document. Deduit du chemin du fichier s'il n'est pas fourni.
        report: Rapporteur d'avancement, appele avec des mots-cles.

    Returns:
        Bilan de l'extraction (elements, chunks, pages).

    Raises:
        UnsupportedFormatError: Si l'extension n'est pas prise en charge.
        BatchExtractionError: Si au moins un batch de pages a echoue.
    """
    identity = document_identity(source_path or _deduce_source_path(path))
    suffix = path.suffix.lower()

    # Le controle de doublon precede la conversion : reconnaitre un ouvrage
    # deja ingere coute une lecture de fichier, le convertir pour rien coute
    # plusieurs minutes de GPU.
    content_hash = file_digest(path)
    deja_ingere = _already_ingested(content_hash, identity)
    if deja_ingere:
        logger.info("[%s] doublon exact de %s : ignore", identity.filename, deja_ingere)
        report(duplicate_of=deja_ingere)
        return {
            "elements": 0,
            "chunks": 0,
            "pages": 0,
            "duplicate_of": deja_ingere,
            "type_file": suffix.lstrip("."),
        }

    # REGISTRE 4.2 : le document est OUBLIE avant d'etre reecrit. Les
    # identifiants derivent du texte, donc un texte modifie produit de nouveaux
    # identifiants et `upsert` laisse les anciens derriere lui, en orphelins,
    # dans les deux stores. Le capteur declenchant sur `mtime`, mettre a jour un
    # document est le chemin NOMINAL : c'est lui qui cassait.
    #
    # La purge vient APRES le controle de doublon, et l'ordre compte : un doublon
    # exact sort plus haut sans rien toucher, donc reingerer un fichier
    # inchange ne detruit rien pour le reecrire a l'identique.
    #
    # Elle leve si un store resiste : reecrire par-dessus une purge a moitie
    # faite est exactement ce que 4.2 decrit.
    storage.forget_document(identity)

    if suffix in PDF_SUFFIXES:
        return _extract_pdf(path, identity, content_hash, report)
    type_file = FLAT_SUFFIXES.get(suffix)
    if type_file is None:
        raise UnsupportedFormatError(
            f"Format non pris en charge : {suffix or path.name} "
            f"(attendus : {', '.join(sorted(SUPPORTED_SUFFIXES))})"
        )
    return _extract_flat(path, identity, type_file, content_hash, report)


def file_digest(path: Path) -> str:
    """Empreinte SHA-256 du fichier, lue par blocs.

    Args:
        path: Fichier a empreindre.

    Returns:
        L'empreinte en hexadecimal, ou une chaine vide si la lecture echoue —
        auquel cas le controle de doublon est simplement inoperant, il ne doit
        jamais empecher une ingestion.
    """
    empreinte = hashlib.sha256()
    try:
        with open(path, "rb") as fichier:
            for bloc in iter(lambda: fichier.read(1 << 20), b""):
                empreinte.update(bloc)
    except OSError as exc:
        logger.warning("Empreinte illisible pour %s : %s", path, exc)
        return ""
    return empreinte.hexdigest()


def _already_ingested(content_hash: str, identity: DocumentIdentity) -> str:
    """Chemin d'un document deja ingere portant le meme fichier, sinon vide.

    Ne leve jamais : un graphe indisponible doit faire echouer l'ecriture, pas
    la detection de doublon, qui n'est qu'un confort.
    """
    try:
        return get_writer().find_duplicate(content_hash, document_vid(identity.key))
    except Exception as exc:
        logger.warning("Controle de doublon impossible : %s", exc)
        return ""


def _deduce_source_path(path: Path) -> str:
    """Deduit le chemin relatif a ``Datas/`` quand le pipeline ne l'a pas fourni.

    Cas d'un appel manuel a l'API. On coupe au dossier ``Datas`` ; a defaut, on
    retombe sur le nom du fichier seul — l'ouvrage sera alors inconnu.
    """
    parts = path.parts
    if "Datas" in parts:
        return "/".join(parts[parts.index("Datas") + 1 :])
    return path.name


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


def html_image_urls(chemin: Path) -> list[str]:
    """URL MinIO des images d'un HTML nettoye, dans l'ordre du document.

    **C'est la seule voie qui reste, et voici pourquoi.** `cleaning.py` reecrit
    `img src` avec l'URL MinIO, mais Docling ne la rend nulle part : `mesure` le
    1er septembre 2026 sur 4 chapitres nettoyes convertis dans l'image
    d'extraction, `item.image` vaut `None` sur **24 items `picture` sur 24**, et
    `item.source`, `item.references` et `item.meta` sont vides aussi. Le test
    `item.image.uri.startswith("http")` qui vivait ici n'etait donc JAMAIS
    atteint : la chaine etait rompue en amont de sa propre garde (registre 3.5).

    Seuls les `src` en `http` sont rendus. Une image restee en `data:` ou en
    chemin relatif n'a pas d'objet MinIO : la compter decalerait toutes les URL
    suivantes d'un rang, et chaque image recevrait celle de sa voisine.

    Ne leve jamais : lire ces URL est un confort, et une image sans URL est un
    defaut connu qui se compte. Un document non ingere, lui, est une perte.

    Args:
        chemin: Fichier HTML nettoye.

    Returns:
        Les URL, dans l'ordre du document.
    """
    try:
        html = chemin.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        logger.warning("HTML nettoye illisible pour les URL d'images (%s) : %s", chemin, exc)
        return []

    soupe = BeautifulSoup(html, "lxml")
    return [
        str(balise.get("src"))
        for balise in soupe.find_all("img")
        if str(balise.get("src") or "").startswith("http")
    ]


def propager_les_url_dimages(elements: list[dict[str, Any]], urls: list[str], stem: str) -> int:
    """Pose les URL sur les elements `picture`, dans l'ordre, ou n'en pose aucune.

    La correspondance est POSITIONNELLE : la n-ieme `<img>` du HTML nettoye est
    la n-ieme `picture` rendue par Docling. `mesure` le 1er septembre 2026 sur
    4 chapitres, les deux comptes concordent **4 fois sur 4** — 4/4, 1/1, 9/9,
    10/10.

    **UNE CORRESPONDANCE POSITIONNELLE EST FRAGILE, DONC ELLE EST GARDEE PAR UN
    REFUS.** Si les deux comptes divergent, aucune URL n'est posee. Une URL
    FAUSSE sur une image est pire qu'une URL absente : l'agent servirait
    l'illustration d'un autre passage, et rien ne le dirait — alors qu'une URL
    absente est deja comptee par `verify_contract`, qui la rapporte comme une
    anomalie. Entre une perte bruyante et une erreur muette, on choisit la perte
    bruyante.

    Seuls les `picture` sont cibles, et non tous les elements visuels : un
    `table` est visuel mais n'est pas une `<img>` du HTML. Le compter decalerait
    toutes les URL.

    Args:
        elements: Elements du document, modifies en place.
        urls: URL lues dans le HTML nettoye, dans l'ordre.
        stem: Nom du document, pour le journal.

    Returns:
        Le nombre d'URL reellement posees.
    """
    cibles = [element for element in elements if str(element.get("label")) == "picture"]
    if not cibles and not urls:
        return 0

    if len(cibles) != len(urls):
        logger.warning(
            "[%s] %d image(s) rendues par Docling pour %d URL dans le HTML "
            "nettoye : AUCUNE URL n'est posee. La correspondance est "
            "positionnelle, et une URL fausse servirait l'illustration d'un "
            "autre passage sans qu'aucune erreur ne le dise. Les images "
            "resteront sans URL, et verify_contract les comptera",
            stem,
            len(cibles),
            len(urls),
        )
        return 0

    for cible, url in zip(cibles, urls, strict=True):
        cible["minio_url"] = url
    return len(cibles)


def _extract_flat(
    path: Path,
    identity: DocumentIdentity,
    type_file: str,
    content_hash: str,
    report: Reporter,
) -> dict[str, Any]:
    """Convertit un document non pagine (HTML, Markdown) d'un seul tenant."""
    stem = identity.filename
    logger.info("[%s] conversion %s...", stem, type_file)
    report(pages_total=1, pages_done=0, elements=0, chunks=0)

    with _prepared_source(path, type_file) as (source_path, image_urls):
        # Le chemin REELLEMENT converti : pour le HTML c'est le fichier nettoye,
        # celui dont `cleaning.py` a reecrit les `img src`. Le lire est la seule
        # facon de recuperer les URL, Docling ne les rendant nulle part.
        source_path_utilise = source_path
        result = get_converter().convert(str(source_path))

    document = result.document
    accumulator = DocumentAccumulator(identity)
    elements: list[dict[str, Any]] = []

    for item, _ in document.iterate_items():
        rang = ranking.flat_rank(item, document)
        element = accumulator.add_item(item, document, heading_rank=rang)

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

        elements.append(element)

    # LES IMAGES DES CAPTURES HTML. Ce bloc testait
    # `item.image.uri.startswith("http")`, et ce test n'etait JAMAIS atteint :
    # `item.image` vaut `None` sur tous les `picture` rendus depuis un HTML
    # (`mesure`, 0/24). Les 199 images du corpus n'avaient donc aucune
    # `minio_url`, et l'agent ne sert que ce que le graphe reference — elles
    # etaient payees en place et en temps, et inatteignables (registre 3.5).
    #
    # L'URL est desormais lue dans le HTML nettoye, ou `cleaning.py` l'a ecrite,
    # et posee par correspondance positionnelle — gardee par un refus.
    if type_file == "html":
        posees = propager_les_url_dimages(elements, html_image_urls(source_path_utilise), stem)
        if posees:
            logger.info("[%s] %d URL d'images posees sur les noeuds Picture", stem, posees)

    langue = _detect_document_language(elements, stem)
    facts = DocumentFacts(
        type_file=type_file, total_pages=1, language=langue, content_hash=content_hash
    )
    chunks = storage.persist(elements, identity, facts, document)
    report(pages_done=1, elements=len(elements), chunks=chunks, language=langue)
    logger.info("[%s] termine : %d elements, %d chunks", stem, len(elements), chunks)
    return {
        "elements": len(elements),
        "chunks": chunks,
        "pages": 1,
        "language": langue,
        "type_file": type_file,
    }


def _detect_document_language(elements: list[dict[str, Any]], stem: str) -> str:
    """Determine la langue du document a partir du texte de ses elements.

    Args:
        elements: Elements extraits, dans l'ordre du document.
        stem: Nom du document, pour le log.

    Returns:
        Le code ISO 639-1, ou une chaine vide si indeterminee.
    """
    echantillon = language.sample_text([str(e.get("text") or "") for e in elements])
    langue = language.detect_language(echantillon)
    logger.info("[%s] langue : %s", stem, langue or "indeterminee")
    return langue


def _extract_pdf(
    path: Path, identity: DocumentIdentity, content_hash: str, report: Reporter
) -> dict[str, Any]:
    """Convertit un PDF par batchs de pages, avec crop des elements visuels."""
    import fitz  # import local : PyMuPDF n'est present que dans l'image d'extraction

    settings = get_settings()
    pdf_path = str(path)
    stem = identity.filename

    with fitz.open(pdf_path) as document:
        total_pages = len(document)
        skipped = _front_back_matter_pages(document, total_pages, stem)
        ranges = matter.kept_ranges(total_pages, skipped)
        besoin_ocr = not _has_text_layer(document, ranges)

    if besoin_ocr:
        # Un scan n'a pas de texte selectionnable. Plutot que de le refuser, on
        # le repasse avec la reconnaissance de caracteres : c'est lent, mais
        # c'est le seul moyen de lire l'ouvrage. Les PDF normaux, eux, gardent
        # leur vitesse puisqu'ils n'empruntent jamais cette branche.
        logger.warning("[%s] aucune couche texte : conversion avec OCR", stem)

    logger.info("[%s] PDF de %d pages, %d ecartees (hors contenu)", stem, total_pages, len(skipped))
    report(
        pages_total=total_pages,
        pages_done=0,
        elements=0,
        chunks=0,
        skipped_pages=len(skipped),
    )

    # Profil typographique du document, releve une fois : taille dominante du
    # corps du texte, et classement des tailles de titre. Rien n'est ecrit en
    # dur — un ouvrage en 24/22/20 points se classe comme un ouvrage en 20/18/16.
    with fitz.open(pdf_path) as document:
        body_size, size_ranks = _pdf_font_profile(document, ranges)
    logger.info(
        "[%s] corps du texte a %.0f pt, %d tailles de titre distinctes",
        stem,
        body_size,
        len(size_ranks),
    )

    accumulator = DocumentAccumulator(identity)
    pages_couvertes: list[dict[str, Any]] = []
    converter = get_converter(ocr=besoin_ocr)
    total_chunks = 0
    failed_batches: list[str] = []
    # Combien de titres ont recu un rang MESURE, et combien le rang de repli.
    # Le PDF ne classe que les tailles superieures au corps du texte : tout le
    # reste s'empile sous le titre courant, et rien ne le comptait. Sans ce
    # chiffre, une profondeur relevee dans le graphe melange des niveaux
    # mesures et un empilement par defaut sans qu'on puisse les distinguer.
    titres_total = 0
    titres_replis = 0
    # Detectee sur le premier lot converti, puis conservee : la langue d'un
    # ouvrage ne change pas en cours de route, et les lots suivants ecrivent
    # le meme noeud Document.
    langue = ""

    # Le decoupage en lots vit dans matter.page_batches et non ici : le contrat
    # « pas de chevauchement » etait realise par un « + 1 » que rien ne gardait.
    # Le motif d'origine ajoutait « et ce module n'est pas importable sans
    # docling, donc pas testable » : ce n'est plus vrai, l'import de `docling`
    # etant descendu dans `get_converter`. Le decoupage reste ici parce que
    # `matter.py` porte deja `kept_ranges`, dont il est la suite.
    lots = matter.page_batches(ranges, settings.pdf_batch_pages)

    # Le PDF est ouvert UNE fois pour tous les crops du document.
    with fitz.open(pdf_path) as document:
        for start_page, end_page in lots:
            logger.info("[%s] batch %d-%d/%d", stem, start_page, end_page, total_pages)

            try:
                batch_elements, batch_document, comptes = _convert_batch(
                    converter,
                    pdf_path,
                    stem,
                    document,
                    accumulator,
                    start_page,
                    end_page,
                    body_size,
                    size_ranks,
                )
            except Exception as exc:
                # Une page illisible ne doit pas condamner les 399 autres : on
                # note l'echec, on continue, et le job echouera a la fin avec
                # la liste des pages manquantes. Jamais un run vert sur un trou.
                logger.exception("[%s] batch %d-%d en echec", stem, start_page, end_page)
                failed_batches.append(f"{start_page}-{end_page} ({type(exc).__name__}: {exc})")
            else:
                titres_total += comptes[0]
                titres_replis += comptes[1]
                # Les plages de pages sont retenues lot par lot : les elements
                # eux-memes ne survivent pas au lot (ils sont persistes puis
                # jetes), et le compteur de pages perdues a besoin de la
                # couverture du DOCUMENT entier.
                pages_couvertes.extend(
                    {"page_no": e["page_no"], "page_no_end": e.get("page_no_end")}
                    for e in batch_elements
                )
                if not langue:
                    langue = _detect_document_language(batch_elements, stem)
                facts = DocumentFacts(
                    type_file="pdf",
                    total_pages=total_pages,
                    language=langue,
                    content_hash=content_hash,
                )
                # L'ecriture, elle, est bloquante : si un store refuse le lot,
                # continuer n'aurait aucun sens.
                total_chunks += storage.persist(batch_elements, identity, facts, batch_document)

            report(
                pages_done=end_page,
                elements=accumulator.count,
                chunks=total_chunks,
                language=langue,
                failed_batches=list(failed_batches),
            )

    if failed_batches:
        # REGISTRE 4.1 : le document PARTIEL est retire avant qu'on ne leve.
        # Sans ce retrait, la partition Dagster est rouge ET l'ouvrage est dans
        # l'index, tronque, sans que rien ne l'en sorte — le pire des deux
        # etats, parce qu'il ressemble a des stores vides. `verify_contract` ne
        # peut pas le voir : les `element_id` ecrits sont parfaitement valides,
        # ce sont les pages manquantes qui ne laissent aucune trace.
        #
        # L'invariant devient : un document est entierement dans les stores, ou
        # pas du tout.
        logger.error(
            "[%s] %d lot(s) sur %d en echec : le document partiel est retire des "
            "stores. %d elements et %d chunks deja ecrits sont annules",
            stem,
            len(failed_batches),
            len(lots),
            accumulator.count,
            total_chunks,
        )
        try:
            storage.forget_document(identity)
        except Exception as exc:
            # LARGEUR VOULUE : l'echec d'extraction est la cause premiere et
            # doit rester la cause levee. Un `raise` depuis ce bloc masquerait
            # les pages manquantes derriere une panne de store. On chaine.
            raise BatchExtractionError(
                f"{len(failed_batches)} batch(s) non convertis pour {stem} : "
                f"{'; '.join(failed_batches)}. ET LE DOCUMENT PARTIEL N'A PAS PU "
                f"ETRE RETIRE ({exc}) : l'index porte un ouvrage tronque"
            ) from exc
        raise BatchExtractionError(
            f"{len(failed_batches)} batch(s) non convertis pour {stem} : "
            f"{'; '.join(failed_batches)}. Le document partiel a ete retire des "
            f"stores : l'index ne porte pas d'ouvrage tronque"
        )

    # LE COMPTEUR DU REGISTRE 4.22, et il ne pouvait pas exister avant
    # `page_no_end`. Une page enjambee n'est PAS une page perdue : elle est
    # couverte par un element qui commence avant elle. Ce qui reste apres ce
    # changement est la vraie perte — une page que personne ne couvre — et c'est
    # elle qu'il faut crier. Sur le corpus, les six pages qui paraissaient vides
    # (8, 18, 19, 25, 68, 69) sont enjambees, donc ce compteur doit se taire
    # dessus : il ne parle que d'une perte reelle.
    perdues = pages_sans_element(pages_couvertes, total_pages, skipped)
    if perdues:
        logger.warning(
            "[%s] %d page(s) sur %d n'ont AUCUN element et ne sont couvertes par "
            "aucun element voisin : %s. Leur texte n'est ni indexe ni citable, et "
            "aucun code de sortie ne le dit",
            stem,
            len(perdues),
            total_pages,
            ", ".join(str(page) for page in perdues),
        )

    enjambees = sum(
        1
        for e in pages_couvertes
        if e.get("page_no_end") and int(e["page_no_end"]) > int(e["page_no"])
    )
    if enjambees:
        logger.info(
            "[%s] %d element(s) enjambent une frontiere de page : leur `page_no` "
            "est la page d'entree, `page_no_end` la page de sortie. Une citation "
            "« page N » sur ces elements couvre en realite deux pages",
            stem,
            enjambees,
        )

    if titres_replis:
        logger.warning(
            "[%s] %d titres sur %d (%.0f %%) ont recu le rang de REPLI et non un "
            "rang mesure : le document ne classe que %d niveau(x) de titre. Leur "
            "profondeur dans le graphe est un empilement par defaut",
            stem,
            titres_replis,
            titres_total,
            100 * titres_replis / titres_total if titres_total else 0,
            len(size_ranks),
        )

    logger.info("[%s] termine : %d elements, %d chunks", stem, accumulator.count, total_chunks)
    return {
        "elements": accumulator.count,
        "chunks": total_chunks,
        "pages": total_pages,
        "pages_skipped": len(skipped),
        "ocr": besoin_ocr,
        "language": langue,
        "type_file": "pdf",
        "headings": titres_total,
        "headings_fallback": titres_replis,
        "pages_without_element": len(perdues),
        "pages_spanned": enjambees,
    }


def _front_back_matter_pages(document: Any, total_pages: int, stem: str) -> set[int]:
    """Pages a ne pas convertir : couverture, copyright, sommaire, index.

    Deux sources, dans cet ordre :

    1. **Les signets du PDF**, qui donnent le titre de chaque partie et sa page
       physique. Ces pages sont resolues par le format lui-meme, elles ne
       souffrent pas du decalage entre numerotation imprimee et rang reel.
    2. **La forme des dernieres pages**, quand aucun signet ne designe l'index.
       Beaucoup de PDF ont des signets partiels, ou pas de signets du tout.

    Args:
        document: Document PyMuPDF ouvert.
        total_pages: Nombre de pages.
        stem: Nom du document, pour les logs.

    Returns:
        Les numeros de page (1-indexes) a sauter.
    """
    try:
        toc: list[tuple[int, str, int]] = [
            (int(level), str(title), int(page)) for level, title, page in document.get_toc()
        ]
    except Exception:
        logger.warning("[%s] signets illisibles, detection par la forme seule", stem)
        toc = []

    skipped = matter.pages_to_skip(toc, total_pages)
    if skipped:
        logger.info("[%s] signets : %d pages hors contenu ecartees", stem, len(skipped))

    # L'index est la partie la plus nuisible : s'il n'a pas ete trouve par les
    # signets, on le cherche a sa forme dans la queue du document.
    if not any(page > total_pages * 0.6 for page in skipped):
        debut = total_pages - max(1, int(total_pages * matter.INDEX_SEARCH_TAIL_RATIO))
        textes = {page: document[page - 1].get_text() for page in range(debut + 1, total_pages + 1)}
        par_la_forme = matter.detect_index_pages(textes, total_pages)
        if par_la_forme:
            logger.info("[%s] index reconnu a sa forme : %d pages", stem, len(par_la_forme))
        skipped |= par_la_forme

    return skipped


def _has_text_layer(document: Any, ranges: list[tuple[int, int]]) -> bool:
    """Indique si le PDF porte du texte selectionnable.

    Un livre scanne n'en a pas : Docling le convertirait sans broncher et
    produirait un document quasi vide, le run passerait au vert sur un trou.
    C'est le pire resultat possible sur une bibliotheque de deux cents
    ouvrages, d'ou ce controle — qui aiguille vers l'OCR plutot que de laisser
    passer.

    Le sondage porte sur des pages reparties dans tout le document et coute
    quelques millisecondes.

    Args:
        document: Document PyMuPDF ouvert.
        ranges: Plages de pages a convertir.

    Returns:
        ``True`` si le document est lisible sans reconnaissance de caracteres.
    """
    pages = matter.sample_pages(ranges)
    if not pages:
        return True
    return matter.has_text_layer([document[page - 1].get_text() for page in pages])


def _pdf_font_profile(
    document: Any, ranges: list[tuple[int, int]]
) -> tuple[float, dict[float, int]]:
    """Releve la taille du corps du texte et classe les tailles de titre.

    Le releve est fait une fois pour tout le document, avec PyMuPDF : c'est
    une lecture, sans modele, de l'ordre de la seconde sur un ouvrage entier.

    La taille dominante — celle qui porte le plus de caracteres — est le corps
    du texte. Les tailles superieures sont celles des titres, et leur rang
    donne le niveau. Un document compose d'une seule taille rend un classement
    vide, et tous ses titres resteront freres.

    Args:
        document: Document PyMuPDF ouvert.
        ranges: Plages de pages conservees.

    Returns:
        La taille du corps du texte, et le rang de chaque taille de titre.
    """
    caracteres: dict[float, int] = {}
    for debut, fin in ranges:
        for numero in range(debut, fin + 1):
            for bloc in document[numero - 1].get_text("dict")["blocks"]:
                for ligne in bloc.get("lines", []):
                    for span in ligne["spans"]:
                        taille = round(float(span["size"]), 1)
                        caracteres[taille] = caracteres.get(taille, 0) + len(span["text"])

    if not caracteres:
        return 0.0, {}

    body_size = max(caracteres.items(), key=lambda paire: paire[1])[0]
    titres = [taille for taille in caracteres if taille > body_size]
    return body_size, ranking.font_size_ranks(titres)


def _heading_size(page: Any, bbox: dict[str, float], page_height: float) -> float:
    """Plus grande taille de police rencontree dans la boite d'un titre.

    Docling exprime les coordonnees avec l'origine en bas de page, PyMuPDF avec
    l'origine en haut : la boite est retournee avant comparaison.

    Args:
        page: Page PyMuPDF.
        bbox: Boite de l'element, au format Docling.
        page_height: Hauteur de la page.

    Returns:
        La taille en points, ou 0 si rien n'est trouve.
    """
    import fitz

    zone = fitz.Rect(bbox["l"], page_height - bbox["t"], bbox["r"], page_height - bbox["b"])
    plus_grande = 0.0
    for bloc in page.get_text("dict")["blocks"]:
        for ligne in bloc.get("lines", []):
            for span in ligne["spans"]:
                if fitz.Rect(span["bbox"]).intersects(zone):
                    plus_grande = max(plus_grande, float(span["size"]))
    return plus_grande


def _figure_boxes(
    elements: list[dict[str, Any]], page_no: int
) -> list[tuple[float, float, float, float]]:
    """Boites des images et tableaux deja rencontres sur une page."""
    boites: list[tuple[float, float, float, float]] = []
    for element in elements:
        bbox = element["bbox"]
        if element["label"] in VISUAL_LABELS and bbox and element["page_no"] == page_no:
            boites.append((bbox["l"], bbox["b"], bbox["r"], bbox["t"]))
    return boites


def _pdf_heading_rank(
    item: Any,
    document: Any,
    elements: list[dict[str, Any]],
    body_size: float,
    size_ranks: dict[float, int],
) -> int | None:
    """Mesure ce qu'il faut au classement d'un titre, puis delegue la decision.

    La mesure vit ici parce qu'elle demande PyMuPDF et le document ouvert ; la
    decision vit dans :func:`src.docling_service.ranking.pdf_heading_rank`, qui
    n'a besoin d'aucun des deux et se verifie donc seule.

    Args:
        item: Item Docling.
        document: Document PyMuPDF ouvert.
        elements: Elements deja produits pour ce lot, pour situer les figures.
        body_size: Taille dominante du corps du texte.
        size_ranks: Rang de chaque taille de titre du document.

    Returns:
        Le rang du titre, ou ``None``.
    """
    label = str(getattr(item, "label", ""))
    prov = item_provenance(item)
    bbox = extract_bbox(prov.bbox if prov else None)

    taille = 0.0
    boites: list[tuple[float, float, float, float]] = []
    if prov and bbox:
        page = document[int(prov.page_no) - 1]
        taille = round(_heading_size(page, bbox, page.rect.height), 1)
        boites = _figure_boxes(elements, int(prov.page_no))

    return ranking.pdf_heading_rank(label, bbox, taille, body_size, size_ranks, boites)


def _convert_batch(
    converter: DocumentConverter,
    pdf_path: str,
    stem: str,
    document: Any,
    accumulator: DocumentAccumulator,
    start_page: int,
    end_page: int,
    body_size: float = 0.0,
    size_ranks: dict[float, int] | None = None,
) -> tuple[list[dict[str, Any]], Any, tuple[int, int]]:
    """Convertit une plage de pages et construit ses elements.

    Returns:
        Les elements du lot, le document Docling converti — necessaire au
        decoupeur, qui travaille sur la structure — et le couple
        ``(titres, titres tombes au rang de repli)`` de ce lot.
    """
    result = converter.convert(pdf_path, page_range=(start_page, end_page))
    elements: list[dict[str, Any]] = []
    rangs = size_ranks or {}
    repli = ranking.fallback_rank(rangs)
    titres = inclassables = 0

    for item, _ in result.document.iterate_items():
        rang = _pdf_heading_rank(item, document, elements, body_size, rangs)
        if rang is not None:
            titres += 1
            # Le compteur lit le repli a la MEME source que la decision : le
            # recalculer ici compterait autre chose que ce qui est attribue.
            inclassables += int(rang == repli)
        element = accumulator.add_item(item, result.document, heading_rank=rang)
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

    return elements, result.document, (titres, inclassables)
