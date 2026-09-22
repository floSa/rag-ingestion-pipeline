"""Factory Dagster : genere partitions, assets, job et sensor pour chaque source.

Toutes les sources (PDF comme HTML) suivent le meme mecanisme :

- une partition dynamique par fichier (cle = chemin relatif a ``Datas/``) ;
- un sensor qui detecte les nouveaux fichiers / modifications via mtime ;
- un job qui materialise les assets de la source pour la partition.

Les sources HTML ont un asset de nettoyage supplementaire en amont de
l'extraction Docling.

NB : pas de ``from __future__ import annotations`` ici — Dagster valide le type
reel de l'argument ``context`` des assets, pas sa forme differee en chaine.
"""

import glob as globlib
import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests
from dagster import (
    AssetExecutionContext,
    AssetIn,
    AssetKey,
    AssetsDefinition,
    AssetSelection,
    Backoff,
    DagsterInstance,
    DefaultSensorStatus,
    DynamicPartitionsDefinition,
    Failure,
    RetryPolicy,
    RunRequest,
    RunsFilter,
    SensorDefinition,
    SensorEvaluationContext,
    SensorResult,
    asset,
    define_asset_job,
    sensor,
)

# Les deux tags que le daemon pose sur un run cree depuis une ``RunRequest``, et
# sur lesquels il interroge l'historique pour decider si la cle est deja
# consommee. Ils sont IMPORTES et non recopies : deux chaines litterales qui
# doivent coincider avec un detail interne de Dagster divergeraient en silence
# le jour d'une montee de version, et le controle ci-dessous cesserait de voir
# quoi que ce soit sans qu'aucun test ne rougisse. Importes, un deplacement du
# module leve au CHARGEMENT — bruyant, donc reparable.
from dagster._core.storage.tags import RUN_KEY_TAG, SENSOR_NAME_TAG

if TYPE_CHECKING:
    from dagster._core.definitions.unresolved_asset_job_definition import (
        UnresolvedAssetJobDefinition,
    )

from src.docling_service.elements import cleaned_path
from src.pipeline.cleaning import clean_html_file
from src.pipeline.media import MinioImageExporter
from src.pipeline.settings import get_settings
from src.pipeline.sources import SourceConfig

# Reprise automatique des echecs transitoires : service d'extraction redemarre
# (sa file vit en memoire), coupure reseau, store momentanement indisponible.
# Sur une ingestion de plusieurs heures sans surveillance, c'est ce qui evite
# de retrouver des partitions rouges pour une raison sans rapport avec les
# documents. Les echecs propres au document, eux, ne sont pas retentes.
EXTRACTION_RETRY_POLICY = RetryPolicy(max_retries=2, delay=120, backoff=Backoff.EXPONENTIAL)

