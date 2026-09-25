"""Tests du declenchement de ``POST /reindex`` : quand, et combien de fois.

``test_reindex.py`` couvre ce que fait l'appel. Ce fichier couvre la propriete
que le contrat avec ``rag-agent-chat`` enonce : l'appel a lieu **en fin
d'ingestion**, pas une fois par document.

Verifier seulement « l'appel a lieu » passerait avec un appel par document
comme avec un seul. Les tests verifient donc un nombre d'appels, du cote qui le
produit : l'asset d'ingestion pour zero, le sensor de reindexation pour un.

Deux controles empechent un faux succes :

- ``TestLEspionFonctionne`` verifie que l'interception voit reellement passer
  un appel ; sinon, une interception cassee rendrait « zero appel » vrai pour
  une mauvaise raison ;
- ``test_l_ingestion_a_bien_eu_lieu`` verifie que les materialisations ont
  reellement tourne : zero appel sur zero ingestion ne prouve rien.
"""

from __future__ import annotations

import re

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
from dagster._core.events import DagsterEvent, DagsterEventType
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

# Tailles de rafale exercees : trois ordres de grandeur, sans lien avec un
# corpus reel, pour que le nombre d'appels puisse differer du nombre de
# documents. Avec une seule taille (1), un appel par document passerait.
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


def _rafale(
    instance, documents: int, statut=DagsterRunStatus.SUCCESS, job_name: str = JOB_INGESTION
) -> None:
    """Simule une rafale : `documents` runs d'ingestion, un par fichier.

    Les runs crees n'ont pas de `start_time` : `create_run_for_test` ecrit une
    ligne de run, pas un evenement de demarrage. Pour un run horodate, voir
    :func:`_demarrer_un_run`.
    """
    for _ in range(documents):
        create_run_for_test(instance, job_name=job_name, status=statut)


def _demarrer_un_run(instance, job_name: str = JOB_INGESTION) -> str:
    """Un run reellement demarre : en `STARTED`, avec son `start_time`.

    L'horodatage est pose par le mecanisme de Dagster, et non a la main : le
    stockage renseigne `start_time` en traitant un evenement `PIPELINE_START`
    (`sql_run_storage.py`, branche
    `event.event_type == DagsterEventType.PIPELINE_START`). Le meme evenement
    fait passer le run en `STARTED`, donc en vol pour le sensor.

    Sans horodatage, `_decrire_le_run` prend sa branche degradee (« depuis une
    date inconnue ») et la branche qui calcule l'age n'est jamais testee.

    Args:
        instance: Instance Dagster ephemere.
        job_name: Nom du job du run.

    Returns:
        L'identifiant du run demarre.
    """
    run = create_run_for_test(instance, job_name=job_name, status=DagsterRunStatus.STARTING)
    instance.report_dagster_event(
        DagsterEvent(
            event_type_value=DagsterEventType.PIPELINE_START.value,
            job_name=job_name,
        ),
        run_id=run.run_id,
    )
    return run.run_id


class _Capteur:
    """Le sensor tel que Dagster le fait tourner : des ticks qui se suivent.

    Le curseur laisse par un tick est repasse au suivant, comme le fait le
    daemon. Un harnais qui reconstruirait un contexte neuf a chaque tick
    effacerait l'etat pose par le sensor, et des tests d'enchainement
    passeraient a tort. Le harnais transporte le curseur tel quel, vide
    compris, sans rien supposer de son contenu.
    """

    def __init__(self, instance, job_names=(JOB_INGESTION,), sensor=None) -> None:
        self.instance = instance
        self.sensor = sensor if sensor is not None else build_reindex(list(job_names)).sensor
        self.curseur: str | None = None

    def tick(self):
        """Fait tourner le sensor une fois.

        Returns:
            Ce qu'il decide.
        """
        context = build_sensor_context(instance=self.instance, cursor=self.curseur)
        decision = self.sensor(context)
        self.curseur = context.cursor
        return decision


