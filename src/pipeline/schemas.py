"""Modèles Pydantic partagés entre le pipeline Dagster et le service Docling."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    """Coordonnées d'une zone dans une page."""

    left: float = Field(alias="l")
    top: float = Field(alias="t")
    right: float = Field(alias="r")
    bottom: float = Field(alias="b")

    model_config = {"populate_by_name": True}


class DocumentMetadata(BaseModel):
    """Métadonnées d'un document extrait."""

    filename: str
    type_file: str
    total_pages: int = 0


class DocumentElement(BaseModel):
    """Élément structurel extrait d'un document (paragraphe, image, table, etc.)."""

    id: str
    label: str
    page_no: int = 1
    bbox: BoundingBox | None = None
    text: str = ""
    order: int = 0
    minio_url: str | None = None
    content: str | None = None
    reference_id: str = "DOC"
    page_position: int = 0
    ref_position: int = 0
    type: str = "text"


class ExtractedDocument(BaseModel):
    """Résultat complet d'une extraction Docling."""

    metadata: DocumentMetadata
    elements: list[DocumentElement] = Field(default_factory=list)


class ExtractRequest(BaseModel):
    """Requête d'extraction envoyée au service Docling."""

    filepath: str


class ExtractResponse(BaseModel):
    """Réponse du service Docling."""

    status: str = "success"