# LE GESTE DE REINGESTION, ET LES TROIS QUESTIONS QU'IL DOIT FERMER AU SITE
# (registre 4.32.a). Le defaut etait un ``run_key`` deterministe sur
# ``(source, partition, mtime)`` : Dagster cherche une cle consommee dans TOUT
# l'historique, sans borne de temps, donc un fichier dont le ``mtime`` n'a pas
# bouge ne pouvait JAMAIS etre reingere par le capteur. `mesure` le 2 septembre
# 2026, curseur vide et verifie a 0 entree : 23 cles demandees, ZERO run cree,
# `skip_reason=None`. Le chiffre vit a son site canonique,
# ``documentation/campagnes/2026-09-02-premiere-campagne-de-reference.md``.
#
# 1. QU'EST-CE QUI DECLENCHE UNE REINGESTION ? Le curseur du capteur, pose a la
#    main sur ``reingerer:<etiquette>``. Rien d'autre. Le tick qui lit ce
#    marqueur oublie les ``mtime`` qu'il connaissait, redemande une partition
#    par fichier, et fait porter L'ETIQUETTE a la cle de run plutot que le
#    ``mtime`` — c'est ce qui la rend neuve pour Dagster.
#
# 2. QU'EST-CE QUI GARANTIT QU'ELLE NE PART PAS SANS QU'ON L'AIT DEMANDEE ? Le
#    curseur, et le fait que SANS marqueur la cle garde exactement sa forme
#    historique ``{source}_{partition}_{mtime}``. Le curseur vit dans le
#    stockage de l'instance, survit au rechargement du code et au redemarrage du
#    daemon, et aucune ligne de ce module ne l'efface. Un deploiement ne
#    reingere donc rien : les cles qu'il reconstruirait sont celles que
#    l'historique porte deja. C'est la propriete que
#    ``TestLaCleNominaleEstInchangee`` fige, et ce n'est pas un detail — jusqu'a
#    ce lot, c'est CE DEFAUT qui protegeait l'index contre une reingestion
#    accidentelle a chaque redemarrage du daemon.
#
# 3. ET SI LE GESTE EST FAIT DEUX FOIS DE SUITE ? Avec la MEME etiquette, le
#    second tick reconstruit les memes cles : Dagster n'en creera aucun run, et
#    c'est exactement le silence du 4.32.a. Le capteur le controle donc AVANT
#    d'emettre, et le DIT avec son compte (:func:`_cles_deja_consommees`). Avec
#    une etiquette NEUVE, la reingestion repart. Le geste est donc repetable, et
#    sa repetition a l'identique est bruyante au lieu d'etre muette.
#
# L'etiquette est libre et obligatoire — une date, un motif. Un marqueur sans
# etiquette est refuse, parce qu'il rendrait le geste non repetable.
PREFIXE_REINGESTION = "reingerer:"


@dataclass
class SourceDefinitions:
    """Objets Dagster generes pour une source."""

    partitions: DynamicPartitionsDefinition
    assets: list[AssetsDefinition]
    job: "UnresolvedAssetJobDefinition"
    sensor: SensorDefinition


def _request_extraction(
    context: AssetExecutionContext, file_path: str, source_path: str
) -> dict[str, Any]:
    """Soumet un fichier au service Docling et suit le job jusqu'a son terme.

    L'extraction ne tient pas dans une requete HTTP : un livre de plusieurs
    centaines de pages depasse tout timeout raisonnable, et la requete bloquee
    faisait echouer le run pendant que le service continuait d'ecrire. On
    soumet, puis on interroge.

    Args:
        context: Contexte d'execution de l'asset (journalisation).
        file_path: Chemin du document, vu par le service.
        source_path: Chemin relatif a ``Datas/`` — la cle de partition. Il
            porte l'identite du document : c'est lui qui distingue deux
            chapitres homonymes appartenant a deux ouvrages differents.

    Returns:
        Bilan du job : elements, chunks, pages.

    Raises:
        RuntimeError: Si le job echoue, ou si le service l'a oublie.
        TimeoutError: Si le plafond par document est atteint.
    """
    settings = get_settings()
    base_url = settings.docling_service_url.rstrip("/")

    _wait_until_ready(context, base_url)
    context.log.info(f"Soumission a l'extraction : {file_path}")
    response = requests.post(
        f"{base_url}/extract",
        json={"filepath": file_path, "source_path": source_path},
        timeout=settings.extraction_submit_timeout,
    )
    response.raise_for_status()
    job_id = str(response.json()["job_id"])
    context.log.info(f"Job {job_id} en file pour {file_path}")

    return _await_job(context, base_url, job_id)


