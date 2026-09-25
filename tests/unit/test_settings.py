"""Tests unitaires pour les settings pydantic-settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.pipeline.settings import PipelineSettings
from src.reglages_s3 import MESSAGE_ENDPOINT_MANQUANT

# L'adresse du stockage objet n'a pas de defaut : toute construction de
# reglages en exige une. Cette valeur ne designe rien de joignable, et aucun
# test de ce fichier n'ouvre de connexion.
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
    """`S3_ENDPOINT` n'a pas de valeur par defaut, dans aucune des deux classes.

    Motif : `python -m src.wipe_stores` lit ces reglages et vide le bucket
    qu'ils designent. Lance dans un environnement sans la variable (shell sans
    `.env`, conteneur recree sans elle, cron), un defaut en dur lui ferait
    purger un autre stockage que celui en service, en annoncant une purge
    reussie. Sans defaut, le demarrage echoue.

    Les deux classes (`PipelineSettings`, `DoclingSettings`) sont testees, car
    les deux construisent un client.
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
        """Sans la variable, la construction echoue en nommant le champ."""
        monkeypatch.delenv("S3_ENDPOINT", raising=False)

        with pytest.raises(ValidationError) as leve:
            self._classe(chemin)(_env_file=None)

        assert "s3_endpoint" in str(leve.value).lower(), (
            f"l'echec ne nomme pas le champ en cause : {leve.value}"
        )

    @pytest.mark.parametrize("chemin", CLASSES)
    def test_un_s3_endpoint_vide_echoue_aussi(self, chemin, monkeypatch):
        """Une variable declaree vide est refusee elle aussi.

        Un `.env` contenant « S3_ENDPOINT= » (la forme de `.env.example` non
        rempli) donne une chaine vide, qu'un champ simplement « requis »
        accepterait.
        """
        monkeypatch.setenv("S3_ENDPOINT", "   ")

        with pytest.raises(ValidationError):
            self._classe(chemin)(_env_file=None)

    @pytest.mark.parametrize("chemin", CLASSES)
    def test_le_message_dit_quoi_faire(self, chemin, monkeypatch):
        """Le message de refus nomme la variable et le risque qu'elle couvre."""
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
        """Controle positif : sans lui, une classe inconstructible passerait les tests."""
        monkeypatch.setenv("S3_ENDPOINT", ENDPOINT_TEMOIN)

        reglages = self._classe(chemin)(_env_file=None)

        assert reglages.s3_endpoint == ENDPOINT_TEMOIN

    def test_aucun_reglage_ne_nomme_plus_le_stockage_retire(self, monkeypatch):
        """Aucun champ « minio_* » ne subsiste.

        Un tel champ lirait encore `MINIO_ENDPOINT`, et un ancien `.env`
        continuerait d'agir sans que rien ne le signale.
        """
        monkeypatch.setenv("S3_ENDPOINT", ENDPOINT_TEMOIN)

        for chemin in self.CLASSES:
            champs = self._classe(chemin)(_env_file=None).model_dump()
            restes = [nom for nom in champs if "minio" in nom.lower()]
            assert restes == [], f"{chemin} porte encore {restes}"


class TestLesIdentifiantsDuStockageObjetNOntAucunDefaut:
    """Les deux identifiants n'ont pas de defaut non plus (registre 4.28.b).

    Avec un defaut vide, un service qui ne recoit pas les variables construit un
    client aux identifiants vides. Rien n'echoue au demarrage : les
    televersements d'images HTML rendent 403 plus tard, un par un, et les images
    manquent au corpus. Un identifiant absent doit donc faire echouer le
    demarrage, comme l'adresse.
    """

    CLASSES = TestLAdresseDuStockageObjetNAAucunDefaut.CLASSES
    CHAMPS = (("S3_ACCESS_KEY", "s3_access_key"), ("S3_SECRET_KEY", "s3_secret_key"))

    @pytest.mark.parametrize("chemin", CLASSES)
    @pytest.mark.parametrize(("variable", "champ"), CHAMPS)
    def test_sans_identifiant_la_construction_echoue(self, chemin, variable, champ, monkeypatch):
        """Sans l'identifiant, la construction echoue."""
        monkeypatch.delenv(variable, raising=False)

        with pytest.raises(ValidationError) as leve:
            TestLAdresseDuStockageObjetNAAucunDefaut._classe(chemin)(_env_file=None)

        message = str(leve.value)
        assert champ in message.lower(), f"l'echec ne nomme pas le champ : {message}"
        assert variable in message, f"le message ne nomme pas la variable : {message}"

    @pytest.mark.parametrize("chemin", CLASSES)
    @pytest.mark.parametrize(("variable", "champ"), CHAMPS)
    def test_un_identifiant_vide_echoue_aussi(self, chemin, variable, champ, monkeypatch):
        """« S3_ACCESS_KEY= » (valeur vide) est refuse aussi."""
        monkeypatch.setenv(variable, "  ")

        with pytest.raises(ValidationError):
            TestLAdresseDuStockageObjetNAAucunDefaut._classe(chemin)(_env_file=None)

    @pytest.mark.parametrize("chemin", CLASSES)
    def test_des_identifiants_declares_passent(self, chemin, monkeypatch):
        """Controle positif : sans lui, une classe inconstructible passerait les tests."""
        monkeypatch.setenv("S3_ACCESS_KEY", "cle-temoin")
        monkeypatch.setenv("S3_SECRET_KEY", "secret-temoin")  # pragma: allowlist secret

        reglages = TestLAdresseDuStockageObjetNAAucunDefaut._classe(chemin)(_env_file=None)

        assert reglages.s3_access_key == "cle-temoin"
        assert reglages.s3_secret_key == "secret-temoin"  # pragma: allowlist secret


class TestLesDeuxClassesDeReglagesSAccordentSurLeStockageObjet:
    """Deux classes de reglages decident du meme objet (registre 4.29.b).

    L'objet est televerse avec `PipelineSettings.s3_bucket` et `.s3_endpoint`
    (`media.py`) ; son adresse publiee est construite par `images.object_url`,
    qui lit `DoclingSettings` (`images.py`). Si les deux divergent, l'image est
    televersee dans un bucket et publiee sous un autre : le televersement
    reussit, l'adresse est ecrite dans le graphe, et seul l'agent obtient un 404.

    Les quatre champs sont declares une seule fois, dans `src/reglages_s3.py`,
    dont les deux classes heritent. Le premier test verifie cet heritage ; les
    suivants verifient les valeurs, car une redeclaration dans une sous-classe
    masquerait le champ herite sans toucher au module partage.
    """

    COUPLES = ("s3_endpoint", "s3_bucket", "s3_access_key", "s3_secret_key")

    def test_les_deux_classes_heritent_du_site_unique(self) -> None:
        """Les deux classes heritent de `ReglagesDuStockageObjet`.

        Une sous-classe qui cesserait d'heriter redeclarerait ses propres
        champs, et seuls les tests suivants detecteraient une divergence.
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
        """Les deux classes ont les memes defauts."""
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
        """Les deux classes lisent la meme variable d'environnement.

        L'egalite des defauts ne suffit pas : les deux classes pourraient avoir
        le meme defaut et lire deux variables differentes. Declarer la variable
        ne changerait alors qu'une des deux.
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
        """Une variable a l'ancien nom (`MINIO_*`) n'a aucun effet.

        Si `MINIO_BUCKET` etait encore lu par un alias oublie, un ancien `.env`
        continuerait d'agir, et on ne saurait plus lequel des deux noms decide.
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
