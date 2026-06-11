"""Tests unitaires pour le nettoyage HTML universel."""

from __future__ import annotations

import base64

from src.pipeline.cleaning import (
    PRECLEANED_FALLBACK,
    clean_html,
    clean_html_file,
    preclean_html,
)
from src.pipeline.sources import CleaningOptions, ExtractionProfile


class FakeExporter:
    """Exporteur d'images factice pour les tests (pas de MinIO)."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple[bytes, str, int]] = []

    def __call__(self, payload: bytes, mime: str, index: int) -> str | None:
        self.calls.append((payload, mime, index))
        if self.fail:
            return None
        return f"http://minio:9000/documents/images/html/test/img_{index:04d}.png"

FAKE_DATA_URI = "data:image/png;base64," + "A" * 10_000

ARTICLE_PARAGRAPHS = " ".join(
    f"Ceci est le paragraphe numero {i} du contenu principal de la page, "
    "avec suffisamment de texte pour ressembler a un vrai chapitre de livre "
    "et passer les seuils de detection des extracteurs de contenu."
    for i in range(10)
)

SINGLEFILE_LIKE_HTML = f"""<!DOCTYPE html>
<html><head>
<title>Chapitre 3 - Les statistiques</title>
<style>body {{ color: red; }} .menu {{ display: none; }}</style>
<script>console.log("tracking");</script>
</head>
<body onload="init()">
<!-- comment SingleFile -->
<nav><ul><li>Accueil</li><li>Catalogue</li></ul></nav>
<header><h1>MonSiteDeLivres.com</h1></header>
<div class="cookie-banner">Acceptez nos cookies pour continuer</div>
<main>
<article>
<h1>Chapitre 3 - Les statistiques</h1>
<p style="font-size: 12px">{ARTICLE_PARAGRAPHS}</p>
<img src="{FAKE_DATA_URI}" alt="figure 3.1"/>
<table><tr><td>moyenne</td><td>3.14</td></tr></table>
</article>
</main>
<footer>Copyright 2026 - Mentions legales</footer>
</body></html>
"""


KNOWN_STRATEGIES = ("article", "main", "[role=main]", "trafilatura", "readability")


class TestPrecleanHtml:
    def test_removes_scripts_and_styles(self):
        result = preclean_html(SINGLEFILE_LIKE_HTML, CleaningOptions())
        assert "console.log" not in result
        assert "color: red" not in result

    def test_removes_structural_noise(self):
        result = preclean_html(SINGLEFILE_LIKE_HTML, CleaningOptions())
        assert "Catalogue" not in result  # nav
        assert "MonSiteDeLivres.com" not in result  # header
        assert "Mentions legales" not in result  # footer

    def test_removes_comments(self):
        result = preclean_html(SINGLEFILE_LIKE_HTML, CleaningOptions())
        assert "comment SingleFile" not in result

    def test_removes_inline_style_and_handlers(self):
        result = preclean_html(SINGLEFILE_LIKE_HTML, CleaningOptions())
        assert "font-size" not in result
        assert "onload" not in result

    def test_strips_large_data_uris(self):
        result = preclean_html(SINGLEFILE_LIKE_HTML, CleaningOptions())
        assert FAKE_DATA_URI not in result
        # L'image reste presente (alt conserve), seule l'URI data: est retiree
        assert "figure 3.1" in result

    def test_keeps_small_data_uris(self):
        small_uri = "data:image/png;base64,AAAA"
        html = f'<html><body><p>texte</p><img src="{small_uri}"/></body></html>'
        result = preclean_html(html, CleaningOptions())
        assert small_uri in result

    def test_keeps_main_content(self):
        result = preclean_html(SINGLEFILE_LIKE_HTML, CleaningOptions())
        assert "paragraphe numero 5" in result
        assert "moyenne" in result

    def test_extra_remove_selectors(self):
        options = CleaningOptions(extra_remove_selectors=[".cookie-banner"])
        result = preclean_html(SINGLEFILE_LIKE_HTML, options)
        assert "Acceptez nos cookies" not in result

    def test_without_extra_selectors_banner_stays_after_preclean(self):
        result = preclean_html(SINGLEFILE_LIKE_HTML, CleaningOptions())
        assert "Acceptez nos cookies" in result

    def test_removes_singlefile_hidden_elements(self):
        html = (
            "<html><body><p>visible</p>"
            '<div class="sf-hidden">cache singlefile</div>'
            '<div style="display: none">cache display</div>'
            '<div style="visibility: hidden">cache visibility</div>'
            "<div hidden>cache attribut</div>"
            "</body></html>"
        )
        result = preclean_html(html, CleaningOptions())
        assert "visible" in result
        assert "cache singlefile" not in result
        assert "cache display" not in result
        assert "cache visibility" not in result
        assert "cache attribut" not in result

    def test_keeps_header_inside_article(self):
        html = (
            "<html><body>"
            "<header>chrome du site</header>"
            "<article><header><h1>Titre de l'article</h1></header>"
            "<p>contenu</p></article>"
            "</body></html>"
        )
        result = preclean_html(html, CleaningOptions())
        assert "Titre de l'article" in result
        assert "chrome du site" not in result

    def test_removes_aria_navigation_roles(self):
        html = (
            "<html><body><p>contenu</p>"
            '<div role="navigation">menu lateral</div>'
            '<div role="dialog">popup newsletter</div>'
            "</body></html>"
        )
        result = preclean_html(html, CleaningOptions())
        assert "contenu" in result
        assert "menu lateral" not in result
        assert "popup newsletter" not in result


class TestImageExport:
    PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8_000
    DATA_URI = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode()

    def _html(self) -> str:
        return (
            f"<html><body><p>{'texte ' * 100}</p>"
            f'<img src="{self.DATA_URI}" alt="figure"/></body></html>'
        )

    def test_big_image_exported_and_src_rewritten(self):
        exporter = FakeExporter()
        result = preclean_html(self._html(), CleaningOptions(), image_exporter=exporter)
        assert len(exporter.calls) == 1
        payload, mime, index = exporter.calls[0]
        assert payload == self.PNG_BYTES
        assert mime == "image/png"
        assert f"img_{index:04d}.png" in result
        assert "data:image/png" not in result

    def test_export_failure_drops_src(self):
        exporter = FakeExporter(fail=True)
        result = preclean_html(self._html(), CleaningOptions(), image_exporter=exporter)
        assert len(exporter.calls) == 1
        assert "data:image/png" not in result
        assert "figure" in result  # le alt reste

    def test_without_exporter_src_dropped(self):
        result = preclean_html(self._html(), CleaningOptions())
        assert "data:image/png" not in result

    def test_small_image_kept_inline_untouched(self):
        small_uri = "data:image/png;base64,AAAA"
        html = f'<html><body><p>texte</p><img src="{small_uri}"/></body></html>'
        exporter = FakeExporter()
        result = preclean_html(html, CleaningOptions(), image_exporter=exporter)
        assert exporter.calls == []
        assert small_uri in result

    def test_invalid_base64_dropped_not_crashed(self):
        bad_uri = "data:image/png;base64,!!!" + "x" * 5_000
        html = f'<html><body><p>texte</p><img src="{bad_uri}"/></body></html>'
        exporter = FakeExporter()
        result = preclean_html(html, CleaningOptions(), image_exporter=exporter)
        assert exporter.calls == []
        assert bad_uri not in result


class TestProfiles:
    PROFILED_HTML = (
        "<html><body>"
        '<div class="site-shell">'
        '<div class="article-reader"><main>'
        f"<h1>Mon article</h1><p>{'contenu utile ' * 50}</p>"
        '<div class="newsletter-banner">Abonnez-vous !</div>'
        "</main></div>"
        f"<div class='junk'>{'bruit lateral ' * 80}</div>"
        "</div></body></html>"
    )

    def _options(self) -> CleaningOptions:
        return CleaningOptions(
            profiles=[
                ExtractionProfile(
                    name="monsite",
                    detect=".article-reader",
                    content=".article-reader main",
                    strip=[".newsletter-banner"],
                )
            ]
        )

    def test_matching_profile_wins(self):
        cleaned, report = clean_html(self.PROFILED_HTML, self._options())
        assert report.strategy == "monsite"
        assert "contenu utile" in cleaned
        assert "bruit lateral" not in cleaned

    def test_strip_applied_inside_content(self):
        cleaned, _ = clean_html(self.PROFILED_HTML, self._options())
        assert "Abonnez-vous" not in cleaned

    def test_non_matching_profile_falls_back_to_generic(self):
        options = CleaningOptions(
            profiles=[ExtractionProfile(name="autre", detect="#nexiste-pas", content="main")]
        )
        cleaned, report = clean_html(SINGLEFILE_LIKE_HTML, options)
        assert report.strategy in (*KNOWN_STRATEGIES, PRECLEANED_FALLBACK)
        assert "paragraphe numero 5" in cleaned


class TestCleanHtml:
    def test_keeps_main_content(self):
        cleaned, report = clean_html(SINGLEFILE_LIKE_HTML, CleaningOptions())
        assert "paragraphe numero 5" in cleaned
        assert report.text_chars > 0

    def test_removes_noise_whatever_the_strategy(self):
        cleaned, _ = clean_html(SINGLEFILE_LIKE_HTML, CleaningOptions())
        assert "console.log" not in cleaned
        assert "Catalogue" not in cleaned

    def test_content_extractor_drops_cookie_banner(self):
        cleaned, report = clean_html(SINGLEFILE_LIKE_HTML, CleaningOptions())
        if report.strategy != PRECLEANED_FALLBACK:
            assert "Acceptez nos cookies" not in cleaned

    def test_report_strategy_is_known(self):
        _, report = clean_html(SINGLEFILE_LIKE_HTML, CleaningOptions())
        assert report.strategy in (*KNOWN_STRATEGIES, PRECLEANED_FALLBACK)

    def test_semantic_container_wins_when_present(self):
        # Le fixture contient un <article> riche : le candidat semantique
        # est le plus complet et doit gagner.
        _, report = clean_html(SINGLEFILE_LIKE_HTML, CleaningOptions())
        assert report.strategy == "article"

    def test_fallback_when_thresholds_unreachable(self):
        options = CleaningOptions(min_text_chars=10_000_000)
        cleaned, report = clean_html(SINGLEFILE_LIKE_HTML, options)
        assert report.strategy == PRECLEANED_FALLBACK
        assert "paragraphe numero 5" in cleaned

    def test_tiny_page_falls_back_without_losing_content(self):
        html = "<html><body><p>Juste une ligne.</p></body></html>"
        cleaned, report = clean_html(html, CleaningOptions())
        assert "Juste une ligne." in cleaned
        assert report.strategy == PRECLEANED_FALLBACK

    def test_report_sizes_consistent(self):
        _, report = clean_html(SINGLEFILE_LIKE_HTML, CleaningOptions())
        assert report.raw_bytes >= report.precleaned_bytes
        assert report.cleaned_bytes > 0


class TestCleanHtmlFile:
    def test_writes_cleaned_file_creating_parents(self, tmp_path):
        source = tmp_path / "captures" / "page.html"
        source.parent.mkdir(parents=True)
        source.write_text(SINGLEFILE_LIKE_HTML, encoding="utf-8")
        dest = tmp_path / ".cleaned" / "captures" / "page.html"

        report = clean_html_file(source, dest, CleaningOptions())

        assert dest.exists()
        assert "paragraphe numero 5" in dest.read_text(encoding="utf-8")
        assert report.cleaned_bytes == len(dest.read_bytes())
