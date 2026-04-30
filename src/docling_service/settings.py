"""Configuration centralisee du microservice Docling via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class DoclingSettings(BaseSettings):  # type: ignore[misc]
    """Variables d'environnement du service d'extraction Docling."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    minio_endpoint: str = "minio:9000"
    minio_root_user: str = ""
    minio_root_password: str = ""
    minio_bucket: str = "documents"

    nebula_host: str = "graphd"
    nebula_port: int = 9669

    chroma_host: str = "chromadb"
    chroma_port: int = 8000

    embedding_model_name: str = "all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def get_settings() -> DoclingSettings:
    """Retourne l'instance unique des settings (cached)."""
    return DoclingSettings()
