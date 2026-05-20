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
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..chat_service import DEFAULT_SYSTEM_PROMPT, ChatService
from ..system_prompt_store import (
    SYSTEM_PROMPT_MAX_CHARS,
    clear_system_prompt,
    get_system_prompt,
    has_override as system_prompt_has_override,
    set_system_prompt,
)
from ..config import settings
from ..database import SessionLocal, get_db
from ..ingestion_service import IngestionService
from ..models import Document, IngestionJob, IngestSchedule, RequestLog, TenantProjectPrompt
from .. import rerank_settings as rerank_settings_module
from ..ollama_client import OllamaClient
from ..path_security import (
    PathSecurityError,
    assert_existing_dir,
    resolve_safe_path,
)
from ..qdrant_store import QdrantStore
from ..schemas import (
    IngestPathRequest,
    IngestScheduleIn,
    PERSONA_PROMPT_MAX_CHARS,
    ScanPathRequest,
)
from ..scheduler_service import scheduler
from ..settings_overrides import (
    EDITABLE_KEYS,
    clear_override,
    overlay_view,
    set_override,
)
from ..source_scanner import scan_directory
from ..utils import capture_logs_for_job, get_logger, job_log_path, new_id
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

    # Reranker — soft dependency, ok=true unless actively broken.
    if not settings.RERANK_ENABLED:
        rerank_ok, rerank_detail = True, "deaktiviert (RERANK_ENABLED=false)"
    else:
        try:
            from .. import reranker as reranker_module
            if reranker_module.is_loaded():
                rerank_ok = True
                rerank_detail = "geladen: " + ", ".join(reranker_module.loaded_model_names())
            else:
                rerank_ok = True
                rerank_detail = (
                    f"aktiv, lazy — erster Aufruf lädt "
                    f"'{settings.RERANK_MODEL}' (5–15 s)"
                )
        except Exception as exc:
            rerank_ok, rerank_detail = False, f"Fehler: {exc}"

    items = [
        {"name": "Backend", "ok": backend_ok, "detail": "läuft"},
        {"name": "Qdrant", "ok": qdrant_ok, "detail": qdrant_detail},
        {"name": "Ollama", "ok": ollama_ok, "detail": ollama_detail},
        {"name": "Embedding-Modell", "ok": emb_ok, "detail": emb_detail},
        {"name": "Chat-Modell", "ok": chat_ok, "detail": chat_detail},
        {"name": "Reranker", "ok": rerank_ok, "detail": rerank_detail},
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
    """Two-level tree: tenant -> projects, with document counts + persona prompts."""
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

    # Pre-fetch persona prompts + per-agent chat models so we don't hammer
    # the DB inside the template.
    config_map: Dict[Tuple[str, str], TenantProjectPrompt] = {}
    for p in db.execute(select(TenantProjectPrompt)).scalars().all():
        config_map[(p.tenant, p.project)] = p

    tree: Dict[str, List[Dict[str, Any]]] = {}
    for tenant, project, docs, chunks, last_updated in rows:
        row = config_map.get((tenant, project))
        # Resolve effective rerank settings so the form shows the current
        # truth — auto/on/off, plus the smart-default explanation.
        effective = rerank_settings_module.resolve(db, tenant=tenant, project=project)
        # Tri-state for the dropdown: "auto" means no override (NULL in DB).
        rerank_choice = "auto"
        if row is not None and row.rerank_enabled is True:
            rerank_choice = "on"
        elif row is not None and row.rerank_enabled is False:
            rerank_choice = "off"
        tree.setdefault(tenant, []).append(
            {
                "project": project,
                "docs": docs,
                "chunks": chunks,
                "last_updated": last_updated,
                "persona_prompt": (row.persona_prompt or "") if row else "",
                "chat_model": (row.chat_model or "") if row else "",
                "rerank_choice": rerank_choice,
                "rerank_overfetch_k_override": (
                    row.rerank_overfetch_k if row and row.rerank_overfetch_k else None
                ),
                "rerank_effective": effective,
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


async def _run_ingest_in_background(
    *,
    job_id: str,
    tenant: str,
    project: str,
    path: str,
    recursive: bool,
    reindex_changed_only: bool,
    include_extensions: Optional[List[str]],
) -> None:
    """Run the ingest with its own DB session so the HTTP request that kicked
    it off can return immediately. Any uncaught exception lands here and is
    written back to the job row as a terminal-state error — otherwise the
    progress polling would never know the run died.
    """
    bg_db: Session = SessionLocal()
    try:
        # Capture every log record emitted during this ingest into the per-job
        # file under storage/job-logs/<job_id>.log. The view/download endpoints
        # serve that file. Wrapping the whole body means an early Ollama or
        # Qdrant connectivity error also shows up there.
        with capture_logs_for_job(job_id):
            try:
                svc = IngestionService()
                await svc.ingest_path(
                    bg_db,
                    tenant=tenant,
                    project=project,
                    path=path,
                    recursive=recursive,
                    reindex_changed_only=reindex_changed_only,
                    include_extensions=include_extensions,
                    job_id=job_id,
                )
            except Exception:
                # Log the traceback *inside* the capture so the per-job file
                # contains it; then re-raise to the outer handler that marks
                # the row as failed.
                log.exception("Background ingest failed")
                raise
    except Exception as exc:
        bg_db.rollback()
        # Best-effort: update the job row so the UI can show the failure
        # instead of a perpetually-spinning progress bar.
        try:
            job = bg_db.execute(
                select(IngestionJob).where(IngestionJob.id == job_id)
            ).scalar_one_or_none()
            if job is not None:
                job.status = "failed"
                job.error_message = str(exc)
                job.current_file = None
                job.completed_at = datetime.now(timezone.utc)
                bg_db.commit()
        except Exception:  # pragma: no cover — defensive against DB outages
            log.exception("Failed to mark job %s as failed", job_id)
            bg_db.rollback()
    finally:
        bg_db.close()


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

    # The scan-result template renders one ``include_extensions=.pdf`` field
    # per checked type, plus a hidden ``include_extensions_picker=1`` marker
    # so we can tell "user unchecked everything" (whitelist=[]) apart from
    # "no picker shown at all" (whitelist=None → legacy behaviour, ingest
    # every supported type).
    raw_includes = form.getlist("include_extensions")
    include_extensions: Optional[List[str]] = None
    if "include_extensions_picker" in form:
        include_extensions = [e.strip().lower() for e in raw_includes if e.strip()]

    # --- 1) Path validation up-front so we can reject before spinning up a
    #        background task and a job row.
    error: Optional[str] = None
    safe_path_str = ""
    try:
        req = IngestPathRequest(
            tenant=tenant,
            project=project,
            path=raw_path,
            recursive=recursive,
            reindex_changed_only=reindex_changed_only,
            include_extensions=include_extensions,
        )
        safe = resolve_safe_path(req.path)
        assert_existing_dir(safe)
        safe_path_str = str(safe)
    except PathSecurityError as exc:
        error = str(exc)
    except Exception as exc:
        log.exception("Ingest validation failed")
        error = f"Fehler beim Ingest: {exc}"

    if error:
        return templates.TemplateResponse(
            "partials/ingest_result.html",
            {"request": request, "error": error, "result": None},
        )

    # --- 2) Pre-create the job row so we have an id to hand to the live
    #        progress template (htmx will start polling it immediately).
    job = IngestionJob(
        id=new_id(),
        tenant=req.tenant,
        project=req.project,
        source_path=safe_path_str,
        status="running",
    )
    db.add(job)
    db.commit()

    # --- 3) Kick off the actual work in the background. asyncio.create_task
    #        runs concurrently with the response below — the task survives
    #        this request as long as the FastAPI process keeps running.
    asyncio.create_task(
        _run_ingest_in_background(
            job_id=job.id,
            tenant=req.tenant,
            project=req.project,
            path=safe_path_str,
            recursive=req.recursive,
            reindex_changed_only=req.reindex_changed_only,
            include_extensions=req.include_extensions,
        )
    )

    # --- 4) Return the progress fragment. It contains an htmx-polling div
    #        that fetches /admin/api/jobs/{id}/progress every 2 seconds until
    #        the job reaches a terminal state.
    return templates.TemplateResponse(
        "partials/ingest_progress.html",
        {
            "request": request,
            "job": job,
            "progress": _build_job_progress(job),
        },
    )


_JOB_LOG_TAIL_LINES = 15
_JOB_LOG_MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024  # 10 MiB — generous, but capped


def _tail_job_log(job_id: str, *, lines: int = _JOB_LOG_TAIL_LINES) -> str:
    """Return the last ``lines`` lines of a job's log file, or '' if absent.

    Reads the whole file — fine because the per-job log is small (each
    ingest is bounded to a few thousand lines max). Avoids the complication
    of streaming a partial read.
    """
    path = job_log_path(job_id)
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    rows = text.splitlines()
    return "\n".join(rows[-lines:])


def _build_job_progress(job: IngestionJob) -> Dict[str, Any]:
    """Compute the values the live progress template needs.

    Pure function over the job row — kept separate so we can unit-test the
    arithmetic (percent + ETA) without dragging in template rendering or a
    full request stack.
    """
    total = job.files_found or 0
    done = (job.files_indexed or 0) + (job.files_skipped or 0) + (job.files_failed or 0)
    percent = 0
    if total > 0:
        percent = min(100, int(round(done * 100 / total)))

    elapsed_seconds = 0.0
    if job.created_at:
        started = job.created_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed_seconds = (datetime.now(timezone.utc) - started).total_seconds()
        elapsed_seconds = max(0.0, elapsed_seconds)

    eta_seconds: Optional[float] = None
    is_terminal = job.status not in ("running", "pending")
    if not is_terminal and done > 0 and total > done and elapsed_seconds > 0.5:
        rate = done / elapsed_seconds  # files per second
        if rate > 0:
            eta_seconds = (total - done) / rate

    return {
        "total": total,
        "done": done,
        "percent": percent,
        "elapsed_human": _format_duration(elapsed_seconds),
        "eta_human": _format_duration(eta_seconds) if eta_seconds is not None else None,
        "is_terminal": is_terminal,
        "log_tail": _tail_job_log(job.id),
        "log_available": job_log_path(job.id).exists(),
    }


def _format_duration(seconds: Optional[float]) -> str:
    """Render a duration as ``Hh Mm`` / ``Mm Ss`` / ``Ss``, never zero-padded."""
    if seconds is None:
        return "—"
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    hours = s // 3600
    minutes = (s % 3600) // 60
    return f"{hours}h {minutes}m"


@router.get("/jobs/{job_id}/progress", response_class=HTMLResponse)
async def job_progress(
    request: Request,
    job_id: str,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """HTMX-polled fragment that renders the current state of an ingest job.

    While ``job.status == "running"`` the response keeps the polling trigger.
    Once the job reaches a terminal state, the response omits the trigger so
    htmx stops polling and the user sees the final counters."""
    job = db.execute(
        select(IngestionJob).where(IngestionJob.id == job_id)
    ).scalar_one_or_none()

    if job is None:
        return HTMLResponse(
            content='<div class="alert alert-warning">Job nicht gefunden.</div>',
            status_code=status.HTTP_404_NOT_FOUND,
        )

    return templates.TemplateResponse(
        "partials/ingest_progress.html",
        {
            "request": request,
            "job": job,
            "progress": _build_job_progress(job),
        },
    )


def _verify_job_log_path(job_id: str, db: Session) -> Path:
    """Look up the job and resolve its log file path, or raise 404.

    Centralised so both the view and the download endpoint share the same
    not-found / not-yet-logged handling and there is one place that decides
    "this job is allowed to expose its log file".
    """
    job = db.execute(
        select(IngestionJob).where(IngestionJob.id == job_id)
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job nicht gefunden.")

    path = job_log_path(job_id)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Für diesen Job wurden noch keine Logs geschrieben.",
        )
    return path


@router.get("/jobs/{job_id}/logs", response_class=Response)
async def job_logs_view(
    job_id: str,
    db: Session = Depends(get_db),
) -> Response:
    """Return the full per-job log file as ``text/plain`` for inline viewing.

    Browsers render this directly in a new tab. Capped at a generous max
    size so a runaway log cannot exhaust memory.
    """
    path = _verify_job_log_path(job_id, db)
    try:
        size = path.stat().st_size
        if size > _JOB_LOG_MAX_DOWNLOAD_BYTES:
            with path.open("rb") as f:
                f.seek(-_JOB_LOG_MAX_DOWNLOAD_BYTES, 2)
                data = f.read()
            notice = (
                f"... (log truncated, only the most recent "
                f"{_JOB_LOG_MAX_DOWNLOAD_BYTES // 1024} KiB shown — use the "
                f"download endpoint and copy from disk for the rest) ...\n"
            )
            data = notice.encode("utf-8") + data
        else:
            data = path.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    return Response(content=data, media_type="text/plain; charset=utf-8")


@router.get("/jobs/{job_id}/logs.txt", response_class=Response)
async def job_logs_download(
    job_id: str,
    db: Session = Depends(get_db),
) -> Response:
    """Same content as the view endpoint but offered as an attachment so the
    browser pops a Save-As dialog."""
    path = _verify_job_log_path(job_id, db)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    filename = f"reineke-rag-ingest-{job_id}.log"
    return Response(
        content=data,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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


# --- Installed Ollama models (for Konfiguration dropdowns) ----------------

# Substring heuristic — model names that match are treated as embedders.
_EMBEDDING_HINTS = ("embed", "bge-m3", "bge-large", "nomic-embed", "mxbai")


def _is_embedding_model(name: str) -> bool:
    n = name.lower()
    return any(hint in n for hint in _EMBEDDING_HINTS)


@router.get("/ollama/models")
async def ollama_models(role: str = Query("all")) -> JSONResponse:
    """List the locally installed Ollama models, optionally filtered by role.

    role=embedding → only embedders (bge-m3, nomic-embed-text, …)
    role=chat      → only chat models (everything else)
    role=all       → all of them
    """
    try:
        names = await OllamaClient().list_models()
    except Exception as exc:
        log.warning("ollama list_models failed: %s", exc)
        names = []

    if role == "embedding":
        filtered = [n for n in names if _is_embedding_model(n)]
    elif role == "chat":
        filtered = [n for n in names if not _is_embedding_model(n)]
    else:
        filtered = list(names)

    filtered.sort(key=str.lower)
    return JSONResponse({"options": filtered})


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


# --- Persona prompts (per agent / collection) -------------------------------

@router.post("/agents/{tenant}/{project}/prompt", response_class=HTMLResponse)
async def agent_prompt_set(
    tenant: str, project: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    form = await request.form()
    raw = (form.get("persona_prompt") or "").strip()
    if len(raw) > PERSONA_PROMPT_MAX_CHARS:
        return HTMLResponse(
            f'<div class="alert alert-danger m-0">'
            f'Persona-Prompt zu lang ({len(raw)} Zeichen, max '
            f'{PERSONA_PROMPT_MAX_CHARS}).</div>',
            status_code=200,
        )

    row = db.get(TenantProjectPrompt, (tenant, project))
    if raw:
        if row is None:
            db.add(TenantProjectPrompt(tenant=tenant, project=project, persona_prompt=raw))
        else:
            row.persona_prompt = raw
    elif row is not None:
        # Empty submit clears the override.
        db.delete(row)
    db.commit()

    return HTMLResponse(
        f'<div class="alert alert-success py-1 m-0 small">'
        f'Persona für <code>{tenant} / {project}</code> '
        f'{"gespeichert" if raw else "geleert"}.</div>'
    )


@router.get("/agents/{tenant}/{project}/prompt-preview", response_class=HTMLResponse)
async def agent_prompt_preview(
    tenant: str, project: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Render the *composed* system prompt (global + persona) that will be
    sent to Ollama for this agent. Read-only, used by the Vorschau button."""
    row = db.get(TenantProjectPrompt, (tenant, project))
    persona = (row.persona_prompt or "").strip() if row else ""
    live_global = get_system_prompt()
    composed = ChatService._compose_system_prompt(
        tenant, project, persona, global_prompt=live_global
    )
    return templates.TemplateResponse(
        "partials/agent_prompt_preview.html",
        {
            "request": request,
            "tenant": tenant,
            "project": project,
            "composed": composed,
            "has_persona": bool(persona),
            "global_prompt": live_global,
        },
    )


# --- Global system prompt editor (Konfiguration page) ----------------------

def _render_system_prompt(
    request: Request,
    *,
    saved: bool = False,
    error: Optional[str] = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        "partials/system_prompt.html",
        {
            "request": request,
            "global_prompt": get_system_prompt(),
            "default_prompt": DEFAULT_SYSTEM_PROMPT,
            "is_override": system_prompt_has_override(),
            "max_chars": SYSTEM_PROMPT_MAX_CHARS,
            "saved": saved,
            "error": error,
        },
    )


@router.get("/system-prompt", response_class=HTMLResponse)
async def system_prompt_panel(request: Request) -> HTMLResponse:
    return _render_system_prompt(request)


@router.post("/system-prompt", response_class=HTMLResponse)
async def system_prompt_save(request: Request) -> HTMLResponse:
    form = await request.form()
    raw = (form.get("system_prompt") or "").strip()
    try:
        set_system_prompt(raw)
    except ValueError as exc:
        return _render_system_prompt(request, error=str(exc))
    return _render_system_prompt(request, saved=True)


@router.post("/system-prompt/reset", response_class=HTMLResponse)
async def system_prompt_reset(request: Request) -> HTMLResponse:
    clear_system_prompt()
    return _render_system_prompt(request, saved=True)


@router.delete("/agents/{tenant}/{project}/prompt", response_class=HTMLResponse)
async def agent_prompt_clear(
    tenant: str, project: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    row = db.get(TenantProjectPrompt, (tenant, project))
    if row is not None:
        db.delete(row)
        db.commit()
    return HTMLResponse(
        f'<div class="alert alert-success py-1 m-0 small">'
        f'Persona für <code>{tenant} / {project}</code> entfernt.</div>'
    )


@router.post("/agents/{tenant}/{project}/chat-model", response_class=HTMLResponse)
async def agent_chat_model_set(
    tenant: str, project: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Set or clear the per-agent chat model. Empty value = use global default."""
    form = await request.form()
    raw = (form.get("chat_model") or "").strip()

    row = db.get(TenantProjectPrompt, (tenant, project))
    if row is None:
        # Agent-Konfig braucht zumindest eine Zeile, damit wir den Wert speichern können.
        row = TenantProjectPrompt(
            tenant=tenant, project=project, persona_prompt="",
            chat_model=raw or None,
        )
        db.add(row)
    else:
        row.chat_model = raw or None
    db.commit()

    if raw:
        msg = (
            f'<div class="alert alert-success py-1 m-0 small">'
            f'Chat-Modell für <code>{tenant} / {project}</code> auf '
            f'<code>{raw}</code> gesetzt.</div>'
        )
    else:
        msg = (
            f'<div class="alert alert-success py-1 m-0 small">'
            f'<code>{tenant} / {project}</code> verwendet jetzt den globalen '
            f'Default <code>{settings.CHAT_MODEL}</code>.</div>'
        )
    return HTMLResponse(msg)


@router.post("/agents/{tenant}/{project}/rerank", response_class=HTMLResponse)
async def agent_rerank_set(
    tenant: str, project: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Set or clear the per-collection reranker controls.

    Form fields:
      rerank_enabled       — "auto" | "on" | "off"
                             "auto" stores NULL (use the smart default).
      rerank_overfetch_k   — positive int or empty
                             empty stores NULL (use the smart default).
    """
    form = await request.form()
    raw_enabled = (form.get("rerank_enabled") or "auto").strip().lower()
    raw_k = (form.get("rerank_overfetch_k") or "").strip()

    if raw_enabled not in {"auto", "on", "off"}:
        return HTMLResponse(
            '<div class="alert alert-danger py-1 m-0 small">'
            'Ungültige Auswahl für Reranking.</div>'
        )

    overfetch_k: Optional[int] = None
    if raw_k:
        try:
            overfetch_k = int(raw_k)
        except ValueError:
            return HTMLResponse(
                '<div class="alert alert-danger py-1 m-0 small">'
                'Overfetch-K muss eine ganze Zahl sein.</div>'
            )
        if not 1 <= overfetch_k <= 500:
            return HTMLResponse(
                '<div class="alert alert-danger py-1 m-0 small">'
                'Overfetch-K muss zwischen 1 und 500 liegen.</div>'
            )

    row = db.get(TenantProjectPrompt, (tenant, project))
    if row is None:
        row = TenantProjectPrompt(tenant=tenant, project=project, persona_prompt="")
        db.add(row)

    if raw_enabled == "auto":
        row.rerank_enabled = None
    elif raw_enabled == "on":
        row.rerank_enabled = True
    else:
        row.rerank_enabled = False
    row.rerank_overfetch_k = overfetch_k
    db.commit()

    effective = rerank_settings_module.resolve(db, tenant=tenant, project=project)
    state = "aktiv" if effective.enabled else "inaktiv"
    return HTMLResponse(
        f'<div class="alert alert-success py-1 m-0 small">'
        f'Reranking: <strong>{state}</strong>, K={effective.overfetch_k} '
        f'(Status: {effective.enabled_source}, K-Quelle: '
        f'{effective.overfetch_k_source}).</div>'
    )
