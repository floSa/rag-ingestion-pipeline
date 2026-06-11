"""Tests unitaires pour les settings pydantic-settings."""

from __future__ import annotations

from src.pipeline.settings import PipelineSettings


class TestPipelineSettingsDefaults:
    def test_source_dir_default(self):
        s = PipelineSettings(_env_file=None)
        assert s.source_dir == "/opt/dagster/app/Datas"

    def test_cleaned_subdir_default(self):
        s = PipelineSettings(_env_file=None)
        assert s.cleaned_subdir == ".cleaned"

    def test_docling_default(self):
        s = PipelineSettings(_env_file=None)
        assert s.docling_service_url == "http://docling-service:8000"


class TestPipelineSettingsEnvOverride:
    def test_override_source_dir(self, monkeypatch):
        monkeypatch.setenv("SOURCE_DIR", "/tmp/datas")
        s = PipelineSettings(_env_file=None)
        assert s.source_dir == "/tmp/datas"

    def test_override_docling_url(self, monkeypatch):
        monkeypatch.setenv("DOCLING_SERVICE_URL", "http://localhost:8000")
        s = PipelineSettings(_env_file=None)
        assert s.docling_service_url == "http://localhost:8000"

    def test_override_cleaned_subdir(self, monkeypatch):
        monkeypatch.setenv("CLEANED_SUBDIR", ".propre")
        s = PipelineSettings(_env_file=None)
        assert s.cleaned_subdir == ".propre"
