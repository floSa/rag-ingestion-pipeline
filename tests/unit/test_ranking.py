"""Tests de la determination du rang d'un titre."""

from __future__ import annotations

from src.docling_service.ranking import (
    docling_level_rank,
    docling_parent_rank,
    exceeds_body_size,
    font_size_ranks,
    is_heading_candidate,
)


class FakeRef:
    """Reference Docling : un cref, et de quoi le resoudre."""

    def __init__(self, cref, cible=None):
        self.cref = cref
        self._cible = cible

    def resolve(self, _document):
        if self._cible is None:
            raise ValueError("reference non resolvable")
        return self._cible


class FakeItem:
    def __init__(self, label="section_header", parent=None, level=None):
        self.label = label
        self.parent = parent
        if level is not None:
            self.level = level


BODY = FakeRef("#/body")


class TestDoclingParentRank:
    def test_a_title_attached_to_the_body_has_rank_zero(self):
        assert docling_parent_rank(FakeItem(parent=BODY), None) == 0

    def test_a_subtitle_counts_its_ancestors(self):
        chapitre = FakeItem("title", parent=BODY)
        item = FakeItem("section_header", parent=FakeRef("#/texts/84", chapitre))
        assert docling_parent_rank(item, None) == 1

    def test_two_levels_of_ancestors(self):
        chapitre = FakeItem("title", parent=BODY)
        section = FakeItem("section_header", parent=FakeRef("#/texts/84", chapitre))
        item = FakeItem("section_header", parent=FakeRef("#/texts/272", section))
        assert docling_parent_rank(item, None) == 2

    def test_anonymous_groups_are_crossed_without_counting(self):
        """Un groupe de mise en page n'a pas de sens editorial."""
        chapitre = FakeItem("title", parent=BODY)
        groupe = FakeItem("group", parent=FakeRef("#/texts/84", chapitre))
        item = FakeItem("section_header", parent=FakeRef("#/groups/14", groupe))
        assert docling_parent_rank(item, None) == 1

    def test_no_parent_at_all_gives_up(self):
        """Aucun parent declare n'est pas « niveau 0 » mais « aucune information ».

        La distinction compte : un parent ``#/body`` affirme que le titre est au
        sommet, alors qu'un parent absent doit laisser la main au signal suivant.
        """
        assert docling_parent_rank(FakeItem(parent=None), None) is None

    def test_unresolvable_reference_gives_up(self):
        item = FakeItem(parent=FakeRef("#/texts/9"))  # pas de cible
        assert docling_parent_rank(item, None) is None

    def test_circular_reference_does_not_hang(self):
        boucle = FakeItem("title")
        boucle.parent = FakeRef("#/texts/1", boucle)
        assert docling_parent_rank(boucle, None) is None


class TestDoclingLevelRank:
    def test_reads_the_level(self):
        assert docling_level_rank(FakeItem(level=2)) == 2

    def test_absent_level(self):
        assert docling_level_rank(FakeItem()) is None


class TestFontSizeRanks:
    def test_biggest_first(self):
        assert font_size_ranks([16.0, 100.0, 20.0]) == {100.0: 0, 20.0: 1, 16.0: 2}

    def test_scale_does_not_matter(self):
        petit = font_size_ranks([100.0, 20.0, 18.0, 16.0])
        grand = font_size_ranks([72.0, 24.0, 22.0, 20.0])
        assert sorted(petit.values()) == sorted(grand.values())


class TestIsHeadingCandidate:
    FIGURE = (100.0, 100.0, 400.0, 300.0)

    def test_rejects_a_heading_inside_a_figure(self):
        assert not is_heading_candidate((150.0, 150.0, 300.0, 200.0), [self.FIGURE])

    def test_keeps_a_heading_outside(self):
        assert is_heading_candidate((150.0, 400.0, 300.0, 450.0), [self.FIGURE])

    def test_unknown_box_is_kept(self):
        assert is_heading_candidate(None, [self.FIGURE])


class TestExceedsBodySize:
    def test_a_real_heading_is_bigger(self):
        assert exceeds_body_size(18.0, 10.0)

    def test_body_sized_text_is_not_a_heading_level(self):
        """« Then: » en 10 pt, dans un corps a 10 pt, n'ouvre pas de niveau."""
        assert not exceeds_body_size(10.0, 10.0)

    def test_unknown_body_size_stays_permissive(self):
        assert not exceeds_body_size(18.0, 0.0)
