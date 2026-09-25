"""Crop des elements visuels d'un PDF et export vers le stockage objet.

Le document PDF est passe en argument au lieu d'etre rouvert pour chaque
image : un livre de 400 pages en compte des centaines.

Ce module est le seul de `src/` qui construit un client S3
(:func:`build_client`) et qui importe la bibliotheque cliente.
`pipeline/media.py` passe par lui, ce qui laisse une seule decision de
reglages et de ``secure=``.

La bibliotheque cliente s'appelle ``minio`` (minio-py). C'est un client S3
generique, qui parle a n'importe quelle passerelle S3 : son nom ne dit rien du
serveur en face (SeaweedFS aujourd'hui), que seul ``S3_ENDPOINT`` designe.
"""

from __future__ import annotations

import io
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from minio import Minio

from src.docling_service.settings import get_settings
from src.reglages_s3 import MESSAGE_ENDPOINT_MANQUANT

logger = logging.getLogger(__name__)

# Le type du client S3, sous un nom qui ne designe pas un produit. Il existe
# pour que `pipeline/media.py` puisse annoter le sien sans importer le SDK :
# le depot n'a qu'un site qui nomme la bibliotheque, et c'est ce module-ci.
ClientS3 = Minio

_client: ClientS3 | None = None
_client_lock = threading.Lock()


def build_client(
    endpoint: str, access_key: str, secret_key: str, *, secure: bool = False
) -> ClientS3:
    """Construit un client S3. C'est le seul site de construction du depot.

    L'adresse vide est refusee ici en plus des reglages : la fonction est
    publique, et un appelant qui lui passerait une adresse lue ailleurs
    contournerait la validation de `ReglagesDuStockageObjet`. Un client sans
    adresse ne leve pas a la construction, mais a l'appel, sur une erreur de
    resolution de nom qui ne dit pas ce qui manque.

    Args:
        endpoint: ``hote:port`` de la passerelle S3, sans schema.
        access_key: Cle d'acces.
        secret_key: Cle secrete.
        secure: TLS. Faux : la passerelle est jointe sur le reseau Docker
            interne, ou elle n'est pas exposee, et aucun certificat n'y est
            emis. Un deploiement qui la sortirait de ce reseau doit passer
            vrai, et c'est le seul site ou la question se pose.

    Returns:
        Un client S3 pret a l'emploi.

    Raises:
        ValueError: Si l'adresse est vide.
    """
    if not endpoint.strip():
        raise ValueError(MESSAGE_ENDPOINT_MANQUANT)
    return Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)


def get_client() -> ClientS3:
    """Retourne le client S3 partage du service d'extraction, cree au premier appel."""
    global _client
    with _client_lock:
        if _client is None:
            settings = get_settings()
            _client = build_client(
                settings.s3_endpoint,
                settings.s3_access_key,
                settings.s3_secret_key,
            )
        return _client


def ensure_bucket(max_attempts: int = 15, wait_seconds: float = 5.0) -> bool:
    """S'assure que le bucket existe, avec retry au demarrage.

    Returns:
        True si le bucket est pret.
    """
    settings = get_settings()
    for attempt in range(1, max_attempts + 1):
        try:
            client = get_client()
            if not client.bucket_exists(settings.s3_bucket):
                client.make_bucket(settings.s3_bucket)
                logger.info("Bucket '%s' cree sur %s.", settings.s3_bucket, settings.s3_endpoint)
            else:
                logger.info("Bucket '%s' pret sur %s.", settings.s3_bucket, settings.s3_endpoint)
            return True
        except Exception as exc:
            logger.warning(
                "Stockage objet %s indisponible (%s), tentative %d/%d",
                settings.s3_endpoint,
                exc,
                attempt,
                max_attempts,
            )
            if attempt < max_attempts:
                time.sleep(wait_seconds)
    logger.error(
        "Stockage objet %s injoignable apres %d tentatives.", settings.s3_endpoint, max_attempts
    )
    return False


def object_url(object_name: str) -> str:
    """Adresse d'un objet du bucket : interne et authentifiee, pas publique.

    Ce n'est pas une URL qu'un navigateur peut ouvrir (registre 4.25). Mesure
    le 1er septembre 2026 :

    - les 13 URL portees par le graphe designent des objets qui existent
      (`stat_object` avec un client S3 authentifie : 0 URL morte sur 13) ;
    - un `GET` anonyme rend 403 AccessDenied, y compris depuis un conteneur
      de `rag_network` : le bucket n'est pas public ;
    - l'hote est un nom de service Docker, qui ne resout pas hors du reseau.

    `rag-agent-chat` sert de proxy : il est sur `rag_network`, porte
    `RESTRICT_MEDIA_TO_GRAPH=true` (il ne sert que ce que le graphe
    reference), lit l'objet avec ses propres identifiants S3 en lecture seule
    et le re-sert a son client. Il ne doit jamais passer cette adresse telle
    quelle a un navigateur.

    Deux alternatives sont ecartees :

    - rendre le bucket public en lecture ne servirait que dans le reseau, et
      rendrait chaque image lisible par tout ce qui y tourne, sans gain pour
      l'agent qui a deja ses identifiants ;
    - stocker une URL presignee la ferait expirer : un graphe est durable, une
      signature ne l'est pas, et les images cesseraient de s'afficher sans
      erreur.

    L'hote reste un reglage (`S3_ENDPOINT`) : un deploiement qui expose la
    passerelle sous un autre nom stocke une adresse atteignable, sans changer
    de code. Cette fonction est le seul site de cette forme ; `pipeline/media.py`
    l'appelle.

    Args:
        object_name: Cle de l'objet dans le bucket.

    Returns:
        L'adresse interne de l'objet, en style chemin
        (``http://hote:port/bucket/cle``). La forme est celle que
        :func:`object_key` sait inverser exactement.
    """
    settings = get_settings()
    return f"http://{settings.s3_endpoint}/{settings.s3_bucket}/{object_name}"


