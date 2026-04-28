"""Server-rendered HTML pages for the admin console (Jinja2 + HTMX).

Each route returns a full ``base.html`` page on direct navigation. Partial
swaps fired by HTMX hit the ``/admin/api/...`` endpoints in ``api.py``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates


_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(prefix="/admin", include_in_schema=False)


def _ctx(request: Request, active: str, **extra) -> dict:
    return {"request": request, "active": active, **extra}


@router.get("/", response_class=HTMLResponse)
async def page_overview(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("overview.html", _ctx(request, "overview"))


@router.get("/agenten", response_class=HTMLResponse)
async def page_agents(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("agents.html", _ctx(request, "tenants"))


@router.get("/mandanten", include_in_schema=False)
async def page_tenants_legacy() -> RedirectResponse:
    """Backwards-compat redirect — Mandanten was renamed to Agenten."""
    return RedirectResponse("/admin/agenten", status_code=308)


@router.get("/dokumente", response_class=HTMLResponse)
async def page_documents(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("documents.html", _ctx(request, "documents"))


@router.get("/ingest", response_class=HTMLResponse)
async def page_ingest(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("ingest.html", _ctx(request, "ingest"))


@router.get("/jobs", response_class=HTMLResponse)
async def page_jobs(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("jobs.html", _ctx(request, "jobs"))


@router.get("/zeitplan", response_class=HTMLResponse)
async def page_schedules(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("schedules.html", _ctx(request, "schedules"))


@router.get("/logs", response_class=HTMLResponse)
async def page_logs(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("logs.html", _ctx(request, "logs"))


@router.get("/konfiguration", response_class=HTMLResponse)
async def page_config(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("config.html", _ctx(request, "config"))
