"""Definition du pipeline Dagster RAG Assistant."""

from dagster import Definitions, define_asset_job, load_assets_from_modules

import src.pipeline.assets.core_assets as core_assets
import src.pipeline.assets.html_assets as html_assets
import src.pipeline.assets.pdf_assets as pdf_assets
from src.pipeline.resources import EmbeddingsResource  # type: ignore[attr-defined]
from src.pipeline.sensors import html_sensor, pdf_sensor

all_assets = load_assets_from_modules([pdf_assets, html_assets, core_assets])

pdf_pipeline_job = define_asset_job(
    name="pdf_pipeline_job",
    selection=[
        "pre_process_pdf",
        "extract_structured_json*",
        "build_knowledge_graph",
        "vectorize_content",
    ],
)

html_pipeline_job = define_asset_job(
    name="html_pipeline_job",
    selection=[
        "pre_process_html",
        "extract_structured_json*",
        "build_knowledge_graph",
        "vectorize_content",
    ],
)

defs = Definitions(
    assets=all_assets,
    jobs=[pdf_pipeline_job, html_pipeline_job],
    sensors=[pdf_sensor, html_sensor],
    resources={"embeddings": EmbeddingsResource()},
)
