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
    DagsterRunStatus,
    DefaultSensorStatus,
    Definitions,
    SensorEvaluationContext,
    SensorResult,
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
    """Les sensors de source sont livres actifs.

    Un sensor sans ``default_status`` est livre STOPPED : Dagster le charge et
    l'affiche, mais il ne tourne jamais. Aucun fichier depose n'est alors
    ingere, sans aucune erreur. Meme controle que
    ``test_reindex_job.py::TestLeSensorEstLivreArme``, pour les sensors de
    source.

    Les assertions portent sur l'objet produit par ``build_source`` et sur celui
    que livre ``definitions.py``, et non sur la presence du mot dans la source.
    Elles couvrent toutes les sources declarees : un harnais limite a une source
    laisserait les autres sans controle.
    """

    # Sources declarees dans ``sources.yaml``. Borne inferieure, et non egalite :
    # une quatrieme source est couverte automatiquement par les boucles
    # ci-dessous, et la disparition de l'une de ces trois fait echouer le test.
    #
    # Chaque test porte sa propre borne, en ligne, sur la collection qu'il
    # parcourt (`sources` pour le premier, `livres` pour le second) : un test de
    # borne separe construirait son propre harnais et ne verifierait pas celui
    # des autres.
    SOURCES_ATTENDUES = {"pdfs", "livres_html", "markdown"}

    def test_chaque_source_declaree_est_livree_armee(self):
        sources = load_sources()
        # Borne en ligne, sur la liste que la boucle parcourt reellement. Un test
        # de borne separe, appelant `load_sources()` de son cote, passerait meme
        # si cette liste etait vide.
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
        # Sinon les assertions ci-dessus seraient vraies sans la ligne
        # `default_status=...`, par exemple si Dagster changeait sa valeur par
        # defaut. Ce test constate que Dagster livre bien STOPPED par defaut.
        @sensor(name="temoin_sans_default_status", job_name="pdfs_job")
        def temoin(context: SensorEvaluationContext) -> SkipReason:
            return SkipReason("temoin")

        assert temoin.default_status is not DefaultSensorStatus.RUNNING


