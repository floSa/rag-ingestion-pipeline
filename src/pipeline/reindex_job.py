"""Quand ``POST /reindex`` part : un job a part, arme quand l'ingestion se tait.

Le contrat avec ``rag-agent-chat`` dit « en fin de pipeline, une fois
l'ingestion terminee ». Le code disait la meme chose et faisait autre chose :
``factory._record_metadata`` postait, et il tourne UNE FOIS PAR PARTITION.
Cela faisait donc autant de reconstructions BM25 completes et synchrones cote
agent qu'il y a de documents, a 300 s de plafond chacune, dont seule la
derniere servait — et le cout suivait la taille du corpus, quelle qu'elle soit.
Et une fonction nommee « publier le bilan d'extraction » n'est pas a la hauteur
d'un appel reseau vers un autre service.

**Ce que « fin d'ingestion » veut dire ici.** L'architecture n'offre aucun point
de fin evident : un job par source, un run par fichier, des partitions
dynamiques creees au fil de l'eau par un sensor. Il n'existe pas d'instant ou
Dagster sait que « le pipeline a fini » — il n'y a que des runs qui vont et
viennent. La fin d'ingestion est donc definie ici comme un ETAT et non comme un
evenement : *aucun run d'ingestion n'est en vol, et au moins un a reussi depuis
la derniere reindexation*. C'est la lecture la plus proche du contrat que cette
architecture permette, et elle a la propriete que l'appel par document n'avait
pas : le nombre d'appels ne depend plus du nombre de documents.

Elle a aussi une limite, qu'il vaut mieux ecrire que decouvrir : un corpus qui
arrive en goutte-a-goutte — un fichier, une pause, un fichier — est une suite
de rafales, donc une suite de reindexations. C'est le comportement voulu (un
document ingere doit devenir cherchable), pas un defaut, mais ce n'est pas
« une seule fois » dans l'absolu : c'est « une seule fois par rafale ».

**Pourquoi un job separe plutot qu'un asset aval.** Un asset aval de
``extracted_document`` se materialiserait dans le run de la partition, donc une
fois par document : le defaut serait deplace, pas corrige. Un
``run_status_sensor`` sur SUCCESS se declenche lui aussi une fois par run et
demanderait la meme garde « rien d'autre en vol » que ce sensor porte, pour un
harnais de test plus lourd. Un asset check verifie, il n'agit pas. Restait le
job separe, arme par un sensor qui lit l'etat des runs d'ingestion.

**Pourquoi pas de dependance declaree vers les assets d'ingestion.** Le
declenchement est temporel (« plus rien en vol »), pas dimensionnel. Dagster n'a
pas de facon d'exprimer « toutes les partitions, quand il n'y en a plus une
seule en cours » : une dependance sur des partitions dynamiques ferait croire a
une fraicheur par partition que ce job ne rend pas.

Les trois proprietes de l'appel survivent, et deux se renforcent :

1. **Un echec ne fait jamais echouer une ingestion reussie.** Il ne le peut
   plus : l'appel vit dans son propre run, et ``request_reindex`` ne leve
   toujours pas. C'est le run de reindexation qui rougit, jamais celui qui a
   converti les pages.
2. **Un echec rougit son run, et il est retente jusqu'a ce qu'il passe.** Voir
   ci-dessous : c'est la propriete qui manquait, et son absence perdait la
   reindexation pour toujours.
3. **Une URL vide desactive l'appel, et c'est annonce au chargement** par
   ``definitions.py``. Le sensor le redit a chaque tick dans sa raison de saut,
   plutot que de lancer des runs qui n'ont rien a faire.

**Pourquoi le sensor ne tient aucun etat a lui.** La premiere version posait un
curseur a l'EMISSION de la demande : le repere avancait des que la demande
partait, avant meme que le run n'ait tourne. Un agent injoignable, ou un run de
reindexation rouge, laissait donc le curseur en avance sur ce qui avait
reellement ete fait — et le tick suivant repondait « rien de nouveau ». La perte
etait definitive, parce qu'un ``run_key`` consomme l'est pour toujours : Dagster
le cherche dans TOUT l'historique et refuse de recreer un run pour une cle deja
vue. Remettre le curseur a zero n'y aurait rien change.

Le sensor compare donc desormais deux FAITS, tous deux lus dans l'historique des
runs, qu'aucun tick n'a besoin d'ecrire :

- le repere de la derniere **ingestion** reussie ;
- le repere de la derniere **reindexation** reussie.

Le repere est un ``storage_id``, entier croissant attribue par le stockage de
Dagster a la creation du run. La reindexation n'est armee que lorsque plus rien
n'est en vol, donc son run est toujours cree apres l'ingestion qu'elle traite :
comparer les deux reperes revient a demander « la derniere reindexation reussie
est-elle posterieure a la derniere ingestion reussie ? ».

Trois consequences, toutes voulues :

- **une reindexation echouee est retentee au tick suivant**, indefiniment, tant
  qu'elle n'a pas reussi. Un agent arrete pendant deux heures produit des runs
  rouges pendant deux heures — c'est bruyant, et c'est le prix a payer pour ne
  jamais perdre en silence ce que le contrat exige. Rendre la reprise finie,
  c'est reintroduire la perte, juste plus tard ;
- **le ``run_key`` varie a chaque tentative** : il porte le repere de la derniere
  tentative en plus de celui de la rafale. Il reste deterministe a l'interieur
  d'un tick — deux evaluations concurrentes du sensor ne creeraient pas deux
  runs —, mais il n'est plus consomme d'avance ;
- **une reindexation lancee a la main depuis l'interface compte**. Si elle
  reussit, le sensor n'en redemande pas une : l'index EST reconstruit, et c'est
  le fait qui decide, pas l'auteur du run.

NB : pas de ``from __future__ import annotations`` ici — Dagster valide le type
reel de l'argument ``context``, pas sa forme differee en chaine.
"""

