"""Tests unitaires pour la factory Dagster (assets, jobs, sensors par source)."""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager

from dagster import (
    AssetKey,
    DagsterInstance,
    DefaultSensorStatus,
    Definitions,
    SensorEvaluationContext,
    SkipReason,
    build_sensor_context,
    sensor,
)
from dagster._core.storage.tags import RUN_KEY_TAG, SENSOR_NAME_TAG
from dagster._core.test_utils import create_run_for_test

from src.docling_service.jobs import Job
from src.pipeline.factory import PREFIXE_REINGESTION, _record_metadata, build_source
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


class TestPolitiqueDeReprise:
    def test_asset_extraction_a_une_politique_de_reprise(self):
        # Sur une ingestion de plusieurs heures sans surveillance, un
        # redemarrage du service ne doit pas laisser des partitions rouges.
        built = build_source(_pdf_source())
        extraction = next(a for a in built.assets if a.key.path[-1] == "extracted_document")
        policy = extraction.op.retry_policy
        assert policy is not None
        assert policy.max_retries == 2

    def test_toutes_les_sources_couvertes(self):
        for source in (_pdf_source(), _md_source(), _html_source()):
            built = build_source(source)
            extraction = next(a for a in built.assets if a.key.path[-1] == "extracted_document")
            assert extraction.op.retry_policy is not None


class TestLesSensorsDIngestionSontLivresArmes:
    """Tout le pipeline est inerte au deploiement si ces sensors arrivent a l'arret.

    Un sensor sans ``default_status`` est livre STOPPED : Dagster le charge, il
    apparait dans l'interface, et il ne tourne jamais. Aucun fichier depose n'est
    alors ingere, et rien ne rougit — la panne est parfaitement muette.

    ``factory.py`` porte la ligne ``default_status=DefaultSensorStatus.RUNNING``
    et rien ne la gardait : la retirer laissait toute la suite verte. Le meme
    garde existait pour le seul sensor de reindexation
    (``test_reindex_job.py::TestLeSensorEstLivreArme``) ; il est ici decline aux
    sensors de source.

    Les assertions portent sur l'objet PRODUIT par ``build_source`` et sur celui
    que ``definitions.py`` livre reellement, jamais sur la presence du mot dans
    la source. Et elles portent sur TOUTES les sources declarees, pas sur une :
    un harnais qui n'appelle la fabrique qu'avec une source laisserait les deux
    autres sans garde — c'est le defaut que ``3603492`` a du corriger sur le
    ``max()`` multi-sources.
    """

    # Les sources declarees aujourd'hui dans ``sources.yaml``. Borne INFERIEURE,
    # jamais une egalite : une quatrieme source doit etre couverte
    # automatiquement par les boucles ci-dessous, tandis que la disparition
    # silencieuse de l'une de ces trois doit rougir. Une egalite serait une
    # phrase d'exhaustivite, donc un defaut en attente.
    #
    # Chacun des deux tests ci-dessous porte SA PROPRE borne, en ligne, sur la
    # collection qu'il parcourt : `sources` pour le premier, `livres` pour le
    # second. C'est la seule place ou une borne garde quelque chose — un test de
    # borne separe reconstruit son propre harnais et reste vert quoi qu'il arrive
    # a celui des autres.
    SOURCES_ATTENDUES = {"pdfs", "livres_html", "markdown"}

    def test_chaque_source_declaree_est_livree_armee(self):
        sources = load_sources()
        # La borne est EN LIGNE, et elle porte sur la liste que la boucle
        # ci-dessous parcourt reellement. Un test de borne separe, qui appelait
        # `load_sources()` de son cote, ne gardait rien : forcer `sources` a une
        # liste vide ici et retirer cette ligne laissait 551 tests VERTS
        # (`mesure`, 31 aout 2026). Il etait vert des deux cotes du defaut, parce
        # qu'il n'observait jamais ce harnais-ci.
        assert {source.name for source in sources} >= self.SOURCES_ATTENDUES
        for source in sources:
            built = build_source(source)
            assert built.sensor.default_status is DefaultSensorStatus.RUNNING, (
                f"le sensor de la source « {source.name} » est livre a l'arret"
            )

    def test_les_sensors_livres_par_les_definitions_sont_armes(self):
        # C'est l'objet reellement charge par Dagster au demarrage : la fabrique
        # peut etre juste et le cablage oublier une source.
        from src.pipeline.definitions import defs

        attendus = {f"{nom}_sensor" for nom in self.SOURCES_ATTENDUES}
        livres = {capteur.name: capteur for capteur in defs.sensors}
        assert set(livres) >= attendus
        for nom in sorted(attendus):
            assert livres[nom].default_status is DefaultSensorStatus.RUNNING, (
                f"le sensor « {nom} » est livre a l'arret par definitions.py"
            )

    def test_l_arme_ne_vient_pas_du_defaut_de_dagster(self):
        # Sinon les assertions ci-dessus seraient vraies sans que la ligne
        # existe, et elles resteraient vertes si Dagster changeait sa valeur par
        # defaut : elles seraient vertes des deux cotes du defaut. Dagster livre
        # bien STOPPED par defaut — c'est ce que ce temoin constate.
        @sensor(name="temoin_sans_default_status", job_name="pdfs_job")
        def temoin(context: SensorEvaluationContext) -> SkipReason:
            return SkipReason("temoin")

        assert temoin.default_status is not DefaultSensorStatus.RUNNING


