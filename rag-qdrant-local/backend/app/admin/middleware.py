"""ASGI middleware that records every HTTP request into ``request_logs``.

Behaves like a no-op on failure — the audit log must never break the app.
"""

from __future__ import annotations

import json
import time
from typing import Any, Awaitable, Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..database import session_scope
from ..models import RequestLog
from ..utils import get_logger

log = get_logger("rag.admin.middleware")


# Paths we deliberately skip — would otherwise spam the audit table with
# noise and create infinite recursion when the admin UI tails its own log.
_SKIP_PREFIXES = (
    "/admin/static/",
    "/admin/api/request-logs/stream",
    "/admin/api/app-log/stream",
    "/favicon.ico",
)


def _should_skip(path: str) -> bool:
    return any(path.startswith(p) for p in _SKIP_PREFIXES)


def _peek_tenant_project(body_bytes: bytes) -> tuple[Optional[str], Optional[str]]:
    """Best-effort extraction of tenant/project from a JSON body — never raises."""
    if not body_bytes:
        return None, None
    try:
        payload = json.loads(body_bytes.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    tenant = payload.get("tenant")
    project = payload.get("project")
    extra = payload.get("extra_body")
    if isinstance(extra, dict):
        tenant = tenant or extra.get("tenant")
        project = project or extra.get("project")
    model = payload.get("model")
    if isinstance(model, str) and model.startswith("rag:") and model.count(":") >= 2:
        _, t, p = model.split(":", 2)
        tenant = tenant or t
        project = project or p
    return (
        str(tenant) if isinstance(tenant, (str, int)) else None,
        str(project) if isinstance(project, (str, int)) else None,
    )


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if _should_skip(path):
            return await call_next(request)

        # Peek at the request body for tenant/project, then re-inject so the
        # downstream route can still read it.
        body_bytes = b""
        if request.method in ("POST", "PUT", "PATCH"):
            body_bytes = await request.body()

            async def receive() -> dict[str, Any]:
                return {"type": "http.request", "body": body_bytes, "more_body": False}

            request._receive = receive  # type: ignore[attr-defined]

        tenant, project = _peek_tenant_project(body_bytes)
        if not tenant:
            tenant = request.query_params.get("tenant") or None
        if not project:
            project = request.query_params.get("project") or None

        start = time.perf_counter()
        error: Optional[str] = None
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:  # pragma: no cover — re-raised, just logged
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            try:
                with session_scope() as db:
                    db.add(
                        RequestLog(
                            method=request.method,
                            path=path,
                            query_string=str(request.url.query) or None,
                            status_code=status_code,
                            duration_ms=duration_ms,
                            tenant=tenant,
                            project=project,
                            client_ip=(request.client.host if request.client else None),
                            error_message=error,
                        )
                    )
            except Exception:
                log.exception("Failed to persist request log entry — skipping.")
