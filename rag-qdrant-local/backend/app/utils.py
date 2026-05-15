"""Small reusable utilities (hashing, time, logging, ids)."""

from __future__ import annotations

import hashlib
import logging
import logging.handlers
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .config import settings


# ----- Logging ---------------------------------------------------------------

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_LOG_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def app_log_path() -> Path:
    """Resolve the rotating application log file path under storage/logs."""
    base = settings.sqlite_path.parent / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base / "app.log"


def configure_logging() -> None:
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)

    root = logging.getLogger()
    root.setLevel(level)

    # Wipe any handlers attached by uvicorn/basicConfig before us so we own
    # the format and avoid double-printed lines.
    for h in list(root.handlers):
        root.removeHandler(h)

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    root.addHandler(stream)

    file_handler = logging.handlers.RotatingFileHandler(
        app_log_path(), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ----- Per-job log capture ---------------------------------------------------
#
# When an ingest job starts, we want every log record produced *during that
# job* to be tee-d into a dedicated file under ``storage/job-logs/<id>.log``
# without disturbing the global app log. We achieve this with two pieces:
#
#  1. A :class:`contextvars.ContextVar` set to the currently-active job id.
#     ContextVars propagate across ``await`` boundaries and ``asyncio.to_thread``,
#     so a single background task can do its work and every nested log call
#     inherits the right job id.
#  2. A :class:`_JobIdLogFilter` attached to a dedicated :class:`FileHandler`.
#     The filter rejects any record whose active job id does not match the
#     handler's id — so two concurrent ingests would each get their own file,
#     never crossing the streams.
#
# Tracker tagging via ContextVar (instead of a custom logger name) means the
# existing ``log = get_logger(__name__)`` declarations in every module continue
# to work unchanged.

current_job_id: ContextVar[Optional[str]] = ContextVar("current_job_id", default=None)


class _JobIdLogFilter(logging.Filter):
    """Pass a record only when the ContextVar matches the handler's job id."""

    def __init__(self, job_id: str) -> None:
        super().__init__()
        self._job_id = job_id

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        return current_job_id.get() == self._job_id


def job_log_path(job_id: str) -> Path:
    """Resolve the on-disk path for a job's log file (creates the dir)."""
    return settings.job_logs_dir / f"{job_id}.log"


@contextmanager
def capture_logs_for_job(job_id: str) -> Iterator[Path]:
    """Tee every log record emitted from within this ``with`` block into the
    job-specific log file.

    The handler is attached to the *root* logger so any module's logger that
    propagates (which is all of ours, by default) is captured. The filter
    keeps records from other jobs out, so two background ingests running at
    once each write to their own file only.

    On context exit, the handler is detached and closed even if the body
    raised — important, otherwise we leak open file descriptors on repeated
    ingests.
    """
    log_path = job_log_path(job_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
    handler.addFilter(_JobIdLogFilter(job_id))

    root = logging.getLogger()
    root.addHandler(handler)
    token = current_job_id.set(job_id)
    try:
        yield log_path
    finally:
        current_job_id.reset(token)
        root.removeHandler(handler)
        handler.close()


# ----- IDs / time ------------------------------------------------------------

def new_id() -> str:
    return str(uuid.uuid4())


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def deterministic_uuid(*parts: str) -> str:
    """Produce a stable UUID-5 from arbitrary parts (used for chunk point ids)."""
    name = "||".join(parts)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


# ----- Hashing ---------------------------------------------------------------

def sha256_file(path: Path, *, chunk_size: int = 1 << 20) -> str:
    """Stream-hash a file (1 MiB chunks)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk_size), b""):
            h.update(block)
    return h.hexdigest()


# ----- File metadata ---------------------------------------------------------

def file_modified_iso(path: Path) -> str:
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def chunked(items: Iterable, n: int):
    """Yield successive n-sized lists from items."""
    buf = []
    for it in items:
        buf.append(it)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf
