"""Declenchement de ``POST /reindex`` : un job dedie, arme quand l'ingestion est retombee.

Le contrat avec ``rag-agent-chat`` place l'appel « en fin de pipeline, une fois
l'ingestion terminee ». Un appel par partition declencherait une reconstruction
BM25 complete et synchrone cote agent par document (300 s de plafond chacune),
dont seule la derniere servirait. Ce module fait un appel par rafale
d'ingestion.

**Definition de « fin d'ingestion ».** L'architecture n'offre aucun instant ou
Dagster sait que « le pipeline a fini » : un job par source, un run par fichier,
des partitions dynamiques creees au fil de l'eau par un sensor. La fin
d'ingestion est donc un etat, pas un evenement : *aucun run d'ingestion n'est en
vol, et au moins un a reussi depuis la derniere reindexation*. Le nombre
d'appels ne depend plus du nombre de documents.

Limite : un corpus qui arrive au goutte-a-goutte (un fichier, une pause, un
fichier) forme une suite de rafales, donc une suite de reindexations. C'est
voulu, car un document ingere doit devenir cherchable : l'appel a lieu une fois
par rafale, pas une fois dans l'absolu.

**Pourquoi un job separe plutot qu'un asset aval.** Un asset aval de
``extracted_document`` se materialiserait dans le run de chaque partition, donc
une fois par document. Un ``run_status_sensor`` sur SUCCESS se declencherait lui
aussi une fois par run, et demanderait le meme controle « rien d'autre en vol »,
avec un harnais de test plus lourd. Un asset check verifie, il n'agit pas. Reste
un job separe, arme par un sensor qui lit l'etat des runs d'ingestion.

**Pourquoi aucune dependance declaree vers les assets d'ingestion.** Le
declenchement est temporel (« plus rien en vol »), pas lie aux partitions.
Dagster ne sait pas exprimer « toutes les partitions, quand plus aucune n'est en
cours » : une dependance sur des partitions dynamiques ferait croire a une
fraicheur par partition que ce job ne fournit pas.

Proprietes de l'appel :

1. **Un echec ne fait jamais echouer une ingestion reussie.** L'appel vit dans
   son propre run, et ``request_reindex`` ne leve pas. Seul le run de
   reindexation passe en echec, jamais celui qui a converti les pages.
2. **Un run de reindexation en echec est retente jusqu'a ce qu'il reussisse**
   (voir ci-dessous).
3. **Une URL vide desactive l'appel.** ``definitions.py`` l'annonce au
   chargement, et le sensor le repete a chaque tick dans sa raison de saut au
   lieu de lancer des runs inutiles.

**Pourquoi le sensor ne garde aucun etat propre.** Un curseur avance a
l'emission de la demande serait en avance sur le travail reellement fait si le
run echoue ou si l'agent est injoignable : le tick suivant repondrait « rien de
nouveau », et la reindexation serait perdue. Un ``run_key`` consomme l'est pour
toujours (Dagster le cherche dans tout l'historique), donc remettre le curseur a
zero n'y changerait rien.

Le sensor compare donc deux faits lus dans l'historique des runs, qu'aucun tick
n'a besoin d'ecrire :

- le repere de la derniere **ingestion** reussie ;
- le repere de la derniere **reindexation** reussie.

Le repere est un ``storage_id``, entier croissant attribue par le stockage de
Dagster a la creation du run. La reindexation n'est armee que lorsque plus rien
n'est en vol, donc son run est toujours cree apres l'ingestion qu'elle traite :
comparer les deux reperes revient a demander « la derniere reindexation reussie
est-elle posterieure a la derniere ingestion reussie ? ».

Consequences voulues :

- **une reindexation echouee est retentee au tick suivant**, sans limite, tant
  qu'elle n'a pas reussi. Un agent arrete deux heures produit deux heures de
  runs en echec : c'est bruyant, mais une reprise bornee finirait par perdre la
  reindexation en silence ;
- **le ``run_key`` change a chaque tentative** : il porte le repere de la
  derniere tentative en plus de celui de la rafale. Il reste deterministe au
  sein d'un tick, donc deux evaluations concurrentes du sensor ne creent pas
  deux runs ;
- **une reindexation lancee a la main depuis l'interface compte**. Si elle
  reussit, le sensor n'en redemande pas : l'index est reconstruit, quel que soit
  l'auteur du run.

NB : pas de ``from __future__ import annotations`` ici — Dagster valide le type
reel de l'argument ``context``, pas sa forme differee en chaine.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dagster import (
    AssetExecutionContext,
    AssetsDefinition,
    AssetSelection,
    DagsterInstance,
    DagsterRunStatus,
    DefaultSensorStatus,
    RunRequest,
    RunsFilter,
    SensorDefinition,
    SensorEvaluationContext,
    SkipReason,
    asset,
    define_asset_job,
    sensor,
)

if TYPE_CHECKING:
    from dagster._core.definitions.unresolved_asset_job_definition import (
        UnresolvedAssetJobDefinition,
    )

from src.pipeline.reindex import request_reindex
from src.pipeline.settings import get_settings

logger = logging.getLogger(__name__)

REINDEX_JOB_NAME = "agent_reindex_job"
REINDEX_SENSOR_NAME = "agent_reindex_sensor"

# Les trois seuls etats dont un run Dagster ne revient pas.
STATUTS_TERMINES = frozenset(
    {
        DagsterRunStatus.SUCCESS,
        DagsterRunStatus.FAILURE,
        DagsterRunStatus.CANCELED,
    }
)

# « En cours » se definit par soustraction, et non par une liste des etats
# actifs : un statut ajoute par une version future de Dagster est ainsi compte
# comme en vol, et non comme termine.
#
# QUEUED en fait partie, et c'est le cas courant en production : le sensor de
# source cree tous les runs d'un depot de fichiers en un seul passage, et la
# file Dagster n'en execute que deux a la fois (`max_concurrent_runs` dans
# dagster.yaml). Les autres attendent en QUEUED.
STATUTS_EN_COURS = tuple(statut for statut in DagsterRunStatus if statut not in STATUTS_TERMINES)


class ReindexError(Exception):
    """L'appel a l'agent a eu lieu et n'a pas abouti.

    Nommee a l'anglaise, comme ``NebulaError`` du service d'extraction : c'est
    la convention du depot pour les exceptions, et celle que ruff impose (N818).

    Elle ne traverse jamais un run d'ingestion : elle ne peut etre levee que par
    l'asset ``agent/lexical_index``, qui vit dans son propre job.
    """


@dataclass
class ReindexDefinitions:
    """Objets Dagster qui portent la reindexation de l'agent."""

    asset: AssetsDefinition
    job: "UnresolvedAssetJobDefinition"
    sensor: SensorDefinition


