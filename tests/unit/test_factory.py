"""Tests unitaires pour la factory Dagster (assets, jobs, sensors par source)."""

from __future__ import annotations

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