def object_key(url: str) -> str:
    """La cle nue d'un objet, retrouvee depuis l'adresse que le contrat publie.

    Le contrat publie la cle a cote de l'adresse (`media_url` + `object_key`)
    parce que l'adresse contient l'hote et perime avec lui : le changement de
    stockage du 25 septembre 2026 a change l'hote de 212 objets. La cle, elle,
    ne change pas d'un stockage a l'autre : c'est l'identite de l'objet. La
    publier evite a chaque consommateur de defaire l'adresse a sa facon.

    C'est l'inverse exact de :func:`object_url`, qui concatene sans encoder.
    Retrouver la cle, c'est retirer le schema, l'hote et le premier segment (le
    bucket), sans rien decoder. Un ``unquote`` rendrait une cle differente de
    celle passee a ``put_object`` des qu'un nom porterait un ``%``, et
    `stat_object` rendrait alors 404. `sanitize_key` borne aujourd'hui les cles
    a ``[A-Za-z0-9/_.-]``, donc le cas ne se presente pas encore.

    Args:
        url: Adresse produite par :func:`object_url`, ou chaine vide.

    Returns:
        La cle, exactement telle qu'elle a ete passee a ``put_object``. Chaine
        vide si l'adresse est vide ou ne porte pas de cle sous un bucket : une
        absence se lit comme une absence, et non comme une cle fausse.
    """
    if not url:
        return ""
    chemin = urlsplit(url).path.lstrip("/")
    _bucket, separateur, cle = chemin.partition("/")
    return cle if separateur else ""


_CONTENT_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
}


def sanitize_key(value: str) -> str:
    """Rend un chemin utilisable comme prefixe d'objet dans le bucket."""
    return re.sub(r"[^A-Za-z0-9/_.-]+", "_", value).strip("/_")


def upload_file(source: Path, doc_key: str, index: int) -> str | None:
    """Envoie un fichier image du disque vers le stockage objet.

    Sert aux images des documents Markdown, qui vivent a cote de la note au
    lieu d'etre embarquees. Les images des PDF passent par ``crop_and_upload``,
    celles des captures HTML par le nettoyage en amont.

    Args:
        source: Chemin du fichier image sur le disque.
        doc_key: Prefixe identifiant le document dans le bucket.
        index: Rang de l'image dans le document.

    Returns:
        L'URL de l'image, ou None si la lecture ou l'envoi echoue.
    """
    try:
        payload = source.read_bytes()
    except OSError as exc:
        logger.warning("Image illisible (%s) : %s", source, exc)
        return None

    extension = source.suffix.lower()
    object_name = f"images/md/{sanitize_key(doc_key)}/{index:04d}_{sanitize_key(source.name)}"
    try:
        get_client().put_object(
            get_settings().s3_bucket,
            object_name,
            io.BytesIO(payload),
            length=len(payload),
            content_type=_CONTENT_TYPES.get(extension, "application/octet-stream"),
        )
    except Exception as exc:
        logger.warning("Upload vers le stockage objet echoue (%s) : %s", object_name, exc)
        return None

    return object_url(object_name)


def crop_and_upload(
    doc: Any,
    pdf_stem: str,
    page_no: int,
    bbox: dict[str, float],
    image_id: str,
    element_type: str,
) -> str | None:
    """Extrait la zone d'une page PDF en PNG et l'envoie sur le stockage objet.

    Args:
        doc: Document PyMuPDF deja ouvert.
        pdf_stem: Nom du PDF sans extension, utilise comme prefixe d'objet.
        page_no: Numero de page (1-indexe).
        bbox: Zone au format Docling (``l``, ``t``, ``r``, ``b``).
        image_id: Identifiant de l'element, utilise dans le nom de l'objet.
        element_type: Label de l'element (``picture``, ``table``...).

    Returns:
        L'URL de l'image, ou None si la zone est vide ou l'upload echoue.
    """
    import fitz  # import local : PyMuPDF n'est present que dans l'image d'extraction

    if not bbox or not all(key in bbox for key in ("l", "t", "r", "b")):
        return None

    try:
        page = doc[page_no - 1]

        # Docling raisonne en origine BOTTOMLEFT (t > b), PyMuPDF en TOPLEFT.
        top, bottom = bbox["t"], bbox["b"]
        if top > bottom:
            page_height = page.rect.height
            top, bottom = page_height - top, page_height - bottom

        rect = fitz.Rect(bbox["l"], min(top, bottom), bbox["r"], max(top, bottom)) & page.rect
        if rect.is_empty or rect.width < 1 or rect.height < 1:
            return None

        zoom = get_settings().image_crop_zoom
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect)
        payload: bytes = pixmap.tobytes("png")
    except Exception as exc:
        logger.warning("Crop impossible (%s p.%d) : %s", image_id, page_no, exc)
        return None

    object_name = f"images/{pdf_stem}/{image_id}_{element_type}.png"
    try:
        get_client().put_object(
            get_settings().s3_bucket,
            object_name,
            io.BytesIO(payload),
            length=len(payload),
            content_type="image/png",
        )
    except Exception as exc:
        logger.warning("Upload vers le stockage objet echoue (%s) : %s", object_name, exc)
        return None

    return object_url(object_name)
