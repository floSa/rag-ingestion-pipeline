"""Tests unitaires pour la factory Dagster (assets, jobs, sensors par source)."""

from __future__ import annotations

from dagster import AssetKey, DagsterInstance, Definitions, build_sensor_context

from src.pipeline.factory import build_source
from src.pipeline.settings import get_settings
from src.pipeline.sources import SourceConfig, load_sources


def _html_source(name: str = "test_html") -> SourceConfig:
    return SourceConfig(name=name, glob="captures/**/*.html", type="html")


def _pdf_source(name: str = "test_pdf") -> SourceConfig:
    return SourceConfig(name=name, glob="pdfs/**/*.pdf", type="pdf")


def _md_source(name: str = "test_md") -> SourceConfig:
    return SourceConfig(name=name, glob="mds/**/*.md", type="md")


class TestBuildSource:
    def test_html_source_has_clean_then_extract(self):
        built = build_source(_html_source())
        keys = {a.key for a in built.assets}
        assert keys == {
            AssetKey(["test_html", "cleaned_html"]),
            AssetKey(["test_html", "extracted_document"]),
        }

    def test_pdf_source_has_single_extract_asset(self):
        built = build_source(_pdf_source())
        keys = {a.key for a in built.assets}
        assert keys == {AssetKey(["test_pdf", "extracted_document"])}

    def test_md_source_has_single_extract_asset(self):
        # Le Markdown suit le chemin direct du PDF, sans etape de nettoyage.
        built = build_source(_md_source())
        keys = {a.key for a in built.assets}
        assert keys == {AssetKey(["test_md", "extracted_document"])}

    def test_md_source_job_and_sensor(self):
        built = build_source(_md_source())
        assert built.job.name == "test_md_job"
        assert built.sensor.name == "test_md_sensor"
        assert built.partitions.name == "test_md_files"

    def test_job_and_sensor_names(self):
        built = build_source(_html_source())
        assert built.job.name == "test_html_job"
        assert built.sensor.name == "test_html_sensor"

    def test_partitions_named_after_source(self):
        built = build_source(_pdf_source())
        assert built.partitions.name == "test_pdf_files"

    def test_assets_share_source_partitions(self):
        built = build_source(_html_source())
        for asset_def in built.assets:
            assert asset_def.partitions_def is built.partitions


class TestDefinitionsResolve:
    def test_declared_sources_build_valid_definitions(self):
        built = [build_source(s) for s in load_sources()]
        defs = Definitions(
            assets=[a for b in built for a in b.assets],
            jobs=[b.job for b in built],
            sensors=[b.sensor for b in built],
        )
        for b in built:
            assert defs.resolve_job_def(b.job.name) is not None


class TestFileSensor:
    def test_detects_new_file_and_creates_partition(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        try:
            captures = tmp_path / "captures"
            captures.mkdir()
            (captures / "page.html").write_text("<html></html>", encoding="utf-8")

            built = build_source(_html_source(name="cap"))
            with DagsterInstance.ephemeral() as instance:
                context = build_sensor_context(instance=instance)
                result = built.sensor(context)

            assert len(result.run_requests) == 1
            assert result.run_requests[0].partition_key == "captures/page.html"
            assert len(result.dynamic_partitions_requests) == 1
        finally:
            get_settings.cache_clear()

    def test_rafale_de_fichiers_en_un_seul_passage(self, tmp_path, monkeypatch):
        # Un corpus de plusieurs dizaines de livres, chacun decoupe en
        # chapitres, produit des centaines de fichiers deposes d'un coup. Le
        # sensor doit tous les voir dans le meme passage, sans en perdre.
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        try:
            captures = tmp_path / "captures"
            captures.mkdir()
            attendus = 250
            for numero in range(attendus):
                (captures / f"page_{numero:04d}.html").write_text("<html></html>", encoding="utf-8")

            built = build_source(_html_source(name="rafale"))
            with DagsterInstance.ephemeral() as instance:
                context = build_sensor_context(instance=instance)
                result = built.sensor(context)

            assert len(result.run_requests) == attendus
            cles = {request.partition_key for request in result.run_requests}
            assert len(cles) == attendus
            ajoutees = {
                cle
                for demande in result.dynamic_partitions_requests
                for cle in demande.partition_keys
            }
            assert len(ajoutees) == attendus
        finally:
            get_settings.cache_clear()

    def test_run_key_unique_par_fichier(self, tmp_path, monkeypatch):
        # Deux fichiers ne doivent jamais partager une run_key, sans quoi
        # Dagster considererait le second comme un doublon et l'ignorerait.
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        try:
            captures = tmp_path / "captures"
            captures.mkdir()
            for numero in range(30):
                (captures / f"page_{numero:02d}.html").write_text("<html></html>", encoding="utf-8")

            built = build_source(_html_source(name="cles"))
            with DagsterInstance.ephemeral() as instance:
                context = build_sensor_context(instance=instance)
                result = built.sensor(context)

            run_keys = [request.run_key for request in result.run_requests]
            assert len(set(run_keys)) == len(run_keys)
        finally:
            get_settings.cache_clear()

    def test_unchanged_file_not_rerun(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        try:
            captures = tmp_path / "captures"
            captures.mkdir()
            (captures / "page.html").write_text("<html></html>", encoding="utf-8")

            built = build_source(_html_source(name="cap"))
            with DagsterInstance.ephemeral() as instance:
                context = build_sensor_context(instance=instance)
                first = built.sensor(context)
                context_second = build_sensor_context(instance=instance, cursor=context.cursor)
                second = built.sensor(context_second)

            assert len(first.run_requests) == 1
            assert len(second.run_requests) == 0
        finally:
            get_settings.cache_clear()
