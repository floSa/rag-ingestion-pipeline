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


class _Capteur:
    """Le sensor tel que Dagster le fait tourner : des ticks qui se suivent.

    Le curseur laisse par un tick est repasse au suivant, comme le daemon le
    fait. C'est indispensable et non cosmetique : un harnais qui reconstruit un
    contexte neuf a chaque tick efface l'etat que le sensor a pu poser, et rend
    alors verts des tests d'enchainement qui devraient etre rouges. Le harnais
    ne suppose rien de ce que le sensor met dans son curseur — il le transporte,
    quel qu'il soit, y compris vide.
    """

    def __init__(self, instance, job_names=(JOB_INGESTION,)) -> None:
        self.instance = instance
        self.sensor = build_reindex(list(job_names)).sensor
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
        # C'est LE cas de production : le sensor de source cree les N runs en un
        # passage, la file n'en execute que deux, les autres attendent. Un run
        # QUEUED ne peut pas etre fabrique sur une instance de test (Dagster
        # exige une origine de job distante), d'ou l'assertion sur la table des
        # statuts plutot que sur le comportement.
        assert DagsterRunStatus.QUEUED in STATUTS_EN_COURS

    def test_aucun_statut_terminal_ne_bloque(self):
        assert set(STATUTS_EN_COURS).isdisjoint(STATUTS_TERMINES)
        assert set(STATUTS_EN_COURS) | STATUTS_TERMINES == set(DagsterRunStatus)

    def test_un_second_tick_ne_redemande_rien_une_fois_la_rafale_reindexee(self):
        # « Ne redemande rien » se merite : c'est la reindexation REUSSIE qui
        # ferme la rafale, pas l'emission de la demande. Sans le run reussi
        # ci-dessous, le sensor doit rearmer — c'est le sujet de
        # TestUnEchecDeReindexationNEstPasPerdu.
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
        # Les documents deja passes sont dans les stores : les taire en
        # recherche lexicale serait pire que le document manquant.
        with DagsterInstance.ephemeral() as instance:
            _rafale(instance, 3)
            create_run_for_test(instance, job_name=JOB_INGESTION, status=DagsterRunStatus.FAILURE)
            resultat = _tick(instance)
        assert isinstance(resultat, RunRequest)


def _reindexation(instance, statut=DagsterRunStatus.SUCCESS) -> None:
    """Enregistre un run du job de reindexation, dans l'etat voulu."""
    create_run_for_test(instance, job_name=REINDEX_JOB_NAME, status=statut)


def _cause_du_rouge(resultat) -> str:
    """Texte de l'erreur qui a fait rougir le run, chaine des causes comprise."""
    echecs = [e for e in resultat.all_events if e.event_type_value == "STEP_FAILURE"]
    assert echecs, "le run n'a pas echoue : il n'y a aucune cause a lire"
    erreur = echecs[0].event_specific_data.error
    morceaux = []
    while erreur is not None:
        morceaux.append(erreur.message or "")
        erreur = erreur.cause
    return "\n".join(morceaux)


class TestUnEchecDeReindexationNEstPasPerdu:
    """Le defaut de comportement : le curseur avancait a l'EMISSION de la demande.

    Deux chemins menaient a la perte, et le premier etait le chemin nominal :
    l'agent injoignable ne faisait pas lever l'asset, le run finissait VERT avec
    une metadonnee « ECHEC », et le curseur avait deja avance ; ou le run
    lui-meme echouait, meme resultat sans meme la metadonnee. Au tick suivant :
    « Rien de nouveau n'a ete ingere ». Et remettre le curseur a zero ne
    sauvait rien, le run_key « reindex-<repere> » etant deterministe et un
    run_key consomme l'etant pour toujours.

    La reparation tient en deux gestes. L'asset LEVE quand l'appel echoue : le
    run rougit, ce qui est la seule visibilite qu'une supervision Dagster sait
    lire. Et le sensor ne tient plus d'etat a lui : il compare le repere de la
    derniere ingestion reussie a celui de la derniere REINDEXATION REUSSIE, deux
    faits que l'historique des runs porte deja. Tant que la reindexation n'a pas
    reussi, le sensor rearme — indefiniment.
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
        # LE test de la reparation. Avant elle, ce second tick rendait
        # « Rien de nouveau n'a ete ingere » et la rafale n'etait jamais
        # reindexee — definitivement, le run_key etant consomme.
        with DagsterInstance.ephemeral() as instance:
            capteur = _Capteur(instance)
            _rafale(instance, 3)
            premier = capteur.tick()
            assert isinstance(premier, RunRequest)
            _reindexation(instance, statut=DagsterRunStatus.FAILURE)
            second = capteur.tick()
        assert isinstance(second, RunRequest), f"reindexation perdue : {second}"

    def test_la_reprise_ne_rejoue_pas_un_run_key_deja_consomme(self):
        # Dagster cherche un run_key dans TOUT l'historique et refuse de
        # recreer un run pour un run_key deja vu. Une reprise qui reutilise la
        # meme cle est une reprise qui n'a pas lieu.
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
        # Sans cette garde, la reprise lancerait un second run de reindexation
        # a chaque tick pendant que le premier travaille.
        with DagsterInstance.ephemeral() as instance:
            capteur = _Capteur(instance)
            _rafale(instance, 3)
            capteur.tick()
            _reindexation(instance, statut=statut)
            resultat = capteur.tick()
        assert isinstance(resultat, SkipReason)
        assert "deja en vol" in str(resultat.skip_message)

    def test_l_echec_de_l_agent_ne_rougit_aucune_ingestion(self, espion, monkeypatch, tmp_path):
        # La propriete que la reparation ne doit PAS perdre : l'appel vit dans
        # son propre run, une ingestion reussie reste verte quoi qu'il advienne
        # de l'agent.
        resultats = _ingerer(monkeypatch, tmp_path, 3)
        assert [resultat.success for resultat in resultats] == [True, True, True]


class TestUrlVide:
    """Desactiver l'appel est un choix ; lancer des runs vides ne l'est pas."""

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
        # Voir TestUnEchecDeReindexationNEstPasPerdu pour le pourquoi : un run
        # vert portant « ECHEC » dans une metadonnee n'alerte personne, et
        # laissait le sensor croire la rafale traitee.
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