def _wait_until_ready(context: AssetExecutionContext, base_url: str) -> None:
    """Attend que le service d'extraction soit pret avant de lui soumettre un job.

    Au demarrage de la stack, le service charge ses modeles et initialise le
    schema du graphe : soumettre avant condamnerait le premier run pour une
    raison qui n'a rien a voir avec le document.

    Raises:
        RuntimeError: Si le service n'est toujours pas pret au bout du delai.
    """
    settings = get_settings()
    deadline = time.monotonic() + settings.extraction_readiness_timeout
    announced = False

    while True:
        try:
            response = requests.get(f"{base_url}/health", timeout=15)
            if response.status_code == 200:
                return
            detail = response.json()
        except requests.RequestException as exc:
            detail = str(exc)

        if time.monotonic() > deadline:
            raise RuntimeError(f"Service Docling toujours pas pret : {detail}")
        if not announced:
            context.log.info(f"Service Docling en cours de demarrage : {detail}")
            announced = True
        time.sleep(settings.extraction_poll_seconds)


def _await_job(context: AssetExecutionContext, base_url: str, job_id: str) -> dict[str, Any]:
    """Interroge un job jusqu'a son etat terminal, en journalisant l'avancement.

    Le premier sondage est immediat, puis l'intervalle croit jusqu'a
    ``extraction_poll_seconds``. Un chapitre HTML s'extrait en une seconde :
    attendre l'intervalle plein avant de regarder ajouterait, sur un corpus de
    plusieurs dizaines de fichiers, plus d'attente que de travail.
    """
    settings = get_settings()
    deadline = time.monotonic() + settings.extraction_timeout_seconds
    consecutive_failures = 0
    last_progress: str = ""
    interval = 0.0

    while True:
        if interval:
            time.sleep(interval)
        interval = min(max(interval * 2, 1.0), settings.extraction_poll_seconds)

        try:
            response = requests.get(f"{base_url}/jobs/{job_id}", timeout=30)
        except requests.RequestException as exc:
            consecutive_failures += 1
            if consecutive_failures > settings.extraction_max_poll_failures:
                raise RuntimeError(
                    f"Service Docling injoignable apres {consecutive_failures} sondages : {exc}"
                ) from exc
            context.log.warning(f"Sondage {job_id} en echec ({exc}), nouvelle tentative.")
            continue

        if response.status_code == 404:
            # Le service a redemarre : la file est en memoire, le job est perdu.
            # Erreur transitoire : la politique de reprise de l'asset la
            # rattrape sans intervention.
            raise RuntimeError(
                f"Job {job_id} inconnu du service Docling (redemarrage ?). Nouvelle tentative."
            )
        response.raise_for_status()
        consecutive_failures = 0

        snapshot: dict[str, Any] = response.json()
        progress = str(snapshot.get("progress") or {})
        if progress != last_progress:
            context.log.info(f"Job {job_id} : {snapshot['status']} — {progress}")
            last_progress = progress

        status = snapshot.get("status")
        if status == "success":
            return snapshot
        if status == "failed":
            # Echec propre au document (page illisible, format inattendu) :
            # inutile de reconvertir plusieurs centaines de pages pour obtenir
            # la meme erreur. On coupe court aux reprises.
            raise Failure(
                description=f"Extraction en echec : {snapshot.get('error')}",
                allow_retries=False,
            )

        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Job {job_id} toujours en cours apres "
                f"{settings.extraction_timeout_seconds}s : abandon."
            )


