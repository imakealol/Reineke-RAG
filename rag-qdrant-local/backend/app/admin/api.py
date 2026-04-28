"""JSON / HTMX endpoints under ``/admin/api`` — backs the admin pages.

These endpoints are read-mostly; the only actions exposed today are document
deletion (forwards to the existing ingestion service) and ingest triggers
(forwards to the existing routes).  Everything else is reporting.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..chat_service import ChatService  # noqa: F401  (kept available for future)
from ..config import settings
from ..database import get_db
from ..ingestion_service import IngestionService
from ..models import Document, IngestionJob, IngestSchedule, RequestLog
from ..ollama_client import OllamaClient
from ..path_security import (
    PathSecurityError,
    assert_existing_dir,
    resolve_safe_path,
)
from ..qdrant_store import QdrantStore
from ..schemas import IngestPathRequest, IngestScheduleIn, ScanPathRequest
from ..scheduler_service import scheduler
from ..settings_overrides import (
    EDITABLE_KEYS,
    clear_override,
    overlay_view,
    set_override,
)
from ..source_scanner import scan_directory
from ..utils import get_logger, new_id
from .log_stream import app_log_stream, request_log_stream


log = get_logger("rag.admin.api")

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(prefix="/admin/api", include_in_schema=False)


# --- Health (uebersicht) -----------------------------------------------------

@router.get("/health", response_class=HTMLResponse)
async def health_panel(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Re-uses the same checks as the public ``/health`` endpoint, but renders
    a Bootstrap card grid for the Übersicht page."""
    backend_ok = True

    ollama = OllamaClient()
    ollama_ok = False
    ollama_detail = "unbekannt"
    emb_ok = False
    emb_detail = "—"
    chat_ok = False
    chat_detail = "—"
    try:
        ollama_ok = await ollama.ping()
        ollama_detail = (
            f"erreichbar unter {settings.OLLAMA_BASE_URL}"
            if ollama_ok
            else "nicht erreichbar"
        )
        if ollama_ok:
            emb_ok = await ollama.has_model(settings.EMBEDDING_MODEL)
            emb_detail = (
                settings.EMBEDDING_MODEL
                if emb_ok
                else f"Modell '{settings.EMBEDDING_MODEL}' nicht geladen"
            )
            chat_ok = await ollama.has_model(settings.CHAT_MODEL)
            chat_detail = (
                settings.CHAT_MODEL
                if chat_ok
                else f"Modell '{settings.CHAT_MODEL}' nicht geladen"
            )
    except Exception as exc:
        ollama_detail = f"Fehler: {exc}"

    qdrant_ok = False
    qdrant_detail = "unbekannt"
    try:
        qdrant_ok = await asyncio.to_thread(QdrantStore().ping)
        qdrant_detail = (
            f"erreichbar unter {settings.QDRANT_URL}"
            if qdrant_ok
            else "nicht erreichbar"
        )
    except Exception as exc:
        qdrant_detail = f"Fehler: {exc}"

    # Quick counts for the secondary tiles.
    doc_total = db.execute(
        select(func.count(Document.id)).where(Document.status != "deleted")
    ).scalar_one()
    chunks_total = db.execute(
        select(func.coalesce(func.sum(Document.chunks_count), 0)).where(
            Document.status != "deleted"
        )
    ).scalar_one()
    tp_count = db.execute(
        select(func.count(func.distinct(Document.tenant + "::" + Document.project)))
    ).scalar_one()

    items = [
        {"name": "Backend", "ok": backend_ok, "detail": "läuft"},
        {"name": "Qdrant", "ok": qdrant_ok, "detail": qdrant_detail},
        {"name": "Ollama", "ok": ollama_ok, "detail": ollama_detail},
        {"name": "Embedding-Modell", "ok": emb_ok, "detail": emb_detail},
        {"name": "Chat-Modell", "ok": chat_ok, "detail": chat_detail},
    ]

    return templates.TemplateResponse(
        "partials/health_panel.html",
        {
            "request": request,
            "items": items,
            "doc_total": doc_total,
            "chunks_total": chunks_total,
            "tp_count": tp_count,
            "allowed_paths": [str(p) for p in settings.allowed_base_paths],
        },
    )


# --- Tenants / projects ------------------------------------------------------

