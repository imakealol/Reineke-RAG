"""Pytest fixtures.

We make the project ``backend/`` importable as ``app.*`` and route the
config to a temporary SQLite + sandboxed allowed base path so the test
suite never touches real data.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Ensure `import app` works when running pytest from the project root.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# A predictable sandbox base path used by the path-security tests.
_SANDBOX_ROOT = Path(tempfile.mkdtemp(prefix="rag-test-"))
os.environ.setdefault("ALLOWED_BASE_PATHS", str(_SANDBOX_ROOT))
os.environ.setdefault("SQLITE_DB_PATH", str(_SANDBOX_ROOT / "rag.sqlite"))

import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402

# Force settings reload after env mutations
get_settings.cache_clear()  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def sandbox_root() -> Path:
    return _SANDBOX_ROOT
