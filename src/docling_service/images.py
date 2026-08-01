"""Crop des elements visuels d'un PDF et export vers MinIO.

Le document PDF est passe en argument au lieu d'etre rouvert : la version
initiale faisait un ``fitz.open()`` du fichier entier pour chaque image, soit
des centaines d'ouvertures d'un livre de 400 pages.
"""

from __future__ import annotations

import io
import logging
import threading
import time
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
    """URL de lecture d'un objet du bucket."""
    settings = get_settings()
    return f"http://{settings.minio_endpoint}/{settings.minio_bucket}/{object_name}"


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