@router.get("/tenants", response_class=HTMLResponse)
async def tenants_panel(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Two-level tree: tenant -> projects, with document counts."""
    rows = db.execute(
        select(
            Document.tenant,
            Document.project,
            func.count(Document.id).label("docs"),
            func.coalesce(func.sum(Document.chunks_count), 0).label("chunks"),
            func.max(Document.updated_at).label("last_updated"),
        )
        .where(Document.status != "deleted")
        .group_by(Document.tenant, Document.project)
        .order_by(Document.tenant, Document.project)
    ).all()

    tree: Dict[str, List[Dict[str, Any]]] = {}
    for tenant, project, docs, chunks, last_updated in rows:
        tree.setdefault(tenant, []).append(
            {
                "project": project,
                "docs": docs,
                "chunks": chunks,
                "last_updated": last_updated,
            }
        )

    return templates.TemplateResponse(
        "partials/tenants_tree.html",
        {"request": request, "tree": tree},
    )


# --- Documents ---------------------------------------------------------------

@router.get("/documents", response_class=HTMLResponse)
async def documents_panel(
    request: Request,
    tenant: str = Query(""),
    project: str = Query(""),
    status_filter: str = Query("", alias="status"),
    q: str = Query(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    stmt = select(Document)
    if tenant:
        stmt = stmt.where(Document.tenant == tenant)
    if project:
        stmt = stmt.where(Document.project == project)
    if status_filter:
        stmt = stmt.where(Document.status == status_filter)
    else:
        stmt = stmt.where(Document.status != "deleted")
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Document.file_name.ilike(like))
    stmt = stmt.order_by(Document.updated_at.desc()).limit(500)
    docs = db.execute(stmt).scalars().all()

    return templates.TemplateResponse(
        "partials/documents_table.html",
        {"request": request, "docs": docs},
    )


@router.delete("/documents/{document_id}", response_class=HTMLResponse)
async def delete_document(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    svc = IngestionService()
    try:
        await svc.delete_document(db, document_id=document_id)
    except Exception as exc:
        log.exception("Delete failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        )
    # Return an empty fragment — HTMX will swap the row out.
    return HTMLResponse("")


# --- Ingest wizard -----------------------------------------------------------

@router.post("/ingest/scan", response_class=HTMLResponse)
async def ingest_scan(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    form = await request.form()
    tenant = (form.get("tenant") or "").strip()
    project = (form.get("project") or "").strip()
    raw_path = (form.get("path") or "").strip()
    recursive = form.get("recursive") == "on"

    error: Optional[str] = None
    result = None
    safe_path = ""
    skip_summary: Dict[str, int] = {"already_indexed": 0, "changed": 0, "new": 0}
    try:
        if not tenant or not project or not raw_path:
            raise PathSecurityError("Bitte Mandant, Projekt und Pfad ausfüllen.")
        safe = resolve_safe_path(raw_path)
        assert_existing_dir(safe)
        safe_path = str(safe)
        result = scan_directory(safe, recursive=recursive)

        # Classify each supported file vs the existing SQLite index so the
        # user sees what will be skipped vs (re-)ingested. We don't compute
        # checksums here — that's expensive and the ingest itself does it.
        # Instead we use file size + mtime as a cheap "changed" heuristic.
        if result.supported:
            existing = {
                d.source_path: d
                for d in db.execute(
                    select(Document).where(
                        Document.tenant == tenant,
                        Document.project == project,
                        Document.status != "deleted",
                    )
                ).scalars().all()
            }
            from datetime import datetime as _dt, timezone as _tz
            for f in result.supported:
                doc = existing.get(f.path)
                if doc is None:
                    skip_summary["new"] += 1
                    continue
                if doc.status != "indexed":
                    skip_summary["changed"] += 1
                    continue
                if doc.file_size != f.size_bytes:
                    skip_summary["changed"] += 1
                    continue
                # Compare mtimes as POSIX timestamps with a 2-second tolerance —
                # robust against timezone serialisation quirks (SQLite stores
                # naive datetimes; the scanner uses tz-aware ISO strings).
                if doc.modified_at and f.modified_at:
                    try:
                        scanned_ts = _dt.fromisoformat(f.modified_at).timestamp()
                    except ValueError:
                        scanned_ts = None
                    indexed_dt = doc.modified_at
                    if indexed_dt.tzinfo is None:
                        indexed_dt = indexed_dt.replace(tzinfo=_tz.utc)
                    indexed_ts = indexed_dt.timestamp()
                    if scanned_ts is not None and abs(scanned_ts - indexed_ts) > 2.0:
                        skip_summary["changed"] += 1
                        continue
                skip_summary["already_indexed"] += 1
    except PathSecurityError as exc:
        error = str(exc)
    except Exception as exc:
        log.exception("Scan failed")
        error = f"Unerwarteter Fehler: {exc}"

    return templates.TemplateResponse(
        "partials/ingest_scan.html",
        {
            "request": request,
            "error": error,
            "result": result,
            "tenant": tenant,
            "project": project,
            "path": safe_path or raw_path,
            "recursive": recursive,
            "skip": skip_summary,
        },
    )


@router.post("/ingest/run", response_class=HTMLResponse)
async def ingest_run(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    form = await request.form()
    tenant = (form.get("tenant") or "").strip()
    project = (form.get("project") or "").strip()
    raw_path = (form.get("path") or "").strip()
    recursive = form.get("recursive") == "on"
    reindex_changed_only = form.get("reindex_changed_only") == "on"

    error: Optional[str] = None
    result = None
    try:
        req = IngestPathRequest(
            tenant=tenant,
            project=project,
            path=raw_path,
            recursive=recursive,
            reindex_changed_only=reindex_changed_only,
        )
        svc = IngestionService()
        result = await svc.ingest_path(
            db,
            tenant=req.tenant,
            project=req.project,
            path=req.path,
            recursive=req.recursive,
            reindex_changed_only=req.reindex_changed_only,
        )
    except PathSecurityError as exc:
        error = str(exc)
    except Exception as exc:
        log.exception("Ingest failed")
        error = f"Fehler beim Ingest: {exc}"

    return templates.TemplateResponse(
        "partials/ingest_result.html",
        {"request": request, "error": error, "result": result},
    )


# --- Job history -------------------------------------------------------------

@router.get("/jobs", response_class=HTMLResponse)
async def jobs_panel(
    request: Request,
    tenant: str = Query(""),
    project: str = Query(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    stmt = select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(200)
    if tenant:
        stmt = stmt.where(IngestionJob.tenant == tenant)
    if project:
        stmt = stmt.where(IngestionJob.project == project)
    jobs = db.execute(stmt).scalars().all()
    return templates.TemplateResponse(
        "partials/jobs_table.html",
        {"request": request, "jobs": jobs},
    )


# --- Request log audit -------------------------------------------------------

@router.get("/request-logs", response_class=HTMLResponse)
async def request_logs_panel(
    request: Request,
    status_filter: str = Query("", alias="status"),
    method: str = Query(""),
    tenant: str = Query(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    stmt = select(RequestLog).order_by(RequestLog.id.desc()).limit(300)
    if status_filter == "errors":
        stmt = stmt.where(RequestLog.status_code >= 400)
    elif status_filter.isdigit():
        stmt = stmt.where(RequestLog.status_code == int(status_filter))
    if method:
        stmt = stmt.where(RequestLog.method == method.upper())
    if tenant:
        stmt = stmt.where(RequestLog.tenant == tenant)
    rows = db.execute(stmt).scalars().all()
    return templates.TemplateResponse(
        "partials/request_logs_table.html",
        {"request": request, "rows": rows},
    )


@router.get("/request-logs/stream")
async def request_logs_sse() -> StreamingResponse:
    return StreamingResponse(
        request_log_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# --- App log tail ------------------------------------------------------------

@router.get("/app-log/stream")
async def app_log_sse() -> StreamingResponse:
    return StreamingResponse(
        app_log_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# --- Configuration view -----------------------------------------------------

_REDACT_KEYS = {"QDRANT_API_KEY"}
_EDITABLE_NAMES = {k.name for k in EDITABLE_KEYS}


def _readonly_settings() -> List[Dict[str, Any]]:
    """Read-only infrastructure settings (env-only)."""
    out: List[Dict[str, Any]] = []
    for name in settings.model_fields:
        if name in _EDITABLE_NAMES:
            continue
        value = getattr(settings, name)
        if name in _REDACT_KEYS and value:
            display = "•" * 8
        elif name == "ALLOWED_BASE_PATHS":
            display = ", ".join(str(p) for p in settings.allowed_base_paths) or "(leer)"
        else:
            display = "" if value is None else str(value)
        out.append({"key": name, "value": display})
    return out


@router.get("/config", response_class=HTMLResponse)
async def config_panel(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "partials/config_table.html",
        {
            "request": request,
            "editable": overlay_view(),
            "readonly": _readonly_settings(),
        },
    )


@router.post("/config/{key}", response_class=HTMLResponse)
async def config_set(
    key: str, request: Request
) -> HTMLResponse:
    form = await request.form()
    raw = (form.get("value") or "").strip()
    try:
        set_override(key, raw)
    except ValueError as exc:
        # Re-render the table with an error banner above it.
        return templates.TemplateResponse(
            "partials/config_table.html",
            {
                "request": request,
                "editable": overlay_view(),
                "readonly": _readonly_settings(),
                "error": str(exc),
            },
        )
    except Exception as exc:  # pragma: no cover
        log.exception("config_set failed")
        return templates.TemplateResponse(
            "partials/config_table.html",
            {
                "request": request,
                "editable": overlay_view(),
                "readonly": _readonly_settings(),
                "error": f"Unerwarteter Fehler: {exc}",
            },
        )
    return templates.TemplateResponse(
        "partials/config_table.html",
        {
            "request": request,
            "editable": overlay_view(),
            "readonly": _readonly_settings(),
            "saved": key,
        },
    )


@router.post("/config/{key}/reset", response_class=HTMLResponse)
async def config_reset(key: str, request: Request) -> HTMLResponse:
    try:
        clear_override(key)
    except ValueError as exc:
        return templates.TemplateResponse(
            "partials/config_table.html",
            {
                "request": request,
                "editable": overlay_view(),
                "readonly": _readonly_settings(),
                "error": str(exc),
            },
        )
    return templates.TemplateResponse(
        "partials/config_table.html",
        {
            "request": request,
            "editable": overlay_view(),
            "readonly": _readonly_settings(),
            "saved": key,
        },
    )


# --- OpenWebUI pipe download -----------------------------------------------

_PIPE_TENANT_RE = re.compile(r'(tenant: str = Field\(default=)"[^"]*"')
_PIPE_PROJECT_RE = re.compile(r'(project: str = Field\(default=)"[^"]*"')
_PIPE_ID_RE = re.compile(r'(self\.id = )"[^"]*"')
_PIPE_NAME_RE = re.compile(r'(self\.name = )"[^"]*"')


def _safe_id_segment(value: str) -> str:
    """Whittle a tenant/project string down to safe chars for a Python id."""
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_") or "default"


def _customize_pipe_source(text: str, tenant: str, project: str) -> str:
    """Return the pipe source with tenant/project + id/name pre-filled."""
    if tenant:
        text = _PIPE_TENANT_RE.sub(rf'\1"{tenant}"', text, count=1)
    if project:
        text = _PIPE_PROJECT_RE.sub(rf'\1"{project}"', text, count=1)
    if tenant and project:
        suffix = f"{_safe_id_segment(tenant)}_{_safe_id_segment(project)}"
        new_id = f"reineke_rag_pipe_{suffix}"
        new_name = f"Reineke RAG · {tenant} / {project}"
        text = _PIPE_ID_RE.sub(rf'\1"{new_id}"', text, count=1)
        text = _PIPE_NAME_RE.sub(rf'\1"{new_name}"', text, count=1)
    return text


@router.get("/openwebui-pipe.py", response_class=Response)
async def openwebui_pipe_source(
    tenant: str = Query(""),
    project: str = Query(""),
) -> Response:
    """Return the canonical Pipe source from docs/openwebui_pipe.py.

    With ``?tenant=X&project=Y``, the response is **customised**: the
    Valve defaults for ``tenant``/``project`` are pre-filled and the
    function gets a unique ``id`` and human-readable ``name`` so multiple
    pipes (one per collection) can coexist in OpenWebUI.

    Used by the Konfiguration page (generic) and the Mandanten page
    (one per collection).
    """
    pipe_file = Path(__file__).resolve().parents[3] / "docs" / "openwebui_pipe.py"
    try:
        text = pipe_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="openwebui_pipe.py not found")

    text = _customize_pipe_source(text, tenant.strip(), project.strip())

    if tenant and project:
        fname = f"openwebui_pipe_{_safe_id_segment(tenant)}_{_safe_id_segment(project)}.py"
    else:
        fname = "openwebui_pipe.py"

    return Response(
        content=text,
        media_type="text/x-python; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# --- Tiny JSON helper used by the Mandanten select ---------------------------

@router.get("/tenants.json")
async def tenants_json(db: Session = Depends(get_db)) -> JSONResponse:
    rows = db.execute(
        select(Document.tenant, Document.project)
        .where(Document.status != "deleted")
        .group_by(Document.tenant, Document.project)
        .order_by(Document.tenant, Document.project)
    ).all()
    return JSONResponse(
        [{"tenant": t, "project": p} for (t, p) in rows]
    )


# --- Auto-ingest schedules --------------------------------------------------

def _render_schedule_table(request: Request, db: Session) -> HTMLResponse:
    rows = db.execute(
        select(IngestSchedule).order_by(IngestSchedule.tenant, IngestSchedule.project)
    ).scalars().all()
    return templates.TemplateResponse(
        "partials/schedules_table.html",
        {"request": request, "rows": rows},
    )


@router.get("/schedules", response_class=HTMLResponse)
async def schedules_panel(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return _render_schedule_table(request, db)


@router.post("/schedules", response_class=HTMLResponse)
async def schedules_create(
    request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    form = await request.form()
    try:
        time_str = (form.get("time") or "").strip()
        if not time_str or ":" not in time_str:
            raise ValueError("Bitte Uhrzeit im Format HH:MM angeben.")
        hour_s, minute_s = time_str.split(":", 1)
        payload = IngestScheduleIn(
            tenant=(form.get("tenant") or "").strip(),
            project=(form.get("project") or "").strip(),
            base_path=(form.get("base_path") or "").strip(),
            recursive=form.get("recursive") == "on",
            reindex_changed_only=form.get("reindex_changed_only") == "on",
            hour=int(hour_s),
            minute=int(minute_s),
            enabled=form.get("enabled", "on") == "on",
        )
        # Validate path now so the user sees the error in the UI rather than
        # at first cron firing.
        resolve_safe_path(payload.base_path)
    except (PathSecurityError, ValueError) as exc:
        return HTMLResponse(
            f'<div class="alert alert-danger" role="alert">{exc}</div>',
            status_code=200,
        )
    except Exception as exc:  # pragma: no cover
        log.exception("schedules_create validation failed")
        return HTMLResponse(
            f'<div class="alert alert-danger" role="alert">Unerwarteter Fehler: {exc}</div>',
            status_code=200,
        )

    row = IngestSchedule(
        id=new_id(),
        tenant=payload.tenant,
        project=payload.project,
        base_path=payload.base_path,
        recursive=payload.recursive,
        reindex_changed_only=payload.reindex_changed_only,
        hour=payload.hour,
        minute=payload.minute,
        enabled=payload.enabled,
    )
    db.add(row)
    db.commit()
    scheduler.reload_schedules()
    return _render_schedule_table(request, db)


@router.post("/schedules/{schedule_id}/toggle", response_class=HTMLResponse)
async def schedules_toggle(
    schedule_id: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    row = db.get(IngestSchedule, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    row.enabled = not row.enabled
    db.commit()
    scheduler.reload_schedules()
    return _render_schedule_table(request, db)


@router.post("/schedules/{schedule_id}/update", response_class=HTMLResponse)
async def schedules_update(
    schedule_id: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    row = db.get(IngestSchedule, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    form = await request.form()
    try:
        time_str = (form.get("time") or "").strip()
        if not time_str or ":" not in time_str:
            raise ValueError("Bitte Uhrzeit im Format HH:MM angeben.")
        hour_s, minute_s = time_str.split(":", 1)
        new_path = (form.get("base_path") or "").strip()
        if new_path:
            resolve_safe_path(new_path)
            row.base_path = new_path
        row.hour = int(hour_s)
        row.minute = int(minute_s)
        row.recursive = form.get("recursive") == "on"
        row.reindex_changed_only = form.get("reindex_changed_only") == "on"
    except (PathSecurityError, ValueError) as exc:
        return HTMLResponse(
            f'<div class="alert alert-danger" role="alert">{exc}</div>',
            status_code=200,
        )
    db.commit()
    scheduler.reload_schedules()
    return _render_schedule_table(request, db)


@router.delete("/schedules/{schedule_id}", response_class=HTMLResponse)
async def schedules_delete(
    schedule_id: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    row = db.get(IngestSchedule, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    db.delete(row)
    db.commit()
    scheduler.reload_schedules()
    return _render_schedule_table(request, db)


@router.post("/schedules/{schedule_id}/run", response_class=HTMLResponse)
async def schedules_run_now(
    schedule_id: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    row = db.get(IngestSchedule, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    scheduler.trigger_now(schedule_id)
    return _render_schedule_table(request, db)
