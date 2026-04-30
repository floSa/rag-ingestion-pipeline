"""Sensors Dagster pour la détection de nouveaux fichiers PDF et HTML."""

from __future__ import annotations

import glob
import json
import os

from dagster import (
    DefaultSensorStatus,
    RunRequest,
    SensorEvaluationContext,
    SensorResult,
    sensor,
)

from src.pipeline.assets.core_assets import pdf_partitions


@sensor(
    name="pdf_sensor",
    minimum_interval_seconds=30,
    job_name="pdf_pipeline_job",
    default_status=DefaultSensorStatus.RUNNING,
)
def pdf_sensor(context: SensorEvaluationContext) -> SensorResult:
    """Détecte les PDFs et crée des partitions individuelles par fichier."""
    source_dir = "/opt/dagster/app/Datas"
    files = sorted(glob.glob(f"{source_dir}/**/*.pdf", recursive=True))

    try:
        cursor_data: dict[str, str] = json.loads(context.cursor) if context.cursor else {}
    except (json.JSONDecodeError, TypeError):
        context.log.warning("Invalid cursor format, resetting.")
        cursor_data = {}

    run_requests: list[RunRequest] = []
    partition_requests = []
    new_cursor = dict(cursor_data)

    for f in files:
        # Utiliser un chemin relatif pour éviter que l'IOManager n'écrase le fichier source
        partition_key = os.path.relpath(f, source_dir)

        if not context.instance.has_dynamic_partition(pdf_partitions.name, partition_key):
            context.log.info(f"Adding new partition for file: {partition_key}")
            partition_requests.append(pdf_partitions.build_add_request([partition_key]))

        mtime = os.path.getmtime(f)
        cursor_key = f"mtime:{partition_key}"
        last_mtime = cursor_data.get(cursor_key)

        if not last_mtime or float(last_mtime) < mtime:
            context.log.info(f"Requesting run for partition: {partition_key}")
            run_requests.append(
                RunRequest(
                    run_key=f"pdf_run_{partition_key}_{mtime}",
                    partition_key=partition_key,
                )
            )
            new_cursor[cursor_key] = str(mtime)

    if new_cursor != cursor_data:
        context.update_cursor(json.dumps(new_cursor))

    return SensorResult(
        run_requests=run_requests,
        dynamic_partitions_requests=partition_requests,
    )


@sensor(
    name="html_sensor",
    minimum_interval_seconds=30,
    job_name="html_pipeline_job",
    default_status=DefaultSensorStatus.RUNNING,
)
def html_sensor(context: SensorEvaluationContext) -> RunRequest | None:
    """Surveille le dossier Datas pour de nouveaux fichiers HTML."""
    source_dir = "/opt/dagster/app/Datas"
    files = sorted(glob.glob(f"{source_dir}/**/*.html", recursive=True))

    current_state = ""
    for f in files:
        mtime = os.path.getmtime(f)
        current_state += f"{f}:{mtime};"

    last_state = context.cursor or ""

    if current_state != last_state:
        context.update_cursor(current_state)
        return RunRequest(run_key=f"html_run_{hash(current_state)}")

    return None