def _build_html_assets(
    source: SourceConfig,
    partitions: DynamicPartitionsDefinition,
) -> list[AssetsDefinition]:
    """Assets d'une source HTML : nettoyage puis extraction."""

    @asset(
        name="cleaned_html",
        key_prefix=source.name,
        partitions_def=partitions,
        group_name=source.name,
    )
    def cleaned_html(context: AssetExecutionContext) -> str:
        """Nettoie le HTML source (boilerplate, nav, bruit SingleFile)."""
        settings = get_settings()
        source_path = Path(settings.source_dir) / context.partition_key
        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        dest_path = cleaned_path(settings.source_dir, context.partition_key)

        exporter: MinioImageExporter | None = None
        if source.cleaning.export_images:
            doc_key = Path(context.partition_key).with_suffix("").as_posix()
            exporter = MinioImageExporter(doc_key=doc_key)

        report = clean_html_file(source_path, dest_path, source.cleaning, image_exporter=exporter)

        if report.strategy == "precleaned":
            context.log.warning(
                f"Content extraction below thresholds for {context.partition_key}; "
                "keeping pre-cleaned HTML."
            )
        context.add_output_metadata(
            {
                "strategy": report.strategy,
                "raw_bytes": report.raw_bytes,
                "cleaned_bytes": report.cleaned_bytes,
                "text_chars": report.text_chars,
                # LE DENOMINATEUR DE LA PERTE, et il manquait. `text_chars` sans
                # rien a quoi le comparer ne dit pas si le nettoyage a retire du
                # boilerplate ou ampute un chapitre : `min_text_ratio` accepte un
                # candidat conservant 5 % du texte, et dans l'interface Dagster
                # un chapitre ampute a 5 % et un chapitre propre a 99,8 %
                # affichaient tous deux un nombre, sans rien qui les distingue
                # (registre 4.6). Le ratio est LU sur le bilan et non recalcule
                # ici : deux calculs du meme rapport peuvent diverger, et une
                # metadonnee de perte qui se trompe est pire qu'absente.
                "precleaned_text_chars": report.precleaned_text_chars,
                "text_ratio": report.text_ratio,
                "images_exported": exporter.exported if exporter else 0,
            }
        )
        return str(dest_path)

    @asset(
        name="extracted_document",
        key_prefix=source.name,
        partitions_def=partitions,
        group_name=source.name,
        retry_policy=EXTRACTION_RETRY_POLICY,
        ins={"cleaned_html": AssetIn(key=AssetKey([source.name, "cleaned_html"]))},
    )
    def extracted_document(context: AssetExecutionContext, cleaned_html: str) -> dict[str, Any]:
        """Envoie le HTML nettoye au service Docling."""
        result = _request_extraction(context, cleaned_html, context.partition_key)
        _record_metadata(context, result)
        return result

    return [cleaned_html, extracted_document]


def _build_direct_assets(
    source: SourceConfig,
    partitions: DynamicPartitionsDefinition,
) -> list[AssetsDefinition]:
    """Asset d'une source sans pre-traitement (PDF, Markdown) : extraction directe.

    Le Markdown rejoint le PDF plutot que le HTML : il est deja propre, il n'y
    a ni boilerplate a retirer ni image inline a exporter.
    """

    @asset(
        name="extracted_document",
        key_prefix=source.name,
        partitions_def=partitions,
        group_name=source.name,
        retry_policy=EXTRACTION_RETRY_POLICY,
    )
    def extracted_document(context: AssetExecutionContext) -> dict[str, Any]:
        """Envoie le document source au service Docling."""
        settings = get_settings()
        file_path = Path(settings.source_dir) / context.partition_key
        if not file_path.exists():
            # Le fichier a disparu entre la detection du sensor et le run :
            # echouer plutot que de marquer la partition comme materialisee.
            raise FileNotFoundError(f"Source file not found: {file_path}")
        result = _request_extraction(context, str(file_path), context.partition_key)
        _record_metadata(context, result)
        return result

    return [extracted_document]


