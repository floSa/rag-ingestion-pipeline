"""Tests du DECLENCHEMENT de ``POST /reindex`` : quand, et combien de fois.

``test_reindex.py`` couvre ce que fait l'appel. Ce fichier couvre la seule
propriete que le contrat avec ``rag-agent-chat`` enonce et que le code n'avait
jamais tenue : l'appel a lieu **en fin d'ingestion**, pas une fois par document.

Le defaut precedent est instructif. Un test qui verifie « l'appel a lieu »
serait reste vert avec un appel par document comme avec un seul : il est vert des deux
cotes du defaut. Les tests ci-dessous asserttent donc un NOMBRE, et ils le
font depuis le cote qui le PRODUIT — l'asset qui ingere pour le zero, le
sensor qui arme la reindexation pour le un.

Deux precautions, sans lesquelles ces tests seraient creux :

- ``TestLEspionFonctionne`` prouve que l'interception voit reellement passer un
  appel. Sans elle, une interception cassee rendrait « zero appel » vrai pour
  la mauvaise raison, et le test serait vert quoi qu'il arrive ;
- ``test_l_ingestion_a_bien_eu_lieu`` prouve que les materialisations ont
  vraiment tourne. Un compte de zero appel sur zero ingestion ne dit rien.
"""

from __future__ import annotations

import pytest
import requests
from dagster import (
    DagsterInstance,
    DagsterRunStatus,
    DefaultSensorStatus,
    Definitions,
    RunRequest,
    RunsFilter,
    SensorEvaluationContext,
    SkipReason,
    build_sensor_context,
    materialize,
    sensor,
)
from dagster._core.test_utils import create_run_for_test

from src.pipeline import factory
from src.pipeline.factory import build_source
from src.pipeline.reindex import request_reindex
from src.pipeline.reindex_job import (
    REINDEX_JOB_NAME,
    REINDEX_SENSOR_NAME,
    STATUTS_EN_COURS,
    STATUTS_TERMINES,
    build_reindex,
    lexical_index,
)
from src.pipeline.settings import get_settings
from src.pipeline.sources import SourceConfig, load_sources

BILAN = {"progress": {"elements": 12, "chunks": 34, "pages": 5}, "elapsed_seconds": 1.5}

# Nom du job d'ingestion surveille par le sensor dans ces tests.
JOB_INGESTION = "pdfs_job"

# Tailles de rafale exercees. Elles ne mesurent aucun corpus et ne pretendent
# pas en decrire un : ce sont trois ordres de grandeur, choisis pour que le
# compte d'appels puisse diverger du nombre de documents. Un test parametre sur
# une seule taille resterait vert si l'appel repartait une fois par document.
RAFALES = (1, 3, 12)

# Toujours posee explicitement. Un `.env` local a AGENT_SERVICE_URL vide
# desactiverait l'appel et rendrait « zero appel » vrai sans rien prouver.
URL_AGENT = "http://agent-api:8000"


class _Reponse:
    """Reponse HTTP minimale de l'agent."""

    def __init__(self, charge) -> None:
        self.charge = charge

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.charge


def _rafale(instance, documents: int, statut=DagsterRunStatus.SUCCESS) -> None:
    """Simule une rafale : `documents` runs d'ingestion, un par fichier."""
    for _ in range(documents):
        create_run_for_test(instance, job_name=JOB_INGESTION, status=statut)


def _tick(instance, cursor: str | None = None):
    """Fait tourner le sensor une fois.

    Returns:
        Ce qu'il decide, et le curseur qu'il laisse derriere lui — celui que
        Dagster lui repasserait au tick suivant. Le reconstituer a la main
        rendrait les tests d'enchainement complaisants.
    """
    built = build_reindex([JOB_INGESTION])
    context = build_sensor_context(instance=instance, cursor=cursor)
    return built.sensor(context), context.cursor


class EspionReseau:
    """Note tout ``requests.post`` sortant, d'ou qu'il vienne."""

    def __init__(self) -> None:
        self.appels: list[str] = []

    def __call__(self, url, headers=None, timeout=None, **_):
        self.appels.append(url)
        raise requests.ConnectionError("aucun agent dans les tests")


@pytest.fixture
def espion(monkeypatch):
    """Intercepte l'envoi au plus bas niveau accessible : ``requests.post``.

    Pas ``request_reindex``, pas ``_reindex`` : bouchonner l'une ou l'autre
    rendrait intestable ce que ces tests pretendent verifier, puisque c'est
    justement le nombre de fois que le producteur les appelle qui est en cause.
    """
    espion = EspionReseau()
    monkeypatch.setattr(requests, "post", espion)
    return espion


