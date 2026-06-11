"""Tests unitaires pour la declaration des sources (sources.yaml)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.pipeline.sources import CleaningOptions, SourceConfig, load_sources


class TestDefaultSourcesFile:
    def test_loads_and_validates(self):
        sources = load_sources()
        names = [s.name for s in sources]
        assert "pdfs" in names
        assert "livres_html" in names

    def test_types_are_valid(self):
        for source in load_sources():
            assert source.type in ("pdf", "html")


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