# --- Contexte Dagster bouchonne -----------------------------------------------
# Les assets sont atteints par `build_source(...).assets[n].op.compute_fn.
# decorated_fn`, c'est-a-dire le corps reellement livre, a travers l'objet que
# `definitions.py` expedie (meme principe que
# `TestLesSensorsDIngestionSontLivresArmes`). `build_asset_context` ne convient
# pas : `add_output_metadata` y leve `DagsterInvalidPropertyError` en
# invocation directe (mesure).


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
    """Le nettoyage publie son denominateur de perte (registre 4.6).

    `min_text_ratio = 0.05` accepte un candidat qui ne conserve que 5 % du
    texte. Sans `precleaned_text_chars` ni ratio, les metadonnees Dagster ne
    distinguent pas un chapitre ampute a 5 % d'un chapitre nettoye a 99,8 %.
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
        """Les cles historiques restent publiees a cote des deux nouvelles."""
        try:
            contexte = self._executer(tmp_path, monkeypatch)
        finally:
            get_settings.cache_clear()

        for cle in ("strategy", "raw_bytes", "cleaned_bytes", "text_chars", "images_exported"):
            assert cle in contexte.metadonnees, f"{cle} a disparu des metadonnees"

    def test_le_ratio_publie_est_celui_du_bilan(self, tmp_path, monkeypatch):
        """Le ratio publie est celui du bilan, et non un second calcul.

        Deux calculs du meme rapport pourraient diverger.
        """
        try:
            contexte = self._executer(tmp_path, monkeypatch)
        finally:
            get_settings.cache_clear()

        attendu = contexte.metadonnees["text_chars"] / contexte.metadonnees["precleaned_text_chars"]
        assert contexte.metadonnees["text_ratio"] == attendu


class TestCeQueLaPurgeDuNettoyeRetireVRAIMENT:
    """`cleaned_html` reecrit toujours sa destination (registre 4.33.a).

    La justification du `rmtree` de `wipe_stores` ne peut pas reposer sur l'idee
    que « l'asset `cleaned_html` ne se rematerialise pas si son fichier existe
    deja » : c'est faux. Ces tests fixent le comportement reel :

    - ce qui est reecrit : une destination au contenu different est remplacee.
      Le test echoue si un court-circuit « le fichier existe » apparaissait ;
    - ce qui survit : la copie nettoyee d'un document retire du corpus. C'est la
      seule chose que la purge retire, donc la vraie raison de la garder.

    Le troisieme test borne le premier : si le capteur voyait `.cleaned/`, les
    orphelins reviendraient par le glob et cette analyse ne tiendrait plus.
    """

    CONTENU = "Du contenu reel qui doit survivre au nettoyage. " * 40
    PERIME = "<html><body><p>PERIME : pointe des objets supprimes</p></body></html>"

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
        """Une destination deja remplie d'un contenu perime est reecrite.

        Le test compare le contenu, pas l'horodatage : verifier seulement que
        le fichier existe encore passerait meme avec un court-circuit.

        Mutation qui doit faire echouer ce test : un court-circuit
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
        """Seule la purge retire la copie nettoyee d'un document retire du corpus.

        Deux documents nettoyes, la source de l'un retiree, l'autre
        rematerialise : l'orphelin est toujours la. Aucun chemin du pipeline ne
        l'efface, car `cleaned_html` controle l'existence de la source. Seul
        `purge_cleaned` le retire, ce que verifie la seconde moitie du test.

        Si l'orphelin disparaissait tout seul, la raison ecrite a
        `purge_cleaned` ne serait plus vraie, et ce test le signalerait.
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
        """Borne de l'analyse precedente : un orphelin n'est atteignable par rien.

        S'il l'etait, le capteur le rendrait a l'ingestion et la purge ne serait
        plus la seule issue. Deux choses l'empechent, toutes deux verifiees : le
        glob est ancre sous le sous-repertoire de la source, et `.cleaned` porte
        un point de tete que `glob` n'ouvre jamais.
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
    """Un document ecarte comme doublon se distingue d'un document vide (registre 4.10).

    `extraction.extract` rend `{"elements": 0, "chunks": 0, "duplicate_of": ...}`
    quand il reconnait un fichier deja ingere. Avec seulement `elements`,
    `chunks`, `pages` et `elapsed_seconds`, un document ecarte et un document
    ingere vide afficheraient les memes quatre zeros dans l'interface Dagster.
    Le premier cas est voulu, le second est une panne.

    Les cinq cles supplementaires sont publiees : `duplicate_of`,
    `pages_skipped`, `ocr`, `language` et `failed_batches`.
    """

    # La fixture reproduit la forme fabriquee en production.
    # `main._run_extraction` appelle `job.report(**result)`, et `Job.report`
    # range tout dans `progress` : les cinq cles arrivent donc dans `progress`,
    # pas au premier niveau du `snapshot()`. Une fixture qui les poserait au
    # premier niveau passerait sur un chemin de production casse. D'ou
    # `_snapshot_reel` ci-dessous, qui construit la forme par le vrai `Job`.
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
        """`duplicate_of` n'apparait que sur un document reellement ecarte.

        Sans ce test, publier `duplicate_of` en dur passerait. Une cle presente
        et vide sur tous les documents finirait par ne plus etre lue.
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
        """Un lot PDF en echec se voit dans les metadonnees, pas seulement dans
        l'echec du run (compteur du registre 4.1, cote Dagster)."""
        contexte = ContexteEspion("pdfs/livre.pdf")
        _record_metadata(contexte, self._snapshot_reel(self.BILAN_PDF_RENDU_PAR_EXTRACT))

        assert contexte.metadonnees["failed_batches"] == 1
        assert "31-35" in str(contexte.metadonnees["failed_batches_detail"])

    def test_les_quatre_metadonnees_historiques_survivent(self):
        """Les quatre cles historiques restent publiees a cote des cinq nouvelles."""
        contexte = ContexteEspion("pdfs/livre.pdf")
        _record_metadata(contexte, self._snapshot_reel(self.BILAN_PDF_RENDU_PAR_EXTRACT))

        assert contexte.metadonnees["elements"] == 3750
        assert contexte.metadonnees["chunks"] == 4365
        assert contexte.metadonnees["pages"] == 71
        assert contexte.metadonnees["elapsed_seconds"] >= 0.0

    def test_un_bilan_minimal_ne_leve_pas(self):
        """Un bilan sans aucune des cles optionnelles (cas d'un HTML nominal)
        ne fait pas echouer la publication."""
        contexte = ContexteEspion("livre/chapitre.html")
        _record_metadata(contexte, self._snapshot_reel({"elements": 12, "chunks": 30}))

        assert contexte.metadonnees["elements"] == 12
        assert "duplicate_of" not in contexte.metadonnees
        assert contexte.metadonnees["failed_batches"] == 0


