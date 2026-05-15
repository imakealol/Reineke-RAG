"""Progress-bar arithmetic for the ingest wizard.

Covers the pure helpers behind the live status panel — percent, elapsed
time, ETA, and terminal-state detection — without dragging in Qdrant,
Ollama, or the full FastAPI stack. Keeps the test fast and deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.admin.api import _build_job_progress, _format_duration


def _job(**overrides) -> SimpleNamespace:
    """Build a fake IngestionJob row for the progress helpers.

    ``_build_job_progress`` only reads attributes — it does not invoke any
    ORM behaviour — so a ``SimpleNamespace`` is enough and avoids needing a
    populated SQLite session for these unit tests.
    """
    base = dict(
        id="job-1",
        status="running",
        files_found=0,
        files_indexed=0,
        files_skipped=0,
        files_failed=0,
        chunks_created=0,
        current_file=None,
        error_message=None,
        created_at=datetime.now(timezone.utc),
        completed_at=None,
        tenant="t",
        project="p",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# _build_job_progress: percent + terminal detection
# ---------------------------------------------------------------------------

def test_progress_zero_files_found_renders_zero_percent():
    p = _build_job_progress(_job(files_found=0))
    assert p["percent"] == 0
    assert p["done"] == 0
    assert p["total"] == 0
    assert p["is_terminal"] is False


def test_progress_halfway_through_renders_50_percent():
    p = _build_job_progress(_job(
        files_found=10,
        files_indexed=3,
        files_skipped=1,
        files_failed=1,
    ))
    assert p["total"] == 10
    assert p["done"] == 5
    assert p["percent"] == 50


def test_progress_completed_status_is_terminal_at_100():
    p = _build_job_progress(_job(
        status="completed",
        files_found=4,
        files_indexed=4,
        completed_at=datetime.now(timezone.utc),
    ))
    assert p["is_terminal"] is True
    assert p["percent"] == 100


def test_progress_failed_status_is_terminal():
    p = _build_job_progress(_job(
        status="failed",
        files_found=10,
        files_indexed=2,
        error_message="Ollama unreachable",
    ))
    assert p["is_terminal"] is True
    # Even a failed run reports its partial progress.
    assert p["percent"] == 20


def test_progress_completed_with_errors_is_terminal():
    """``completed_with_errors`` is the status the service writes when some
    files failed but the run as a whole finished — must count as terminal so
    polling stops."""
    p = _build_job_progress(_job(
        status="completed_with_errors",
        files_found=5,
        files_indexed=4,
        files_failed=1,
    ))
    assert p["is_terminal"] is True


def test_progress_percent_caps_at_100_even_when_done_exceeds_total():
    """Defensive: if the loop ever double-counts (rare race), the bar must
    not overflow visually past 100 %."""
    p = _build_job_progress(_job(
        files_found=4,
        files_indexed=5,  # impossible in reality, but be safe
    ))
    assert p["percent"] == 100


# ---------------------------------------------------------------------------
# _build_job_progress: elapsed time + ETA
# ---------------------------------------------------------------------------

def test_progress_eta_computed_from_observed_rate():
    """With half the files done after 30 seconds, the remaining half should
    take ~30 seconds → ETA somewhere around 25-35 seconds."""
    started = datetime.now(timezone.utc) - timedelta(seconds=30)
    p = _build_job_progress(_job(
        files_found=10,
        files_indexed=5,
        created_at=started,
    ))
    assert p["eta_human"] is not None
    # Elapsed time string should reflect the ~30 s offset
    assert p["elapsed_human"].endswith("s")


def test_progress_eta_none_when_nothing_done_yet():
    """Right after kicking off, with done == 0, we cannot estimate ETA."""
    started = datetime.now(timezone.utc) - timedelta(seconds=3)
    p = _build_job_progress(_job(
        files_found=10,
        files_indexed=0,
        created_at=started,
    ))
    assert p["eta_human"] is None


def test_progress_eta_none_on_terminal_job():
    """Once the run is over, an ETA would be misleading — must be None."""
    started = datetime.now(timezone.utc) - timedelta(seconds=10)
    p = _build_job_progress(_job(
        status="completed",
        files_found=4,
        files_indexed=4,
        created_at=started,
        completed_at=datetime.now(timezone.utc),
    ))
    assert p["eta_human"] is None


def test_progress_handles_naive_created_at():
    """SQLite occasionally hands back naive datetimes — must not crash."""
    started = (datetime.now(timezone.utc) - timedelta(seconds=15)).replace(tzinfo=None)
    p = _build_job_progress(_job(
        files_found=4,
        files_indexed=2,
        created_at=started,
    ))
    assert p["elapsed_human"] != "—"


# ---------------------------------------------------------------------------
# _format_duration
# ---------------------------------------------------------------------------

def test_format_duration_seconds():
    assert _format_duration(0) == "0s"
    assert _format_duration(1) == "1s"
    assert _format_duration(59) == "59s"


def test_format_duration_minutes():
    assert _format_duration(60) == "1m 0s"
    assert _format_duration(95) == "1m 35s"
    assert _format_duration(3599) == "59m 59s"


def test_format_duration_hours():
    assert _format_duration(3600) == "1h 0m"
    assert _format_duration(3660) == "1h 1m"
    assert _format_duration(7322) == "2h 2m"


def test_format_duration_none():
    assert _format_duration(None) == "—"


def test_format_duration_rounds_subsecond():
    assert _format_duration(0.4) == "0s"
    assert _format_duration(0.6) == "1s"