def _tick(instance, job_names=(JOB_INGESTION,)):
    """Fait tourner le sensor une seule fois, sur une instance vierge de ticks.

    Returns:
        Ce que le sensor decide.
    """
    return _Capteur(instance, job_names).tick()


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

    Ni ``request_reindex`` ni ``_reindex`` ne sont remplaces : c'est justement
    le nombre de fois qu'ils sont appeles que ces tests verifient.
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
        # Publier le bilan d'extraction ne fait aucun appel reseau : aucune cle
        # `reindex` ne doit figurer dans les metadonnees de l'asset.
        resultats = _ingerer(monkeypatch, tmp_path, 1)
        materialisations = resultats[0].asset_materializations_for_node("pdfs__extracted_document")
        assert "reindex" not in materialisations[0].metadata


class TestLeSensorNArmeQuUneFois:
    """L'autre moitie de la propriete : une rafale de N documents, une demande.

    L'asset d'ingestion ne fait aucun appel (ci-dessus) ; le nombre d'appels
    se decide ici. Le compte est verifie pour chaque taille de ``RAFALES`` :
    s'il suivait le nombre de documents, seul N = 1 passerait.
    """

    @pytest.mark.parametrize("documents", RAFALES)
    def test_une_seule_demande_par_rafale(self, documents):
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, documents)
            resultat = _tick(instance)
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
            resultat = _tick(instance)
        assert isinstance(resultat, SkipReason)
        assert "Ingestion en cours" in str(resultat.skip_message)

    def test_queued_compte_comme_en_vol(self):
        # Cas courant en production : le sensor de source cree les N runs en un
        # passage, la file n'en execute que deux, les autres attendent. Un run
        # QUEUED ne peut pas etre fabrique sur une instance de test (Dagster
        # exige une origine de job distante), d'ou l'assertion sur la table des
        # statuts plutot que sur le comportement.
        assert DagsterRunStatus.QUEUED in STATUTS_EN_COURS

    def test_aucun_statut_terminal_ne_bloque(self):
        assert set(STATUTS_EN_COURS).isdisjoint(STATUTS_TERMINES)
        assert set(STATUTS_EN_COURS) | STATUTS_TERMINES == set(DagsterRunStatus)

    def test_un_second_tick_ne_redemande_rien_une_fois_la_rafale_reindexee(self):
        # Seule une reindexation reussie clot la rafale, pas l'emission de la
        # demande. Sans le run reussi ci-dessous, le sensor doit rearmer (voir
        # TestUnEchecDeReindexationNEstPasPerdu).
        with DagsterInstance.ephemeral() as instance:
            capteur = _Capteur(instance)
            _rafale(instance, 3)
            premier = capteur.tick()
            assert isinstance(premier, RunRequest)
            _reindexation(instance, statut=DagsterRunStatus.SUCCESS)
            second = capteur.tick()
        assert isinstance(second, SkipReason)
        assert "Rien de nouveau" in str(second.skip_message)

    def test_une_nouvelle_rafale_rearme(self):
        with DagsterInstance.ephemeral() as instance:
            capteur = _Capteur(instance)
            _rafale(instance, 3)
            capteur.tick()
            _reindexation(instance, statut=DagsterRunStatus.SUCCESS)
            _rafale(instance, 2)
            resultat = capteur.tick()
        assert isinstance(resultat, RunRequest)

    def test_le_run_de_reindexation_ne_se_compte_pas_lui_meme(self):
        # Sans quoi le sensor s'auto-entretiendrait : sa propre execution
        # deplacerait le repere, et la reindexation ne s'arreterait jamais.
        with DagsterInstance.ephemeral() as instance:
            capteur = _Capteur(instance)
            _rafale(instance, 2)
            premier = capteur.tick()
            assert isinstance(premier, RunRequest)
            _reindexation(instance, statut=DagsterRunStatus.SUCCESS)
            second = capteur.tick()
            assert isinstance(second, SkipReason)
            _reindexation(instance, statut=DagsterRunStatus.SUCCESS)
            troisieme = capteur.tick()
        assert isinstance(troisieme, SkipReason)

    def test_un_job_etranger_ne_declenche_rien(self):
        with DagsterInstance.ephemeral() as instance:
            create_run_for_test(instance, job_name="autre_job", status=DagsterRunStatus.SUCCESS)
            resultat = _tick(instance)
        assert isinstance(resultat, SkipReason)
        assert "Aucune ingestion" in str(resultat.skip_message)

    def test_une_ingestion_qui_echoue_seule_ne_declenche_rien(self):
        # Rien n'est entre dans les stores : il n'y a rien a rendre cherchable.
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, 3, statut=DagsterRunStatus.FAILURE)
            resultat = _tick(instance)
        assert isinstance(resultat, SkipReason)
        assert "Aucune ingestion" in str(resultat.skip_message)

    def test_une_rafale_partiellement_rouge_reindexe_quand_meme(self):
        # Les documents deja ingeres sont dans les stores : les laisser
        # invisibles en recherche lexicale serait pire que le document manquant.
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, 3)
            create_run_for_test(instance, job_name=JOB_INGESTION, status=DagsterRunStatus.FAILURE)
            resultat = _tick(instance)
        assert isinstance(resultat, RunRequest)


