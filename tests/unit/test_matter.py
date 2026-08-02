"""Tests du reperage des parties hors contenu d'un ouvrage."""

from __future__ import annotations

from src.docling_service.matter import (
    detect_index_pages,
    has_text_layer,
    is_front_back_matter,
    kept_ranges,
    looks_like_index_page,
    normalize_title,
    pages_to_skip,
    sample_pages,
)


class TestNormalizeTitle:
    def test_strips_leading_numbering(self):
        assert normalize_title("13. Appendix") == "appendix"
        assert normalize_title("0. Preface") == "preface"
        assert normalize_title("A. Key Terms") == "key terms"

    def test_strips_accents_and_case(self):
        assert normalize_title("Table des Matières") == "table des matieres"
        assert normalize_title("INDEX") == "index"

    def test_keeps_title_made_only_of_a_number(self):
        assert normalize_title("12") == "12"

    def test_collapses_punctuation_and_spaces(self):
        assert normalize_title("  About   the — Author!  ") == "about the author"


class TestIsFrontBackMatter:
    def test_recognises_worthless_parts(self):
        for titre in ("Index", "Table of Contents", "Copyright", "Cover", "Credits"):
            assert is_front_back_matter(titre), titre

    def test_keeps_prose_parts(self):
        """Preface, glossaire et annexes sont de la prose : on les garde."""
        for titre in ("Preface", "0. Preface", "A. Key Terms", "13 Appendix", "Glossary"):
            assert not is_front_back_matter(titre), titre

    def test_accepts_a_custom_list(self):
        assert is_front_back_matter("Remerciements", frozenset({"remerciements"}))
        assert not is_front_back_matter("Index", frozenset({"remerciements"}))


class TestPagesToSkip:
    TOC = [
        (1, "Cover", 1),
        (1, "Copyright", 3),
        (1, "Table of Contents", 11),
        (1, "Preface", 12),
        (1, "Chapter 1", 20),
        (2, "A section", 22),
        (1, "Index", 274),
    ]

    def test_skips_front_and_back_matter(self):
        ignorees = pages_to_skip(self.TOC, 280)
        assert 1 in ignorees and 2 in ignorees  # Cover court jusqu'a Copyright
        assert 11 in ignorees  # Table of Contents, une page
        assert ignorees >= set(range(274, 281))  # Index jusqu'a la fin

    def test_keeps_the_body(self):
        ignorees = pages_to_skip(self.TOC, 280)
        assert not ignorees & set(range(12, 274))

    def test_a_subsection_does_not_close_its_parent(self):
        """Chapter 1 court jusqu'a Index, pas jusqu'a sa propre sous-section."""
        ignorees = pages_to_skip([(1, "Chapter 1", 20), (2, "Index", 22)], 30)
        assert ignorees == set(range(22, 31))

    def test_ignores_a_section_covering_the_whole_book(self):
        """Un signet « Contents » parent de tout l'ouvrage n'est pas un sommaire."""
        assert pages_to_skip([(1, "Contents", 1)], 300) == set()

    def test_ignores_out_of_range_bookmarks(self):
        assert pages_to_skip([(1, "Index", 999)], 100) == set()

    def test_empty_document(self):
        assert pages_to_skip([(1, "Index", 1)], 0) == set()

    def test_no_bookmarks(self):
        assert pages_to_skip([], 100) == set()