import logging
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
# actifs. Une enumeration en dur serait une phrase d'exhaustivite : le jour ou
# Dagster ajoute un statut, elle le classerait comme termine et le sensor
# reindexerait au milieu d'une ingestion. Par soustraction, un statut inconnu
# est prudemment compte comme en vol.
#
# QUEUED en fait partie, et c'est le cas de production : le sensor de source
# cree tous les runs d'un depot de fichiers en un seul passage, et la file
# Dagster n'en execute que deux a la fois (`max_concurrent_runs` dans
# dagster.yaml). Les autres attendent, et attendre n'est pas avoir fini.
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

    **Leve si l'appel a ete tente et n'a pas abouti.** La regle precedente etait
    « ne leve jamais », et elle avait une raison qui a disparu avec le
    demenagement : l'appel vivait alors dans le run de la partition, ou une
    reprise aurait reconverti des centaines de pages pour un appel HTTP. Ici,
    une reprise coute UN appel HTTP.

    Rougir est ce qui rend l'echec visible. Un run vert portant « ECHEC » dans
    une metadonnee ne declenche aucune alerte, n'apparait dans aucun filtre
    d'echec, et laissait le sensor croire la rafale traitee. Le run rouge est en
    outre le fait que le sensor relit pour decider s'il doit retenter : sans
    lui, il n'a aucun moyen de distinguer une reindexation faite d'une
    reindexation perdue.

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
            if context.instance.get_run_records(
                RunsFilter(job_name=nom, statuses=list(STATUTS_EN_COURS)), limit=1
            ):
                return SkipReason(
                    f"Ingestion en cours ({nom}) : la reindexation attend qu'elle retombe."
                )

        repere = _derniere_ingestion_reussie(context.instance, job_names)
        if repere is None:
            return SkipReason("Aucune ingestion reussie a reindexer.")

        # Une reindexation en vol n'est ni faite ni perdue : on attend son
        # issue. Sans cette garde, la reprise en lancerait une nouvelle a chaque
        # tick pendant que la premiere travaille.
        derniere_tentative = _dernier_repere(context.instance, REINDEX_JOB_NAME)
        en_vol = _dernier_repere(context.instance, REINDEX_JOB_NAME, STATUTS_EN_COURS)
        if en_vol is not None:
            return SkipReason("Une reindexation est deja en vol : la suivante attend son issue.")

        # Le fait qui ferme la rafale est un run de reindexation REUSSI, pas une
        # demande emise. Tant qu'il n'existe pas, il reste quelque chose a faire
        # et le sensor rearme.
        reussie = _dernier_repere(context.instance, REINDEX_JOB_NAME, [DagsterRunStatus.SUCCESS])
        if reussie is not None and reussie > repere:
            return SkipReason(
                "Rien de nouveau n'a ete ingere depuis la derniere reindexation reussie."
            )

        # La cle porte la rafale ET la tentative. Un run_key consomme l'est pour
        # toujours : une cle qui ne bougerait pas d'une tentative a l'autre
        # serait une reprise qui n'a jamais lieu.
        return RunRequest(run_key=f"reindex-{repere}-apres-{derniere_tentative or 0}")

    return ReindexDefinitions(
        asset=lexical_index, job=agent_reindex_job, sensor=agent_reindex_sensor
    )
