"""Small reusable utilities (hashing, time, logging, ids)."""

from __future__ import annotations

import hashlib
import logging
import logging.handlers
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

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
