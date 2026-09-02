"""Tests unitaires pour les settings pydantic-settings."""

from __future__ import annotations

import pytest

from src.pipeline.settings import PipelineSettings


class TestPipelineSettingsDefaults:
    def test_source_dir_default(self):
        s = PipelineSettings(_env_file=None)
        assert s.source_dir == "/opt/dagster/app/Datas"

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


class TestLesDeuxClassesDeReglagesSAccordentSurMinio:
    """Registre 4.29.b — DEUX classes de reglages decident du MEME objet MinIO.

    L'objet est televerse dans `PipelineSettings.minio_bucket` /
    `.minio_endpoint` (`media.py`), et l'URL publiee est construite par
    `images.object_url`, qui lit `DoclingSettings` (`images.py`). Les deux lisent
    les memes variables d'environnement et portent les memes defauts, donc il n'y
    a **aucune consequence aujourd'hui** — et rien ne gardait leur accord.

    `mesure` le 2 septembre 2026 sur le code livre par le lot 4, mutation
    appliquee puis revoquee, texte verifie change :

    ==================================================== ================
    mutation                                             suite entiere
    ==================================================== ================
    `PipelineSettings.minio_bucket` -> "autre-bucket"    VERTE, 847 tests
    `PipelineSettings.minio_endpoint` -> "ailleurs:9000" VERTE, 847 tests
    ==================================================== ================

    **Une image televersee dans un bucket et publiee sous un autre est un objet
    qui existe et une URL qui 404** — c'est le registre 4.28.b refait, dans le
    geste qui vient de le fermer. Et la panne est silencieuse : le televersement
    reussit, l'URL est ecrite dans le graphe, et seul l'agent la voit echouer.

    Le garde va plus loin que l'egalite des defauts, et il faut dire pourquoi :
    deux classes peuvent porter le meme defaut et lire des variables
    d'environnement DIFFERENTES. Un poste qui declare `MINIO_BUCKET` verrait
    alors une seule des deux bouger. Le second test ferme ce chemin.
    """

    COUPLES = ("minio_endpoint", "minio_bucket")

    @pytest.mark.parametrize("champ", COUPLES)
    def test_les_defauts_sont_les_memes(self, champ: str) -> None:
        """LE GARDE. Un defaut qui derive d'un cote rougit ici."""
        from src.docling_service.settings import DoclingSettings
        from src.pipeline.settings import PipelineSettings

        cote_pipeline = getattr(PipelineSettings(_env_file=None), champ)
        cote_docling = getattr(DoclingSettings(_env_file=None), champ)

        assert cote_pipeline == cote_docling, (
            f"« {champ} » vaut {cote_pipeline!r} pour le pipeline, qui TELEVERSE "
            f"l'objet, et {cote_docling!r} pour le service, qui PUBLIE son URL. "
            "L'objet existerait et son URL rendrait 404, sans aucune erreur "
            "(registre 4.29.b)"
        )

    @pytest.mark.parametrize("champ", COUPLES)
    def test_les_deux_classes_lisent_la_meme_variable(self, champ: str, monkeypatch) -> None:
        """LE TEMOIN, et il ferme un chemin que l'egalite des defauts laisse ouvert.

        Sans lui, les deux classes pourraient s'accorder sur leur defaut et lire
        deux variables d'environnement differentes : tout poste qui declare la
        variable verrait une seule des deux bouger, et le test ci-dessus
        resterait vert.
        """
        from src.docling_service.settings import DoclingSettings
        from src.pipeline.settings import PipelineSettings

        temoin = "valeur-de-controle-partagee"
        monkeypatch.setenv(champ.upper(), temoin)

        cote_pipeline = getattr(PipelineSettings(_env_file=None), champ)
        cote_docling = getattr(DoclingSettings(_env_file=None), champ)

        assert cote_pipeline == temoin, (
            f"le pipeline ne lit pas {champ.upper()} : il a rendu {cote_pipeline!r}"
        )
        assert cote_docling == temoin, (
            f"le service ne lit pas {champ.upper()} : il a rendu {cote_docling!r}"
        )
