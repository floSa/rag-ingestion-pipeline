"""Tests unitaires pour la factory Dagster (assets, jobs, sensors par source)."""

from __future__ import annotations

import glob as globlib
import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path

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

from src.docling_service.elements import cleaned_path, cleaned_root
from src.docling_service.jobs import Job
from src.pipeline.factory import (
    PREFIXE_REINGESTION,
    CurseurIllisibleError,
    _record_metadata,
    build_source,
)
from src.pipeline.settings import get_settings
from src.pipeline.sources import SourceConfig, load_sources
from src.wipe_stores import purge_cleaned


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


class TestCeQueLaPurgeDuNettoyeRetireVRAIMENT:
    """Registre 4.33.a — le `README` et `wipe_stores` justifiaient un `rmtree` par
    un mecanisme qui n'existe pas.

    La phrase, mot pour mot : « l'asset `cleaned_html` ne se rematerialise pas si
    son fichier existe deja ». Elle portait a elle seule la necessite du `rmtree`
    le plus dangereux du depot — celui dont le registre 4.29.a raconte qu'un
    reglage mal pose y emportait 24 des 25 fichiers du corpus versionne.

    Les DEUX NATURES sont gardees ici, et une seule serait creuse :

    - ce qui est REECRIT : une destination au contenu different est remplacee.
      C'est la phrase du `README` mise en defaut, et le garde qui rougit si un
      court-circuit « le fichier existe » revenait ;
    - ce qui SURVIT : la copie nettoyee d'un document retire du corpus. C'est la
      seule chose que la purge retire, donc la vraie raison de la garder.

    Le troisieme test borne la premiere : si le capteur voyait `.cleaned/`, les
    orphelins reviendraient par le glob et l'analyse ci-dessus tomberait.
    """

    CONTENU = "Du contenu reel qui doit survivre au nettoyage. " * 40
    PERIME = "<html><body><p>PERIME : pointe des objets MinIO supprimes</p></body></html>"

    def _asset(self, tmp_path, monkeypatch):
        """L'asset `cleaned_html` livre, arme sur un faux corpus jetable."""
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        source = [s for s in load_sources() if s.type == "html"][0]
        source = source.model_copy(update={"cleaning": source.cleaning.model_copy()})
        source.cleaning.export_images = False
        return source, _asset_par_nom(source, "cleaned_html")

    def _deposer(self, tmp_path, cle: str, titre: str) -> None:
        chemin = tmp_path / cle
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(
            f"<html><head><title>{titre}</title></head><body><nav>menu</nav>"
            f"<article><h1>{titre}</h1><p>{self.CONTENU}</p></article></body></html>",
            encoding="utf-8",
        )

    def test_une_destination_perimee_est_reecrite_par_la_materialisation_suivante(
        self, tmp_path, monkeypatch
    ):
        """LA PHRASE DU `README` MISE EN DEFAUT, et le garde qui la tient fausse.

        Le temoin est le contenu, pas l'horodatage : une destination REMPLIE d'un
        contenu perime doit avoir disparu. Asserter seulement « le fichier existe
        encore » serait vert des deux cotes du defaut.

        Mutation qui doit faire rougir ce test : un court-circuit
        `if dest_path.exists(): return` en tete de `clean_html_file`.
        """
        cle = "htms/livre/chapitre.html"
        try:
            _, asset = self._asset(tmp_path, monkeypatch)
            self._deposer(tmp_path, cle, "Un chapitre")
            asset(ContexteEspion(cle))
            destination = cleaned_path(tmp_path, cle)
            premier = destination.read_text(encoding="utf-8")

            destination.write_text(self.PERIME, encoding="utf-8")
            asset(ContexteEspion(cle))
            second = destination.read_text(encoding="utf-8")
        finally:
            get_settings.cache_clear()

        assert "PERIME" not in second, second[:200]
        assert second == premier, "le second nettoyage devait rendre le meme octet"

    def test_la_copie_nettoyee_d_un_document_retire_du_corpus_survit(self, tmp_path, monkeypatch):
        """CE QUE LA PURGE RETIRE, ET ELLE SEULE.

        Deux documents nettoyes, la source de l'un retiree, l'autre
        rematerialise : l'orphelin est toujours la. Aucun chemin du pipeline ne
        l'efface — `cleaned_html` ne peut pas s'executer pour lui, son controle
        d'existence portant sur la SOURCE. Seul `purge_cleaned` le retire, et
        c'est ce que la seconde moitie de ce test asserte.

        Si ce test rougissait parce que l'orphelin a disparu tout seul, la raison
        ecrite a `purge_cleaned` aurait cesse d'etre vraie : c'est exactement ce
        qu'il est la pour dire.
        """
        reste = "htms/livre/reste.html"
        parti = "htms/livre/parti.html"
        try:
            _, asset = self._asset(tmp_path, monkeypatch)
            self._deposer(tmp_path, reste, "Chapitre qui reste")
            self._deposer(tmp_path, parti, "Chapitre qui part")
            asset(ContexteEspion(reste))
            asset(ContexteEspion(parti))

            (tmp_path / parti).unlink()
            asset(ContexteEspion(reste))

            orphelin = cleaned_path(tmp_path, parti)
            vivant = cleaned_path(tmp_path, reste)
            survivant = orphelin.exists()
            retires = purge_cleaned(cleaned_root(tmp_path), tmp_path)
        finally:
            get_settings.cache_clear()

        assert survivant, "l'orphelin devait survivre a la rematerialisation du corpus"
        assert retires == 2, retires
        assert not orphelin.exists(), "la purge devait retirer l'orphelin"
        assert not vivant.exists()

    def test_le_glob_de_la_source_ne_voit_jamais_le_repertoire_nettoye(self, tmp_path, monkeypatch):
        """LA BORNE de l'analyse ci-dessus : un orphelin n'est atteignable par rien.

        S'il l'etait, le capteur le rendrait a l'ingestion et la purge cesserait
        d'etre la seule issue. Deux choses l'en empechent, et ce test les tient
        toutes les deux : le glob est ancre sous le sous-repertoire de la source,
        et `.cleaned` porte un point de tete que `glob` n'ouvre jamais.
        """
        cle = "htms/livre/chapitre.html"
        try:
            source, asset = self._asset(tmp_path, monkeypatch)
            self._deposer(tmp_path, cle, "Un chapitre")
            asset(ContexteEspion(cle))

            vus = sorted(globlib.glob(str(tmp_path / source.glob), recursive=True))
            larges = sorted(globlib.glob(str(tmp_path / "**" / "*.html"), recursive=True))
        finally:
            get_settings.cache_clear()

        assert cleaned_path(tmp_path, cle).exists(), "le nettoyage n'a rien ecrit"
        attendu = [str(tmp_path / cle)]
        assert vus == attendu, vus
        assert larges == attendu, larges


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

    def test_l_etiquette_est_la_meme_avec_ou_sans_espace_apres_le_marqueur(
        self, tmp_path, monkeypatch
    ):
        """LE `.strip()` DE L'ETIQUETTE EST PORTEUR, ET RIEN NE LE GARDAIT.

        Le marqueur se pose **a la main**, dans un champ de saisie de l'interface
        Dagster ou sur une ligne de commande. « reingerer: 2026-09-22 » et
        « reingerer:2026-09-22 » sont le MEME geste pour celui qui les tape.
        Sans le `.strip()`, ce sont deux etiquettes differentes, donc deux jeux
        de cles de run differents — et la propriete que tout le 4.32.a repose
        dessus tombe : le geste refait a l'identique cesse d'etre bruyant, il
        reingere tout une seconde fois **en silence**, parce que l'espace en
        trop a rendu ses cles neuves. `mesure` : retirer ce `.strip()` laissait
        la suite entierement verte.

        La borne de ce garde est exacte, et elle corrige une lecture repandue :
        `reingerer:` suivi de SEULS espaces ne teste PAS ce `.strip()`-ci — le
        `curseur.strip()` de la ligne precedente a deja mange ces espaces, et
        l'etiquette est vide dans les deux cas. Ce qui est en jeu est la
        NORMALISATION d'une etiquette reelle, pas le refus du marqueur nu, que
        `test_un_marqueur_sans_etiquette_est_refuse_et_dit_pourquoi` garde.
        """
        try:
            built = self._capteur(tmp_path, monkeypatch)
            with DagsterInstance.ephemeral() as instance:
                serre = build_sensor_context(
                    instance=instance, cursor=f"{PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                lache = build_sensor_context(
                    instance=instance, cursor=f"{PREFIXE_REINGESTION}   {self.ETIQUETTE}"
                )
                cles_serrees = {r.run_key for r in built.sensor(serre).run_requests}
                cles_laches = {r.run_key for r in built.sensor(lache).run_requests}

            assert cles_serrees, cles_serrees
            assert cles_laches == cles_serrees, (cles_laches, cles_serrees)
        finally:
            get_settings.cache_clear()

    def test_le_marqueur_est_honore_avec_un_espace_de_tete(self, tmp_path, monkeypatch):
        """LE PREMIER `curseur.strip()`, ET IL ETAIT NU (H21).

        Deux `.strip()` vivent dans `_etiquette_de_reingestion`, et le test
        voisin ne garde que le SECOND — celui qui normalise l'etiquette. Le
        premier absorbe un espace de TETE, et coller une chaine dans un champ de
        l'interface Dagster est le geste manuel le plus banal qui soit. `mesure`
        le 22 septembre 2026 : le retirer laissait les 904 tests verts.

        CE QUE LA SUBSTITUTION PRODUIT, ET C'EST MESURE. Sur une instance dont
        l'historique porte deja les cles nominales — la production, donc — le
        curseur « ␣␣reingerer:2026-09-22 » cesse d'etre un ordre : il repart en
        curseur JSON, echoue a se decoder, et le capteur redemande le corpus
        avec les cles NOMINALES. Dagster n'en cree aucun run. Le geste
        **n'a pas lieu**, et il le dit — mais il le dit de travers, par
        « Invalid cursor format, resetting » puis « 3 demande(s) de run sur 3
        portent un run_key DEJA CONSOMME ». Aucune des deux lignes ne nomme
        l'espace, et le geste refait echoue a l'identique.

        L'assertion porte sur la FORME des cles, et non sur leur nombre : les
        trois demandes existent des deux cotes du defaut.
        """
        try:
            built = self._capteur(tmp_path, monkeypatch)
            with DagsterInstance.ephemeral() as instance:
                colle = build_sensor_context(
                    instance=instance, cursor=f"  {PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                cles = {r.run_key for r in built.sensor(colle).run_requests}
        finally:
            get_settings.cache_clear()

        attendues = {
            f"reing_captures/page_{numero:02d}.html_reingestion_{self.ETIQUETTE}"
            for numero in range(3)
        }
        assert cles == attendues, (cles, attendues)

    def test_le_marqueur_n_est_honore_qu_en_tete_du_curseur(self, tmp_path, monkeypatch):
        """`startswith` EST PORTEUR, ET `in` SURVIVAIT A TOUTE LA SUITE.

        Un curseur qui **contient** `reingerer:` sans commencer par lui n'est pas
        un ordre de reingestion : c'est un curseur mal forme, et le capteur doit
        le traiter comme tel — repartir du corpus avec les cles NOMINALES, que
        l'historique porte deja, donc sans rien relancer.

        Avec `in` a la place de `startswith`, le meme curseur est lu comme un
        ordre, et l'etiquette devient la decoupe a l'aveugle de dix caracteres —
        ici « er:2026-09-22-apres-purge ». Le capteur reingere alors **tout le
        corpus** sur un curseur que personne n'a voulu marquer. `mesure` : la
        substitution laissait la suite entierement verte.
        """
        try:
            built = self._capteur(tmp_path, monkeypatch)
            with DagsterInstance.ephemeral() as instance:
                egare = build_sensor_context(
                    instance=instance, cursor=f"# {PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                resultat = built.sensor(egare)
                cles = {r.run_key for r in resultat.run_requests}

            # Les cles se reconstruisent A LA MAIN, et surtout pas par un second
            # appel a `_corpus` : celui-ci REECRIT les fichiers, donc deplacerait
            # les `mtime` que cette assertion compare.
            attendues = {
                f"reing_captures/page_{numero:02d}.html_"
                f"{os.path.getmtime(tmp_path / f'captures/page_{numero:02d}.html')}"
                for numero in range(3)
            }
            assert cles == attendues, (cles, attendues)
            assert all(self.ETIQUETTE not in str(cle) for cle in cles), cles
        finally:
            get_settings.cache_clear()

    def test_le_tick_qui_honore_le_marqueur_le_confirme_au_journal(self, tmp_path, monkeypatch):
        """LA SEULE CONFIRMATION QUE L'OPERATEUR RECOIT, ET RIEN NE LA GARDAIT.

        Le geste est manuel et il est irreversible a l'echelle du corpus : celui
        qui l'a pose n'a que cette ligne pour savoir qu'il a ete **lu**, avec
        quelle etiquette et sur combien de fichiers.

        `mesure` : supprimer cette ligne laissait la suite verte — et faisait
        apparaitre a sa place « Invalid cursor format, resetting. », qui dit le
        CONTRAIRE de ce qui se passe. La seconde assertion garde donc aussi
        l'absence de ce message-la : un marqueur bien forme n'est pas un curseur
        invalide, et le journaliser ainsi apprendrait a l'operateur que son geste
        a echoue au moment meme ou il reussit.
        """
        try:
            built = self._capteur(tmp_path, monkeypatch)
            with DagsterInstance.ephemeral() as instance:
                marque = build_sensor_context(
                    instance=instance, cursor=f"{PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                with _ecoute(marque) as journal:
                    built.sensor(marque)

            confirmations = [
                ligne
                for ligne in journal.infos
                if self.ETIQUETTE in ligne and "Reingestion demandee" in ligne
            ]
            assert confirmations, journal.infos
            assert "les 3 fichiers" in confirmations[0], confirmations[0]
            assert not [ligne for ligne in journal.avertissements if "Invalid cursor" in ligne], (
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

        L'assertion est POSITIONNELLE, et ce n'est pas un detail de style. Elle
        a d'abord ete ecrite `"3" in perdu[0]` — or le message porte « {N}
        demande(s) de run sur {M} », et M vaut 3 ici : le DENOMINATEUR
        satisfaisait le test a lui seul, quel que soit le numerateur. `mesure` :
        remplacer `{len(perdues)}` par le litteral `zero` laissait la suite
        entierement verte. Un garde qui lit un chiffre sans savoir lequel ne
        garde rien. Le numerateur est desormais lu a sa place, et
        `test_le_compte_annonce_est_celui_des_perdues_et_non_du_total` le
        separe du denominateur en les rendant differents.
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
            assert perdu[0].startswith("3 demande(s) de run sur 3 "), perdu[0]
        finally:
            get_settings.cache_clear()

    def test_le_compte_annonce_est_celui_des_perdues_et_non_du_total(self, tmp_path, monkeypatch):
        """LE NUMERATEUR ET LE DENOMINATEUR SONT RENDUS DIFFERENTS, EXPRES.

        Tant que les deux valent 3, aucune assertion ne peut distinguer « le
        capteur compte ses pertes » de « le capteur recopie le total ». Ici
        **deux** cles sur trois ont ete consommees : le message doit dire 2 sur
        3. Le README et le registre 4.32.a promettent tous deux que le capteur
        le DIT *avec le nombre* ; c'est ce test-ci qui tient cette promesse, et
        aucun autre.
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
                cles = [r.run_key for r in built.sensor(premier).run_requests]
                assert len(cles) == 3, cles
                # DEUX seulement : la troisieme demande passera, les deux autres
                # sont perdues, et c'est 2 que le capteur doit annoncer.
                self._instance_avec_les_cles(instance, cles[:2], "reing_sensor")

                second = build_sensor_context(
                    instance=instance, cursor=f"{PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                with _ecoute(second) as journal:
                    built.sensor(second)

            perdu = [ligne for ligne in journal.avertissements if "run_key" in ligne]
            assert perdu, journal.avertissements
            assert perdu[0].startswith("2 demande(s) de run sur 3 "), perdu[0]
        finally:
            get_settings.cache_clear()

    def test_l_alerte_nomme_les_trois_premieres_cles_perdues(self, tmp_path, monkeypatch):
        """LE DIAGNOSTIC, ET IL ETAIT NU (H3).

        `perdues[:3]` n'etait garde par rien : `mesure` le 22 septembre 2026,
        le remplacer par `perdues[:0]` laissait les 904 tests verts. L'alerte
        annoncait alors son compte et **plus aucune cle** — le chiffre restait,
        le diagnostic etait ampute, et l'operateur n'avait plus de quoi savoir
        quelle partition avait ete perdue.

        Les DEUX bornes sont tenues ici, et la seconde sans la premiere serait
        creuse : l'alerte nomme **trois** cles quand quatre sont perdues, et ce
        sont les trois PREMIERES dans l'ordre des demandes. Une borne haute
        seule laisserait passer zero.
        """
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        try:
            _corpus(tmp_path, 4)
            built = build_source(_html_source(name="reing"))
            with DagsterInstance.ephemeral() as instance:
                premier = build_sensor_context(
                    instance=instance, cursor=f"{PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                cles = [r.run_key for r in built.sensor(premier).run_requests]
                assert len(cles) == 4, cles
                self._instance_avec_les_cles(instance, cles, "reing_sensor")

                second = build_sensor_context(
                    instance=instance, cursor=f"{PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                with _ecoute(second) as journal:
                    built.sensor(second)
        finally:
            get_settings.cache_clear()

        perdu = [ligne for ligne in journal.avertissements if "run_key" in ligne]
        assert perdu, journal.avertissements
        nommees = [cle for cle in cles if cle in perdu[0]]
        assert nommees == cles[:3], (nommees, cles)

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

    def test_une_instance_deja_peuplee_par_ce_capteur_ne_fait_pas_crier(
        self, tmp_path, monkeypatch
    ):
        """LE CAS ORDINAIRE DE TOUS LES JOURS, ET AUCUN TEST NE LE MONTAIT.

        Les deux temoins voisins ne discriminent pas : l'un part d'une instance
        **vierge**, l'autre filtre sur l'autre axe — un AUTRE capteur, pas
        d'AUTRES cles. Il manquait celui-ci : le capteur a deja des runs derriere
        lui, sous SES propres cles et SON propre nom, et la demande du jour porte
        des cles NEUVES.

        `mesure` : sans ce garde, remplacer `RunsFilter(tags={RUN_KEY_TAG: cle})`
        par `RunsFilter(tags={SENSOR_NAME_TAG: nom_du_capteur})` — la requete
        cesse alors de porter sur la cle — laissait la suite entierement verte.
        Le mutant leve une alerte FAUSSE a chaque tick des que le capteur a un
        seul run derriere lui, c'est-a-dire toujours en production. Et une alerte
        fausse se desapprend aussi vite qu'une alerte absente.
        """
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        try:
            _corpus(tmp_path, 3)
            built = build_source(_html_source(name="reing"))
            with DagsterInstance.ephemeral() as instance:
                # L'HISTORIQUE ORDINAIRE : la premiere ingestion a eu lieu, ses
                # trois runs portent les cles NOMINALES et le nom de CE capteur.
                nominal = build_sensor_context(instance=instance)
                deja = [r.run_key for r in built.sensor(nominal).run_requests]
                self._instance_avec_les_cles(instance, deja, "reing_sensor")

                # LA DEMANDE DU JOUR : une reingestion a etiquette neuve. Ses
                # cles n'existent nulle part dans l'historique.
                marque = build_sensor_context(
                    instance=instance, cursor=f"{PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                with _ecoute(marque) as journal:
                    resultat = built.sensor(marque)

            neuves = {r.run_key for r in resultat.run_requests}
            assert len(neuves) == 3, neuves
            assert neuves.isdisjoint(set(deja)), (neuves, deja)
            assert not [ligne for ligne in journal.avertissements if "run_key" in ligne], (
                journal.avertissements
            )
        finally:
            get_settings.cache_clear()


class TestLeCurseurAvanceEtLOrdreNeBougePas:
    """Deux bornes du capteur que la mutation a trouvees nues (registre 4.33, H7 et H13).

    Elles n'ont rien de commun sauf cela : chacune laissait les 904 tests verts,
    `mesure` le 22 septembre 2026, et chacune arme une reingestion perpetuelle
    ou un diagnostic qui bouge d'un tick a l'autre.
    """

    def _capteur(self, tmp_path, monkeypatch, fichiers: int = 3):
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        _corpus(tmp_path, fichiers)
        return build_source(_html_source(name="reing"))

    def test_un_fichier_modifie_voit_son_nouveau_mtime_ecrit_au_curseur(
        self, tmp_path, monkeypatch
    ):
        """H7 — `str(mtime)` et non `str(last_mtime or mtime)`.

        La substitution ne se voit pas au premier tick : `last_mtime` y est
        `None`, donc les deux expressions coincident. Elle ne se voit que sur un
        fichier **deja connu** et **modifie** — le capteur reecrit alors son
        ANCIEN mtime au curseur, donc le retrouve en retard au tick suivant, donc
        le redemande. A chaque tick, indefiniment, toutes les 30 secondes.

        Le troisieme tick est ce qui fait de ce test un garde et non une lecture :
        asserter la seule valeur du curseur dirait que le capteur a ECRIT le bon
        nombre, pas qu'il en a FINI avec ce fichier.
        """
        cle = "captures/page_00.html"
        try:
            built = self._capteur(tmp_path, monkeypatch)
            chemin = tmp_path / cle
            with DagsterInstance.ephemeral() as instance:
                premier = build_sensor_context(instance=instance)
                built.sensor(premier)
                curseur = premier.cursor

                # Le fichier est MODIFIE : contenu ET mtime, comme un vrai depot.
                chemin.write_text("<html>modifie</html>", encoding="utf-8")
                plus_tard = os.path.getmtime(chemin) + 10
                os.utime(chemin, (plus_tard, plus_tard))

                second = build_sensor_context(instance=instance, cursor=curseur)
                demandes = built.sensor(second).run_requests
                curseur_apres = second.cursor

                troisieme = build_sensor_context(instance=instance, cursor=curseur_apres)
                encore = built.sensor(troisieme).run_requests
        finally:
            get_settings.cache_clear()

        assert [r.partition_key for r in demandes] == [cle], demandes
        assert json.loads(curseur_apres)[cle] == str(plus_tard), curseur_apres
        assert encore == [], "le fichier modifie est redemande une seconde fois"

    def test_l_ordre_des_demandes_est_celui_du_tri_et_non_celui_du_disque(
        self, tmp_path, monkeypatch
    ):
        """H13 — `sorted(glob(...))` et non `list(glob(...))`.

        `glob` rend l'ordre de `os.scandir`, qui est celui du systeme de
        fichiers : il n'est ni trie, ni stable d'une machine ou d'un tick a
        l'autre. Sans le tri, l'ordre des demandes de run et celui des
        « premieres cles perdues » de l'alerte du 4.32.a changent sans que rien
        n'ait change — et un diagnostic qui bouge tout seul ne se compare pas
        d'un tick au suivant.

        Le desordre est POSE, il n'est pas espere : compter sur `scandir` pour
        rendre un ordre faux serait un test qui passe par accident. C'est
        `glob` qui est bouchonne — l'environnement — et non le capteur, qui
        reste celui qui produit l'ordre asserte.
        """
        try:
            built = self._capteur(tmp_path, monkeypatch, fichiers=5)
            vrai_glob = globlib.glob

            def glob_a_l_envers(motif, **kwargs):
                return list(reversed(sorted(vrai_glob(motif, **kwargs))))

            monkeypatch.setattr(globlib, "glob", glob_a_l_envers)
            with DagsterInstance.ephemeral() as instance:
                context = build_sensor_context(instance=instance)
                cles = [r.partition_key for r in built.sensor(context).run_requests]
        finally:
            get_settings.cache_clear()

        assert cles == sorted(cles), cles
        assert len(cles) == 5, cles


class TestUnCurseurJsonQuiNEstPasUnCurseurEchoueEnLeDisant:
    """Registre 4.33 — trois curseurs BIEN FORMES faisaient planter le tick.

    Antériorité verifiee : la ligne `float(last_mtime)` vient de `b157e84`,
    11 juin 2026. NON imputable au lot 8 — mais elle est sur le chemin du geste
    qu'il a ouvert, et le cas declencheur est exactement la maladresse que ce
    geste invite : poser le marqueur DANS le JSON au lieu de remplacer le
    curseur.

    **CE QUI N'EST PAS CHANGE, ET C'EST LE PLUS IMPORTANT.** Le tick echoue
    toujours, et il echoue encore au tick suivant : `update_cursor` n'est jamais
    atteint, le curseur fautif reste en place, et chaque tick echoue a son tour
    toutes les 30 secondes jusqu'a correction manuelle. Rattraper l'erreur pour
    « reinitialiser le curseur » aurait remplace cette sortie bruyante par un
    SILENCE qui redemande tout le corpus — le 4.32.a par l'autre bout. Ce qui
    change est ce que l'echec DIT.

    Le TEMOIN de cette classe est le dernier test : un curseur qui n'est pas du
    JSON du tout continue de repartir a zero avec son avertissement. Sans lui,
    un capteur qui leverait sur TOUT curseur non nominal rendrait les trois
    premiers verts.
    """

    def _capteur(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        _corpus(tmp_path, 2)
        return build_source(_html_source(name="reing"))

    def _tick(self, built, curseur):
        with DagsterInstance.ephemeral() as instance:
            context = build_sensor_context(instance=instance, cursor=curseur)
            with _ecoute(context) as journal:
                try:
                    resultat = built.sensor(context)
                except CurseurIllisibleError as exc:
                    return None, str(exc), context.cursor, journal
                return resultat, None, context.cursor, journal

    def test_le_marqueur_pose_dans_le_json_est_nomme_et_le_geste_explique(
        self, tmp_path, monkeypatch
    ):
        """LE CAS DECLENCHEUR, et le message doit porter les trois choses utiles.

        La cle fautive, sa valeur, et le geste a refaire. `ValueError: could not
        convert string to float: 'reingerer:2026-09-22'` n'en portait qu'une, la
        moins actionnable des trois.
        """
        curseur = json.dumps({"captures/page_00.html": f"{PREFIXE_REINGESTION}2026-09-22"})
        try:
            built = self._capteur(tmp_path, monkeypatch)
            resultat, message, apres, _ = self._tick(built, curseur)
        finally:
            get_settings.cache_clear()

        assert resultat is None, "le tick devait echouer, pas reussir en silence"
        assert message is not None
        assert "captures/page_00.html" in message, message
        assert f"{PREFIXE_REINGESTION}2026-09-22" in message, message
        assert "A LA PLACE DU CURSEUR ENTIER" in message, message

    def test_le_curseur_fautif_n_est_pas_consomme_par_le_tick_qui_echoue(
        self, tmp_path, monkeypatch
    ):
        """CE QUI REND L'ECHEC PERSISTANT, donc visible, donc reparable.

        Un tick qui avalerait le curseur fautif en le remplacant ferait
        disparaitre la trace du geste rate — et, le curseur vide, redemanderait
        tout le corpus au tick suivant. C'est la forme qu'il ne faut PAS prendre.
        """
        curseur = json.dumps({"captures/page_00.html": "hier"})
        try:
            built = self._capteur(tmp_path, monkeypatch)
            _, message, apres, _ = self._tick(built, curseur)
        finally:
            get_settings.cache_clear()

        assert message is not None
        assert apres == curseur, (apres, curseur)

    def test_un_json_bien_forme_qui_n_est_pas_un_objet_est_refuse_pareil(
        self, tmp_path, monkeypatch
    ):
        """Les deux autres curseurs mesures, et ils levaient un `TypeError` NU.

        Il tombait hors du `try`, sur `dict(cursor_data)`, donc aucun `except` ne
        le couvrait. Le message ne nommait pas meme le curseur.
        """
        try:
            built = self._capteur(tmp_path, monkeypatch)
            rendus = {}
            for curseur in ("[1, 2, 3]", "3"):
                _, message, apres, _ = self._tick(built, curseur)
                rendus[curseur] = (message, apres)
        finally:
            get_settings.cache_clear()

        for curseur, (message, apres) in rendus.items():
            assert message is not None, curseur
            assert "n'est pas un objet" in message, message
            assert apres == curseur, (apres, curseur)

    def test_un_curseur_qui_n_est_pas_du_json_repart_a_zero_comme_avant(
        self, tmp_path, monkeypatch
    ):
        """LE TEMOIN. Un garde qui leverait sur tout curseur non nominal serait creux.

        Ce cas-la n'est PAS traite par ce lot : il avertit et repart a zero,
        exactement comme avant. La distinction est ce que cette classe garde —
        « ce n'est pas du JSON » et « c'est du JSON qui n'est pas un curseur »
        ne demandent pas le meme geste.
        """
        try:
            built = self._capteur(tmp_path, monkeypatch)
            resultat, message, _, journal = self._tick(built, "pas du json du tout {")
        finally:
            get_settings.cache_clear()

        assert message is None, message
        assert resultat is not None
        assert len(resultat.run_requests) == 2, resultat.run_requests
        assert any("Invalid cursor format" in ligne for ligne in journal.avertissements), (
            journal.avertissements
        )


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


class TestLaCleDeReingestionNePorteQueLEtiquette:
    """LE PENDANT DE `TestLaCleNominaleEstInchangee`, ET IL MANQUAIT.

    La cle nominale etait figee, la cle de reingestion ne l'etait pas : seule
    sa forme APPROXIMATIVE etait gardee — « elle contient l'etiquette » et
    « elle differe des nominales ». `mesure` : lui rajouter le `mtime`,
    `..._reingestion_{etiquette}_{mtime}`, laissait la suite entierement verte.

    Ce que cette laxite coute est precisement ce que le 4.32.a punit. Toute la
    repetabilite du geste tient a ce que **la meme etiquette redonne les memes
    cles** : c'est ce qui rend le geste refait a l'identique bruyant au lieu de
    muet. Une cle qui reprend le `mtime` cesse d'etre fonction de la seule
    etiquette — un `touch`, une restauration de sauvegarde, une copie du corpus
    suffisent alors a rendre neuves des cles que l'operateur croit rejouer, et
    la reingestion repart en silence. Le `mtime` est justement l'entree dont
    tout le 4.32.a demande de se defaire dans ce chemin-la.
    """

    ETIQUETTE = "2026-09-22-apres-purge"

    def test_la_cle_marquee_est_source_partition_reingestion_etiquette(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        try:
            cles = _corpus(tmp_path, 2)
            built = build_source(_html_source(name="forme"))
            with DagsterInstance.ephemeral() as instance:
                marque = build_sensor_context(
                    instance=instance, cursor=f"{PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                resultat = built.sensor(marque)

            attendues = {f"forme_{cle}_reingestion_{self.ETIQUETTE}" for cle in cles}
            assert {r.run_key for r in resultat.run_requests} == attendues
        finally:
            get_settings.cache_clear()

    def test_deux_gestes_de_meme_etiquette_redonnent_les_memes_cles(self, tmp_path, monkeypatch):
        """LE TEMOIN DE LA FORME : c'est cette egalite-la qui rend la repetition bruyante.

        Le `mtime` des fichiers est deplace ENTRE les deux ticks — c'est ce
        qu'un `touch`, une restauration ou une recopie du corpus produisent.
        Les cles ne doivent pas bouger pour autant : elles sont fonction de
        l'etiquette, et de rien d'autre.
        """
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        try:
            _corpus(tmp_path, 2)
            built = build_source(_html_source(name="forme"))
            with DagsterInstance.ephemeral() as instance:
                marque = build_sensor_context(
                    instance=instance, cursor=f"{PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                avant = {r.run_key for r in built.sensor(marque).run_requests}

                for chemin in sorted((tmp_path / "captures").glob("*.html")):
                    os.utime(chemin, (2_000_000_000, 2_000_000_000))

                rejoue = build_sensor_context(
                    instance=instance, cursor=f"{PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                apres = {r.run_key for r in built.sensor(rejoue).run_requests}

            assert avant, avant
            assert apres == avant, (apres, avant)
        finally:
            get_settings.cache_clear()


class TestLaDocumentationNommeLeGesteQuExisteVraiment:
    """Registre 4.32.a — **une documentation qui prescrit un chemin mort EST le defaut.**

    Le `README` donnait la purge par `wipe_stores`, puis « redemarrer, puis
    reingerer », sans une ligne sur ce qui provoque la reingestion. Le chemin
    nominal etait le capteur, et il en etait incapable. Un operateur qui purgeait
    puis attendait gardait des stores VIDES indefiniment.

    Ce garde ne verifie pas que le README « parle de reingestion » — il en
    parlait deja, et c'est bien le probleme. Il verifie que le marqueur qu'il
    prescrit est celui que le capteur LIT, en interrogeant la constante qui en
    est le seul site canonique.
    """

    README = Path(__file__).resolve().parents[2] / "README.md"

    def test_le_readme_prescrit_le_marqueur_que_le_capteur_lit(self):
        texte = self.README.read_text(encoding="utf-8")

        assert PREFIXE_REINGESTION in texte, (
            "le README ne nomme pas le marqueur de reingestion : il prescrit donc "
            "un geste sans dire comment le faire, ce qui est le defaut 4.32.a"
        )

    def test_le_readme_nomme_les_capteurs_sur_lesquels_le_poser(self):
        """Le marqueur se pose SUR UN CAPTEUR, un par source.

        Sans les nommer, le README prescrirait un geste qu'on ne sait ou faire.
        La borne est INFERIEURE et porte sur les sources reellement declarees :
        une quatrieme source devra etre nommee elle aussi.
        """
        texte = self.README.read_text(encoding="utf-8")
        manquants = [
            f"{source.name}_sensor"
            for source in load_sources()
            if f"{source.name}_sensor" not in texte
        ]

        assert not manquants, f"capteurs absents du README : {manquants}"