# --- Contexte Dagster bouchonne -----------------------------------------------
# Les assets sont atteints par `build_source(...).assets[n].op.compute_fn.
# decorated_fn`, c'est-a-dire le corps REELLEMENT livre, a travers l'objet que
# `definitions.py` expedie. C'est la meme discipline que
# `TestLesSensorsDIngestionSontLivresArmes`, qui asserte sur `build_source(...)
# .sensor` et jamais sur la presence d'un mot dans la source. Un contexte
# `build_asset_context` ne convient pas : `add_output_metadata` y leve
# `DagsterInvalidPropertyError` en invocation directe (`mesure`).


class JournalEspion:
    def __init__(self) -> None:
        self.avertissements: list[str] = []
        self.infos: list[str] = []

    def warning(self, message: str) -> None:
        self.avertissements.append(str(message))

    def info(self, message: str) -> None:
        self.infos.append(str(message))


class ContexteEspion:
    """Contexte d'asset bouchonne qui retient les metadonnees publiees."""

    def __init__(self, partition_key: str) -> None:
        self.partition_key = partition_key
        self.log = JournalEspion()
        self.metadonnees: dict[str, object] = {}

    def add_output_metadata(self, metadata: dict[str, object]) -> None:
        self.metadonnees.update(metadata)


def _asset_par_nom(source, nom: str):
    """Le corps livre de l'asset nomme, pris sur l'objet que la fabrique rend."""
    definitions = build_source(source)
    for asset_def in definitions.assets:
        if asset_def.key.path[-1] == nom:
            return asset_def.op.compute_fn.decorated_fn
    raise AssertionError(f"asset {nom!r} absent de {source.name}")


