"""Tests de la reconstruction de la hierarchie des titres."""

from __future__ import annotations

from src.docling_service.hierarchy import (
    MAX_DEPTH,
    HeadingStack,
    dense_ranks,
    is_inside,
)


class TestDenseRanks:
    def test_biggest_gets_rank_zero(self):
        assert dense_ranks([20.0, 100.0, 18.0]) == {100.0: 0, 20.0: 1, 18.0: 2}

    def test_ranks_are_consecutive(self):
        """Deux tailles seulement donnent 0 et 1, jamais 0 et 4."""
        assert sorted(dense_ranks([24.0, 10.0]).values()) == [0, 1]

    def test_duplicates_share_a_rank(self):
        rangs = dense_ranks([18.0, 18.0, 18.0, 20.0])
        assert rangs[18.0] == 1 and rangs[20.0] == 0

    def test_two_documents_with_different_scales_give_the_same_ranks(self):
        """Un livre en 24/22/20 se segmente comme un livre en 20/18/16."""
        a = dense_ranks([100.0, 20.0, 18.0, 16.0])
        b = dense_ranks([72.0, 24.0, 22.0, 20.0])
        assert sorted(a.values()) == sorted(b.values())

    def test_empty(self):
        assert dense_ranks([]) == {}


class TestHeadingStack:
    def test_first_heading_is_attached_to_the_document(self):
        placement = HeadingStack().place("chapitre", 0)
        assert placement.parent_id is None
        assert placement.depth == 0

    def test_a_lower_rank_nests_under_the_previous(self):
        pile = HeadingStack()
        pile.place("chapitre", 0)
        placement = pile.place("section", 1)
        assert placement.parent_id == "chapitre"
        assert placement.depth == 1

    def test_an_equal_rank_becomes_a_sibling(self):
        pile = HeadingStack()
        pile.place("chapitre1", 0)
        placement = pile.place("chapitre2", 0)
        assert placement.parent_id is None
        assert placement.depth == 0

    def test_a_higher_rank_closes_the_open_headings(self):
        pile = HeadingStack()
        pile.place("chapitre", 0)
        pile.place("section", 1)
        pile.place("sous_section", 2)
        placement = pile.place("chapitre_suivant", 0)
        assert placement.parent_id is None
        assert placement.depth == 0

    def test_depth_follows_the_parent_not_the_rank(self):
        """Un faux titre minuscule ne doit pas creer de trou dans l'arbre."""
        pile = HeadingStack()
        pile.place("chapitre", 0)
        pile.place("section", 1)
        placement = pile.place("faux_titre", 9)  # rang tres bas
        assert placement.depth == 2  # et non 9

    def test_depth_is_capped(self):
        pile = HeadingStack()
        for index in range(10):
            placement = pile.place(f"h{index}", index)
        assert placement.depth == MAX_DEPTH

    def test_current_id_follows_the_last_heading(self):
        pile = HeadingStack()
        assert pile.current_id is None
        pile.place("chapitre", 0)
        assert pile.current_id == "chapitre"
        pile.place("section", 1)
        assert pile.current_id == "section"

    def test_reset_empties_the_stack(self):
        pile = HeadingStack()
        pile.place("chapitre", 0)
        pile.reset()
        assert pile.current_id is None

    def test_a_document_with_a_single_size_stays_flat(self):
        """Aucun signal de niveau : on retombe sur le comportement anterieur."""
        pile = HeadingStack()
        profondeurs = [pile.place(f"h{index}", 0).depth for index in range(5)]
        assert profondeurs == [0, 0, 0, 0, 0]

    def test_real_chapter_shape(self):
        """Reproduit le chapitre 3 de statisticsfordatascience, valide sur son sommaire."""
        pile = HeadingStack()
        titres = [
            ("chapitre", 0),
            ("understanding", 1),
            ("common", 2),
            ("contextual", 2),
            ("cleaning", 2),
            ("r_and_common", 1),
            ("outliers", 2),
            ("step1", 3),
            ("step2", 3),
            ("domain", 2),
        ]
        arbre = {nom: pile.place(nom, rang) for nom, rang in titres}
        assert arbre["understanding"].parent_id == "chapitre"
        assert arbre["common"].parent_id == "understanding"
        assert arbre["r_and_common"].parent_id == "chapitre"
        assert arbre["outliers"].parent_id == "r_and_common"
        assert arbre["step1"].parent_id == "outliers"
        assert arbre["domain"].parent_id == "r_and_common"


class TestIsInside:
    IMAGE = (100.0, 100.0, 400.0, 300.0)

    def test_detects_a_heading_inside_a_picture(self):
        assert is_inside((150.0, 150.0, 300.0, 200.0), [self.IMAGE])

    def test_a_heading_outside_is_kept(self):
        assert not is_inside((150.0, 400.0, 300.0, 450.0), [self.IMAGE])

    def test_partial_overlap_is_not_containment(self):
        assert not is_inside((50.0, 150.0, 300.0, 200.0), [self.IMAGE])

    def test_tolerance_absorbs_rounding(self):
        assert is_inside((99.0, 99.0, 401.0, 301.0), [self.IMAGE])

    def test_no_picture_on_the_page(self):
        assert not is_inside((150.0, 150.0, 300.0, 200.0), [])
