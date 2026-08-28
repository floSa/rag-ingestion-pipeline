"""Definition du pipeline Dagster RAG Assistant.

Les sources sont declarees dans ``sources.yaml`` : la factory genere pour
chacune ses partitions, assets, job et sensor. La persistance (NebulaGraph,
ChromaDB, MinIO) est assuree par le service Docling lui-meme.

S'y ajoute un objet qui n'appartient a aucune source : la reindexation de
``rag-agent-chat``. Elle est declenchee par son propre sensor, qui surveille
les jobs d'ingestion et arme son job quand plus aucun run n'est en vol — voir
``reindex_job.py``.
"""

import logging

from dagster import Definitions

from src.pipeline.factory import build_source
from src.pipeline.reindex_job import build_reindex
from src.pipeline.settings import get_settings
from src.pipeline.sources import load_sources

logger = logging.getLogger(__name__)


def _annoncer_reindexation() -> None:
    """Dit au chargement ce qu'il adviendra de ``POST /reindex``.

    L'absence d'URL doit etre un choix visible au demarrage, et non une
    surprise a la fin d'une ingestion de plusieurs heures.
    """
    url = get_settings().agent_service_url.strip()
    if url:
        logger.info(
            "Fin d'ingestion : POST %s/reindex sera appele sur rag-agent-chat, une fois par "
            "rafale de documents et non une fois par document.",
            url,
        )
    else:
        logger.warning(
            "AGENT_SERVICE_URL est vide : POST /reindex NE SERA PAS appele. Les documents "
            "ingeres resteront invisibles en recherche lexicale cote rag-agent-chat "
            "jusqu'au redemarrage de celui-ci. Renseigner AGENT_SERVICE_URL pour retablir "
            "le contrat."
        )


_annoncer_reindexation()

_built = [build_source(source) for source in load_sources()]
_reindex = build_reindex([built.job.name for built in _built])

defs = Definitions(
    assets=[asset_def for built in _built for asset_def in built.assets] + [_reindex.asset],
    jobs=[built.job for built in _built] + [_reindex.job],
    sensors=[built.sensor for built in _built] + [_reindex.sensor],
)
