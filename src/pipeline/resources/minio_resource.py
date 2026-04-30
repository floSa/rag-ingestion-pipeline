"""Ressource Dagster pour le client MinIO."""

from __future__ import annotations

from dagster import ConfigurableResource
from minio import Minio
from pydantic import Field

from src.pipeline.settings import get_settings


class MinIOResource(ConfigurableResource):  # type: ignore[misc]
    """Client MinIO configurable via pydantic-settings."""

    endpoint: str = Field(
        default_factory=lambda: get_settings().minio_endpoint,
        description="MinIO endpoint",
    )
    access_key: str = Field(
        default_factory=lambda: get_settings().minio_root_user,
        description="MinIO Access Key",
    )
    secret_key: str = Field(
        default_factory=lambda: get_settings().minio_root_password,
        description="MinIO Secret Key",
    )
    secure: bool = Field(default=False, description="Use HTTPS")
    bucket_name: str = Field(
        default_factory=lambda: get_settings().minio_bucket,
        description="Bucket pour les medias",
    )

    def get_client(self) -> Minio:
        """Cree et retourne un client MinIO."""
        return Minio(
            endpoint=self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
        )

    def init_bucket(self) -> None:
        """Cree le bucket s'il n'existe pas."""
        client = self.get_client()
        if not client.bucket_exists(self.bucket_name):
            client.make_bucket(self.bucket_name)