def _reindexation(instance, statut=DagsterRunStatus.SUCCESS) -> None:
    """Enregistre un run du job de reindexation, dans l'etat voulu."""
    create_run_for_test(instance, job_name=REINDEX_JOB_NAME, status=statut)


def _cause_du_rouge(resultat) -> str:
    """Texte de l'erreur qui a fait echouer le run, chaine des causes comprise."""
    echecs = [e for e in resultat.all_events if e.event_type_value == "STEP_FAILURE"]
    assert echecs, "le run n'a pas echoue : il n'y a aucune cause a lire"
    erreur = echecs[0].event_specific_data.error
    morceaux = []
    while erreur is not None:
        morceaux.append(erreur.message or "")
        erreur = erreur.cause
    return "\n".join(morceaux)


class TestUnEchecDeReindexationNEstPasPerdu:
    """Une reindexation echouee est retentee jusqu'a ce qu'elle reussisse.

    Deux mecanismes y concourent. L'asset leve quand l'appel echoue : le run
    echoue, seul signal qu'une supervision Dagster sait lire. Et le sensor ne
    garde aucun etat propre : il compare le repere de la derniere ingestion
    reussie a celui de la derniere reindexation reussie, deux faits deja
    presents dans l'historique des runs. Tant que la reindexation n'a pas
    reussi, le sensor rearme, sans limite.

    Un curseur avance a l'emission de la demande perdrait la reindexation des
    le premier echec : le tick suivant repondrait « Rien de nouveau n'a ete
    ingere », et un run_key consomme l'est pour toujours.
    """

    def test_un_echec_de_reindexation_rougit_son_run(self, espion):
        # Un run vert portant « ECHEC » dans une metadonnee n'est pas une
        # visibilite : aucune alerte Dagster ne se declenche dessus.
        resultat = materialize([lexical_index], raise_on_error=False)
        assert resultat.success is False

    def test_le_run_rouge_nomme_la_panne(self, espion):
        resultat = materialize([lexical_index], raise_on_error=False)
        assert "ConnectionError" in _cause_du_rouge(resultat)

    def test_une_reindexation_echouee_est_retentee_au_tick_suivant(self):
        # Test central : apres un echec, ce second tick doit redemander la
        # reindexation, et non repondre « Rien de nouveau n'a ete ingere ».
        with DagsterInstance.ephemeral() as instance:
            capteur = _Capteur(instance)
            _rafale(instance, 3)
            premier = capteur.tick()
            assert isinstance(premier, RunRequest)
            _reindexation(instance, statut=DagsterRunStatus.FAILURE)
            second = capteur.tick()
        assert isinstance(second, RunRequest), f"reindexation perdue : {second}"

    def test_la_reprise_ne_rejoue_pas_un_run_key_deja_consomme(self):
        # Dagster cherche un run_key dans tout l'historique et refuse de
        # recreer un run pour un run_key deja vu : une reprise avec la meme cle
        # n'aurait pas lieu.
        with DagsterInstance.ephemeral() as instance:
            capteur = _Capteur(instance)
            _rafale(instance, 3)
            premier = capteur.tick()
            _reindexation(instance, statut=DagsterRunStatus.FAILURE)
            second = capteur.tick()
            _reindexation(instance, statut=DagsterRunStatus.FAILURE)
            troisieme = capteur.tick()
        cles = [premier.run_key, second.run_key, troisieme.run_key]
        assert len(set(cles)) == 3, cles

    def test_une_reindexation_annulee_est_retentee(self):
        # CANCELED est un statut terminal qui n'est pas un succes : la rafale
        # n'a pas ete rendue cherchable, il reste quelque chose a faire.
        with DagsterInstance.ephemeral() as instance:
            capteur = _Capteur(instance)
            _rafale(instance, 3)
            capteur.tick()
            _reindexation(instance, statut=DagsterRunStatus.CANCELED)
            resultat = capteur.tick()
        assert isinstance(resultat, RunRequest)

    def test_une_reindexation_reussie_ferme_la_rafale(self):
        with DagsterInstance.ephemeral() as instance:
            capteur = _Capteur(instance)
            _rafale(instance, 3)
            capteur.tick()
            _reindexation(instance, statut=DagsterRunStatus.SUCCESS)
            resultat = capteur.tick()
        assert isinstance(resultat, SkipReason)
        assert "Rien de nouveau" in str(resultat.skip_message)

    @pytest.mark.parametrize(
        "statut",
        [
            DagsterRunStatus.NOT_STARTED,
            DagsterRunStatus.STARTING,
            DagsterRunStatus.STARTED,
            DagsterRunStatus.CANCELING,
        ],
    )
    def test_rien_ne_repart_pendant_qu_une_reindexation_est_en_vol(self, statut):
        # Sans ce controle, chaque tick lancerait un nouveau run de
        # reindexation pendant que le premier travaille.
        with DagsterInstance.ephemeral() as instance:
            capteur = _Capteur(instance)
            _rafale(instance, 3)
            capteur.tick()
            _reindexation(instance, statut=statut)
            resultat = capteur.tick()
        assert isinstance(resultat, SkipReason)
        assert "deja en vol" in str(resultat.skip_message)

    def test_l_echec_de_l_agent_ne_rougit_aucune_ingestion(self, espion, monkeypatch, tmp_path):
        # L'appel vit dans son propre run : une ingestion reussie reste reussie
        # quoi qu'il advienne de l'agent.
        resultats = _ingerer(monkeypatch, tmp_path, 3)
        assert [resultat.success for resultat in resultats] == [True, True, True]