class TestLEspionFonctionne:
    """Sans ceci, « zero appel » serait vrai meme si l'interception etait morte."""

    def test_un_appel_reel_est_bien_vu(self, espion):
        request_reindex(URL_AGENT)
        assert espion.appels == [f"{URL_AGENT}/reindex"]


def _ingerer(monkeypatch, tmp_path, nombre: int):
    """Materialise ``nombre`` partitions d'une source PDF, extraction bouchonnee.

    Seule l'extraction Docling est remplacee : elle sort du perimetre et exige
    un service HTTP. Tout le reste du chemin d'ingestion s'execute pour de bon.

    Returns:
        La liste des resultats de materialisation, un par document.
    """
    monkeypatch.setenv("SOURCE_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(factory, "_request_extraction", lambda context, chemin, source: BILAN)

    cles = [f"livre_{numero:02d}.pdf" for numero in range(nombre)]
    for cle in cles:
        (tmp_path / cle).write_bytes(b"%PDF-1.7")

    built = build_source(SourceConfig(name="pdfs", glob="*.pdf", type="pdf"))
    resultats = []
    with DagsterInstance.ephemeral() as instance:
        instance.add_dynamic_partitions("pdfs_files", cles)
        for cle in cles:
            resultats.append(materialize(built.assets, partition_key=cle, instance=instance))
    return resultats


@pytest.fixture(autouse=True)
def _url_agent_posee(monkeypatch):
    """Impose une URL d'agent non vide, et rend les settings propres apres coup."""
    monkeypatch.setenv("AGENT_SERVICE_URL", URL_AGENT)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_le_montage_configure_bien_un_agent():
    # Les tests « zero appel » ne valent que si l'appel etait possible.
    assert get_settings().agent_service_url == URL_AGENT


class TestIngererNeReindexePas:
    """La propriete de fond : le nombre d'appels ne suit pas le nombre de documents."""

    @pytest.mark.parametrize("documents", RAFALES)
    def test_aucun_appel_pendant_l_ingestion(self, documents, espion, monkeypatch, tmp_path):
        _ingerer(monkeypatch, tmp_path, documents)
        assert espion.appels == []

    def test_l_ingestion_a_bien_eu_lieu(self, espion, monkeypatch, tmp_path):
        # Sinon « zero appel » serait vrai parce que rien n'a tourne.
        resultats = _ingerer(monkeypatch, tmp_path, 3)
        assert [resultat.success for resultat in resultats] == [True, True, True]
        for resultat in resultats:
            materialisations = resultat.asset_materializations_for_node("pdfs__extracted_document")
            assert materialisations[0].metadata["chunks"].value == 34

    def test_les_metadonnees_ne_parlent_plus_de_reindexation(self, espion, monkeypatch, tmp_path):
        # « publier le bilan d'extraction » ne poste pas sur le reseau : la cle
        # `reindex` dans les metadonnees de l'asset etait la trace de ce
        # melange de hauteurs.
        resultats = _ingerer(monkeypatch, tmp_path, 1)
        materialisations = resultats[0].asset_materializations_for_node("pdfs__extracted_document")
        assert "reindex" not in materialisations[0].metadata


class TestLeSensorNArmeQuUneFois:
    """L'autre moitie de la propriete : une rafale de N documents, UNE demande.

    L'asset d'ingestion ne poste plus rien (ci-dessus) ; c'est ici que le
    nombre d'appels se decide. Le compte est asserte pour chaque taille de
    ``RAFALES`` : s'il suivait le nombre de documents, seul N = 1 resterait vert.
    """

    @pytest.mark.parametrize("documents", RAFALES)
    def test_une_seule_demande_par_rafale(self, documents):
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, documents)
            resultat, _ = _tick(instance)
        assert isinstance(resultat, RunRequest)

    def test_la_rafale_a_bien_eu_lieu(self):
        # Sinon « une seule demande » serait vrai faute d'ingestion a reindexer.
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, max(RAFALES))
            assert len(instance.get_runs(RunsFilter(job_name=JOB_INGESTION))) == max(RAFALES)

    @pytest.mark.parametrize(
        "statut",
        [
            DagsterRunStatus.NOT_STARTED,
            DagsterRunStatus.STARTING,
            DagsterRunStatus.STARTED,
            DagsterRunStatus.CANCELING,
        ],
    )
    def test_rien_ne_part_tant_qu_un_run_est_en_vol(self, statut):
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, 3)
            create_run_for_test(instance, job_name=JOB_INGESTION, status=statut)
            resultat, _ = _tick(instance)
        assert isinstance(resultat, SkipReason)
        assert "Ingestion en cours" in str(resultat.skip_message)

    def test_queued_compte_comme_en_vol(self):
        # C'est LE cas de production : le sensor de source cree les N runs en un
        # passage, la file n'en execute que deux, les autres attendent. Un run
        # QUEUED ne peut pas etre fabrique sur une instance de test (Dagster
        # exige une origine de job distante), d'ou l'assertion sur la table des
        # statuts plutot que sur le comportement.
        assert DagsterRunStatus.QUEUED in STATUTS_EN_COURS

    def test_aucun_statut_terminal_ne_bloque(self):
        assert set(STATUTS_EN_COURS).isdisjoint(STATUTS_TERMINES)
        assert set(STATUTS_EN_COURS) | STATUTS_TERMINES == set(DagsterRunStatus)

    def test_un_second_tick_ne_redemande_rien(self):
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, 3)
            premier, curseur = _tick(instance)
            assert isinstance(premier, RunRequest)
            assert curseur, "le sensor doit poser un curseur, sinon il rearme sans fin"
            second, _ = _tick(instance, cursor=curseur)
        assert isinstance(second, SkipReason)
        assert "Rien de nouveau" in str(second.skip_message)

    def test_une_nouvelle_rafale_rearme(self):
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, 3)
            _, curseur = _tick(instance)
            _rafale(instance, 2)
            resultat, _ = _tick(instance, cursor=curseur)
        assert isinstance(resultat, RunRequest)

    def test_le_run_de_reindexation_ne_se_compte_pas_lui_meme(self):
        # Sans quoi le sensor s'auto-entretiendrait : sa propre execution
        # deplacerait le repere, et la reindexation ne s'arreterait jamais.
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, 2)
            premier, curseur = _tick(instance)
            assert isinstance(premier, RunRequest)
            create_run_for_test(
                instance, job_name=REINDEX_JOB_NAME, status=DagsterRunStatus.SUCCESS
            )
            second, _ = _tick(instance, cursor=curseur)
        assert isinstance(second, SkipReason)

    def test_un_job_etranger_ne_declenche_rien(self):
        with DagsterInstance.ephemeral() as instance:
            create_run_for_test(instance, job_name="autre_job", status=DagsterRunStatus.SUCCESS)
            resultat, _ = _tick(instance)
        assert isinstance(resultat, SkipReason)
        assert "Aucune ingestion" in str(resultat.skip_message)

    def test_une_ingestion_qui_echoue_seule_ne_declenche_rien(self):
        # Rien n'est entre dans les stores : il n'y a rien a rendre cherchable.
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, 3, statut=DagsterRunStatus.FAILURE)
            resultat, _ = _tick(instance)
        assert isinstance(resultat, SkipReason)
        assert "Aucune ingestion" in str(resultat.skip_message)

    def test_une_rafale_partiellement_rouge_reindexe_quand_meme(self):
        # Les documents deja passes sont dans les stores : les taire en
        # recherche lexicale serait pire que le document manquant.
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, 3)
            create_run_for_test(instance, job_name=JOB_INGESTION, status=DagsterRunStatus.FAILURE)
            resultat, _ = _tick(instance)
        assert isinstance(resultat, RunRequest)