def _record_metadata(context: AssetExecutionContext, result: dict[str, Any]) -> None:
    """Publie le bilan d'extraction dans les metadonnees de l'asset.

    Rien d'autre. Cette fonction a longtemps poste ``/reindex`` sur l'agent au
    passage : un appel reseau vers un autre service, dans une fonction qui
    publie des metadonnees, et une fois par partition alors que le contrat le
    veut en fin d'ingestion. Le declenchement vit desormais dans
    ``reindex_job.py``, hors du chemin du document.

    **Elle ne publiait que quatre cles, et c'est ce qui rendait deux etats
    indistinguables** (registre 4.10). ``extract`` retourne ``elements: 0`` et
    ``duplicate_of`` quand il reconnait un fichier deja ingere ; la fonction
    n'en publiait rien. Dans l'interface Dagster, un document **ECARTE** et un
    document **ingere VIDE** affichaient donc exactement la meme chose — quatre
    zeros. Le premier est le comportement voulu, le second est une panne.

    Les cinq cles que le constat nomme sont publiees. ``duplicate_of`` n'apparait
    que sur un document reellement ecarte : une cle presente et vide sur les 23
    documents redonnerait le defaut par l'autre bout, en habituant l'oeil a la
    voir. ``failed_batches`` est publie comme un COMPTE, plus son detail : c'est
    le compteur du 4.1 vu depuis Dagster, la ou le run rouge ne dit pas combien
    de pages manquent.
    """
    # LES CINQ CLES VIVENT DANS `progress`, ET C'EST LE POINT DELICAT.
    # `main._run_extraction` fait `job.report(**result)`, et `Job.report` verse
    # tout dans `progress` : le retour d'`extract` n'apparait donc JAMAIS au
    # premier niveau du `snapshot()` que Dagster recoit. Les lire au premier
    # niveau publierait des zeros sans qu'aucune erreur ne le dise — le defaut
    # que cette fonction ferme, reintroduit dans le geste qui le ferme.
    progress = result.get("progress") or {}
    lots_en_echec = list(progress.get("failed_batches") or [])
    metadonnees: dict[str, Any] = {
        "elements": progress.get("elements", 0),
        "chunks": progress.get("chunks", 0),
        "pages": progress.get("pages", progress.get("pages_total", 0)),
        "elapsed_seconds": result.get("elapsed_seconds", 0),
        "pages_skipped": progress.get("pages_skipped", 0),
        "ocr": bool(progress.get("ocr", False)),
        "language": str(progress.get("language") or ""),
        "failed_batches": len(lots_en_echec),
        "failed_batches_detail": "; ".join(str(lot) for lot in lots_en_echec),
    }
    doublon = progress.get("duplicate_of")
    if doublon:
        metadonnees["duplicate_of"] = str(doublon)
    context.add_output_metadata(metadonnees)


def _etiquette_de_reingestion(curseur: str | None) -> str | None:
    """Lit l'ordre de reingestion pose dans le curseur, s'il y en a un.

    Le marqueur est du TEXTE et non du JSON, et c'est delibere : il se pose a la
    main, depuis l'interface Dagster ou en une ligne de commande, sans
    echappement de guillemets. Un curseur nominal est un objet JSON, qui ne
    commence jamais par ``reingerer:``.

    Args:
        curseur: Curseur du capteur, tel que Dagster le rend.

    Returns:
        ``None`` s'il n'y a pas d'ordre — le cas nominal, et celui de tout
        deploiement. L'etiquette s'il y en a une. La chaine VIDE si le marqueur
        est la sans etiquette : c'est un ordre mal forme, distinct de l'absence
        d'ordre, et l'appelant le refuse en le disant.
    """
    if not curseur:
        return None
    texte = curseur.strip()
    if not texte.startswith(PREFIXE_REINGESTION):
        return None
    return texte[len(PREFIXE_REINGESTION) :].strip()


