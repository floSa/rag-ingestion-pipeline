"""Tests unitaires pour la declaration des sources (sources.yaml)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.pipeline.sources import SOURCE_TYPES, CleaningOptions, SourceConfig, load_sources


class TestDefaultSourcesFile:
    def test_loads_and_validates(self):
        sources = load_sources()
        names = [s.name for s in sources]
        assert "pdfs" in names
        assert "livres_html" in names
        assert "markdown" in names

    def test_types_are_valid(self):
        for source in load_sources():
            assert source.type in SOURCE_TYPES

    def test_only_html_sources_are_cleaned(self):
        for source in load_sources():
            assert source.needs_cleaning == (source.type == "html")


class TestLoadCustomFile:
    def test_full_source_block(self, tmp_path):
        yaml_file = tmp_path / "sources.yaml"
        yaml_file.write_text(
            """
sources:
  - name: capture_site
    glob: "captures/site/**/*.html"
    type: html
    cleaning:
      extra_remove_selectors: [".cookie-banner"]
      min_text_chars: 100
""",
            encoding="utf-8",
        )
        sources = load_sources(yaml_file)
        assert len(sources) == 1
        source = sources[0]
        assert source.name == "capture_site"
        assert source.cleaning.extra_remove_selectors == [".cookie-banner"]
        assert source.cleaning.min_text_chars == 100
        # Les options non precisees gardent leur defaut
        assert source.cleaning.max_data_uri_bytes == CleaningOptions().max_data_uri_bytes
        assert source.cleaning.export_images is True
        assert source.cleaning.profiles == []

    def test_profiles_parsed(self, tmp_path):
        yaml_file = tmp_path / "sources.yaml"
        yaml_file.write_text(
            """
sources:
  - name: capture
    glob: "captures/**/*.html"
    type: html
    cleaning:
      export_images: false
      profiles:
        - name: monsite
          detect: ".reader"
          content: ".reader main"
          strip: [".banner"]
""",
            encoding="utf-8",
        )
        source = load_sources(yaml_file)[0]
        assert source.cleaning.export_images is False
        profile = source.cleaning.profiles[0]
        assert profile.name == "monsite"
        assert profile.detect == ".reader"
        assert profile.content == ".reader main"
        assert profile.strip == [".banner"]

    def test_duplicate_names_rejected(self, tmp_path):
        yaml_file = tmp_path / "sources.yaml"
        yaml_file.write_text(
            """
sources:
  - {name: doublon, glob: "a/**/*.pdf", type: pdf}
  - {name: doublon, glob: "b/**/*.pdf", type: pdf}
""",
            encoding="utf-8",
        )
        with pytest.raises(ValidationError, match="doublon"):
            load_sources(yaml_file)


class TestMarkdownSource:
    def test_md_type_accepted(self):
        source = SourceConfig(name="notes", glob="mds/**/*.md", type="md")
        assert source.type == "md"

    def test_md_source_is_not_cleaned(self):
        # Le Markdown est deja propre : ni boilerplate a retirer, ni image
        # inline a exporter, donc pas d'etape de nettoyage.
        assert SourceConfig(name="notes", glob="mds/**/*.md", type="md").needs_cleaning is False

    def test_html_source_is_cleaned(self):
        assert SourceConfig(name="cap", glob="**/*.html", type="html").needs_cleaning is True

    def test_pdf_source_is_not_cleaned(self):
        assert SourceConfig(name="livres", glob="**/*.pdf", type="pdf").needs_cleaning is False

    def test_md_source_from_yaml(self, tmp_path):
        yaml_file = tmp_path / "sources.yaml"
        yaml_file.write_text(
            """
sources:
  - name: notes
    glob: "mds/**/*.md"
    type: md
""",
            encoding="utf-8",
        )
        source = load_sources(yaml_file)[0]
        assert source.type == "md"
        assert source.glob == "mds/**/*.md"


class TestSourceConfigValidation:
    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            SourceConfig(name="x", glob="**/*.docx", type="docx")

    def test_invalid_name_rejected(self):
        with pytest.raises(ValidationError):
            SourceConfig(name="Pas-Valide", glob="**/*.pdf", type="pdf")

    def test_default_cleaning_options(self):
        source = SourceConfig(name="ok", glob="**/*.html", type="html")
        assert source.cleaning.extra_remove_selectors == []
        assert source.cleaning.min_text_chars == 250


class TestFrontBackMatterFilter:
    def _source(self, **kwargs) -> SourceConfig:
        return SourceConfig(name="livres", glob="htms/**/*.html", type="html", **kwargs)

    def test_ignores_index_and_contents(self):
        source = self._source()
        assert source.is_ignored("htms/Practical MLOps/Index.html")
        assert source.is_ignored("htms/Practical MLOps/Table of Contents.html")
        assert source.is_ignored("/opt/dagster/app/Datas/htms/Livre/Copyright.html")

    def test_keeps_the_body_of_the_book(self):
        source = self._source()
        for nom in (
            "htms/Practical MLOps/1. Introduction to MLOps.html",
            "htms/Practical MLOps/Preface.html",
            "htms/Practical MLOps/A. Key Terms.html",
            "htms/Workshop/13 Appendix.html",
        ):
            assert not source.is_ignored(nom), nom

    def test_extra_titles_are_honoured(self):
        source = self._source(extra_skip_titles=["About This Book"])
        assert source.is_ignored("htms/Livre/About this book.html")
        assert not source.is_ignored("htms/Livre/Chapitre 1.html")

    def test_filter_can_be_disabled(self):
        source = self._source(skip_front_back_matter=False)
        assert source.skip_titles is None
        assert not source.is_ignored("htms/Livre/Index.html")
