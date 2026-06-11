"""Export vers MinIO des images base64 embarquees dans les captures HTML.

Les captures SingleFile inlinent les images en URI ``data:``. Plutot que de
les supprimer au nettoyage, les images de contenu sont uploadees sur MinIO et
le ``src`` est reecrit avec leur URL — meme convention que les crops PDF du
service Docling.
"""

from __future__ import annotations

import io
import re

from minio import Minio

from src.pipeline.settings import get_settings

_MIME_EXTENSIONS: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/avif": "avif",
}


def _sanitize_key(value: str) -> str:
    """Rend un chemin utilisable comme prefixe d'objet MinIO (et d'URL)."""
    return re.sub(r"[^A-Za-z0-9/_-]+", "_", value).strip("/_")


class MinioImageExporter:
    """Upload les images d'un document et retourne leur URL publique.

    Compatible avec le protocole ``ImageExporter`` de ``cleaning`` :
    l'instance est appelable avec (payload, mime, index).
    """

    def __init__(self, doc_key: str) -> None:
        self.doc_key = _sanitize_key(doc_key)
        self.exported = 0
        self._client: Minio | None = None

    def _get_client(self) -> Minio:
        if self._client is None:
            settings = get_settings()
            self._client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_root_user,
                secret_key=settings.minio_root_password,
                secure=False,
            )
        return self._client

    def __call__(self, payload: bytes, mime: str, index: int) -> str | None:
        """Upload une image ; retourne son URL, ou None si l'upload echoue."""
        settings = get_settings()
        extension = _MIME_EXTENSIONS.get(mime.lower(), "bin")
        object_name = f"images/html/{self.doc_key}/img_{index:04d}.{extension}"
        try:
            self._get_client().put_object(
                settings.minio_bucket,
                object_name,
                io.BytesIO(payload),
                length=len(payload),
                content_type=mime,
            )
        except Exception as exc:
            print(f"MinIO image export failed ({object_name}): {exc}")
            return None
        self.exported += 1
        return f"http://{settings.minio_endpoint}/{settings.minio_bucket}/{object_name}"