class TestLooksLikeIndexPage:
    INDEX = "\n".join(
        [
            "Index",
            "80 20 sample rule  136",
            "abstract data type (ADT)  237",
            "AdaBoost  210",
            "adaptive boosting  210",
            "adding context  169",
            "additive smoothing  153",
            "Analysis of Variance (ANOVA)  40, 204",
            "Apache Spark  12",
            "arithmetic mean  33",
            "array  55",
            "association rules  188",
            "attribute  21",
            "average  33",
            "axis  77",
            "bar chart  91",
            "Bayes theorem  150",
        ]
    )

    PROSE = "\n".join(
        [
            "In this chapter we introduced the notion of statistical inference,",
            "which allows a practitioner to draw conclusions about a population",
            "from a limited sample. We saw that the sample mean converges to the",
            "population mean as the sample size grows, a result known as the law",
            "of large numbers. The next chapter builds on this foundation to",
            "present hypothesis testing in detail, with worked examples in Python",
            "that you can run against the datasets provided with this book.",
            "Before moving on, make sure you are comfortable with the vocabulary",
            "introduced here, since every later chapter depends on it heavily.",
            "The exercises at the end of this section are there for that purpose,",
            "and their solutions are given in the appendix for you to check them.",
            "We also recommend reading the references cited along the way, which",
            "go into far more depth than a single chapter possibly could.",
            "Statistics rewards patience more than it rewards cleverness, and the",
            "time spent on the fundamentals is never time wasted in the long run.",
            "That is the single most useful piece of advice this book can offer.",
        ]
    )

    def test_recognises_an_index_page(self):
        assert looks_like_index_page(self.INDEX)

    def test_rejects_prose(self):
        assert not looks_like_index_page(self.PROSE)

    def test_rejects_a_short_page(self):
        assert not looks_like_index_page("Chapitre 4\n\nUne page de garde  12")


class TestDetectIndexPages:
    def test_walks_back_from_the_end(self):
        textes = {
            98: TestLooksLikeIndexPage.PROSE,
            99: TestLooksLikeIndexPage.INDEX,
            100: TestLooksLikeIndexPage.INDEX,
        }
        assert detect_index_pages(textes, 100) == {99, 100}

    def test_stops_at_the_first_normal_page(self):
        textes = {99: TestLooksLikeIndexPage.PROSE, 100: TestLooksLikeIndexPage.INDEX}
        assert detect_index_pages(textes, 100) == {100}

    def test_no_index_at_all(self):
        textes = {99: TestLooksLikeIndexPage.PROSE, 100: TestLooksLikeIndexPage.PROSE}
        assert detect_index_pages(textes, 100) == set()

    def test_never_swallows_the_whole_book(self):
        textes = {page: TestLooksLikeIndexPage.INDEX for page in range(1, 101)}
        detectees = detect_index_pages(textes, 100)
        assert len(detectees) <= 25

    def test_empty_document(self):
        assert detect_index_pages({}, 0) == set()


class TestKeptRanges:
    def test_no_page_skipped(self):
        assert kept_ranges(10, set()) == [(1, 10)]

    def test_head_and_tail_skipped(self):
        assert kept_ranges(10, {1, 2, 9, 10}) == [(3, 8)]

    def test_hole_in_the_middle(self):
        assert kept_ranges(10, {5, 6}) == [(1, 4), (7, 10)]

    def test_everything_skipped(self):
        assert kept_ranges(3, {1, 2, 3}) == []

    def test_pages_stay_absolute(self):
        """Les numeros restent ceux du fichier : aucune renumerotation."""
        plages = kept_ranges(280, set(range(1, 12)) | set(range(274, 281)))
        assert plages == [(12, 273)]


class TestSamplePages:
    def test_returns_everything_when_short(self):
        assert sample_pages([(1, 5)], sample_size=20) == [1, 2, 3, 4, 5]

    def test_spreads_over_the_whole_document(self):
        pages = sample_pages([(1, 300)], sample_size=10)
        assert len(pages) == 10
        assert pages[0] == 1
        assert pages[-1] > 250  # la fin du livre est bien sondee

    def test_never_samples_a_skipped_page(self):
        pages = sample_pages([(1, 10), (21, 30)], sample_size=8)
        assert all(page <= 10 or page >= 21 for page in pages)

    def test_no_range_at_all(self):
        assert sample_pages([], sample_size=5) == []


class TestHasTextLayer:
    def test_normal_book(self):
        assert has_text_layer(["x" * 2000] * 10)

    def test_scan_without_ocr(self):
        """Un scan ne rend que quelques artefacts par page, voire rien."""
        assert not has_text_layer(["", "  ", "3", ""] * 5)

    def test_illustrated_book_still_passes(self):
        """Un ouvrage tres illustre garde ses legendes : il ne doit pas etre rejete."""
        legende = "Figure 4.2 — Repartition des residus par quantile, echantillon complet."
        assert has_text_layer([legende] * 10)

    def test_a_few_empty_pages_do_not_condemn_the_book(self):
        assert has_text_layer(["x" * 2000] * 8 + ["", ""])

    def test_no_sample(self):
        assert has_text_layer([])
