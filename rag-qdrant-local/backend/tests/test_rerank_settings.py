"""Three-layer rerank-settings resolver.

The resolver decides for one ``(tenant, project)`` whether reranking is on,
how many candidates to overfetch, and which model to load. Tests cover:

* the smart-default helpers in isolation (pure functions)
* the killswitch path (global ``RERANK_ENABLED=false`` wins over everything)
* per-collection overrides shadowing the smart default
* the smart default kicking in below / at / above the doc-count threshold
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import rerank_settings
from app.config import settings
from app.models import Base, Document, TenantProjectPrompt
from app.rerank_settings import (
    EffectiveRerankSettings,
    resolve,
    smart_default_overfetch_k,
    smart_default_rerank_enabled,
)


# ---------------------------------------------------------------------------
# In-memory SQLite for these tests so we don't touch the project's real DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with SessionLocal() as s:
        yield s


def _add_docs(db: Session, tenant: str, project: str, n: int, status: str = "indexed") -> None:
    # Namespace ids and source_paths by status so a single test can layer
    # several statuses into the same (tenant, project) without colliding
    # on the unique-index.
    for i in range(n):
        db.add(Document(
            id=f"{tenant}-{project}-{status}-{i}",
            tenant=tenant, project=project,
            source_path=f"/x/{tenant}/{project}/{status}/{i}.pdf",
            file_name=f"{i}.pdf", file_extension=".pdf",
            file_size=100, checksum=f"c{i}",
            status=status,
        ))
    db.commit()


# ---------------------------------------------------------------------------
# Smart-default helpers (pure)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "doc_count, expected_k",
    [(0, 15), (1, 15), (99, 15), (100, 30), (500, 30), (999, 30),
     (1000, 50), (4999, 50), (5000, 100), (50000, 100)],
)
def test_smart_default_overfetch_k_buckets(doc_count: int, expected_k: int) -> None:
    assert smart_default_overfetch_k(doc_count) == expected_k


def test_smart_default_rerank_enabled_threshold(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RERANK_AUTO_ENABLE_MIN_DOCS", 100, raising=False)
    assert smart_default_rerank_enabled(0) is False
    assert smart_default_rerank_enabled(99) is False
    assert smart_default_rerank_enabled(100) is True
    assert smart_default_rerank_enabled(1000) is True


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

def test_global_killswitch_forces_disabled(db_session: Session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "RERANK_ENABLED", False, raising=False)
    _add_docs(db_session, "t", "p", 500)
    db_session.add(TenantProjectPrompt(
        tenant="t", project="p", persona_prompt="",
        rerank_enabled=True,   # per-collection wants reranking
        rerank_overfetch_k=80,
    ))
    db_session.commit()

    eff = resolve(db_session, tenant="t", project="p")
    assert eff.enabled is False
    assert eff.enabled_source == "global-killswitch"
    # Other fields still resolve normally — only the on/off was vetoed.
    assert eff.overfetch_k == 80
    assert eff.overfetch_k_source == "override"


def test_smart_default_below_threshold_means_off(db_session: Session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "RERANK_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "RERANK_AUTO_ENABLE_MIN_DOCS", 100, raising=False)
    _add_docs(db_session, "t", "p", 50)  # below threshold

    eff = resolve(db_session, tenant="t", project="p")
    assert eff.enabled is False
    assert eff.enabled_source == "smart-default"
    assert eff.doc_count == 50


def test_smart_default_at_threshold_means_on(db_session: Session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "RERANK_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "RERANK_AUTO_ENABLE_MIN_DOCS", 100, raising=False)
    _add_docs(db_session, "t", "p", 100)

    eff = resolve(db_session, tenant="t", project="p")
    assert eff.enabled is True
    assert eff.enabled_source == "smart-default"
    assert eff.overfetch_k == 30  # bucket for 100-999


def test_per_collection_override_wins_over_smart_default(
    db_session: Session, monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "RERANK_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "RERANK_AUTO_ENABLE_MIN_DOCS", 100, raising=False)
    _add_docs(db_session, "t", "p", 5)  # smart default would be off
    db_session.add(TenantProjectPrompt(
        tenant="t", project="p", persona_prompt="",
        rerank_enabled=True, rerank_overfetch_k=42,
    ))
    db_session.commit()

    eff = resolve(db_session, tenant="t", project="p")
    assert eff.enabled is True
    assert eff.enabled_source == "override"
    assert eff.overfetch_k == 42
    assert eff.overfetch_k_source == "override"


def test_only_indexed_docs_count_for_smart_default(db_session: Session, monkeypatch) -> None:
    """Failed / deleted / pending docs don't make a collection "big" — they
    don't contribute searchable chunks, so they shouldn't influence the
    smart default either."""
    monkeypatch.setattr(settings, "RERANK_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "RERANK_AUTO_ENABLE_MIN_DOCS", 100, raising=False)
    _add_docs(db_session, "t", "p", 50, status="indexed")
    _add_docs(db_session, "t", "p2", 60, status="failed")   # different project, doesn't count anyway
    # 80 failed + 80 pending in the same collection → still below threshold
    _add_docs(db_session, "t", "p", 80, status="failed")
    _add_docs(db_session, "t", "p", 80, status="pending")

    eff = resolve(db_session, tenant="t", project="p")
    assert eff.doc_count == 50
    assert eff.enabled is False


def test_model_falls_through_to_global_when_no_override(
    db_session: Session, monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "RERANK_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "RERANK_MODEL", "global/model", raising=False)
    _add_docs(db_session, "t", "p", 5)

    eff = resolve(db_session, tenant="t", project="p")
    assert eff.model == "global/model"
    assert eff.model_source == "global"


def test_model_override_wins(db_session: Session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "RERANK_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "RERANK_MODEL", "global/model", raising=False)
    _add_docs(db_session, "t", "p", 5)
    db_session.add(TenantProjectPrompt(
        tenant="t", project="p", persona_prompt="",
        rerank_model="custom/model",
    ))
    db_session.commit()

    eff = resolve(db_session, tenant="t", project="p")
    assert eff.model == "custom/model"
    assert eff.model_source == "override"