@asset(
    name="lexical_index",
    key_prefix="agent",
    group_name="agent",
    description="Index lexical BM25 de rag-agent-chat, reconstruit en fin d'ingestion.",
)
def lexical_index(context: AssetExecutionContext) -> None:
    """Demande a l'agent de reconstruire son index lexical, et rend compte.

    **Leve si l'appel a ete tente et n'a pas abouti.** Ce run ne contient que
    l'appel HTTP : une reprise ne coute qu'un appel.

    L'echec du run rend la panne visible : un run reussi portant « ECHEC » dans
    une metadonnee ne declenche aucune alerte et n'apparait dans aucun filtre
    d'echec. C'est aussi ce que le sensor relit pour decider s'il doit
    retenter : sans lui, une reindexation perdue ressemblerait a une
    reindexation faite.

    Une URL vide ne leve pas : l'appel n'a pas ete tente, c'est un choix de
    configuration annonce au chargement, pas une panne.

    Raises:
        ReindexError: Si l'appel a eu lieu et n'a pas abouti.
    """
    settings = get_settings()
    resultat = request_reindex(
        settings.agent_service_url,
        api_key=settings.agent_api_key,
        timeout=settings.reindex_timeout_seconds,
    )
    if resultat.called and not resultat.ok:
        context.log.error(
            f"POST /reindex non honore ({resultat.detail}). Les documents sont bien ingeres, "
            "mais ils resteront invisibles en recherche LEXICALE cote agent tant que cet "
            "appel n'aura pas abouti. Le sensor le retentera au prochain tick."
        )
        raise ReindexError(
            f"POST /reindex n'a pas abouti sur {settings.agent_service_url} : {resultat.detail}"
        )

    if resultat.ok:
        context.log.info(f"rag-agent-chat reindexe : {resultat.chunks_indexed} chunks")
    else:
        context.log.warning(resultat.detail)
    metadonnees: dict[str, Any] = {"reindex": resultat.metadata_value}
    if resultat.chunks_indexed is not None:
        metadonnees["chunks_indexed"] = resultat.chunks_indexed
    context.add_output_metadata(metadonnees)