class TestToutesLesSourcesComptentDansLeRepere:
    """Le repere est le ``max()`` sur toutes les sources, et non celui d'une seule.

    Le reste de ce fichier appelle ``build_reindex([JOB_INGESTION])``, avec un
    seul nom de job : ``max`` et ``min`` y rendent la meme chose. Or
    ``sources.yaml`` declare trois sources.

    Avec ``min``, le repere resterait accroche a la source la plus anciennement
    ingeree : une rafale sur une seconde source ne le ferait pas avancer, et ne
    serait jamais reindexee.
    """

    AUTRE_JOB = "livres_html_job"
    DEUX = (JOB_INGESTION, AUTRE_JOB)

    def test_les_deux_sources_ont_bien_reussi(self):
        # Verifie que le scenario du test suivant est bien atteint ; sinon il
        # passerait sans rien prouver.
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, 2)
            _reindexation(instance, statut=DagsterRunStatus.SUCCESS)
            _rafale(instance, 2, job_name=self.AUTRE_JOB)
            for nom in self.DEUX:
                reussis = instance.get_runs(
                    RunsFilter(job_name=nom, statuses=[DagsterRunStatus.SUCCESS])
                )
                assert len(reussis) == 2, nom

    def test_une_rafale_sur_une_seconde_source_rearme(self):
        with DagsterInstance.ephemeral() as instance:
            capteur = _Capteur(instance, job_names=self.DEUX)
            _rafale(instance, 2)
            assert isinstance(capteur.tick(), RunRequest)
            _reindexation(instance, statut=DagsterRunStatus.SUCCESS)
            assert isinstance(capteur.tick(), SkipReason)

            # Une autre source depose a son tour. Avec min, le repere reste
            # celui de la premiere source, anterieur a la reindexation deja
            # faite, et cette rafale-ci n'est jamais rendue cherchable.
            _rafale(instance, 2, job_name=self.AUTRE_JOB)
            resultat = capteur.tick()
        assert isinstance(resultat, RunRequest), f"seconde source jamais reindexee : {resultat}"

    def test_le_cablage_reel_suit_toutes_les_sources_declarees(self):
        # Le meme enchainement, sur le sensor que livre definitions.py et sur
        # les sources reellement declarees dans sources.yaml, et non sur une
        # liste ecrite par le test.
        from src.pipeline.definitions import defs

        sensor_livre = next(c for c in defs.sensors if c.name == REINDEX_SENSOR_NAME)
        sources = load_sources()
        assert len(sources) >= 2, (
            "il faut deux sources declarees pour que ce test dise quelque chose"
        )
        premier, second = (f"{source.name}_job" for source in sources[:2])

        with DagsterInstance.ephemeral() as instance:
            capteur = _Capteur(instance, sensor=sensor_livre)
            _rafale(instance, 2, job_name=premier)
            assert isinstance(capteur.tick(), RunRequest)
            _reindexation(instance, statut=DagsterRunStatus.SUCCESS)
            _rafale(instance, 2, job_name=second)
            resultat = capteur.tick()
        assert isinstance(resultat, RunRequest), f"{second} n'avance pas le repere : {resultat}"


