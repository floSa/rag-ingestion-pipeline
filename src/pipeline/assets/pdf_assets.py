"""Asset de pré-traitement des fichiers PDF."""

from __future__ import annotations

import glob
import os

from dagster import asset


@asset(group_name="pre_processing")
def pre_process_pdf() -> list[str]:
    """Identifie et valide les fichiers PDF à traiter.

    Returns:
        Liste des chemins absolus des PDFs valides.
    """
    source_dir = "/opt/dagster/app/Datas"
    files = glob.glob(f"{source_dir}/**/*.pdf", recursive=True)
    return [f for f in files if os.path.exists(f) and os.path.getsize(f) > 0]
