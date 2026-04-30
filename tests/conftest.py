"""Fixtures partagees pour les tests."""

from __future__ import annotations

import pytest

from src.pipeline.schemas import (
    BoundingBox,
    DocumentElement,
    DocumentMetadata,
    ExtractedDocument,
)


@pytest.fixture()
def sample_metadata() -> DocumentMetadata:
    return DocumentMetadata(filename="test_doc", type_file="pdf", total_pages=3)


@pytest.fixture()
def sample_bbox() -> BoundingBox:
    return BoundingBox(l=10.0, t=200.0, r=100.0, b=150.0)


@pytest.fixture()
def sample_element(sample_bbox: BoundingBox) -> DocumentElement:
    return DocumentElement(
        id="abc123",
        label="text",
        page_no=1,
        bbox=sample_bbox,
        text="Hello world",
        order=0,
        reference_id="DOC",
        page_position=1,
        ref_position=1,
        type="text",
    )


@pytest.fixture()
def sample_document(
    sample_metadata: DocumentMetadata, sample_element: DocumentElement
) -> ExtractedDocument:
    return ExtractedDocument(metadata=sample_metadata, elements=[sample_element])
