"""Service FastAPI d'extraction structuree de documents via Docling.

L'extraction d'un livre dure des heures : elle ne se fait donc pas dans la
requete HTTP. ``POST /extract`` met le document en file et rend un identifiant
de job ; ``GET /jobs/{job_id}`` en expose l'avancement, et c'est l'asset
Dagster qui interroge jusqu'a la fin. L'event loop reste libre, le healthcheck
repond meme pendant une conversion, et une coupure reseau ne perd plus un run.

La logique metier vit dans les modules voisins (``extraction``, ``storage``,
``nebula``, ``vectors``, ``chunking``, ``elements``, ``ngql``, ``jobs``) ; ce
fichier n'assemble que l'application.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from src.docling_service import extraction, images, vectors
from src.docling_service.embedding import verify_model_name
from src.docling_service.jobs import Job, JobQueue
from src.docling_service.nebula import get_writer
from src.docling_service.settings import get_settings
from src.pipeline.schemas import ExtractRequest, ExtractResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Etat de disponibilite des dependances, renseigne par l'initialisation en
# arriere-plan et expose par /health (le healthcheck compose s'en sert pour
# empecher Dagster de soumettre avant que les stores soient prets).
_readiness: dict[str, bool] = {
    "graph_ready": False,
    "objects_ready": False,
    "models_ready": False,
}


def _run_extraction(filepath: str, job: Job) -> None:
    """Traitement d'un job : extraction complete d'un document."""
    result = extraction.extract(
        Path(filepath), source_path=job.progress.get("source_path", ""), report=job.report
    )
    job.report(**result)


queue = JobQueue(_run_extraction, history_size=get_settings().job_history_size)


def _warm_up() -> None:
    """Precharge les modeles pour que le premier job ne les attende pas."""
    try:
        extraction.get_converter()
        vectors.get_embedding_model()
    except Exception:
        logger.exception("Prechargement des modeles echoue")
    else:
        _readiness["models_ready"] = True


def _init_graph() -> None:
    """Initialise le schema NebulaGraph."""
    _readiness["graph_ready"] = get_writer().init_schema()


def _init_objects() -> None:
    """S'assure que le bucket MinIO existe."""
    _readiness["objects_ready"] = images.ensure_bucket()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Demarre le worker et lance les initialisations en arriere-plan.

    Le modele d'embedding est verifie AVANT tout le reste, et l'exception n'est
    pas rattrapee : le service refuse de demarrer plutot que d'indexer avec un
    modele que rag-agent-chat ne saura pas interroger. C'est deliberement plus
    brutal que le prechargement ci-dessous, qui se contente de journaliser :
    un service mort se voit, un index silencieusement anglais, non.

    Raises:
        EmbeddingContractError: Si EMBEDDING_MODEL_NAME n'est pas celui du contrat.
    """
    verify_model_name(get_settings().embedding_model_name)
    queue.start()
    for target in (_init_graph, _init_objects, _warm_up):
        threading.Thread(target=target, name=target.__name__, daemon=True).start()
    yield


app = FastAPI(title="Docling Extraction API", lifespan=lifespan)


@app.post("/extract", response_model=ExtractResponse)
def extract_document(request: ExtractRequest) -> ExtractResponse:
    """Met un document en file d'extraction et retourne son identifiant de job.

    Raises:
        HTTPException: 404 si le fichier est introuvable, 415 si son format
            n'est pas pris en charge.
    """
    path = Path(request.filepath)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Fichier introuvable : {request.filepath}")
    if path.suffix.lower() not in extraction.SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Format non pris en charge : {path.suffix or path.name} "
                f"(attendus : {', '.join(sorted(extraction.SUPPORTED_SUFFIXES))})"
            ),
        )

    # L'identite du document voyage avec le job : le pipeline la connait (c'est
    # sa cle de partition), le service ne saurait que la deviner. Elle est
    # inscrite des la soumission, le worker pouvant demarrer aussitot.
    job = queue.submit(str(path), source_path=request.source_path)
    return ExtractResponse(job_id=job.id, status=job.status)


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    """Retourne l'etat et l'avancement d'un job.

    Raises:
        HTTPException: 404 si le job est inconnu — cas typique d'un service
            redemarre en cours de route, que Dagster doit signaler clairement
            plutot que d'attendre indefiniment.
    """
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job inconnu : {job_id}")
    return job.snapshot()


@app.get("/health")
def health() -> dict[str, Any]:
    """Etat du service : file de jobs et disponibilite des stores.

    Raises:
        HTTPException: 503 tant que le worker ou les stores ne sont pas prets.
    """
    stats = queue.stats()
    payload: dict[str, Any] = {"status": "ok", "queue": stats, **_readiness}

    if not stats["worker_alive"] or not all(_readiness.values()):
        payload["status"] = "starting"
        raise HTTPException(status_code=503, detail=payload)
    return payload


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
