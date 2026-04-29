from dagster import Definitions, load_assets_from_modules, define_asset_job
import src.pipeline.assets.pdf_assets as pdf_assets
import src.pipeline.assets.html_assets as html_assets
import src.pipeline.assets.core_assets as core_assets
from src.pipeline.sensors import pdf_sensor, html_sensor
from src.pipeline.resources import EmbeddingsResource

# Chargement automatique des assets
all_assets = load_assets_from_modules([
    pdf_assets,
    html_assets,
    core_assets
])

# Jobs consolidés pour assurer que toutes les dépendances sont servies
# On sélectionne les assets de pre-process OU l'asset core s'ils existent dans la run.
# Utiliser "*" permet de sélectionner toutes les dépendances descendantes.
pdf_pipeline_job = define_asset_job(
    name="pdf_pipeline_job", 
    selection=["pre_process_pdf", "extract_structured_json*", "build_knowledge_graph", "vectorize_content"]
)

html_pipeline_job = define_asset_job(
    name="html_pipeline_job", 
    selection=["pre_process_html", "extract_structured_json*", "build_knowledge_graph", "vectorize_content"]
)

# Définition du pipeline Dagster
defs = Definitions(
    assets=all_assets,
    jobs=[pdf_pipeline_job, html_pipeline_job],
    sensors=[pdf_sensor, html_sensor],
    resources={
        "embeddings": EmbeddingsResource()
    }
)
