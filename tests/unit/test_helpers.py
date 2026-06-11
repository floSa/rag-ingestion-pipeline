"""Tests unitaires pour les fonctions helpers (compute_id, extract_bbox)."""

from __future__ import annotations

import hashlib


def _compute_id(filename: str, page_no: int, order: int, text: str) -> str:
    """Replique de la fonction compute_id de docling_service/main.py."""
    raw = f"{filename}|{page_no}|{order}|{text[:50]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:10]


def _extract_bbox(bbox: object) -> dict[str, float]:
    """Replique de la fonction extract_bbox de docling_service/main.py."""
    if not bbox:
        return {}
    return {
        "l": round(bbox.l, 2),  # type: ignore[attr-defined]
        "t": round(bbox.t, 2),  # type: ignore[attr-defined]
        "r": round(bbox.r, 2),  # type: ignore[attr-defined]
        "b": round(bbox.b, 2),  # type: ignore[attr-defined]
    }


class TestComputeId:
    def test_deterministic(self):
        id1 = _compute_id("doc", 1, 0, "hello")
        id2 = _compute_id("doc", 1, 0, "hello")
        assert id1 == id2

    def test_length_10(self):
        result = _compute_id("test", 1, 0, "some text")
        assert len(result) == 10

    def test_hex_chars_only(self):
        result = _compute_id("test", 1, 0, "text")
        assert all(c in "0123456789abcdef" for c in result)

    def test_different_inputs_different_ids(self):
        id1 = _compute_id("doc1", 1, 0, "text")
        id2 = _compute_id("doc2", 1, 0, "text")
        assert id1 != id2

    def test_different_pages(self):
        id1 = _compute_id("doc", 1, 0, "text")
        id2 = _compute_id("doc", 2, 0, "text")
        assert id1 != id2

    def test_long_text_truncated(self):
        long_text = "x" * 200
        raw = f"doc|1|0|{long_text[:50]}"
        expected = hashlib.sha256(raw.encode()).hexdigest()[:10]
        assert _compute_id("doc", 1, 0, long_text) == expected

    def test_empty_text(self):
        result = _compute_id("doc", 1, 0, "")
        assert len(result) == 10


class TestExtractBbox:
    def test_none_returns_empty(self):
        assert _extract_bbox(None) == {}

    def test_falsy_returns_empty(self):
        assert _extract_bbox(0) == {}

    def test_valid_bbox(self):
        class MockBbox:
            l = 10.123
            t = 20.456
            r = 30.789
            b = 40.012

        result = _extract_bbox(MockBbox())
        assert result == {"l": 10.12, "t": 20.46, "r": 30.79, "b": 40.01}

    def test_rounding(self):
        class MockBbox:
            l = 1.005
            t = 2.555
            r = 3.999
            b = 4.001

        result = _extract_bbox(MockBbox())
        assert result["l"] == 1.0
        assert result["t"] == 2.56
        assert result["r"] == 4.0
        assert result["b"] == 4.0
