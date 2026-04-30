"""Ressources Dagster partagées."""

from __future__ import annotations

from typing import Any

from dagster import ConfigurableResource
from pydantic import Field

from src.pipeline.settings import get_settings
from .minio_resource import MinIOResource


class EmbeddingsResource(ConfigurableResource):  # type: ignore[misc]
    """Ressource Dagster pour charger le modele d'embeddings une seule fois."""

    model_name: str = Field(
        default_factory=lambda: get_settings().embedding_model_name,
    )

    def get_model(self) -> Any:
        """Charge et retourne le modele SentenceTransformer."""
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(self.model_name)