class TestUrlVide:
    """Une URL vide desactive l'appel : le sensor saute au lieu de lancer des runs inutiles."""

    def test_le_sensor_saute_et_dit_pourquoi(self, monkeypatch):
        monkeypatch.setenv("AGENT_SERVICE_URL", "")
        get_settings.cache_clear()
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, 3)
            resultat = _tick(instance)
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

    def test_un_echec_rougit_le_run_et_le_crie(self, espion):
        # Voir TestUnEchecDeReindexationNEstPasPerdu : un run reussi portant
        # « ECHEC » dans une metadonnee ne declenche aucune alerte, et le
        # sensor croirait la rafale traitee.
        resultat = materialize([lexical_index], raise_on_error=False)

        assert resultat.success is False
        assert "ConnectionError" in _cause_du_rouge(resultat)

    def test_url_vide_le_dit_dans_les_metadonnees(self, espion, monkeypatch):
        monkeypatch.setenv("AGENT_SERVICE_URL", "")
        get_settings.cache_clear()
        resultat = materialize([lexical_index])

        assert espion.appels == []
        metadata = resultat.asset_materializations_for_node("agent__lexical_index")[0].metadata
        assert "non appele" in metadata["reindex"].value


class TestLeSensorEstLivreArme:
    """Le sensor de reindexation est livre actif.

    Un sensor sans ``default_status`` est livre STOPPED : Dagster le charge et
    l'affiche, mais il ne tourne jamais. Plus aucune ingestion n'est alors
    reindexee, sans aucune erreur.

    L'assertion porte sur l'objet produit par ``build_reindex`` et sur celui que
    livre ``definitions.py``, et non sur la presence du mot dans la source.
    """

    def test_le_sensor_construit_est_arme(self):
        built = build_reindex([JOB_INGESTION])
        assert built.sensor.default_status is DefaultSensorStatus.RUNNING

    def test_le_sensor_livre_par_les_definitions_est_arme(self):
        from src.pipeline.definitions import defs

        capteur = next(c for c in defs.sensors if c.name == REINDEX_SENSOR_NAME)
        assert capteur.default_status is DefaultSensorStatus.RUNNING

    def test_l_arme_ne_vient_pas_du_defaut_de_dagster(self):
        # Sinon les deux assertions ci-dessus seraient vraies meme sans la
        # ligne `default_status=...`.
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

    L'oubli le plus probable : ajouter une source dans ``sources.yaml`` sans la
    brancher au sensor. La reindexation partirait alors au milieu de son
    ingestion.
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


