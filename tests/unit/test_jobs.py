"""Tests unitaires pour la file de jobs d'extraction."""

from __future__ import annotations

import threading
import time

from src.docling_service.jobs import FAILED, PENDING, RUNNING, SUCCESS, Job, JobQueue


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    """Attend qu'une condition devienne vraie, sans bloquer indefiniment."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestJob:
    def test_snapshot_is_serializable(self):
        job = Job(id="abc", filepath="/x.pdf")
        snapshot = job.snapshot()
        assert snapshot["job_id"] == "abc"
        assert snapshot["status"] == PENDING
        assert snapshot["progress"] == {}

    def test_report_accumulates(self):
        job = Job(id="abc", filepath="/x.pdf")
        job.report(pages_done=1)
        job.report(pages_done=2, elements=10)
        assert job.snapshot()["progress"] == {"pages_done": 2, "elements": 10}

    def test_snapshot_copies_progress(self):
        job = Job(id="abc", filepath="/x.pdf")
        job.report(pages_done=1)
        snapshot = job.snapshot()
        job.report(pages_done=2)
        assert snapshot["progress"]["pages_done"] == 1


class TestJobQueue:
    def test_submit_returns_pending_job(self):
        queue = JobQueue(lambda path, job: None)
        job = queue.submit("/a.pdf")
        assert job.status == PENDING
        assert job.filepath == "/a.pdf"

    def test_worker_runs_handler(self):
        seen: list[str] = []
        queue = JobQueue(lambda path, job: seen.append(path))
        queue.start()
        job = queue.submit("/a.pdf")
        assert _wait_for(lambda: job.status == SUCCESS)
        assert seen == ["/a.pdf"]

    def test_handler_progress_is_exposed(self):
        queue = JobQueue(lambda path, job: job.report(pages_done=7))
        queue.start()
        job = queue.submit("/a.pdf")
        assert _wait_for(lambda: job.status == SUCCESS)
        assert job.snapshot()["progress"]["pages_done"] == 7

    def test_failure_is_captured_not_raised(self):
        def boom(path: str, job: Job) -> None:
            raise ValueError("page illisible")

        queue = JobQueue(boom)
        queue.start()
        job = queue.submit("/a.pdf")
        assert _wait_for(lambda: job.status == FAILED)
        assert job.error is not None
        assert "page illisible" in job.error

    def test_worker_survives_a_failing_job(self):
        def sometimes(path: str, job: Job) -> None:
            if path == "/bad.pdf":
                raise RuntimeError("nope")

        queue = JobQueue(sometimes)
        queue.start()
        failed = queue.submit("/bad.pdf")
        assert _wait_for(lambda: failed.status == FAILED)
        ok = queue.submit("/good.pdf")
        assert _wait_for(lambda: ok.status == SUCCESS)

    def test_jobs_run_one_at_a_time(self):
        concurrent = 0
        peak = 0
        lock = threading.Lock()

        def slow(path: str, job: Job) -> None:
            nonlocal concurrent, peak
            with lock:
                concurrent += 1
                peak = max(peak, concurrent)
            time.sleep(0.05)
            with lock:
                concurrent -= 1

        queue = JobQueue(slow)
        queue.start()
        jobs = [queue.submit(f"/{i}.pdf") for i in range(4)]
        assert _wait_for(lambda: all(j.status == SUCCESS for j in jobs), timeout=10)
        assert peak == 1

    def test_same_file_resubmitted_reuses_active_job(self):
        release = threading.Event()
        queue = JobQueue(lambda path, job: release.wait(timeout=5))
        queue.start()
        first = queue.submit("/a.pdf")
        assert _wait_for(lambda: first.status == RUNNING)
        second = queue.submit("/a.pdf")
        assert second.id == first.id
        release.set()
        assert _wait_for(lambda: first.status == SUCCESS)

    def test_finished_file_can_be_resubmitted(self):
        queue = JobQueue(lambda path, job: None)
        queue.start()
        first = queue.submit("/a.pdf")
        assert _wait_for(lambda: first.status == SUCCESS)
        second = queue.submit("/a.pdf")
        assert second.id != first.id

    def test_get_unknown_job_returns_none(self):
        assert JobQueue(lambda path, job: None).get("inconnu") is None

    def test_stats_report_queue_state(self):
        queue = JobQueue(lambda path, job: None)
        queue.submit("/a.pdf")
        stats = queue.stats()
        assert stats["queued"] == 1
        assert stats["known_jobs"] == 1
        assert stats["worker_alive"] is False

    def test_start_is_idempotent(self):
        queue = JobQueue(lambda path, job: None)
        queue.start()
        queue.start()
        assert queue.stats()["worker_alive"] is True

    def test_history_is_bounded(self):
        queue = JobQueue(lambda path, job: None, history_size=3)
        queue.start()
        jobs = [queue.submit(f"/{i}.pdf") for i in range(10)]
        assert _wait_for(lambda: all(j.status == SUCCESS for j in jobs), timeout=10)
        queue.submit("/final.pdf")
        assert queue.stats()["known_jobs"] <= 4

    def test_unfinished_jobs_are_never_forgotten(self):
        release = threading.Event()
        queue = JobQueue(lambda path, job: release.wait(timeout=5), history_size=1)
        queue.start()
        first = queue.submit("/a.pdf")
        assert _wait_for(lambda: first.status == RUNNING)
        for i in range(5):
            queue.submit(f"/other{i}.pdf")
        # Un job qu'un client attend encore doit rester interrogeable.
        assert queue.get(first.id) is not None
        release.set()
