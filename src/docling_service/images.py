"""Crop des elements visuels d'un PDF et export vers MinIO.

Le document PDF est passe en argument au lieu d'etre rouvert : la version
initiale faisait un ``fitz.open()`` du fichier entier pour chaque image, soit
des centaines d'ouvertures d'un livre de 400 pages.
"""

from __future__ import annotations

import io
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from minio import Minio

from src.docling_service.settings import get_settings

logger = logging.getLogger(__name__)

_client: Minio | None = None
_client_lock = threading.Lock()


def get_client() -> Minio:
    """Retourne le client MinIO partage, cree au premier appel."""
    global _client
    with _client_lock:
        if _client is None:
            settings = get_settings()
            _client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_root_user,
                secret_key=settings.minio_root_password,
                secure=False,
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
            if not client.bucket_exists(settings.minio_bucket):
                client.make_bucket(settings.minio_bucket)
                logger.info("Bucket MinIO '%s' cree.", settings.minio_bucket)
            else:
                logger.info("Bucket MinIO '%s' pret.", settings.minio_bucket)
            return True
        except Exception as exc:
            logger.warning("MinIO indisponible (%s), tentative %d/%d", exc, attempt, max_attempts)
            if attempt < max_attempts:
                time.sleep(wait_seconds)
    logger.error("MinIO injoignable apres %d tentatives.", max_attempts)
    return False


def object_url(object_name: str) -> str:
    """Adresse d'un objet du bucket — INTERNE et AUTHENTIFIEE, pas publique.

    **La forme stockee n'est pas une URL qu'un navigateur peut ouvrir, et le
    registre 4.25 demandait de trancher ce qu'elle est.** Trois faits mesures le
    1er septembre 2026 :

    - les 13 URL portees par le graphe designent des objets qui EXISTENT
      (`stat_object` avec un client S3 authentifie : 0 URL morte sur 13) ;
    - un `GET` **anonyme** rend **403 AccessDenied**, et pas seulement hors du
      reseau Docker : depuis un conteneur DANS `rag_network` aussi. Ce n'est donc
      pas un probleme de resolution de nom, c'est le bucket qui n'est pas public ;
    - `minio_endpoint` vaut `minio:9000` par defaut, un nom de service Docker :
      hors du reseau, il ne resout pas.

    « 0 URL morte » dependait donc entierement de la methode de lecture, et
    c'etait le vrai defaut du constat : une mesure juste, presentee sans sa
    condition.

    **CE QUE L'AGENT PEUT EN FAIRE, et c'est la decision.** `rag-agent-chat` se
    raccroche a `rag_network` et porte `RESTRICT_MEDIA_TO_GRAPH=true` : il ne
    sert que ce que le graphe reference. Il est donc le PROXY, et cette adresse
    est faite pour lui : il resout `minio:9000`, lit l'objet avec ses
    identifiants S3, et le re-sert a son client. Il ne doit jamais passer cette
    adresse telle quelle a un navigateur.

    **Les deux autres issues ont ete ECARTEES, et pour des motifs mesurables :**

    - *rendre le bucket public en lecture* ferait passer le `GET` anonyme, mais
      seulement dans le reseau, et rendrait chaque image du corpus lisible par
      tout ce qui y tourne. Le gain est nul pour l'agent, qui a deja ses
      identifiants ;
    - *stocker une URL presignee* la ferait EXPIRER. Un graphe est durable ; une
      signature ne l'est pas. Le jour de l'expiration, les images cesseraient de
      s'afficher sans qu'aucune erreur ne le dise — c'est-a-dire exactement la
      famille de defaut que ce lot ferme, plantee volontairement.

    L'hote reste un reglage (`MINIO_ENDPOINT`) : un deploiement qui expose MinIO
    sous un autre nom stocke une adresse atteignable de la, sans changer de code.

    **C'est aussi le SEUL site de cette forme.** `pipeline/media.py` la
    reconstruisait a l'identique par une seconde f-string : deux sites pour la
    forme que le contrat publie, donc deux facons de deriver.

    Args:
        object_name: Cle de l'objet dans le bucket.

    Returns:
        L'adresse interne de l'objet.
    """
    settings = get_settings()
    return f"http://{settings.minio_endpoint}/{settings.minio_bucket}/{object_name}"


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
    """Rend un chemin utilisable comme prefixe d'objet MinIO."""
    return re.sub(r"[^A-Za-z0-9/_.-]+", "_", value).strip("/_")


def upload_file(source: Path, doc_key: str, index: int) -> str | None:
    """Envoie un fichier image du disque vers MinIO.

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
            get_settings().minio_bucket,
            object_name,
            io.BytesIO(payload),
            length=len(payload),
            content_type=_CONTENT_TYPES.get(extension, "application/octet-stream"),
        )
    except Exception as exc:
        logger.warning("Upload MinIO echoue (%s) : %s", object_name, exc)
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
    """Extrait la zone d'une page PDF en PNG et l'envoie sur MinIO.

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
            get_settings().minio_bucket,
            object_name,
            io.BytesIO(payload),
            length=len(payload),
            content_type="image/png",
        )
    except Exception as exc:
        logger.warning("Upload MinIO echoue (%s) : %s", object_name, exc)
        return None

    return object_url(object_name)