def _cles_deja_consommees(
    instance: DagsterInstance, nom_du_capteur: str, cles: Sequence[str]
) -> list[str]:
    """Rend, parmi ces cles de run, celles dont Dagster ne creera AUCUN run.

    **C'EST L'AUTRE MOITIE DU 4.32.a, ET LA PLUS COUTEUSE** : sans elle,
    personne n'aurait jamais trouve le reste. Le tick qui a perdu 22 runs le 2
    septembre 2026 portait ``skip_reason=None`` — le daemon n'ecrit rien quand
    il ecarte une demande dont la cle est deja consommee, et la phrase « Sensor
    function returned an empty result » n'apparait qu'aux ticks SUIVANTS, pour
    une autre raison. Le capteur regarde donc lui-meme, avant d'emettre.

    La regle reproduite ici est celle du daemon, et non une approximation :
    ``dagster/_daemon/sensor.py::fetch_existing_runs`` interroge
    ``RunsFilter(tags={RUN_KEY_TAG: cle})`` une cle a la fois — il commente
    lui-meme que le faire en une requete ``IN`` est plus lent — puis ne retient
    que les runs dont ``SENSOR_NAME_TAG`` est celui du capteur, pour que deux
    capteurs homonymes de depots differents ne se genent pas. Le filtre sur le
    nom est ce qui evite une alerte FAUSSE, et une alerte fausse se desapprend
    aussi vite qu'une alerte absente.

    Le cout suit le nombre de demandes, pas celui des fichiers : en regime
    nominal le capteur n'emet rien, donc cette fonction n'est jamais appelee.
    Elle ne coute ses N requetes que sur le tick d'une reingestion.

    Args:
        instance: Instance Dagster interrogee.
        nom_du_capteur: Nom du capteur, celui que le daemon tague.
        cles: Cles de run sur le point d'etre emises.

    Returns:
        Les cles deja consommees, dans l'ordre ou elles ont ete presentees.
    """
    consommees: list[str] = []
    for cle in cles:
        runs = instance.get_runs(filters=RunsFilter(tags={RUN_KEY_TAG: cle}))
        if any((run.tags or {}).get(SENSOR_NAME_TAG) == nom_du_capteur for run in runs):
            consommees.append(cle)
    return consommees


