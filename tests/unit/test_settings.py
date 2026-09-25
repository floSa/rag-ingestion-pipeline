"""Tests unitaires pour les settings pydantic-settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.pipeline.settings import PipelineSettings
from src.reglages_s3 import MESSAGE_ENDPOINT_MANQUANT

# L'adresse du stockage objet n'a plus de defaut : toute construction de
# reglages en exige une. Cette valeur-ci ne designe rien — c'est un temoin, et
# aucun test de ce fichier n'ouvre de connexion.
ENDPOINT_TEMOIN = "stockage-de-controle:8333"


@pytest.fixture()
def endpoint_declare(monkeypatch):
    """Declare `S3_ENDPOINT`, sans quoi aucun reglage ne se construit."""
    monkeypatch.setenv("S3_ENDPOINT", ENDPOINT_TEMOIN)


class TestPipelineSettingsDefaults:
    def test_source_dir_default(self, endpoint_declare):
        s = PipelineSettings(_env_file=None)
        assert s.source_dir == "/opt/dagster/app/Datas"

    def test_docling_default(self, endpoint_declare):
        s = PipelineSettings(_env_file=None)
        assert s.docling_service_url == "http://docling-service:8000"


class TestPipelineSettingsEnvOverride:
    def test_override_source_dir(self, monkeypatch, endpoint_declare):
        monkeypatch.setenv("SOURCE_DIR", "/tmp/datas")
        s = PipelineSettings(_env_file=None)
        assert s.source_dir == "/tmp/datas"

    def test_override_docling_url(self, monkeypatch, endpoint_declare):
        monkeypatch.setenv("DOCLING_SERVICE_URL", "http://localhost:8000")
        s = PipelineSettings(_env_file=None)
        assert s.docling_service_url == "http://localhost:8000"


class TestLAdresseDuStockageObjetNAAucunDefaut:
    """LE GARDE DU LOT : `minio:9000` etait le defaut, a DEUX sites.

    `settings.py:18` et `settings.py:32` portaient la meme adresse en dur. Tant
    qu'elle a designe le stockage en service, ce n'etait qu'un raccourci ; le
    25 septembre 2026 SeaweedFS l'a remplace, et le raccourci a survecu au lieu
    qu'il designait.

    **CE QUE LE DEFAUT RENDAIT POSSIBLE, ET C'EST LE MOTIF DE CE FICHIER.**
    `python -m src.wipe_stores` lit ces memes reglages et VIDE le bucket qu'ils
    designent. Lance depuis un poste dont l'environnement ne porte pas la
    variable — un shell sans `.env`, un conteneur recree sans elle, un cron —
    il aurait purge l'ANCIEN stockage, en rendant compte d'une purge reussie.
    Une purge ne previent pas : elle rend compte.

    Un defaut absent fait echouer le demarrage ; un defaut faux fait REUSSIR la
    purge du mauvais stockage. Les deux classes sont tenues, parce que les deux
    construisent un client.
    """

    CLASSES = (
        "src.pipeline.settings:PipelineSettings",
        "src.docling_service.settings:DoclingSettings",
    )

    @staticmethod
    def _classe(chemin: str) -> type:
        import importlib

        module, nom = chemin.split(":")
        classe = getattr(importlib.import_module(module), nom)
        assert isinstance(classe, type)
        return classe

    @pytest.mark.parametrize("chemin", CLASSES)
    def test_sans_s3_endpoint_la_construction_echoue(self, chemin, monkeypatch):
        """LE GARDE. Un defaut remis ici rend ce test vert et la purge aveugle."""
        monkeypatch.delenv("S3_ENDPOINT", raising=False)

        with pytest.raises(ValidationError) as leve:
            self._classe(chemin)(_env_file=None)

        assert "s3_endpoint" in str(leve.value).lower(), (
            f"l'echec ne nomme pas le champ en cause : {leve.value}"
        )

    @pytest.mark.parametrize("chemin", CLASSES)
    def test_un_s3_endpoint_vide_echoue_aussi(self, chemin, monkeypatch):
        """Une variable DECLAREE VIDE est le meme defaut, et pydantic ne la voit pas seul.

        Un `.env` qui porte « S3_ENDPOINT= » — la forme exacte de
        `.env.example` avant qu'on le remplisse — rend une chaine vide, et un
        champ simplement « requis » l'accepterait.
        """
        monkeypatch.setenv("S3_ENDPOINT", "   ")

        with pytest.raises(ValidationError):
            self._classe(chemin)(_env_file=None)

    @pytest.mark.parametrize("chemin", CLASSES)
    def test_le_message_dit_quoi_faire(self, chemin, monkeypatch):
        """Un refus qui ne dit pas quoi faire se contourne en remettant un defaut."""
        monkeypatch.delenv("S3_ENDPOINT", raising=False)

        with pytest.raises(ValidationError) as leve:
            self._classe(chemin)(_env_file=None)

        message = str(leve.value)
        assert "S3_ENDPOINT" in message
        assert "wipe_stores" in message, (
            f"le message ne dit pas ce que le defaut rendait possible : {message}"
        )
        assert MESSAGE_ENDPOINT_MANQUANT.split(".")[0] in message

    @pytest.mark.parametrize("chemin", CLASSES)
    def test_une_adresse_declaree_passe(self, chemin, monkeypatch):
        """LE TEMOIN. Sans lui, une classe inconstructible rendrait tout vert."""
        monkeypatch.setenv("S3_ENDPOINT", ENDPOINT_TEMOIN)

        reglages = self._classe(chemin)(_env_file=None)

        assert reglages.s3_endpoint == ENDPOINT_TEMOIN

    def test_aucun_reglage_ne_nomme_plus_le_stockage_retire(self, monkeypatch):
        """Aucun champ « minio_* » ne subsiste : un renommage partiel serait pire.

        Un champ laisse en place continuerait de lire `MINIO_ENDPOINT`, donc de
        rendre l'ancien `.env` vivant sans que rien ne le dise.
        """
        monkeypatch.setenv("S3_ENDPOINT", ENDPOINT_TEMOIN)

        for chemin in self.CLASSES:
            champs = self._classe(chemin)(_env_file=None).model_dump()
            restes = [nom for nom in champs if "minio" in nom.lower()]
            assert restes == [], f"{chemin} porte encore {restes}"


class TestLesIdentifiantsDuStockageObjetNOntAucunDefaut:
    """Le meme garde que l'adresse, pour les deux identifiants (livraison §4.28.b).

    `s3_access_key` et `s3_secret_key` valaient « "" » par defaut. Un service
    qui ne recoit pas les variables — c'etait le cas de `dagster-webserver` et
    `dagster-daemon`, qui n'avaient que `env_file: .env` alors que le `.env` ne
    porte pas ces deux noms — construisait donc un client aux identifiants
    VIDES. Rien n'echouait au demarrage : les televersements d'images HTML
    rendaient 403 plus tard, un par un, et les images manquaient au corpus.

    Un identifiant absent doit faire echouer le demarrage, comme l'adresse.
    """

    CLASSES = TestLAdresseDuStockageObjetNAAucunDefaut.CLASSES
    CHAMPS = (("S3_ACCESS_KEY", "s3_access_key"), ("S3_SECRET_KEY", "s3_secret_key"))

    @pytest.mark.parametrize("chemin", CLASSES)
    @pytest.mark.parametrize(("variable", "champ"), CHAMPS)
    def test_sans_identifiant_la_construction_echoue(self, chemin, variable, champ, monkeypatch):
        """LE GARDE. Un defaut vide remis ici rend ce test vert et le televersement muet."""
        monkeypatch.delenv(variable, raising=False)

        with pytest.raises(ValidationError) as leve:
            TestLAdresseDuStockageObjetNAAucunDefaut._classe(chemin)(_env_file=None)

        message = str(leve.value)
        assert champ in message.lower(), f"l'echec ne nomme pas le champ : {message}"
        assert variable in message, f"le message ne nomme pas la variable : {message}"

    @pytest.mark.parametrize("chemin", CLASSES)
    @pytest.mark.parametrize(("variable", "champ"), CHAMPS)
    def test_un_identifiant_vide_echoue_aussi(self, chemin, variable, champ, monkeypatch):
        """« S3_ACCESS_KEY= » dans un environnement est le meme defaut."""
        monkeypatch.setenv(variable, "  ")

        with pytest.raises(ValidationError):
            TestLAdresseDuStockageObjetNAAucunDefaut._classe(chemin)(_env_file=None)

    @pytest.mark.parametrize("chemin", CLASSES)
    def test_des_identifiants_declares_passent(self, chemin, monkeypatch):
        """LE TEMOIN. Sans lui, une classe inconstructible rendrait tout vert."""
        monkeypatch.setenv("S3_ACCESS_KEY", "cle-temoin")
        monkeypatch.setenv("S3_SECRET_KEY", "secret-temoin")  # pragma: allowlist secret

        reglages = TestLAdresseDuStockageObjetNAAucunDefaut._classe(chemin)(_env_file=None)

        assert reglages.s3_access_key == "cle-temoin"
        assert reglages.s3_secret_key == "secret-temoin"  # pragma: allowlist secret


class TestLesDeuxClassesDeReglagesSAccordentSurLeStockageObjet:
    """Registre 4.29.b — DEUX classes de reglages decident du MEME objet.

    L'objet est televerse avec `PipelineSettings.s3_bucket` / `.s3_endpoint`
    (`media.py`), et l'adresse publiee est construite par `images.object_url`,
    qui lit `DoclingSettings` (`images.py`). Les deux lisaient les memes
    variables et portaient les memes defauts, donc il n'y avait **aucune
    consequence** — et rien ne gardait leur accord.

    `mesure` le 2 septembre 2026 sur le code livre par le lot 4, mutation
    appliquee puis revoquee, texte verifie change :

    ==================================================== ================
    mutation                                             suite entiere
    ==================================================== ================
    `PipelineSettings.minio_bucket` -> "autre-bucket"    VERTE, 847 tests
    `PipelineSettings.minio_endpoint` -> "ailleurs:9000" VERTE, 847 tests
    ==================================================== ================

    **Une image televersee dans un bucket et publiee sous un autre est un objet
    qui existe et une adresse qui rend 404** — la panne est silencieuse : le
    televersement reussit, l'adresse est ecrite dans le graphe, et seul l'agent
    la voit echouer.

    **CE QUI A CHANGE AU LOT « SANS-MINIO » : L'ACCORD N'EST PLUS VERIFIE, IL
    EST STRUCTUREL.** Les quatre champs etaient DECLARES DEUX FOIS ; ils vivent
    desormais a un seul site, `src/reglages_s3.py`, dont les deux classes
    heritent. Deux declarations peuvent diverger, une seule ne le peut pas. Le
    premier test tient cette structure ; les deux suivants restent, parce
    qu'une redeclaration dans une sous-classe masquerait le champ herite sans
    toucher au module partage.
    """

    COUPLES = ("s3_endpoint", "s3_bucket", "s3_access_key", "s3_secret_key")

    def test_les_deux_classes_heritent_du_site_unique(self) -> None:
        """LE GARDE STRUCTUREL, et il passe en premier.

        Une sous-classe qui cesserait d'heriter reprendrait ses propres champs,
        et les deux tests suivants redeviendraient le seul filet.
        """
        from src.docling_service.settings import DoclingSettings
        from src.pipeline.settings import PipelineSettings
        from src.reglages_s3 import ReglagesDuStockageObjet

        for classe in (PipelineSettings, DoclingSettings):
            assert issubclass(classe, ReglagesDuStockageObjet), (
                f"{classe.__name__} ne tient plus ses reglages de stockage de "
                "`src/reglages_s3.py` : les quatre champs sont redeclares "
                "quelque part, et deux declarations finissent par diverger "
                "(registre 4.29.b)"
            )

    @pytest.mark.parametrize("champ", COUPLES)
    def test_les_defauts_sont_les_memes(self, champ: str, endpoint_declare) -> None:
        """Un defaut qui deriverait d'un cote rougit ici."""
        from src.docling_service.settings import DoclingSettings
        from src.pipeline.settings import PipelineSettings

        cote_pipeline = getattr(PipelineSettings(_env_file=None), champ)
        cote_docling = getattr(DoclingSettings(_env_file=None), champ)

        assert cote_pipeline == cote_docling, (
            f"« {champ} » vaut {cote_pipeline!r} pour le pipeline, qui TELEVERSE "
            f"l'objet, et {cote_docling!r} pour le service, qui PUBLIE son "
            "adresse. L'objet existerait et son adresse rendrait 404, sans "
            "aucune erreur (registre 4.29.b)"
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
        monkeypatch.setenv("S3_ENDPOINT", ENDPOINT_TEMOIN)
        monkeypatch.setenv(champ.upper(), temoin)

        cote_pipeline = getattr(PipelineSettings(_env_file=None), champ)
        cote_docling = getattr(DoclingSettings(_env_file=None), champ)

        assert cote_pipeline == temoin, (
            f"le pipeline ne lit pas {champ.upper()} : il a rendu {cote_pipeline!r}"
        )
        assert cote_docling == temoin, (
            f"le service ne lit pas {champ.upper()} : il a rendu {cote_docling!r}"
        )

    @pytest.mark.parametrize("champ", COUPLES)
    def test_l_ancienne_variable_n_est_plus_lue(self, champ: str, monkeypatch) -> None:
        """Un `.env` reste a l'ancien nom ne doit RIEN faire, et surtout pas discretement.

        C'est le pendant du renommage : si `MINIO_BUCKET` continuait d'etre lu
        par un alias oublie, l'ancien `.env` resterait vivant et personne ne
        saurait lequel des deux noms decide.
        """
        from src.docling_service.settings import DoclingSettings
        from src.pipeline.settings import PipelineSettings

        monkeypatch.setenv("S3_ENDPOINT", ENDPOINT_TEMOIN)
        ancien = champ.upper().replace("S3_", "MINIO_", 1)
        monkeypatch.setenv(ancien, "valeur-de-l-ancien-nom")

        for classe in (PipelineSettings, DoclingSettings):
            assert getattr(classe(_env_file=None), champ) != "valeur-de-l-ancien-nom", (
                f"{classe.__name__} lit encore {ancien}"
            )
