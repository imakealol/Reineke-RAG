"""Recurring auto-ingest scheduler.

A single :class:`AsyncIOScheduler` runs inside the FastAPI process. Each row
in the ``ingest_schedules`` table becomes one daily cron job at HH:MM.

Workflow:
  * On startup: load all enabled schedules from SQLite and register their
    cron triggers.
  * On CRUD mutations from the admin UI: call :func:`reload_schedules` to
    reconcile in-memory jobs with the DB.
  * When a job fires: it runs ``IngestionService.ingest_path`` against the
    stored ``base_path``/``tenant``/``project`` and writes the outcome back
    to the schedule row (``last_run_at``, ``last_status``, …).
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from .database import session_scope
from .ingestion_service import IngestionService
from .models import IngestSchedule
from .path_security import PathSecurityError
from .utils import get_logger

log = get_logger("rag.scheduler")


_JOB_PREFIX = "ingest_schedule:"


class IngestSchedulerService:
    def __init__(self) -> None:
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._lock = threading.Lock()

    # ----- lifecycle -------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._scheduler is not None:
                return
            self._scheduler = AsyncIOScheduler(timezone="UTC")
            self._scheduler.start()
        log.info("Scheduler started")
        self.reload_schedules()

    def shutdown(self) -> None:
        with self._lock:
            if self._scheduler is None:
                return
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:  # pragma: no cover
                log.exception("Scheduler shutdown failed")
            self._scheduler = None
        log.info("Scheduler stopped")

    # ----- public reconciliation ------------------------------------------

    def reload_schedules(self) -> int:
        """Synchronise the in-memory jobs with the DB. Idempotent."""
        if self._scheduler is None:
            log.warning("reload_schedules() called before start()")
            return 0

        # Materialise the rows into plain tuples *inside* the session so we
        # don't access attributes on detached ORM instances afterwards.
        with session_scope() as db:
            snapshot = [
                (r.id, r.enabled, r.hour, r.minute, r.tenant, r.project, r.base_path)
                for r in db.execute(select(IngestSchedule)).scalars().all()
            ]

        wanted_ids = {self._job_id(s[0]) for s in snapshot if s[1]}

        # Drop any in-memory jobs that no longer have an enabled DB row.
        for job in list(self._scheduler.get_jobs()):
            if job.id.startswith(_JOB_PREFIX) and job.id not in wanted_ids:
                self._scheduler.remove_job(job.id)
                log.info("Removed schedule job %s", job.id)

        # Add or replace enabled jobs.
        for sid, enabled, hour, minute, tenant, project, base_path in snapshot:
            if not enabled:
                continue
            trigger = CronTrigger(
                hour=hour, minute=minute, second=0, timezone="UTC"
            )
            self._scheduler.add_job(
                _run_schedule,
                trigger=trigger,
                args=[sid],
                id=self._job_id(sid),
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=3600,
            )
            log.info(
                "Registered schedule %s (%s/%s @ %02d:%02d UTC, path=%s)",
                sid, tenant, project, hour, minute, base_path,
            )

        return len(wanted_ids)

    def trigger_now(self, schedule_id: str) -> None:
        """Fire one schedule immediately (admin 'Run now' button)."""
        if self._scheduler is None:
            log.warning("trigger_now() called before start()")
            return
        # Schedule a one-shot job at the current time so it runs in the
        # AsyncIO loop the scheduler owns.
        self._scheduler.add_job(
            _run_schedule,
            args=[schedule_id],
            id=f"manual:{schedule_id}:{datetime.now(timezone.utc).isoformat()}",
            replace_existing=False,
            coalesce=True,
            max_instances=1,
        )

    # ----- helpers ---------------------------------------------------------

    @staticmethod
    def _job_id(schedule_id: str) -> str:
        return f"{_JOB_PREFIX}{schedule_id}"


# Module-global singleton — FastAPI imports `scheduler` from here.
scheduler = IngestSchedulerService()


# ---------------------------------------------------------------------------
# Job body
# ---------------------------------------------------------------------------

async def _run_schedule(schedule_id: str) -> None:
    """Look up the schedule row, run the ingest, write the outcome back."""
    log.info("Running schedule %s", schedule_id)

    with session_scope() as db:
        row: Optional[IngestSchedule] = db.get(IngestSchedule, schedule_id)
        if row is None or not row.enabled:
            log.info("Schedule %s not found or disabled — skipping.", schedule_id)
            return
        tenant = row.tenant
        project = row.project
        base_path = row.base_path
        recursive = row.recursive
        reindex_changed_only = row.reindex_changed_only

    started_at = datetime.now(timezone.utc)
    error_msg: Optional[str] = None
    result = None

    try:
        svc = IngestionService()
        with session_scope() as db:
            result = await svc.ingest_path(
                db,
                tenant=tenant,
                project=project,
                path=base_path,
                recursive=recursive,
                reindex_changed_only=reindex_changed_only,
            )
    except PathSecurityError as exc:
        error_msg = f"Pfad-Fehler: {exc}"
        log.error("Schedule %s failed: %s", schedule_id, error_msg)
    except Exception as exc:  # pragma: no cover
        error_msg = f"{type(exc).__name__}: {exc}"
        log.exception("Schedule %s crashed", schedule_id)

    # Persist outcome
    with session_scope() as db:
        row = db.get(IngestSchedule, schedule_id)
        if row is None:
            return
        row.last_run_at = started_at
        if error_msg:
            row.last_status = "failed"
            row.last_error = error_msg
            row.last_indexed = 0
            row.last_skipped = 0
            row.last_failed = 0
            row.last_chunks = 0
            row.last_job_id = None
        else:
            assert result is not None
            row.last_status = (
                "completed_with_errors" if result.failed_files else "completed"
            )
            row.last_error = (
                "; ".join(e.error for e in result.errors[:5]) if result.errors else None
            )
            row.last_indexed = result.indexed_files
            row.last_skipped = result.skipped_unchanged
            row.last_failed = result.failed_files
            row.last_chunks = result.chunks_created
            row.last_job_id = result.job_id

    log.info("Schedule %s done — status=%s", schedule_id, error_msg or "ok")
