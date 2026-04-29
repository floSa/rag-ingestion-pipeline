from dagster import sensor, RunRequest, SensorEvaluationContext, DefaultSensorStatus, RunConfig, SensorResult
import os
import glob
from src.pipeline.assets.core_assets import pdf_partitions

import json

@sensor(name="pdf_sensor", minimum_interval_seconds=30, job_name="pdf_pipeline_job", default_status=DefaultSensorStatus.RUNNING)
def pdf_sensor(context: SensorEvaluationContext):
    """Détecte les PDFs et crée des partitions individuelles pour chaque livre."""
    source_dir = "/opt/dagster/app/Datas"
    files = glob.glob(f"{source_dir}/**/*.pdf", recursive=True)
    
    # Correction : On charge le curseur existant s'il existe, avec une sécurité pour l'ancien format
    try:
        cursor_data = json.loads(context.cursor) if context.cursor else {}
    except (json.JSONDecodeError, TypeError):
        context.log.warning("Old cursor format detected or invalid JSON, resetting cursor.")
        cursor_data = {}
    
    run_requests = []
    partition_requests = []
    new_cursor = dict(cursor_data)
    
    for f in sorted(files):
        partition_key = f
        
        # Ajout dynamique de partition
        if not context.instance.has_dynamic_partition(pdf_partitions.name, partition_key):
            context.log.info(f"Adding new partition for file: {f}")
            partition_requests.append(pdf_partitions.build_add_request([partition_key]))
        
        # Détection de changement via mtime
        mtime = os.path.getmtime(f)
        cursor_key = f"mtime:{f}"
        last_mtime = cursor_data.get(cursor_key)
        
        if not last_mtime or float(last_mtime) < mtime:
            context.log.info(f"Requesting run for partition: {partition_key}")
            run_requests.append(RunRequest(
                run_key=f"pdf_run_{partition_key}_{mtime}",
                partition_key=partition_key
            ))
            new_cursor[cursor_key] = str(mtime)
            
    # Mise à jour du curseur
    if new_cursor != cursor_data:
        context.update_cursor(json.dumps(new_cursor))
            
    return SensorResult(
        run_requests=run_requests,
        dynamic_partitions_requests=partition_requests
    )

@sensor(name="html_sensor", minimum_interval_seconds=30, job_name="html_pipeline_job", default_status=DefaultSensorStatus.RUNNING)
def html_sensor(context: SensorEvaluationContext):
    """Surveille le dossier Datas pour de nouveaux fichiers HTML."""
    source_dir = "/opt/dagster/app/Datas"
    files = glob.glob(f"{source_dir}/**/*.html", recursive=True)
    
    current_state = ""
    for f in sorted(files):
        mtime = os.path.getmtime(f)
        current_state += f"{f}:{mtime};"
    
    last_state = context.cursor or ""
    
    if current_state != last_state:
        context.update_cursor(current_state)
        return RunRequest(run_key=f"html_run_{hash(current_state)}")
    
    return None