class TestLeNettoyagePublieCeQuIlAJete:
    """Registre 4.6 : ni `precleaned_text_chars` ni le ratio n'etaient publies.

    `min_text_ratio = 0.05` accepte un candidat qui ne conserve que 5 % du texte.
    Les metadonnees Dagster portaient `text_chars` sans aucun denominateur : dans
    l'interface, un chapitre ampute a 5 % et un chapitre nettoye a 99,8 %
    affichaient tous deux un nombre, et rien ne les distinguait.
    """

    CONTENU = "Du contenu reel qui doit survivre au nettoyage. " * 40
    HTML = (
        "<html><head><title>Un chapitre</title></head><body>"
        "<nav>menu</nav><article><h1>Un chapitre</h1><p>" + CONTENU + "</p></article>"
        "</body></html>"
    )

    def _executer(self, tmp_path, monkeypatch, html: str = ""):
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        source = [s for s in load_sources() if s.type == "html"][0]
        source = source.model_copy(update={"cleaning": source.cleaning.model_copy()})
        source.cleaning.export_images = False

        cle = "livre/chapitre.html"
        chemin = tmp_path / cle
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(html or self.HTML, encoding="utf-8")

        contexte = ContexteEspion(cle)
        _asset_par_nom(source, "cleaned_html")(contexte)
        return contexte

    def test_les_metadonnees_portent_le_denominateur_et_le_ratio(self, tmp_path, monkeypatch):
        try:
            contexte = self._executer(tmp_path, monkeypatch)
        finally:
            get_settings.cache_clear()

        assert "precleaned_text_chars" in contexte.metadonnees, contexte.metadonnees
        assert "text_ratio" in contexte.metadonnees, contexte.metadonnees
        assert contexte.metadonnees["precleaned_text_chars"] > 0
        assert 0.0 < contexte.metadonnees["text_ratio"] <= 1.0

    def test_les_metadonnees_historiques_sont_conservees(self, tmp_path, monkeypatch):
        """LE TEMOIN : ajouter deux cles ne doit pas en retirer cinq."""
        try:
            contexte = self._executer(tmp_path, monkeypatch)
        finally:
            get_settings.cache_clear()

        for cle in ("strategy", "raw_bytes", "cleaned_bytes", "text_chars", "images_exported"):
            assert cle in contexte.metadonnees, f"{cle} a disparu des metadonnees"

    def test_le_ratio_publie_est_celui_du_bilan(self, tmp_path, monkeypatch):
        """Le ratio publie n'est pas recalcule a cote : c'est celui du bilan.

        Sans cette assertion, deux calculs du meme rapport pourraient diverger —
        et une metadonnee de perte qui se trompe est pire qu'absente.
        """
        try:
            contexte = self._executer(tmp_path, monkeypatch)
        finally:
            get_settings.cache_clear()

        attendu = contexte.metadonnees["text_chars"] / contexte.metadonnees["precleaned_text_chars"]
        assert contexte.metadonnees["text_ratio"] == attendu


