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
    DefaultSensorStatus,
    DynamicPartitionsDefinition,
    Failure,
    RetryPolicy,
    RunRequest,
    SensorDefinition,
    SensorEvaluationContext,
    SensorResult,
    asset,
    define_asset_job,
    sensor,
)

if TYPE_CHECKING:
    from dagster._core.definitions.unresolved_asset_job_definition import (
        UnresolvedAssetJobDefinition,
    )

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


@dataclass
class SourceDefinitions:
    """Objets Dagster generes pour une source."""

    partitions: DynamicPartitionsDefinition
    assets: list[AssetsDefinition]
    job: "UnresolvedAssetJobDefinition"
    sensor: SensorDefinition


def _request_extraction(context: AssetExecutionContext, file_path: str) -> dict[str, Any]:
    """Soumet un fichier au service Docling et suit le job jusqu'a son terme.

    L'extraction ne tient pas dans une requete HTTP : un livre de plusieurs
    centaines de pages depasse tout timeout raisonnable, et la requete bloquee
    faisait echouer le run pendant que le service continuait d'ecrire. On
    soumet, puis on interroge.

    Args:
        context: Contexte d'execution de l'asset (journalisation).
        file_path: Chemin du document, vu par le service.

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
        json={"filepath": file_path},
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

        dest_path = Path(settings.source_dir) / settings.cleaned_subdir / context.partition_key

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
        result = _request_extraction(context, cleaned_html)
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
        result = _request_extraction(context, str(file_path))
        _record_metadata(context, result)
        return result

    return [extracted_document]


def _record_metadata(context: AssetExecutionContext, result: dict[str, Any]) -> None:
    """Publie le bilan d'extraction dans les metadonnees de l'asset."""
    progress = result.get("progress") or {}
    context.add_output_metadata(
        {
            "elements": progress.get("elements", 0),
            "chunks": progress.get("chunks", 0),
            "pages": progress.get("pages", progress.get("pages_total", 0)),
            "elapsed_seconds": result.get("elapsed_seconds", 0),
        }
    )


def _build_sensor(
    source: SourceConfig,
    partitions_name: str,
    partitions: DynamicPartitionsDefinition,
    job_name: str,
) -> SensorDefinition:
    """Sensor de detection de fichiers : une partition + un run par fichier nouveau/modifie."""

    @sensor(
        name=f"{source.name}_sensor",
        minimum_interval_seconds=30,
        job_name=job_name,
        default_status=DefaultSensorStatus.RUNNING,
    )
    def file_sensor(context: SensorEvaluationContext) -> SensorResult:
        source_dir = get_settings().source_dir
        pattern = str(Path(source_dir) / source.glob)
        files = sorted(globlib.glob(pattern, recursive=True))

        try:
            cursor_data: dict[str, str] = json.loads(context.cursor) if context.cursor else {}
        except (json.JSONDecodeError, TypeError):
            context.log.warning("Invalid cursor format, resetting.")
            cursor_data = {}

        run_requests: list[RunRequest] = []
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
                context.log.info(f"Requesting run for partition: {partition_key}")
                run_requests.append(
                    RunRequest(
                        run_key=f"{source.name}_{partition_key}_{mtime}",
                        partition_key=partition_key,
                    )
                )
                new_cursor[partition_key] = str(mtime)

        if new_cursor != cursor_data:
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
