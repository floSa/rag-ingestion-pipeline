"""Ressources Dagster partagées."""

from __future__ import annotations

import os
from typing import Any

from dagster import ConfigurableResource


class EmbeddingsResource(ConfigurableResource):  # type: ignore[misc]
    """Ressource Dagster pour charger le modèle d'embeddings une seule fois."""

    model_name: str = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")

    def get_model(self) -> Any:
        """Charge et retourne le modèle SentenceTransformer."""
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(self.model_name)