class TestUnDocumentEcarteNeRessembleePlusAUnDocumentVide:
    """Registre 4.10 : un doublon exact rendait une partition VERTE a zero element.

    `extraction.extract` retourne `{"elements": 0, "chunks": 0, "duplicate_of":
    ...}` quand il reconnait un fichier deja ingere. Mais `_record_metadata` ne
    publiait que `elements`, `chunks`, `pages` et `elapsed_seconds` : dans
    l'interface Dagster, un document ECARTE et un document ingere VIDE affichaient
    exactement la meme chose — quatre zeros. Le premier est le comportement voulu,
    le second est une panne, et rien ne les distinguait.

    Le constat nomme cinq cles absentes : `duplicate_of`, `pages_skipped`, `ocr`,
    `language` et `failed_batches`. Les cinq sont publiees.
    """

    # LA FORME EST CELLE QUE LA PRODUCTION FABRIQUE, et elle a failli m'echapper.
    # `main._run_extraction` fait `job.report(**result)`, et `Job.report` met tout
    # dans `progress` : les cinq cles du constat arrivent donc DANS `progress` et
    # non au premier niveau du `snapshot()`. Une fixture qui les aurait posees au
    # premier niveau aurait rendu ce garde VERT sur un chemin de production casse.
    # D'ou `_snapshot_reel` ci-dessous, qui construit la forme par le vrai `Job`.
    BILAN_DOUBLON_RENDU_PAR_EXTRACT = {
        "elements": 0,
        "chunks": 0,
        "pages": 0,
        "duplicate_of": "htms/MLOps with Databricks/Preface.html",
        "type_file": "html",
    }
    BILAN_PDF_RENDU_PAR_EXTRACT = {
        "elements": 3750,
        "chunks": 4365,
        "pages": 71,
        "pages_skipped": 6,
        "ocr": False,
        "language": "en",
        "failed_batches": ["31-35 (RuntimeError: page illisible)"],
        "type_file": "pdf",
    }

    @staticmethod
    def _snapshot_reel(bilan: dict[str, object]) -> dict[str, object]:
        """Le bilan tel que Dagster le recoit, fabrique par le vrai `Job`.

        C'est la composition qui compte : `_record_metadata` lit un `snapshot()`,
        pas le retour d'`extract`. Les tester separement laisserait passer un
        desaccord sur l'emplacement des cles.
        """
        job = Job(id="essai", filepath="/opt/dagster/app/Datas/x")
        job.report(**bilan)
        return job.snapshot()

    def test_un_doublon_est_nomme_dans_les_metadonnees(self):
        contexte = ContexteEspion("livre/chapitre.html")
        _record_metadata(contexte, self._snapshot_reel(self.BILAN_DOUBLON_RENDU_PAR_EXTRACT))

        assert contexte.metadonnees.get("duplicate_of") == (
            "htms/MLOps with Databricks/Preface.html"
        ), contexte.metadonnees

    def test_un_document_ingere_ne_porte_pas_de_duplicate_of(self):
        """LE TEMOIN. Sans lui, publier `duplicate_of` en dur passerait.

        La cle ne doit apparaitre que sur un document reellement ecarte : une
        cle presente et vide sur les 23 documents redonnerait exactement le
        defaut, par l'autre bout.
        """
        contexte = ContexteEspion("pdfs/livre.pdf")
        _record_metadata(contexte, self._snapshot_reel(self.BILAN_PDF_RENDU_PAR_EXTRACT))

        assert "duplicate_of" not in contexte.metadonnees, contexte.metadonnees

    def test_les_quatre_autres_cles_du_constat_sont_publiees(self):
        contexte = ContexteEspion("pdfs/livre.pdf")
        _record_metadata(contexte, self._snapshot_reel(self.BILAN_PDF_RENDU_PAR_EXTRACT))

        assert contexte.metadonnees["pages_skipped"] == 6
        assert contexte.metadonnees["ocr"] is False
        assert contexte.metadonnees["language"] == "en"
        assert contexte.metadonnees["failed_batches"] == 1

    def test_les_lots_en_echec_sont_comptes_et_nommes(self):
        """Un lot PDF en echec doit se voir dans les metadonnees, pas seulement
        dans le rouge du run : c'est le compteur du 4.1, cote Dagster."""
        contexte = ContexteEspion("pdfs/livre.pdf")
        _record_metadata(contexte, self._snapshot_reel(self.BILAN_PDF_RENDU_PAR_EXTRACT))

        assert contexte.metadonnees["failed_batches"] == 1
        assert "31-35" in str(contexte.metadonnees["failed_batches_detail"])

    def test_les_quatre_metadonnees_historiques_survivent(self):
        """LE TEMOIN : ajouter cinq cles ne doit pas en retirer quatre."""
        contexte = ContexteEspion("pdfs/livre.pdf")
        _record_metadata(contexte, self._snapshot_reel(self.BILAN_PDF_RENDU_PAR_EXTRACT))

        assert contexte.metadonnees["elements"] == 3750
        assert contexte.metadonnees["chunks"] == 4365
        assert contexte.metadonnees["pages"] == 71
        assert contexte.metadonnees["elapsed_seconds"] >= 0.0

    def test_un_bilan_minimal_ne_leve_pas(self):
        """Le cas que le code naturel fait planter : un bilan sans aucune des
        cles optionnelles — c'est celui d'un HTML nominal."""
        contexte = ContexteEspion("livre/chapitre.html")
        _record_metadata(contexte, self._snapshot_reel({"elements": 12, "chunks": 30}))

        assert contexte.metadonnees["elements"] == 12
        assert "duplicate_of" not in contexte.metadonnees
        assert contexte.metadonnees["failed_batches"] == 0


# --- La reingestion, et le silence qui la cachait (registre 4.32.a) -----------


class JournalDuCapteur(logging.Handler):
    """Retient les lignes que le capteur ecrit reellement a son journal.

    `caplog` ne convient pas : le logger « dagster » ne propage pas
    (`mesure` — le message atteint stderr, `caplog.records` reste vide). C'est
    donc un handler attache au logger que le capteur utilise, c'est-a-dire a
    l'objet rendu par `context.log`, et non une interception du module logging.
    """

    def __init__(self) -> None:
        super().__init__()
        self.avertissements: list[str] = []
        self.infos: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.WARNING:
            self.avertissements.append(record.getMessage())
        else:
            self.infos.append(record.getMessage())


@contextmanager
def _ecoute(context):
    """Ecoute le journal du capteur le temps d'une evaluation."""
    journal = JournalDuCapteur()
    context.log.addHandler(journal)
    try:
        yield journal
    finally:
        context.log.removeHandler(journal)


