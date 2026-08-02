"""Tests du rattachement des chunks Docling a nos elements."""

from __future__ import annotations

from src.docling_service.anchoring import (
    block_size,
    index_by_self_ref,
    resolve_anchors,
)


def element(identifiant: str, self_ref: str) -> dict[str, object]:
    return {"id": identifiant, "self_ref": self_ref, "text": f"texte de {identifiant}"}


ELEMENTS = [
    element("aaaaaaaaaa", "#/texts/0"),
    element("bbbbbbbbbb", "#/texts/1"),
    element("cccccccccc", "#/texts/2"),
]


class TestIndexBySelfRef:
    def test_indexes_by_reference(self):
        index = index_by_self_ref(ELEMENTS)
        assert index["#/texts/1"]["id"] == "bbbbbbbbbb"

    def test_ignores_elements_without_reference(self):
        index = index_by_self_ref([*ELEMENTS, element("dddddddddd", "")])
        assert len(index) == 3


class TestResolveAnchors:
    def test_one_chunk_per_element(self):
        ancres = resolve_anchors([["#/texts/0"], ["#/texts/1"]], ELEMENTS)
        assert [a.element["id"] for a in ancres] == ["aaaaaaaaaa", "bbbbbbbbbb"]
        assert all(a.count == 1 for a in ancres)

    def test_a_chunk_covering_several_elements_anchors_on_the_first(self):
        """Regrouper est le but de HybridChunker : l'ancre est le point de depart."""
        ancres = resolve_anchors([["#/texts/1", "#/texts/2"]], ELEMENTS)
        assert ancres[0].element["id"] == "bbbbbbbbbb"

    def test_several_chunks_sharing_an_anchor_are_numbered(self):
        """Un element trop long pour la fenetre est reparti sur plusieurs chunks."""
        ancres = resolve_anchors([["#/texts/0"], ["#/texts/0"], ["#/texts/1"]], ELEMENTS)
        assert (ancres[0].index, ancres[0].count) == (0, 2)
        assert (ancres[1].index, ancres[1].count) == (1, 2)
        assert (ancres[2].index, ancres[2].count) == (0, 1)

    def test_an_unknown_reference_is_dropped(self):
        """Mieux vaut ecarter un chunk que le rattacher au mauvais element."""
        ancres = resolve_anchors([["#/tables/9"]], ELEMENTS)
        assert ancres == [None]

    def test_the_first_known_reference_wins(self):
        ancres = resolve_anchors([["#/tables/9", "#/texts/2"]], ELEMENTS)
        assert ancres[0].element["id"] == "cccccccccc"

    def test_no_chunk_at_all(self):
        assert resolve_anchors([], ELEMENTS) == []


class TestBlockSize:
    def test_counts_the_covered_elements(self):
        assert block_size(["#/texts/0", "#/texts/1"], ELEMENTS) == 2

    def test_never_below_one(self):
        assert block_size(["#/tables/9"], ELEMENTS) == 1

    def test_unknown_references_are_not_counted(self):
        assert block_size(["#/texts/0", "#/tables/9"], ELEMENTS) == 1
