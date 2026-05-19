"""Fixtures for the retrieval-quality eval suite.

The eval runs against a *live* backend (with a populated Qdrant collection
and Ollama models loaded), so it deliberately lives in its own directory
with its own fixtures and its own ``eval`` pytest marker. The default
fixture target points at a local ``uvicorn`` on port 8000 — override via
``RAG_EVAL_*`` environment variables for other setups.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def eval_backend_url() -> str:
    """Base URL of the running Reineke-RAG backend."""
    return os.environ.get("RAG_EVAL_BACKEND_URL", "http://localhost:8000").rstrip("/")


@pytest.fixture(scope="session")
def eval_tenant() -> str:
    return os.environ.get("RAG_EVAL_TENANT", "reineke")


@pytest.fixture(scope="session")
def eval_project() -> str:
    return os.environ.get("RAG_EVAL_PROJECT", "watch")


@pytest.fixture(scope="session")
def eval_questions_path() -> Path:
    """YAML file with the evaluation questions."""
    return Path(__file__).parent / "questions.yaml"


@pytest.fixture(scope="session")
def eval_results_dir() -> Path:
    """Directory where per-run JSON scorecards are written.

    Created on demand. Ignored by git (see ``.gitignore``) so the working
    tree stays clean across many local runs.
    """
    d = Path(__file__).parent / "results"
    d.mkdir(parents=True, exist_ok=True)
    return d