def _corpus(tmp_path, fichiers: int = 3) -> list[str]:
    """Depose `fichiers` HTML sous `captures/`, et rend leurs cles de partition."""
    captures = tmp_path / "captures"
    captures.mkdir(exist_ok=True)
    cles = []
    for numero in range(fichiers):
        chemin = captures / f"page_{numero:02d}.html"
        chemin.write_text("<html></html>", encoding="utf-8")
        cles.append(f"captures/page_{numero:02d}.html")
    return cles


class TestLaReingestionSeDemandeEtNeSeDeclenchePasSeule:
    """Registre 4.32.a : le `run_key` etait deterministe sur `(source, partition, mtime)`.

    Dagster cherche un `run_key` consomme dans TOUT l'historique, sans borne de
    temps : un fichier dont le `mtime` n'a pas bouge ne pouvait donc JAMAIS etre
    reingere par le capteur. `mesure` le 2 septembre 2026, curseur vide et
    verifie a 0 entree : 23 `run_key` demandees, **0 run cree**,
    `skip_reason=None` — le site canonique du chiffre est la campagne,
    `documentation/campagnes/2026-09-02-premiere-campagne-de-reference.md`.

    Les trois tests qui suivent sont les trois questions que la reparation doit
    fermer, et le quatrieme est leur TEMOIN : sans lui, un `run_key` rendu
    aleatoire les rendrait tous verts en relancant l'ingestion a chaque tick.
    """

    ETIQUETTE = "2026-09-22-apres-purge"

    def _capteur(self, tmp_path, monkeypatch, fichiers: int = 3):
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        _corpus(tmp_path, fichiers)
        return build_source(_html_source(name="reing"))

    def test_sans_marqueur_un_corpus_inchange_ne_demande_rien(self, tmp_path, monkeypatch):
        """QUESTION 2 — ce qui garantit qu'une reingestion ne part pas toute seule.

        C'est le curseur, et rien d'autre. Il vit dans le stockage de l'instance,
        il survit au rechargement du code, et aucune ligne de `factory.py` ne
        l'efface. Dix ticks de suite sur un corpus inchange : zero demande.
        """
        try:
            built = self._capteur(tmp_path, monkeypatch)
            with DagsterInstance.ephemeral() as instance:
                context = build_sensor_context(instance=instance)
                premier = built.sensor(context)
                curseur = context.cursor
                suivants = []
                for _ in range(10):
                    suite = build_sensor_context(instance=instance, cursor=curseur)
                    suivants.append(built.sensor(suite))
                    curseur = suite.cursor or curseur

            assert len(premier.run_requests) == 3
            assert [len(r.run_requests) for r in suivants] == [0] * 10
        finally:
            get_settings.cache_clear()

    def test_le_marqueur_redemande_tout_le_corpus_avec_des_cles_neuves(self, tmp_path, monkeypatch):
        """QUESTION 1 — ce qui declenche une reingestion : le marqueur, pose a la main.

        L'assertion porte sur les CLES et non sur le nombre de demandes. C'est le
        point du 4.32.a : le capteur construisait deja ses 23 demandes, et
        Dagster n'en creait aucun run parce que les cles etaient celles de la
        premiere ingestion. Un test qui compte les demandes est vert des deux
        cotes du defaut.
        """
        try:
            built = self._capteur(tmp_path, monkeypatch)
            with DagsterInstance.ephemeral() as instance:
                premier = built.sensor(build_sensor_context(instance=instance))
                nominales = {r.run_key for r in premier.run_requests}

                marque = build_sensor_context(
                    instance=instance, cursor=f"{PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                reingestion = built.sensor(marque)

            neuves = {r.run_key for r in reingestion.run_requests}
            assert len(reingestion.run_requests) == 3
            assert neuves.isdisjoint(nominales), (neuves, nominales)
            assert all(self.ETIQUETTE in str(cle) for cle in neuves), neuves
        finally:
            get_settings.cache_clear()

    def test_le_marqueur_est_consomme_par_le_tick_qui_l_honore(self, tmp_path, monkeypatch):
        """QUESTION 3, premiere moitie — le geste ne vaut que pour UN tick.

        Sans cela, le marqueur resterait en place et le capteur relancerait
        l'ingestion complete a chaque tick, indefiniment : le defaut inverse de
        celui que ce lot ferme, et le pire des deux.
        """
        try:
            built = self._capteur(tmp_path, monkeypatch)
            with DagsterInstance.ephemeral() as instance:
                marque = build_sensor_context(
                    instance=instance, cursor=f"{PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                built.sensor(marque)
                apres = marque.cursor
                suite = build_sensor_context(instance=instance, cursor=apres)
                second = built.sensor(suite)

            assert apres and not apres.startswith(PREFIXE_REINGESTION), apres
            assert len(json.loads(apres)) == 3
            assert len(second.run_requests) == 0
        finally:
            get_settings.cache_clear()

    def test_un_corpus_vide_consomme_le_marqueur_lui_aussi(self, tmp_path, monkeypatch):
        """LE CAS QUE LE CODE NATUREL LAISSE BOUCLER.

        Le capteur n'ecrivait son curseur que s'il avait change. Sur un corpus
        vide, le curseur calcule vaut `{}` — egal au curseur de depart une fois
        le marqueur lu — donc le marqueur serait RESTE en place, et chaque tick
        l'aurait rejoue. Il n'y a pas de demande a perdre, mais le marqueur doit
        quand meme etre consomme.
        """
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        try:
            (tmp_path / "captures").mkdir()
            built = build_source(_html_source(name="vide"))
            with DagsterInstance.ephemeral() as instance:
                marque = build_sensor_context(
                    instance=instance, cursor=f"{PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                built.sensor(marque)

            assert marque.cursor == "{}", marque.cursor
        finally:
            get_settings.cache_clear()

    def test_un_marqueur_sans_etiquette_est_refuse_et_dit_pourquoi(self, tmp_path, monkeypatch):
        """L'etiquette n'est pas decorative : c'est elle qui distingue deux gestes.

        Sans elle, le second geste porterait les memes cles que le premier, et
        Dagster le refuserait EN SILENCE — exactement le defaut 4.32.a, rouvert
        par le geste cense le fermer. Le capteur refuse donc, et il le dit.
        """
        try:
            built = self._capteur(tmp_path, monkeypatch)
            with DagsterInstance.ephemeral() as instance:
                marque = build_sensor_context(instance=instance, cursor=PREFIXE_REINGESTION)
                with _ecoute(marque) as journal:
                    resultat = built.sensor(marque)

            assert len(resultat.run_requests) == 0
            assert marque.cursor in (None, PREFIXE_REINGESTION), marque.cursor
            assert any("etiquette" in ligne for ligne in journal.avertissements), (
                journal.avertissements
            )
        finally:
            get_settings.cache_clear()


class TestLeTickQuiPerdSesRunsLeDit:
    """Registre 4.32.a, seconde moitie : **22 runs perdus sans un mot**.

    Le tick qui perd ses runs porte `skip_reason=None` : le journal du daemon ne
    dit rien, et « Sensor function returned an empty result » n'apparait qu'aux
    ticks SUIVANTS, pour une autre raison. Sans cette ligne-ci, personne
    n'aurait jamais trouve le reste du constat.

    Le controle reproduit la regle du daemon, et non une approximation : il
    interroge `RunsFilter(tags={RUN_KEY_TAG: cle})` puis retient les runs dont
    `SENSOR_NAME_TAG` est celui du capteur — c'est mot pour mot
    `dagster/_daemon/sensor.py::fetch_existing_runs` (`mesure`, dagster 1.13.16,
    lignes 1290-1333).
    """

    ETIQUETTE = "geste-refait"

    def _instance_avec_les_cles(self, instance, cles, nom_du_capteur: str) -> None:
        """Consomme ces `run_key` comme le daemon les consomme : par des runs tagues."""
        for cle in cles:
            create_run_for_test(
                instance,
                job_name="reing_job",
                tags={RUN_KEY_TAG: str(cle), SENSOR_NAME_TAG: nom_du_capteur},
            )

    def test_des_cles_deja_consommees_sont_annoncees_avec_leur_compte(self, tmp_path, monkeypatch):
        """QUESTION 3, seconde moitie — le geste refait a l'identique.

        Deux fois le MEME marqueur : le second tick reconstruit les memes cles,
        Dagster n'en creera aucun run. Le capteur doit le dire, et dire COMBIEN.
        """
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        try:
            _corpus(tmp_path, 3)
            built = build_source(_html_source(name="reing"))
            with DagsterInstance.ephemeral() as instance:
                premier = build_sensor_context(
                    instance=instance, cursor=f"{PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                demande = built.sensor(premier)
                self._instance_avec_les_cles(
                    instance, [r.run_key for r in demande.run_requests], "reing_sensor"
                )

                second = build_sensor_context(
                    instance=instance, cursor=f"{PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                with _ecoute(second) as journal:
                    built.sensor(second)

            perdu = [ligne for ligne in journal.avertissements if "run_key" in ligne]
            assert perdu, journal.avertissements
            assert "3" in perdu[0], perdu[0]
        finally:
            get_settings.cache_clear()

    def test_des_cles_neuves_ne_declenchent_aucun_avertissement(self, tmp_path, monkeypatch):
        """LE TEMOIN. Sans lui, un avertissement pose en dur passerait le test ci-dessus.

        Un capteur qui crie a chaque tick est aussi muet qu'un capteur qui se
        tait : l'oeil s'habitue, et la ligne cesse d'etre lue.
        """
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        try:
            _corpus(tmp_path, 3)
            built = build_source(_html_source(name="reing"))
            with DagsterInstance.ephemeral() as instance:
                context = build_sensor_context(instance=instance)
                with _ecoute(context) as journal:
                    resultat = built.sensor(context)

            assert len(resultat.run_requests) == 3
            assert not [ligne for ligne in journal.avertissements if "run_key" in ligne], (
                journal.avertissements
            )
        finally:
            get_settings.cache_clear()

    def test_une_cle_consommee_par_un_autre_capteur_ne_compte_pas(self, tmp_path, monkeypatch):
        """Le daemon filtre sur `SENSOR_NAME_TAG`, et ce controle doit filtrer pareil.

        Sans ce filtre, le controle annoncerait des pertes qui n'en sont pas —
        une alerte fausse se desapprend aussi vite qu'une alerte absente.
        """
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        try:
            _corpus(tmp_path, 3)
            built = build_source(_html_source(name="reing"))
            with DagsterInstance.ephemeral() as instance:
                sonde = build_sensor_context(instance=instance)
                cles = [r.run_key for r in built.sensor(sonde).run_requests]
                self._instance_avec_les_cles(instance, cles, "un_autre_sensor")

                context = build_sensor_context(instance=instance)
                with _ecoute(context) as journal:
                    built.sensor(context)

            assert not [ligne for ligne in journal.avertissements if "run_key" in ligne], (
                journal.avertissements
            )
        finally:
            get_settings.cache_clear()


class TestLaCleNominaleEstInchangee:
    """CE QUI REND PREUVABLE LE PREMIER TICK APRES LA FUSION.

    Fusionner dans `main` est un deploiement : les conteneurs montent `src/`
    depuis le clone principal (`mesure` le 22 septembre 2026,
    `docker inspect`). Le premier tick apres la fusion ne doit rien faire — et
    il ne le peut que si, SANS marqueur, la cle construite est exactement celle
    que l'historique porte deja.

    Ce test fige donc la forme litterale de la cle nominale. Il rougit si un lot
    futur la touche, et c'est bien ce qu'on lui demande : la toucher relancerait
    l'ingestion complete du corpus au chargement du code.
    """

    def test_la_cle_sans_marqueur_est_source_partition_mtime(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        try:
            cles = _corpus(tmp_path, 2)
            built = build_source(_html_source(name="forme"))
            with DagsterInstance.ephemeral() as instance:
                resultat = built.sensor(build_sensor_context(instance=instance))

            attendues = {f"forme_{cle}_{os.path.getmtime(tmp_path / cle)}" for cle in cles}
            assert {r.run_key for r in resultat.run_requests} == attendues
        finally:
            get_settings.cache_clear()
