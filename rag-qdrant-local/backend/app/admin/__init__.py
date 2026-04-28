"""Admin / operations console (HTMX + Jinja2, German UI)."""

from .routes import router as html_router  # noqa: F401
from .api import router as api_router  # noqa: F401
from .middleware import RequestLogMiddleware  # noqa: F401