class TestLaClassificationDesStatutsTerminaux:
    """Contenu de `STATUTS_TERMINES` (registre 4.17).

    Sans `CANCELED` dans `STATUTS_TERMINES`, une ingestion annulee bloquerait
    la reindexation pour toujours. Verifier que les deux ensembles partitionnent
    `DagsterRunStatus` ne suffit pas, car la soustraction est faite par le code
    lui-meme : ces tests verifient le contenu, contre la version epinglee de
    Dagster.
    """

    def test_les_trois_statuts_terminaux_sont_nommes(self):
        assert (
            frozenset(
                {
                    DagsterRunStatus.SUCCESS,
                    DagsterRunStatus.FAILURE,
                    DagsterRunStatus.CANCELED,
                }
            )
            == STATUTS_TERMINES
        )

    def test_ils_sont_exactement_ceux_que_dagster_declare_finis(self):
        """Les statuts terminaux sont exactement ceux que Dagster declare finis.

        Ce test echoue si une montee de version de Dagster change sa propre
        liste, au lieu de laisser le sensor reindexer au milieu d'une ingestion.
        """
        from dagster import DagsterRunStatus as Statuts
        from dagster._core.storage.dagster_run import FINISHED_STATUSES

        assert frozenset(FINISHED_STATUSES) == STATUTS_TERMINES, (
            "la liste des statuts terminaux de Dagster a change : le sensor "
            "classerait un statut inconnu du mauvais cote"
        )
        assert Statuts.CANCELED in STATUTS_TERMINES, (
            "sans CANCELED, une ingestion ANNULEE bloque la reindexation pour "
            "toujours : le sensor l'attend indefiniment"
        )

    def test_un_statut_non_terminal_est_prudemment_compte_en_vol(self):
        """La soustraction reste le mecanisme : un statut inconnu doit bloquer.

        Les deux erreurs comptent : mal classer un statut terminal bloque la
        reindexation, mal classer un statut non terminal la lance au milieu
        d'une ingestion.
        """
        assert DagsterRunStatus.STARTED in STATUTS_EN_COURS
        assert DagsterRunStatus.STARTING in STATUTS_EN_COURS
        assert DagsterRunStatus.QUEUED in STATUTS_EN_COURS
        assert DagsterRunStatus.CANCELING in STATUTS_EN_COURS


