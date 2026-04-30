"""Tests unitaires pour les settings pydantic-settings."""

from __future__ import annotations

from src.pipeline.settings import PipelineSettings


class TestPipelineSettingsDefaults:
    def test_minio_defaults(self):
        s = PipelineSettings(_env_file=None)
        assert s.minio_endpoint == "minio:9000"
        assert s.minio_root_user == ""
        assert s.minio_root_password == ""
        assert s.minio_bucket == "documents"

    def test_nebula_defaults(self):
        s = PipelineSettings(_env_file=None)
        assert s.nebula_host == "graphd"
        assert s.nebula_port == 9669

    def test_chroma_defaults(self):
        s = PipelineSettings(_env_file=None)
        assert s.chroma_host == "chromadb"
        assert s.chroma_port == 8000

    def test_embedding_default(self):
        s = PipelineSettings(_env_file=None)
        assert s.embedding_model_name == "all-MiniLM-L6-v2"

    def test_docling_default(self):
        s = PipelineSettings(_env_file=None)
        assert s.docling_service_url == "http://docling-service:8000"


class TestPipelineSettingsEnvOverride:
    def test_override_minio(self, monkeypatch):
        monkeypatch.setenv("MINIO_ENDPOINT", "localhost:9000")
        monkeypatch.setenv("MINIO_ROOT_USER", "testuser")
        monkeypatch.setenv("MINIO_ROOT_PASSWORD", "testpass")
        monkeypatch.setenv("MINIO_BUCKET", "test-bucket")
        s = PipelineSettings(_env_file=None)
        assert s.minio_endpoint == "localhost:9000"
        assert s.minio_root_user == "testuser"
        assert s.minio_root_password == "testpass"
        assert s.minio_bucket == "test-bucket"

    def test_override_nebula(self, monkeypatch):
        monkeypatch.setenv("NEBULA_HOST", "localhost")
        monkeypatch.setenv("NEBULA_PORT", "19669")
        s = PipelineSettings(_env_file=None)
        assert s.nebula_host == "localhost"
        assert s.nebula_port == 19669

    def test_override_chroma(self, monkeypatch):
        monkeypatch.setenv("CHROMA_HOST", "localhost")
        monkeypatch.setenv("CHROMA_PORT", "18000")
        s = PipelineSettings(_env_file=None)
        assert s.chroma_host == "localhost"
        assert s.chroma_port == 18000

    def test_override_embedding(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_MODEL_NAME", "custom-model")
        s = PipelineSettings(_env_file=None)
        assert s.embedding_model_name == "custom-model"

    def test_port_type_coercion(self, monkeypatch):
        monkeypatch.setenv("NEBULA_PORT", "1234")
        s = PipelineSettings(_env_file=None)
        assert s.nebula_port == 1234
        assert isinstance(s.nebula_port, int)
