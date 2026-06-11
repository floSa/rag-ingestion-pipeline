"""Configuration centralisee du pipeline Dagster via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineSettings(BaseSettings):
    """Variables d'environnement du pipeline d'ingestion.

    Les credentials des stores (MinIO, Nebula, Chroma) vivent dans
    ``src.docling_service.settings`` : seul le service Docling y ecrit.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    source_dir: str = "/opt/dagster/app/Datas"
    cleaned_subdir: str = ".cleaned"
    docling_service_url: str = "http://docling-service:8000"


@lru_cache(maxsize=1)
def get_settings() -> PipelineSettings:
    """Retourne l'instance unique des settings (cached)."""
    return PipelineSettings()