class TestLeSensorDitDepuisCombienDeTempsIlAttend:
    """La raison de saut nomme le run qui bloque et son age (registre 4.15).

    Un run bloque en `STARTED` bloque la reindexation. Le delai au-dela duquel
    il est passe en echec est regle dans `dagster.yaml`. En attendant, la raison
    de saut du sensor nomme le run et depuis combien de temps il est en cours,
    pour distinguer un run qui travaille d'un run bloque.
    """

    def test_la_raison_de_saut_nomme_le_run_qui_bloque(self):
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, 1, DagsterRunStatus.STARTED, JOB_INGESTION)
            resultat = _tick(instance)

        assert isinstance(resultat, SkipReason)
        message = str(resultat.skip_message)
        assert JOB_INGESTION in message
        assert "run " in message, message

    def test_la_raison_de_saut_donne_l_age_du_run_qui_bloque(self):
        """La raison de saut donne l'age du run en secondes.

        Le run est demarre par `_demarrer_un_run`, qui pose l'horodatage par le
        mecanisme de Dagster : c'est la branche qui calcule l'age qui est
        testee, celle qui tourne en production. Mesure sur l'historique Dagster
        du poste de developpement (`SELECT status, count(*), count(start_time)
        FROM runs GROUP BY status`) : 23/23 runs reussis et 67/67 runs echoues
        ont un `start_time` ; seul un run `QUEUED`, jamais demarre, n'en a pas.

        L'assertion porte sur un nombre de secondes, et non sur la presence
        d'une lettre : un simple « s » serait satisfait par le « S » de STARTED
        dans le message degrade « depuis une date inconnue ».
        """
        with DagsterInstance.ephemeral() as instance:
            _demarrer_un_run(instance, JOB_INGESTION)
            resultat = _tick(instance)

        message = str(resultat.skip_message)
        assert "date inconnue" not in message, (
            f"le montage n'a pas pose de start_time : la branche DEGRADEE a "
            f"tourne, et la branche de production n'est pas eprouvee\n{message}"
        )
        secondes = re.search(r"depuis (\d+) s\.", message)
        assert secondes is not None, (
            f"la raison de saut ne porte pas un age en secondes : {message}"
        )
        assert int(secondes.group(1)) < 300, (
            f"l'age lu vaut {secondes.group(1)} s : ce n'est pas l'age d'un run "
            f"cree a l'instant, donc ce n'est pas l'age du run"
        )

    def test_le_montage_pose_bien_l_horodatage_qu_il_croit(self):
        """Controle du montage : `_demarrer_un_run` pose bien `start_time`.

        Si ce n'etait plus le cas, le test precedent echouerait comme si le
        code etait en defaut, alors que le defaut serait dans le montage. Ce
        test situe l'erreur.
        """
        with DagsterInstance.ephemeral() as instance:
            _demarrer_un_run(instance, JOB_INGESTION)
            enregistrements = instance.get_run_records()

            assert len(enregistrements) == 1, enregistrements
            assert enregistrements[0].start_time is not None, (
                "le montage ne pose pas de start_time : la branche de production "
                "de `_decrire_le_run` reste inatteignable par cette suite"
            )
            assert enregistrements[0].dagster_run.status in STATUTS_EN_COURS, (
                "le run n'est pas en vol : le sensor ne le verrait pas bloquer"
            )

    def test_un_run_sans_horodatage_est_dit_degrade_et_ne_fait_pas_echouer_le_tick(self):
        """La branche degradee decrit un etat reel : un run jamais demarre.

        Sur l'historique Dagster mesure, le seul run sans `start_time` est reste
        en attente, jamais demarre (registre 4.28.c). Un tel run ne doit pas
        faire echouer le tick du sensor. Sans ce test, supprimer la branche
        degradee passerait le test precedent.

        Le test reproduit l'absence d'horodatage (une ligne de run sans
        evenement de demarrage), et non le statut `QUEUED`, que
        `create_run_for_test` refuse de fabriquer sans origine de job distante.
        C'est l'absence d'horodatage qui determine la branche.
        """
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, 1, DagsterRunStatus.STARTED, JOB_INGESTION)
            enregistrements = instance.get_run_records()
            assert enregistrements[0].start_time is None, (
                "le cas voulu n'est pas atteint : ce run porte un horodatage"
            )
            resultat = _tick(instance)

        assert isinstance(resultat, SkipReason)
        message = str(resultat.skip_message)
        assert "date inconnue" in message, message
        assert JOB_INGESTION in message, message

    def test_rien_n_est_dit_quand_aucun_run_ne_bloque(self):
        """Sans run en cours, le sensor emet sa demande au lieu d'une raison de saut."""
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, 1)
            resultat = _tick(instance)

        assert isinstance(resultat, RunRequest), resultat
