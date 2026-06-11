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
    DefaultSensorStatus,
    DynamicPartitionsDefinition,
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
from src.pipeline.settings import get_settings
from src.pipeline.sources import SourceConfig


@dataclass
class SourceDefinitions:
    """Objets Dagster generes pour une source."""

    partitions: DynamicPartitionsDefinition
    assets: list[AssetsDefinition]
    job: "UnresolvedAssetJobDefinition"
    sensor: SensorDefinition


def _request_extraction(context: AssetExecutionContext, file_path: str) -> dict[str, Any]:
    """Appelle le microservice Docling pour un fichier donne."""
    settings = get_settings()
    docling_url = f"{settings.docling_service_url}/extract"
    context.log.info(f"Requesting extraction for: {file_path}")
    resp = requests.post(docling_url, json={"filepath": file_path}, timeout=1200)
    resp.raise_for_status()
    context.log.info(f"Successfully extracted: {file_path}")
    result: dict[str, Any] = resp.json()
    return result


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
        report = clean_html_file(source_path, dest_path, source.cleaning)

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
            }
        )
        return str(dest_path)

    @asset(
        name="extracted_document",
        key_prefix=source.name,
        partitions_def=partitions,
        group_name=source.name,
        ins={"cleaned_html": AssetIn(key=AssetKey([source.name, "cleaned_html"]))},
    )
    def extracted_document(context: AssetExecutionContext, cleaned_html: str) -> dict[str, Any]:
        """Envoie le HTML nettoye au service Docling."""
        return _request_extraction(context, cleaned_html)

    return [cleaned_html, extracted_document]


def _build_pdf_assets(
    source: SourceConfig,
    partitions: DynamicPartitionsDefinition,
) -> list[AssetsDefinition]:
    """Asset d'une source PDF : extraction directe."""

    @asset(
        name="extracted_document",
        key_prefix=source.name,
        partitions_def=partitions,
        group_name=source.name,
    )
    def extracted_document(context: AssetExecutionContext) -> dict[str, Any]:
        """Envoie le PDF source au service Docling."""
        settings = get_settings()
        file_path = Path(settings.source_dir) / context.partition_key
        if not file_path.exists():
            context.log.warning(f"File not found for partition: {file_path}")
            return {}
        return _request_extraction(context, str(file_path))

    return [extracted_document]


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

    if source.type == "html":
        assets_list = _build_html_assets(source, partitions)
    else:
        assets_list = _build_pdf_assets(source, partitions)

    job_name = f"{source.name}_job"
    job = define_asset_job(name=job_name, selection=AssetSelection.assets(*assets_list))
    sensor_def = _build_sensor(source, partitions_name, partitions, job_name)

    return SourceDefinitions(
        partitions=partitions,
        assets=assets_list,
        job=job,
        sensor=sensor_def,
    )
