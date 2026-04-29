"""Ressource Dagster pour le client MinIO."""

from __future__ import annotations

import os

from dagster import ConfigurableResource
from minio import Minio
from pydantic import Field


class MinIOResource(ConfigurableResource):  # type: ignore[misc]
    """Client MinIO configurable via variables d'environnement."""

    endpoint: str = Field(
        default_factory=lambda: os.getenv("MINIO_ENDPOINT", "minio:9000"),
        description="MinIO endpoint",
    )
    access_key: str = Field(
        default_factory=lambda: os.getenv("MINIO_ROOT_USER", ""),
        description="MinIO Access Key",
    )
    secret_key: str = Field(
        default_factory=lambda: os.getenv("MINIO_ROOT_PASSWORD", ""),
        description="MinIO Secret Key",
    )
    secure: bool = Field(default=False, description="Use HTTPS")
    bucket_name: str = Field(
        default_factory=lambda: os.getenv("MINIO_BUCKET", "documents"),
        description="Bucket pour les médias",
    )

    def get_client(self) -> Minio:
        """Crée et retourne un client MinIO."""
        return Minio(
            endpoint=self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
        )

    def init_bucket(self) -> None:
        """Crée le bucket s'il n'existe pas."""
        client = self.get_client()
        if not client.bucket_exists(self.bucket_name):
            client.make_bucket(self.bucket_name)