class TestUrlVide:
    """Desactiver l'appel est un choix ; lancer des runs vides ne l'est pas."""

    def test_le_sensor_saute_et_dit_pourquoi(self, monkeypatch):
        monkeypatch.setenv("AGENT_SERVICE_URL", "")
        get_settings.cache_clear()
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, 3)
            resultat, _ = _tick(instance)
        assert isinstance(resultat, SkipReason)
        assert "AGENT_SERVICE_URL est vide" in str(resultat.skip_message)


class TestLAssetDeReindexation:
    """Ce que le run produit : l'appel, et sa trace."""

    def test_l_appel_part_une_fois_et_le_compte_est_publie(self, monkeypatch):
        appels: list[str] = []

        def repondre(url, headers=None, timeout=None, **_):
            appels.append(url)
            return _Reponse({"chunks_indexed": 8421})

        monkeypatch.setattr(requests, "post", repondre)
        resultat = materialize([lexical_index])

        assert appels == [f"{URL_AGENT}/reindex"]
        metadata = resultat.asset_materializations_for_node("agent__lexical_index")[0].metadata
        assert metadata["chunks_indexed"].value == 8421
        assert "ok" in metadata["reindex"].value

    def test_un_echec_ne_rougit_pas_le_run_mais_le_crie(self, espion):
        resultat = materialize([lexical_index])

        assert resultat.success is True
        metadata = resultat.asset_materializations_for_node("agent__lexical_index")[0].metadata
        assert "ECHEC" in metadata["reindex"].value
        assert "ConnectionError" in metadata["reindex"].value

    def test_url_vide_le_dit_dans_les_metadonnees(self, espion, monkeypatch):
        monkeypatch.setenv("AGENT_SERVICE_URL", "")
        get_settings.cache_clear()
        resultat = materialize([lexical_index])

        assert espion.appels == []
        metadata = resultat.asset_materializations_for_node("agent__lexical_index")[0].metadata
        assert "non appele" in metadata["reindex"].value