def _build_sensor(
    source: SourceConfig,
    partitions_name: str,
    partitions: DynamicPartitionsDefinition,
    job_name: str,
) -> SensorDefinition:
    """Sensor de detection de fichiers : une partition + un run par fichier nouveau/modifie.

    Il porte aussi le GESTE DE REINGESTION, decrit au-dessus de
    :data:`PREFIXE_REINGESTION` : le chemin nominal detecte ce qui a change, le
    marqueur redemande tout, et les deux se distinguent par la forme de la cle
    de run.
    """
    nom_du_capteur = f"{source.name}_sensor"

    @sensor(
        name=nom_du_capteur,
        minimum_interval_seconds=30,
        job_name=job_name,
        default_status=DefaultSensorStatus.RUNNING,
    )
    def file_sensor(context: SensorEvaluationContext) -> SensorResult:
        etiquette = _etiquette_de_reingestion(context.cursor)
        if etiquette == "":
            # Ordre mal forme : on ne fait RIEN, et on laisse le curseur tel
            # quel pour que l'operateur le corrige. Honorer un marqueur sans
            # etiquette rouvrirait le 4.32.a par le geste cense le fermer — la
            # cle serait constante, donc le second geste serait refuse en
            # silence.
            context.log.warning(
                f"Curseur pose sur « {PREFIXE_REINGESTION} » SANS etiquette : aucune "
                "reingestion n'est lancee, et le curseur n'est pas touche. L'etiquette "
                "est ce qui distingue deux gestes successifs ; sans elle, le second "
                "porterait les memes cles de run et Dagster le refuserait sans un mot "
                f"(registre 4.32.a). Posez par exemple « {PREFIXE_REINGESTION}"
                "2026-09-22-apres-purge »."
            )
            return SensorResult(run_requests=[], dynamic_partitions_requests=[])

        source_dir = get_settings().source_dir
        pattern = str(Path(source_dir) / source.glob)
        discovered = sorted(globlib.glob(pattern, recursive=True))

        # Index, sommaire, page de copyright : aucune phrase a indexer, mais
        # tout le vocabulaire de l'ouvrage. Ecartes avant meme la partition,
        # ils ne coutent ni run, ni place, ni bruit dans les reponses.
        files = [f for f in discovered if not source.is_ignored(f)]
        if len(files) < len(discovered):
            ecartes = [os.path.relpath(f, source_dir) for f in discovered if f not in set(files)]
            context.log.info(f"Ignored (front/back matter): {', '.join(sorted(ecartes))}")

        cursor_data: dict[str, str] = {}
        if etiquette:
            context.log.info(
                f"Reingestion demandee, etiquette « {etiquette} » : les {len(files)} "
                "fichiers de la source sont redemandes, quel que soit leur mtime. "
                "Le marqueur est consomme par ce tick."
            )
        elif context.cursor:
            try:
                cursor_data = json.loads(context.cursor)
            except (json.JSONDecodeError, TypeError):
                context.log.warning("Invalid cursor format, resetting.")

        run_requests: list[RunRequest] = []
        cles_demandees: list[str] = []
        partition_requests = []
        new_cursor = dict(cursor_data)

        for f in files:
            # Chemin relatif : cle de partition stable et lisible dans l'UI
            partition_key = os.path.relpath(f, source_dir)

            if not context.instance.has_dynamic_partition(partitions_name, partition_key):
                context.log.info(f"Adding new partition for file: {partition_key}")
                partition_requests.append(partitions.build_add_request([partition_key]))

            mtime = os.path.getmtime(f)
            last_mtime = cursor_data.get(partition_key)

            if not last_mtime or float(last_mtime) < mtime:
                # LA CLE NOMINALE NE BOUGE PAS, et c'est ce qui rend un
                # deploiement inoffensif : sur un corpus inchange, elle est
                # celle que l'historique porte deja, donc rien ne repart. Seul
                # le marqueur fabrique une cle neuve, et il porte SON etiquette.
                run_key = (
                    f"{source.name}_{partition_key}_reingestion_{etiquette}"
                    if etiquette
                    else f"{source.name}_{partition_key}_{mtime}"
                )
                context.log.info(f"Requesting run for partition: {partition_key}")
                run_requests.append(RunRequest(run_key=run_key, partition_key=partition_key))
                cles_demandees.append(run_key)
                new_cursor[partition_key] = str(mtime)

        if cles_demandees:
            perdues = _cles_deja_consommees(context.instance, nom_du_capteur, cles_demandees)
            if perdues:
                context.log.warning(
                    f"{len(perdues)} demande(s) de run sur {len(cles_demandees)} portent un "
                    "run_key DEJA CONSOMME : Dagster n'en creera aucun run, et il ne le dira "
                    "pas — le tick portera skip_reason=None (registre 4.32.a). Le curseur, "
                    "lui, avance : le geste est a refaire avec une etiquette NEUVE, "
                    f"« {PREFIXE_REINGESTION}<etiquette> ». Premieres cles perdues : "
                    f"{', '.join(perdues[:3])}"
                )

        # `or etiquette` : sans lui, un marqueur pose sur une source dont le
        # corpus est VIDE ne serait jamais consomme — le curseur calcule vaut
        # alors `{}`, egal a celui dont ce tick est parti — et chaque tick le
        # rejouerait indefiniment.
        if new_cursor != cursor_data or etiquette:
            context.update_cursor(json.dumps(new_cursor))

        return SensorResult(
            run_requests=run_requests,
            dynamic_partitions_requests=partition_requests,
        )

    return file_sensor


def build_source(source: SourceConfig) -> SourceDefinitions:
    """Genere l'ensemble des objets Dagster pour une source declaree.

    Args:
        source: Configuration de la source (voir ``sources.yaml``).

    Returns:
        Partitions, assets, job et sensor de la source.
    """
    partitions_name = f"{source.name}_files"
    partitions = DynamicPartitionsDefinition(name=partitions_name)

    if source.needs_cleaning:
        assets_list = _build_html_assets(source, partitions)
    else:
        assets_list = _build_direct_assets(source, partitions)

    job_name = f"{source.name}_job"
    job = define_asset_job(name=job_name, selection=AssetSelection.assets(*assets_list))
    sensor_def = _build_sensor(source, partitions_name, partitions, job_name)

    return SourceDefinitions(
        partitions=partitions,
        assets=assets_list,
        job=job,
        sensor=sensor_def,
    )
