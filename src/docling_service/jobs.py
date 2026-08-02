"""File de jobs d'extraction executee par un worker unique.

L'extraction d'un livre de 400 pages dure des heures. La faire tenir dans une
requete HTTP posait deux problemes : le client Dagster expirait avant la fin
(run rouge alors que le service continuait a ecrire), et l'endpoint bloquait
l'event loop du service, figeant y compris le healthcheck.

Le POST se contente donc de mettre en file et de rendre un identifiant ; un
worker unique deroule les jobs les uns apres les autres, et Dagster interroge
l'avancement. Le worker est unique a dessein : la conversion sature deja le GPU,
et c'est la file Dagster en amont qui cadence le debit global.

Module sans dependance externe : testable sans Docling ni FastAPI.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

PENDING = "pending"
RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"

TERMINAL_STATUSES = frozenset({SUCCESS, FAILED})

# Nombre de jobs termines conserves en memoire. Au-dela, les plus anciens sont
# oublies : Dagster a deja recupere leur resultat.
DEFAULT_HISTORY_SIZE = 500


@dataclass
class Job:
    """Un job d'extraction et son avancement."""

    id: str
    filepath: str
    status: str = PENDING
    error: str | None = None
    progress: dict[str, Any] = field(default_factory=dict)
    submitted_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def report(self, **values: Any) -> None:
        """Met a jour l'avancement du job (appele depuis le worker)."""
        with self._lock:
            self.progress.update(values)

    def snapshot(self) -> dict[str, Any]:
        """Retourne une vue coherente du job, serialisable en JSON."""
        with self._lock:
            elapsed = (self.finished_at or time.time()) - (self.started_at or self.submitted_at)
            return {
                "job_id": self.id,
                "filepath": self.filepath,
                "status": self.status,
                "error": self.error,
                "progress": dict(self.progress),
                "elapsed_seconds": round(elapsed, 1),
            }


class JobQueue:
    """File FIFO de jobs d'extraction servie par un worker unique."""

    def __init__(
        self,
        handler: Callable[[str, Job], None],
        history_size: int = DEFAULT_HISTORY_SIZE,
    ) -> None:
        """Initialise la file.

        Args:
            handler: Traitement d'un job ; recoit le chemin du fichier et le job,
                sur lequel il publie son avancement via ``job.report()``.
            history_size: Nombre de jobs termines conserves en memoire.
        """
        self._handler = handler
        self._history_size = history_size
        self._queue: queue.Queue[Job] = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._active_by_path: dict[str, str] = {}
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        """Demarre le worker (idempotent)."""
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._run, name="extraction-worker", daemon=True)
            self._worker.start()

    def submit(self, filepath: str, **contexte: Any) -> Job:
        """Met un fichier en file d'extraction.

        Si le meme fichier est deja en attente ou en cours, le job existant est
        retourne : une relance Dagster sur une partition deja soumise ne
        declenche pas une seconde conversion.

        Args:
            filepath: Chemin du document a extraire.
            contexte: Donnees inscrites sur le job **avant** sa mise en file.
                Le worker peut demarrer des la mise en file : les renseigner
                apres coup exposerait a une course.

        Returns:
            Le job cree, ou celui deja actif pour ce fichier.
        """
        with self._lock:
            existing_id = self._active_by_path.get(filepath)
            if existing_id is not None:
                existing = self._jobs.get(existing_id)
                if existing is not None and existing.status not in TERMINAL_STATUSES:
                    logger.info("Job deja actif pour %s : %s", filepath, existing_id)
                    return existing

            job = Job(id=uuid.uuid4().hex[:12], filepath=filepath)
            if contexte:
                job.report(**contexte)
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._active_by_path[filepath] = job.id
            self._prune()

        self._queue.put(job)
        logger.info("Job %s en file : %s", job.id, filepath)
        return job

    def get(self, job_id: str) -> Job | None:
        """Retourne un job par son identifiant, ou None s'il est inconnu."""
        with self._lock:
            return self._jobs.get(job_id)

    def stats(self) -> dict[str, Any]:
        """Retourne l'etat global de la file (expose par /health)."""
        with self._lock:
            statuses = [job.status for job in self._jobs.values()]
            worker_alive = self._worker is not None and self._worker.is_alive()
        return {
            "queued": statuses.count(PENDING),
            "running": statuses.count(RUNNING),
            "known_jobs": len(statuses),
            "worker_alive": worker_alive,
        }

    def _prune(self) -> None:
        """Oublie les jobs termines les plus anciens. Appele sous ``_lock``."""
        while len(self._order) > self._history_size:
            oldest_id = self._order[0]
            oldest = self._jobs.get(oldest_id)
            if oldest is not None and oldest.status not in TERMINAL_STATUSES:
                # Ne jamais oublier un job qu'un client attend encore.
                return
            self._order.pop(0)
            self._jobs.pop(oldest_id, None)
            if oldest is not None and self._active_by_path.get(oldest.filepath) == oldest_id:
                self._active_by_path.pop(oldest.filepath, None)

    def _run(self) -> None:
        """Boucle du worker : deroule les jobs un par un, sans jamais mourir."""
        while True:
            job = self._queue.get()
            try:
                self._execute(job)
            finally:
                self._queue.task_done()

    def _execute(self, job: Job) -> None:
        """Execute un job en capturant toute erreur dans son statut."""
        job.status = RUNNING
        job.started_at = time.time()
        logger.info("Job %s demarre : %s", job.id, job.filepath)
        try:
            self._handler(job.filepath, job)
        except Exception as exc:
            job.status = FAILED
            job.error = f"{type(exc).__name__}: {exc}"
            logger.exception("Job %s en echec : %s", job.id, job.filepath)
        else:
            job.status = SUCCESS
            logger.info("Job %s termine : %s", job.id, job.filepath)
        finally:
            job.finished_at = time.time()
            with self._lock:
                if self._active_by_path.get(job.filepath) == job.id:
                    self._active_by_path.pop(job.filepath, None)