agent_reindex_job = define_asset_job(
    name=REINDEX_JOB_NAME,
    selection=AssetSelection.assets(lexical_index),
    description="Reconstruit l'index lexical de rag-agent-chat, une fois l'ingestion retombee.",
)


def _decrire_le_run(record: Any) -> str:
    """Nomme un run et son age, pour une raison de saut lisible.

    Un `SkipReason` qui ne nomme que le job est le meme au premier tick et au
    dix-millieme. Le run et son age rendent un blocage visible. Le delai au-dela
    duquel un run bloque est passe en echec vit dans `dagster.yaml`
    (registre 4.15).

    Args:
        record: Enregistrement de run rendu par `get_run_records`.

    Returns:
        Une phrase, ou une phrase degradee si l'horodatage manque — un run sans
        date de debut ne doit pas faire echouer le tick du sensor.
    """
    run = getattr(record, "dagster_run", None)
    identifiant = getattr(run, "run_id", None) or "inconnu"
    statut = getattr(getattr(run, "status", None), "value", "?")
    debut = getattr(record, "start_time", None)
    if not debut:
        return f"Le run {identifiant} est en {statut}, depuis une date inconnue."
    age = max(0, int(time.time() - float(debut)))
    return f"Le run {identifiant} est en {statut} depuis {age} s."


def _derniere_ingestion_reussie(instance: DagsterInstance, job_names: Sequence[str]) -> int | None:
    """Repere de la derniere ingestion reussie, ou ``None`` s'il n'y en a pas.

    Le repere est le ``storage_id`` du run le plus recent, un entier croissant
    attribue par le stockage de Dagster. Il sert de curseur : tant qu'il ne
    bouge pas, rien n'a ete ingere depuis la derniere reindexation, et il n'y a
    donc rien a reconstruire.

    Args:
        instance: Instance Dagster interrogee.
        job_names: Noms des jobs d'ingestion, un par source.

    Returns:
        Le repere, ou ``None`` si aucun run d'ingestion n'a jamais reussi.
    """
    reperes = [
        record.storage_id
        for nom in job_names
        for record in instance.get_run_records(
            RunsFilter(job_name=nom, statuses=[DagsterRunStatus.SUCCESS]), limit=1
        )
    ]
    return max(reperes) if reperes else None


def _dernier_repere(
    instance: DagsterInstance, job_name: str, statuts: Sequence[DagsterRunStatus] | None = None
) -> int | None:
    """Repere du run le plus recent de ce job, ``None`` s'il n'y en a aucun.

    Args:
        instance: Instance Dagster interrogee.
        job_name: Nom du job.
        statuts: Statuts retenus. Tous, si omis.

    Returns:
        Le ``storage_id`` du run le plus recent, ou ``None``.
    """
    records = instance.get_run_records(
        RunsFilter(job_name=job_name, statuses=list(statuts) if statuts else None), limit=1
    )
    return records[0].storage_id if records else None


