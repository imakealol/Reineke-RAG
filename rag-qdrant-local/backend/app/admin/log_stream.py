"""SSE handlers for tailing live data into the admin Logs page."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import select

from ..database import session_scope
from ..models import RequestLog
from ..utils import app_log_path


def _format_sse(event: str, data: str) -> str:
    safe = data.replace("\r", "").split("\n")
    payload = "\n".join(f"data: {line}" for line in safe)
    return f"event: {event}\n{payload}\n\n"


async def request_log_stream() -> AsyncIterator[str]:
    """Tail the ``request_logs`` table — one event per new row."""
    last_id = 0
    with session_scope() as db:
        row = db.execute(
            select(RequestLog.id).order_by(RequestLog.id.desc()).limit(1)
        ).scalar_one_or_none()
        if row:
            last_id = int(row)

    yield _format_sse("ready", json.dumps({"since_id": last_id}))

    while True:
        await asyncio.sleep(1.0)
        try:
            payloads: list[dict] = []
            with session_scope() as db:
                rows = (
                    db.execute(
                        select(RequestLog)
                        .where(RequestLog.id > last_id)
                        .order_by(RequestLog.id.asc())
                        .limit(200)
                    )
                    .scalars()
                    .all()
                )
                # Materialise inside the session — once it closes the rows are
                # detached and lazy-loading attributes will raise.
                for r in rows:
                    last_id = int(r.id)
                    payloads.append(
                        {
                            "id": r.id,
                            "ts": r.created_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
                            "method": r.method,
                            "path": r.path,
                            "status": r.status_code,
                            "duration_ms": r.duration_ms,
                            "tenant": r.tenant or "",
                            "project": r.project or "",
                            "client_ip": r.client_ip or "",
                            "error": r.error_message or "",
                        }
                    )
            for p in payloads:
                yield _format_sse("entry", json.dumps(p, ensure_ascii=False))
        except Exception as exc:  # pragma: no cover
            yield _format_sse("error", json.dumps({"message": str(exc)}))
            await asyncio.sleep(5.0)


async def app_log_stream() -> AsyncIterator[str]:
    """Tail the rotating app log file line by line."""
    path: Path = app_log_path()
    yield _format_sse("ready", json.dumps({"path": str(path)}))

    # Open at the current end so the user only sees new lines after they
    # opened the page — matches `tail -f` behaviour.
    while not path.exists():
        await asyncio.sleep(1.0)

    with path.open("r", encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)  # 2 = end
        while True:
            line = f.readline()
            if not line:
                await asyncio.sleep(0.5)
                # The rotating handler may have rotated the file out from
                # under us — re-open if it shrank.
                try:
                    if path.exists() and f.tell() > path.stat().st_size:
                        f.close()
                        f = path.open("r", encoding="utf-8", errors="replace")
                except OSError:
                    pass
                continue
            payload = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "line": line.rstrip("\n"),
            }
            yield _format_sse("line", json.dumps(payload, ensure_ascii=False))
