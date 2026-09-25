"""Export vers le stockage objet des images base64 embarquees dans les captures HTML.

Les captures SingleFile inlinent les images en URI ``data:``. Plutot que de les
supprimer au nettoyage, les images de contenu sont televersees et le ``src`` est
reecrit avec leur adresse — meme convention que les crops PDF du service
Docling.
"""

from __future__ import annotations

import io
import re

from src.docling_service.images import ClientS3, build_client, object_url
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
    """Rend un chemin utilisable comme prefixe d'objet (et d'adresse)."""
    return re.sub(r"[^A-Za-z0-9/_-]+", "_", value).strip("/_")


class ExportateurDImages:
    """Televerse les images d'un document et retourne leur adresse.

    Compatible avec le protocole ``ImageExporter`` de ``cleaning`` :
    l'instance est appelable avec (payload, mime, index).

    **CETTE CLASSE NE CONSTRUIT PLUS SON CLIENT ELLE-MEME.** Elle en batissait
    un second, avec son propre ``secure=`` et son propre appel au SDK : deux
    sites pour la meme decision. `images.build_client` est desormais le seul
    site de construction du depot, et c'est aussi ce qui garde ce module libre
    de toute mention du SDK — son type arrive par `ClientS3`.
    """

    def __init__(self, doc_key: str) -> None:
        self.doc_key = _sanitize_key(doc_key)
        self.exported = 0
        self._client: ClientS3 | None = None

    def _get_client(self) -> ClientS3:
        if self._client is None:
            settings = get_settings()
            self._client = build_client(
                settings.s3_endpoint,
                settings.s3_access_key,
                settings.s3_secret_key,
            )
        return self._client

    def __call__(self, payload: bytes, mime: str, index: int) -> str | None:
        """Televerse une image ; retourne son adresse, ou None si l'envoi echoue."""
        settings = get_settings()
        extension = _MIME_EXTENSIONS.get(mime.lower(), "bin")
        object_name = f"images/html/{self.doc_key}/img_{index:04d}.{extension}"
        try:
            self._get_client().put_object(
                settings.s3_bucket,
                object_name,
                io.BytesIO(payload),
                length=len(payload),
                content_type=mime,
            )
        except Exception as exc:
            print(f"Export d'image vers le stockage objet echoue ({object_name}): {exc}")
            return None
        self.exported += 1
        # La forme de l'adresse a UN seul site, `images.object_url`, qui dit
        # aussi ce qu'elle est : interne, authentifiee, et destinee a l'agent
        # comme proxy (registre 4.25). Elle etait reconstruite ici a l'identique
        # par une seconde f-string — deux sites pour la forme que le contrat
        # publie, donc deux facons de deriver.
        return object_url(object_name)