def build_reindex(ingestion_job_names: Sequence[str]) -> ReindexDefinitions:
    """Assemble l'asset, le job et le sensor de reindexation.

    Args:
        ingestion_job_names: Noms des jobs d'ingestion a surveiller, un par
            source. Un run d'un autre job — celui-ci compris — ne compte ni
            comme ingestion en vol, ni comme ingestion a reindexer.

    Returns:
        L'asset ``agent/lexical_index``, son job, et le sensor qui l'arme.
    """
    job_names = tuple(ingestion_job_names)

    @sensor(
        name=REINDEX_SENSOR_NAME,
        job_name=REINDEX_JOB_NAME,
        minimum_interval_seconds=30,
        default_status=DefaultSensorStatus.RUNNING,
        description="Arme la reindexation quand plus aucun run d'ingestion n'est en vol.",
    )
    def agent_reindex_sensor(context: SensorEvaluationContext) -> RunRequest | SkipReason:
        if not get_settings().agent_service_url.strip():
            return SkipReason(
                "AGENT_SERVICE_URL est vide : POST /reindex est desactive. Les documents "
                "ingeres resteront invisibles en recherche lexicale cote rag-agent-chat."
            )

        for nom in job_names:
            en_cours = context.instance.get_run_records(
                RunsFilter(job_name=nom, statuses=list(STATUTS_EN_COURS)), limit=1
            )
            if en_cours:
                # Nommer le run et son age distingue un run qui travaille d'un
                # run bloque (registre 4.15) : le run le plus long mesure sur ce
                # corpus dure 111 s, donc un age de plusieurs heures signale un
                # blocage.
                #
                # Le delai au-dela duquel un run est declare mort vit dans
                # `dagster.yaml` (run monitoring du daemon), et non dans chaque
                # sensor.
                return SkipReason(
                    f"Ingestion en cours ({nom}) : la reindexation attend qu'elle "
                    f"retombe. {_decrire_le_run(en_cours[0])}"
                )

        repere = _derniere_ingestion_reussie(context.instance, job_names)
        if repere is None:
            return SkipReason("Aucune ingestion reussie a reindexer.")

        # Une reindexation en vol n'est ni faite ni perdue : attendre son issue.
        # Sans ce controle, chaque tick en lancerait une nouvelle pendant que la
        # premiere travaille.
        derniere_tentative = _dernier_repere(context.instance, REINDEX_JOB_NAME)
        en_vol = _dernier_repere(context.instance, REINDEX_JOB_NAME, STATUTS_EN_COURS)
        if en_vol is not None:
            en_vol_records = context.instance.get_run_records(
                RunsFilter(job_name=REINDEX_JOB_NAME, statuses=list(STATUTS_EN_COURS)), limit=1
            )
            details = _decrire_le_run(en_vol_records[0]) if en_vol_records else ""
            return SkipReason(
                f"Une reindexation est deja en vol : la suivante attend son issue. {details}"
            )

        # Seul un run de reindexation reussi clot la rafale, pas une demande
        # emise. Tant qu'il n'existe pas, le sensor rearme.
        reussie = _dernier_repere(context.instance, REINDEX_JOB_NAME, [DagsterRunStatus.SUCCESS])
        if reussie is not None and reussie > repere:
            return SkipReason(
                "Rien de nouveau n'a ete ingere depuis la derniere reindexation reussie."
            )

        # La cle porte la rafale et la tentative. Un run_key consomme l'est pour
        # toujours : une cle identique d'une tentative a l'autre empecherait
        # toute reprise.
        return RunRequest(run_key=f"reindex-{repere}-apres-{derniere_tentative or 0}")

    return ReindexDefinitions(
        asset=lexical_index, job=agent_reindex_job, sensor=agent_reindex_sensor
    )
