"""Definition du pipeline Dagster RAG Assistant.

Les sources sont declarees dans ``sources.yaml`` : la factory genere pour
chacune ses partitions, assets, job et sensor. La persistance (NebulaGraph,
ChromaDB, MinIO) est assuree par le service Docling lui-meme.
"""

from dagster import Definitions

from src.pipeline.factory import build_source
from src.pipeline.sources import load_sources

_built = [build_source(source) for source in load_sources()]

defs = Definitions(
    assets=[asset_def for built in _built for asset_def in built.assets],
    jobs=[built.job for built in _built],
    sensors=[built.sensor for built in _built],
)