# --- La reingestion, et son refus silencieux par Dagster (registre 4.32.a) ----


class JournalDuCapteur(logging.Handler):
    """Retient les lignes que le capteur ecrit reellement a son journal.

    `caplog` ne convient pas : le logger « dagster » ne propage pas (mesure :
    le message atteint stderr, `caplog.records` reste vide). Un handler est donc
    attache au logger utilise par le capteur, l'objet rendu par `context.log`.
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
    """Geste de reingestion (registre 4.32.a).

    Dagster cherche un `run_key` consomme dans tout l'historique, sans limite de
    temps : avec une cle deterministe sur `(source, partition, mtime)`, un
    fichier dont le `mtime` n'a pas bouge ne peut pas etre reingere. Mesure du
    2 septembre 2026, curseur vide : 23 `run_key` demandees, aucun run cree,
    `skip_reason=None` (detail dans
    `documentation/campagnes/2026-09-02-premiere-campagne-de-reference.md`).

    Les tests suivent les questions numerotees du bloc de commentaires
    au-dessus de `factory.PREFIXE_REINGESTION`. Le dernier sert de controle :
    sans lui, un `run_key` aleatoire les ferait tous passer en relancant
    l'ingestion a chaque tick.
    """

    ETIQUETTE = "2026-09-22-apres-purge"

    def _capteur(self, tmp_path, monkeypatch, fichiers: int = 3):
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        _corpus(tmp_path, fichiers)
        return build_source(_html_source(name="reing"))

    def test_sans_marqueur_un_corpus_inchange_ne_demande_rien(self, tmp_path, monkeypatch):
        """Question 2 : sans marqueur, aucune reingestion ne part.

        Le curseur l'empeche. Il vit dans le stockage de l'instance, survit au
        rechargement du code, et `factory.py` ne l'efface jamais. Dix ticks de
        suite sur un corpus inchange : aucune demande.
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
        """Question 1 : le marqueur, pose a la main, declenche une reingestion.

        L'assertion porte sur les cles, et non sur le nombre de demandes : sans
        marqueur, le capteur construit aussi ses 23 demandes, mais avec les cles
        de la premiere ingestion, et Dagster n'en cree aucun run.
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
        """Question 3, premiere moitie : le marqueur ne vaut que pour un tick.

        Sinon, le marqueur resterait en place et le capteur relancerait
        l'ingestion complete a chaque tick, indefiniment.
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
        """Sur un corpus vide, le marqueur est quand meme consomme.

        Le curseur calcule vaut alors `{}`, egal au curseur de depart une fois
        le marqueur lu. Si le curseur n'etait ecrit que lorsqu'il change, le
        marqueur resterait en place et chaque tick le rejouerait.
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
        """L'etiquette est obligatoire : c'est elle qui distingue deux gestes.

        Sans elle, le second geste porterait les memes cles que le premier, et
        Dagster le refuserait sans avertissement (registre 4.32.a). Le capteur
        refuse donc le marqueur nu, et le signale.
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
        """Le `.strip()` final normalise l'etiquette.

        Le marqueur se pose a la main (champ de saisie de l'interface Dagster ou
        ligne de commande). « reingerer: 2026-09-22 » et « reingerer:2026-09-22 »
        doivent donner les memes cles de run. Sans ce `.strip()`, l'espace en
        trop rendrait les cles neuves, et un geste repete reingererait tout sans
        avertissement au lieu d'etre refuse et signale.

        Un marqueur suivi de seuls espaces ne teste pas ce `.strip()` : le
        `curseur.strip()` precedent a deja retire ces espaces, et l'etiquette
        est vide dans les deux cas. Ce cas est couvert par
        `test_un_marqueur_sans_etiquette_est_refuse_et_dit_pourquoi`.
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
        """Le premier `curseur.strip()` accepte un espace en tete (registre, H21).

        `_etiquette_de_reingestion` contient deux `.strip()` ; le test precedent
        ne couvre que le second. Le premier absorbe un espace de tete, frequent
        apres un copier-coller dans un champ de l'interface Dagster.

        Sans lui, sur une instance dont l'historique porte deja les cles
        nominales (la production), le curseur « ␣␣reingerer:2026-09-22 » n'est
        plus un ordre : il est traite comme un curseur JSON, echoue a se
        decoder, et le capteur redemande le corpus avec les cles nominales, dont
        Dagster ne cree aucun run. Le geste n'a pas lieu, et les messages
        (« Invalid cursor format, resetting », puis « ... run_key DEJA
        CONSOMME ») ne nomment pas l'espace.

        L'assertion porte sur la forme des cles, et non sur leur nombre : les
        trois demandes existent dans les deux cas.
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
        """Le marqueur n'est un ordre qu'en tete du curseur (`startswith`, et non `in`).

        Un curseur qui contient `reingerer:` sans commencer par lui est un
        curseur mal forme : le capteur repart du corpus avec les cles nominales,
        deja dans l'historique, donc sans rien relancer.

        Avec `in`, le meme curseur serait lu comme un ordre, avec une etiquette
        decoupee a l'aveugle (ici « er:2026-09-22-apres-purge »), et le capteur
        reingererait tout le corpus sans que personne l'ait demande.
        """
        try:
            built = self._capteur(tmp_path, monkeypatch)
            with DagsterInstance.ephemeral() as instance:
                egare = build_sensor_context(
                    instance=instance, cursor=f"# {PREFIXE_REINGESTION}{self.ETIQUETTE}"
                )
                resultat = built.sensor(egare)
                cles = {r.run_key for r in resultat.run_requests}

            # Cles reconstruites a la main, et non par un second appel a
            # `_corpus`, qui reecrirait les fichiers et deplacerait les `mtime`
            # que cette assertion compare.
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
        """Le capteur confirme la lecture du marqueur dans son journal.

        Le geste est manuel et touche tout le corpus : cette ligne est la seule
        confirmation qu'il a ete lu, avec quelle etiquette et sur combien de
        fichiers.

        La seconde assertion verifie l'absence de « Invalid cursor format,
        resetting. » : un marqueur bien forme n'est pas un curseur invalide, et
        ce message laisserait croire que le geste a echoue.
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
    """Le capteur signale les demandes que Dagster refusera (registre 4.32.a).

    Le tick qui perd ses runs porte `skip_reason=None` : le daemon n'ecrit rien
    quand il ecarte une demande dont la cle est deja consommee (22 runs perdus
    ainsi). Le capteur verifie donc lui-meme, et l'annonce.

    Le controle reproduit la regle du daemon : il interroge
    `RunsFilter(tags={RUN_KEY_TAG: cle})` puis retient les runs dont
    `SENSOR_NAME_TAG` est celui du capteur, comme
    `dagster/_daemon/sensor.py::fetch_existing_runs` (dagster 1.13.16, lignes
    1290-1333).
    """

    ETIQUETTE = "geste-refait"

    def _instance_avec_les_cles(self, instance, cles, nom_du_capteur: str) -> None:
        """Consomme ces `run_key` comme le daemon les consomme : par des runs tagues.

        Les runs crees sont en statut terminal : `create_run_for_test` cree par
        defaut un run `NOT_STARTED`, donc en vol, alors que cette fixture
        modelise une ingestion passee dont les cles sont deja consommees. Un
        run en vol ferait refuser le marqueur par le controle d'ingestion en
        cours (registre 4.33.c), et ces tests ne mesureraient plus rien.
        """
        for cle in cles:
            create_run_for_test(
                instance,
                job_name="reing_job",
                status=DagsterRunStatus.SUCCESS,
                tags={RUN_KEY_TAG: str(cle), SENSOR_NAME_TAG: nom_du_capteur},
            )

    def test_des_cles_deja_consommees_sont_annoncees_avec_leur_compte(self, tmp_path, monkeypatch):
        """Question 3, seconde moitie : le geste repete a l'identique est signale.

        Deux fois le meme marqueur : le second tick reconstruit les memes cles,
        et Dagster n'en creera aucun run. Le capteur doit le signaler, avec le
        nombre de demandes perdues.

        L'assertion lit le numerateur a sa place dans « {N} demande(s) de run
        sur {M} ». Un simple `"3" in message` serait satisfait par le
        denominateur (M = 3). `test_le_compte_annonce_est_celui_des_perdues_et_non_du_total`
        rend les deux differents.
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
        """Numerateur et denominateur sont rendus differents, volontairement.

        Tant que les deux valent 3, rien ne distingue « le capteur compte ses
        pertes » de « le capteur recopie le total ». Ici, deux cles sur trois
        sont consommees : le message doit dire 2 sur 3. C'est ce test qui
        verifie que le capteur annonce le bon nombre, comme le promettent le
        README et le registre 4.32.a.
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
                # Deux seulement : la troisieme demande passera, les deux autres
                # sont perdues, et le capteur doit annoncer 2.
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
        """L'alerte nomme les trois premieres cles perdues (registre, H3).

        Sans ces cles, l'alerte donnerait son compte sans permettre de savoir
        quelle partition a ete perdue. Les deux bornes sont verifiees : l'alerte
        nomme trois cles quand quatre sont perdues, et ce sont les trois
        premieres dans l'ordre des demandes. Une borne haute seule laisserait
        passer zero cle.
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
        """Des cles neuves ne declenchent aucun avertissement.

        Sans ce test, un avertissement emis en dur passerait le test precedent.
        Un capteur qui avertit a chaque tick finit par ne plus etre lu.
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

        Sans ce filtre, le controle annoncerait des pertes qui n'en sont pas, et
        une fausse alerte finit par ne plus etre lue.
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
        """Cas courant : historique sous les cles de ce capteur, demande a cles neuves.

        Les deux tests voisins ne couvrent pas ce cas : l'un part d'une instance
        vierge, l'autre varie le nom du capteur, pas les cles. Ici, le capteur a
        deja des runs sous ses propres cles et son propre nom, et la demande du
        jour porte des cles neuves.

        Sans ce test, une requete filtrant sur le seul nom du capteur
        (`RunsFilter(tags={SENSOR_NAME_TAG: nom_du_capteur})`, sans la cle)
        passerait la suite, alors qu'elle leverait une fausse alerte a chaque
        tick des que le capteur a un run derriere lui, c'est-a-dire toujours en
        production.
        """
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        try:
            _corpus(tmp_path, 3)
            built = build_source(_html_source(name="reing"))
            with DagsterInstance.ephemeral() as instance:
                # Historique ordinaire : la premiere ingestion a eu lieu, ses
                # trois runs portent les cles nominales et le nom de ce capteur.
                nominal = build_sensor_context(instance=instance)
                deja = [r.run_key for r in built.sensor(nominal).run_requests]
                self._instance_avec_les_cles(instance, deja, "reing_sensor")

                # Demande du jour : une reingestion a etiquette neuve, dont les
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


class TestUneReingestionDejaEnVolNEnDeclenchePasUneSeconde:
    """Refus du marqueur quand une ingestion de la source est en cours (registre 4.33.c).

    Un second marqueur d'etiquette neuve, pose pendant une reingestion,
    produirait un second jeu complet de demandes, a cles neuves, que Dagster
    accepterait. `dagster.yaml` fixe `max_concurrent_runs: 2` sans cle de
    concurrence par partition : deux runs simultanes pourraient alors ecrire le
    meme `Datas/.cleaned/<fichier>`. Meme principe que `reindex_job.py` : une
    operation en cours n'est ni faite ni perdue, on attend son issue.

    Quatre cas sont couverts :

    - le refus, quand un run de ce job est non terminal ;
    - la meme etiquette, une fois le run termine, emet ses demandes (sans ce
      test, un controle qui refuserait toujours rendrait la reingestion
      impossible) ;
    - le marqueur n'est pas consomme par le tick qui refuse : le geste est
      differe, pas perdu ;
    - un run d'un autre job ne bloque rien : le filtre porte sur `job_name`.
    """

    ETIQUETTE = "2026-09-22-seconde-vague"

    def _capteur(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        _corpus(tmp_path, 3)
        return build_source(_html_source(name="reing"))

    def _tick_marque(self, built, instance):
        context = build_sensor_context(
            instance=instance, cursor=f"{PREFIXE_REINGESTION}{self.ETIQUETTE}"
        )
        return built.sensor(context), context.cursor

    def test_un_marqueur_lu_pendant_un_run_en_vol_n_emet_rien_et_le_dit(
        self, tmp_path, monkeypatch
    ):
        """Le refus est un `SkipReason` qui nomme le run en cours.

        Un `SensorResult` sans demande porterait `skip_reason=None`, sans
        explication (registre 4.32.a).
        """
        try:
            built = self._capteur(tmp_path, monkeypatch)
            with DagsterInstance.ephemeral() as instance:
                create_run_for_test(instance, job_name="reing_job", status=DagsterRunStatus.STARTED)
                resultat, _ = self._tick_marque(built, instance)
        finally:
            get_settings.cache_clear()

        assert isinstance(resultat, SkipReason), resultat
        assert "deja en vol" in resultat.skip_message, resultat.skip_message
        assert f"{PREFIXE_REINGESTION}{self.ETIQUETTE}" in resultat.skip_message

    def test_le_marqueur_n_est_pas_consomme_par_le_tick_qui_refuse(self, tmp_path, monkeypatch):
        """Le geste est differe, pas perdu : le marqueur reste en place.

        Si le tick qui refuse consommait le curseur, le marqueur aurait ete pose
        pour rien, sans que rien ne le signale. Le tick suivant le relira.
        """
        try:
            built = self._capteur(tmp_path, monkeypatch)
            with DagsterInstance.ephemeral() as instance:
                create_run_for_test(instance, job_name="reing_job", status=DagsterRunStatus.STARTED)
                _, apres = self._tick_marque(built, instance)
        finally:
            get_settings.cache_clear()

        assert apres == f"{PREFIXE_REINGESTION}{self.ETIQUETTE}", apres

    def test_le_meme_marqueur_emet_ses_demandes_une_fois_le_run_terminal(
        self, tmp_path, monkeypatch
    ):
        """Une fois le run termine, le meme marqueur produit ses demandes.

        Le run en cours se termine entre les deux ticks ; le marqueur, relu,
        doit alors produire le jeu complet de demandes. Un controle qui
        refuserait toujours rendrait la reingestion impossible.
        """
        try:
            built = self._capteur(tmp_path, monkeypatch)
            with DagsterInstance.ephemeral() as instance:
                run = create_run_for_test(
                    instance, job_name="reing_job", status=DagsterRunStatus.STARTED
                )
                refus, _ = self._tick_marque(built, instance)

                instance.report_run_canceled(run)
                apres, curseur = self._tick_marque(built, instance)
        finally:
            get_settings.cache_clear()

        assert isinstance(refus, SkipReason), refus
        assert isinstance(apres, SensorResult), apres
        cles = {r.run_key for r in apres.run_requests}
        assert cles == {
            f"reing_captures/page_{numero:02d}.html_reingestion_{self.ETIQUETTE}"
            for numero in range(3)
        }, cles
        assert curseur != f"{PREFIXE_REINGESTION}{self.ETIQUETTE}", curseur

    def test_un_run_d_un_autre_job_ne_bloque_pas_le_marqueur(self, tmp_path, monkeypatch):
        """Un run d'un autre job ne bloque pas le marqueur : le filtre porte sur `job_name`.

        Un controle qui verrait tous les runs non terminaux passerait le premier
        test, mais bloquerait la reingestion des qu'une reindexation, ou
        l'ingestion d'une autre source, serait en cours (cf. registre 4.15).
        """
        try:
            built = self._capteur(tmp_path, monkeypatch)
            with DagsterInstance.ephemeral() as instance:
                create_run_for_test(
                    instance,
                    job_name="agent_reindex_job",
                    status=DagsterRunStatus.STARTED,
                )
                resultat, _ = self._tick_marque(built, instance)
        finally:
            get_settings.cache_clear()

        assert isinstance(resultat, SensorResult), resultat
        assert len(resultat.run_requests) == 3, resultat.run_requests

    def test_le_chemin_nominal_n_est_pas_garde_et_c_est_borne_expres(self, tmp_path, monkeypatch):
        """Limite du controle : le chemin nominal n'est pas bloque.

        Le controle ne porte que sur le marqueur. Un fichier modifie pendant une
        reingestion produit une cle neuve sur une partition en cours, et sa
        demande part. Ce cas reste ouvert au registre ; ce test fixe la limite
        exacte du controle.
        """
        try:
            built = self._capteur(tmp_path, monkeypatch)
            with DagsterInstance.ephemeral() as instance:
                create_run_for_test(instance, job_name="reing_job", status=DagsterRunStatus.STARTED)
                context = build_sensor_context(instance=instance)
                resultat = built.sensor(context)
        finally:
            get_settings.cache_clear()

        assert isinstance(resultat, SensorResult), resultat
        assert len(resultat.run_requests) == 3, resultat.run_requests


class TestLeCurseurAvanceEtLOrdreNeBougePas:
    """Deux details du capteur (registre 4.33, H7 et H13).

    Sans eux, le capteur rearme une reingestion a chaque tick, ou produit un
    diagnostic qui change d'un tick a l'autre. Aucun autre test ne les couvre.
    """

    def _capteur(self, tmp_path, monkeypatch, fichiers: int = 3):
        monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
        get_settings.cache_clear()
        _corpus(tmp_path, fichiers)
        return build_source(_html_source(name="reing"))

    def test_un_fichier_modifie_voit_son_nouveau_mtime_ecrit_au_curseur(
        self, tmp_path, monkeypatch
    ):
        """H7 : le curseur recoit `str(mtime)`, et non `str(last_mtime or mtime)`.

        La difference ne se voit pas au premier tick (`last_mtime` y vaut
        `None`), seulement sur un fichier deja connu et modifie : le capteur
        reecrirait son ancien mtime au curseur, le trouverait en retard au tick
        suivant, et le redemanderait toutes les 30 secondes, indefiniment.

        Le troisieme tick verifie que le capteur en a fini avec ce fichier, et
        pas seulement qu'il a ecrit la bonne valeur.
        """
        cle = "captures/page_00.html"
        try:
            built = self._capteur(tmp_path, monkeypatch)
            chemin = tmp_path / cle
            with DagsterInstance.ephemeral() as instance:
                premier = build_sensor_context(instance=instance)
                built.sensor(premier)
                curseur = premier.cursor

                # Le fichier est modifie : contenu et mtime, comme un vrai depot.
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
        """H13 : `sorted(glob(...))`, et non `list(glob(...))`.

        `glob` rend l'ordre de `os.scandir`, celui du systeme de fichiers : ni
        trie, ni stable d'une machine ou d'un tick a l'autre. Sans tri, l'ordre
        des demandes de run et celui des « premieres cles perdues » de l'alerte
        (registre 4.32.a) changeraient sans raison, et le diagnostic ne se
        comparerait plus d'un tick a l'autre.

        Le desordre est impose en remplacant `glob` (l'environnement), et non
        le capteur, qui reste le producteur de l'ordre verifie.
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
    """Curseurs JSON valides qui ne sont pas des curseurs (registre 4.33).

    Cas typique : le marqueur pose dans le JSON au lieu de remplacer le curseur
    entier. Le tick echoue, et echoue de nouveau au tick suivant :
    `update_cursor` n'est pas atteint, le curseur fautif reste en place, et
    l'echec se repete toutes les 30 secondes jusqu'a correction. Rattraper
    l'erreur pour reinitialiser le curseur ferait redemander tout le corpus
    sans avertissement. L'erreur, en revanche, nomme la cle fautive et le
    geste correct.

    Le dernier test sert de controle : un curseur qui n'est pas du JSON repart
    toujours a zero avec un avertissement. Sans lui, un capteur qui leverait
    sur tout curseur non nominal ferait passer les trois premiers.
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
        """Cas typique : le message nomme la cle fautive, sa valeur et le geste correct.

        Le `ValueError: could not convert string to float:
        'reingerer:2026-09-22'` brut ne donnait que la valeur.
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
        """Le curseur fautif reste en place, donc l'echec reste visible.

        Un tick qui remplacerait le curseur fautif effacerait la trace du geste
        rate et, le curseur vide, redemanderait tout le corpus au tick suivant.
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
        """Les deux autres curseurs mesures (liste, entier) levent un message explicite.

        Sans controle, ils levaient un `TypeError` brut sur `dict(cursor_data)`,
        hors de tout `try`, sans nommer le curseur.
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
        """Un curseur qui n'est pas du JSON avertit et repart a zero.

        Ce comportement est distinct du precedent : « ce n'est pas du JSON » et
        « c'est du JSON qui n'est pas un curseur » ne demandent pas le meme
        geste. Sans ce test, un capteur qui leverait sur tout curseur non
        nominal passerait les autres.
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
    """La cle de run nominale garde sa forme exacte.

    Fusionner dans `main` deploie le code : les conteneurs montent `src/` depuis
    le clone principal (mesure du 22 septembre 2026, `docker inspect`). Le
    premier tick apres un deploiement ne doit rien relancer, ce qui suppose que,
    sans marqueur, la cle construite soit exactement celle deja presente dans
    l'historique.

    Ce test fige donc la forme litterale de la cle nominale : la modifier
    relancerait l'ingestion complete du corpus au chargement du code.
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
    """La cle de reingestion garde sa forme exacte : fonction de l'etiquette seule.

    Pendant de `TestLaCleNominaleEstInchangee`. Verifier seulement que la cle
    « contient l'etiquette » et « differe des nominales » laisserait passer
    `..._reingestion_{etiquette}_{mtime}`.

    Or la repetabilite du geste repose sur ce que la meme etiquette redonne les
    memes cles : c'est ce qui fait signaler un geste repete a l'identique. Une
    cle qui inclurait le `mtime` changerait apres un `touch`, une restauration
    de sauvegarde ou une copie du corpus, et la reingestion repartirait sans
    avertissement.
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
        """Les cles de reingestion ne dependent pas du `mtime`.

        Le `mtime` des fichiers change entre les deux ticks (comme apres un
        `touch`, une restauration ou une recopie du corpus). Les cles ne
        doivent pas changer : elles ne dependent que de l'etiquette.
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
    """Le README prescrit le marqueur que le capteur lit (registre 4.32.a).

    Apres une purge par `wipe_stores`, la reingestion passe par le marqueur de
    reingestion pose sur le curseur de chaque capteur ; attendre ne suffit pas.
    Ce test verifie que le README cite le marqueur que le capteur lit, via la
    constante `PREFIXE_REINGESTION`, sa seule definition.
    """

    README = Path(__file__).resolve().parents[2] / "README.md"

    def test_le_readme_prescrit_le_marqueur_que_le_capteur_lit(self):
        texte = self.README.read_text(encoding="utf-8")

        assert PREFIXE_REINGESTION in texte, (
            "le README ne nomme pas le marqueur de reingestion : il prescrit donc "
            "un geste sans dire comment le faire, ce qui est le defaut 4.32.a"
        )

    def test_le_readme_nomme_les_capteurs_sur_lesquels_le_poser(self):
        """Le README nomme le capteur de chaque source, ou poser le marqueur.

        Borne inferieure, sur les sources reellement declarees : une quatrieme
        source devra etre nommee elle aussi.
        """
        texte = self.README.read_text(encoding="utf-8")
        manquants = [
            f"{source.name}_sensor"
            for source in load_sources()
            if f"{source.name}_sensor" not in texte
        ]

        assert not manquants, f"capteurs absents du README : {manquants}"
