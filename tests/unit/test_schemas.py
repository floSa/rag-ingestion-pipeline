"""Tests unitaires pour les schemas Pydantic."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.pipeline.schemas import (
    BoundingBox,
    DocumentElement,
    DocumentMetadata,
    ExtractedDocument,
    ExtractRequest,
    ExtractResponse,
)


class TestBoundingBox:
    def test_create_with_aliases(self):
        bbox = BoundingBox(l=1.0, t=2.0, r=3.0, b=4.0)
        assert bbox.left == 1.0
        assert bbox.top == 2.0
        assert bbox.right == 3.0
        assert bbox.bottom == 4.0

    def test_create_with_full_names(self):
        bbox = BoundingBox(left=1.0, top=2.0, right=3.0, bottom=4.0)
        assert bbox.left == 1.0

    def test_serialization_uses_aliases(self):
        bbox = BoundingBox(l=1.0, t=2.0, r=3.0, b=4.0)
        data = bbox.model_dump(by_alias=True)
        assert data == {"l": 1.0, "t": 2.0, "r": 3.0, "b": 4.0}

    def test_from_dict_with_aliases(self):
        bbox = BoundingBox.model_validate({"l": 10.5, "t": 20.0, "r": 30.5, "b": 40.0})
        assert bbox.left == 10.5
        assert bbox.bottom == 40.0

    def test_missing_field_raises(self):
        with pytest.raises(ValidationError):
            BoundingBox(l=1.0, t=2.0, r=3.0)  # type: ignore[call-arg]


class TestDocumentMetadata:
    def test_defaults(self):
        meta = DocumentMetadata(filename="test", type_file="pdf")
        assert meta.total_pages == 0

    def test_with_pages(self):
        meta = DocumentMetadata(filename="doc", type_file="html", total_pages=42)
        assert meta.total_pages == 42

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            DocumentMetadata(filename="test")  # type: ignore[call-arg]


class TestDocumentElement:
    def test_defaults(self):
        elem = DocumentElement(id="x", label="text")
        assert elem.page_no == 1
        assert elem.text == ""
        assert elem.order == 0
        assert elem.bbox is None
        assert elem.minio_url is None
        assert elem.content is None
        assert elem.reference_id == "DOC"
        assert elem.page_position == 0
        assert elem.ref_position == 0
        assert elem.type == "text"

    def test_with_bbox(self, sample_bbox):
        elem = DocumentElement(id="y", label="picture", bbox=sample_bbox)
        assert elem.bbox is not None
        assert elem.bbox.left == 10.0

    def test_optional_fields(self):
        elem = DocumentElement(
            id="z",
            label="table",
            minio_url="http://minio/img.png",
            content="| col1 | col2 |",
            type="resource",
        )
        assert elem.minio_url == "http://minio/img.png"
        assert elem.content == "| col1 | col2 |"
        assert elem.type == "resource"


class TestExtractedDocument:
    def test_empty_elements(self):
        meta = DocumentMetadata(filename="f", type_file="pdf")
        doc = ExtractedDocument(metadata=meta)
        assert doc.elements == []

    def test_with_elements(self, sample_document):
        assert len(sample_document.elements) == 1
        assert sample_document.metadata.filename == "test_doc"

    def test_roundtrip_json(self, sample_document):
        json_str = sample_document.model_dump_json()
        restored = ExtractedDocument.model_validate_json(json_str)
        assert restored.metadata.filename == sample_document.metadata.filename
        assert len(restored.elements) == len(sample_document.elements)
        assert restored.elements[0].id == sample_document.elements[0].id

    def test_from_docling_json(self):
        raw = {
            "metadata": {"filename": "2408.09869", "type_file": "pdf", "total_pages": 9},
            "elements": [
                {
                    "id": "a950b65a3b",
                    "label": "picture",
                    "page_no": 1,
                    "bbox": {"l": 256.38, "t": 719.3, "r": 355.54, "b": 622.85},
                    "reference_id": "DOC",
                    "page_position": 1,
                    "ref_position": 1,
                    "type": "resource",
                    "text": "",
                },
                {
                    "id": "023351d5f4",
                    "label": "section_header",
                    "page_no": 1,
                    "text": "1 Introduction",
                    "reference_id": "DOC",
                    "page_position": 8,
                    "ref_position": 8,
                    "type": "text",
                },
            ],
        }
        doc = ExtractedDocument.model_validate(raw)
        assert doc.metadata.total_pages == 9
        assert len(doc.elements) == 2
        assert doc.elements[0].bbox is not None
        assert doc.elements[0].bbox.left == 256.38
        assert doc.elements[1].text == "1 Introduction"


class TestExtractRequest:
    def test_valid(self):
        req = ExtractRequest(filepath="/data/test.pdf")
        assert req.filepath == "/data/test.pdf"

    def test_missing_raises(self):
        with pytest.raises(ValidationError):
            ExtractRequest()  # type: ignore[call-arg]


class TestExtractResponse:
    def test_default(self):
        resp = ExtractResponse()
        assert resp.status == "success"

    def test_custom_status(self):
        resp = ExtractResponse(status="error")
        assert resp.status == "error"