class TestLeSensorEstLivreArme:
    """Le lot entier est inerte si le sensor arrive a l'arret.

    Un sensor sans ``default_status`` est livre STOPPED : Dagster le charge, il
    apparait dans l'interface, et il ne tourne jamais. Aucune ingestion ne
    reindexe plus rien, et rien ne rougit — c'est la panne muette que ce lot
    existe pour supprimer, revenue par la porte du deploiement.

    Retirer la ligne ``default_status=DefaultSensorStatus.RUNNING`` laissait
    toute la suite verte. L'assertion porte donc sur l'objet PRODUIT par
    ``build_reindex`` et sur celui que ``definitions.py`` livre reellement,
    jamais sur la presence du mot dans la source.
    """

    def test_le_sensor_construit_est_arme(self):
        built = build_reindex([JOB_INGESTION])
        assert built.sensor.default_status is DefaultSensorStatus.RUNNING

    def test_le_sensor_livre_par_les_definitions_est_arme(self):
        from src.pipeline.definitions import defs

        capteur = next(c for c in defs.sensors if c.name == REINDEX_SENSOR_NAME)
        assert capteur.default_status is DefaultSensorStatus.RUNNING

    def test_l_arme_ne_vient_pas_du_defaut_de_dagster(self):
        # Sinon les deux assertions ci-dessus seraient vraies sans que la ligne
        # existe, et le test serait vert des deux cotes du defaut.
        @sensor(name="temoin_sans_default_status", job_name=REINDEX_JOB_NAME)
        def temoin(context: SensorEvaluationContext) -> SkipReason:
            return SkipReason("temoin")

        assert temoin.default_status is not DefaultSensorStatus.RUNNING


class TestDefinitionsResolvent:
    def test_le_job_et_le_sensor_sont_declares(self):
        built = build_reindex([JOB_INGESTION])
        defs = Definitions(assets=[built.asset], jobs=[built.job], sensors=[built.sensor])
        assert defs.resolve_job_def(REINDEX_JOB_NAME) is not None
        assert built.sensor.name == REINDEX_SENSOR_NAME


class TestLeCablageReel:
    """Le sensor doit surveiller TOUTES les sources declarees, pas une liste figee.

    C'est le defaut d'oubli le plus probable de ce montage : ajouter une source
    dans ``sources.yaml`` et ne pas la brancher au sensor. La reindexation
    partirait alors au milieu de son ingestion.
    """

    def _sensor_reel(self):
        from src.pipeline.definitions import defs

        return next(capteur for capteur in defs.sensors if capteur.name == REINDEX_SENSOR_NAME)

    def test_il_y_a_bien_plusieurs_sources_a_surveiller(self):
        # Sinon la boucle ci-dessous ne prouverait qu'un cas.
        assert len(load_sources()) >= 2

    def test_chaque_source_declaree_retient_la_reindexation(self):
        capteur = self._sensor_reel()
        for source in load_sources():
            nom_job = f"{source.name}_job"
            with DagsterInstance.ephemeral() as instance:
                create_run_for_test(instance, job_name=nom_job, status=DagsterRunStatus.SUCCESS)
                create_run_for_test(instance, job_name=nom_job, status=DagsterRunStatus.STARTED)
                resultat = capteur(build_sensor_context(instance=instance))
            assert isinstance(resultat, SkipReason), f"{nom_job} n'est pas surveille"
            assert nom_job in str(resultat.skip_message)

    def test_l_asset_et_le_job_sont_dans_les_definitions(self):
        from src.pipeline.definitions import defs

        assert defs.resolve_job_def(REINDEX_JOB_NAME) is not None
        assert any(a.key.to_user_string() == "agent/lexical_index" for a in defs.assets)
