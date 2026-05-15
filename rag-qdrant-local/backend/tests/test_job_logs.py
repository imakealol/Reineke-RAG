"""Per-job log capture: ContextVar isolation, tail helper, view/download.

These tests cover the logging plumbing without going through the actual
ingest pipeline — fast and deterministic.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from app.admin.api import _tail_job_log
from app.utils import capture_logs_for_job, current_job_id, get_logger, job_log_path


@pytest.fixture(autouse=True)
def _enable_info_logging():
    """``configure_logging`` is the production entry point that lowers the
    root level to INFO. The test suite doesn't invoke FastAPI startup, so
    without this fixture the root level stays at WARNING and INFO records
    from ingest_path would never reach our capture handler."""
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.DEBUG)
    yield
    root.setLevel(previous)


# ---------------------------------------------------------------------------
# capture_logs_for_job: writes the log file under storage/job-logs/<id>.log
# and tears down on exit
# ---------------------------------------------------------------------------

def test_capture_writes_log_records_to_job_file():
    job_id = "job-write-1"
    target = job_log_path(job_id)
    if target.exists():
        target.unlink()

    log = get_logger("rag.test_job_logs")
    with capture_logs_for_job(job_id) as path:
        log.info("hello from %s", job_id)

    assert path == target
    assert target.exists()
    body = target.read_text(encoding="utf-8")
    assert "hello from job-write-1" in body


def test_capture_tears_down_handler_on_exit():
    job_id = "job-cleanup-1"
    root = logging.getLogger()
    handlers_before = list(root.handlers)

    with capture_logs_for_job(job_id):
        # During the block, exactly one extra handler attached.
        assert len(root.handlers) == len(handlers_before) + 1

    # After exit, handler is gone — no leaked file descriptors.
    assert root.handlers == handlers_before


def test_capture_resets_contextvar_even_on_exception():
    """If the body raises, the context var must still be reset; otherwise
    the next ingest would log to the previous job's file."""
    assert current_job_id.get() is None

    with pytest.raises(RuntimeError):
        with capture_logs_for_job("job-exc-1"):
            assert current_job_id.get() == "job-exc-1"
            raise RuntimeError("boom")

    assert current_job_id.get() is None


# ---------------------------------------------------------------------------
# Isolation across concurrent captures (two tasks must not cross-contaminate)
# ---------------------------------------------------------------------------

async def _async_log_in_capture(job_id: str, message: str) -> Path:
    log = get_logger("rag.test_job_logs")
    with capture_logs_for_job(job_id) as path:
        log.info(message)
        # Yield control to the event loop so the other task can interleave —
        # this is the realistic scenario: two ingests running concurrently
        # under asyncio.create_task.
        await asyncio.sleep(0)
        log.info("%s second", message)
    return path


def test_two_concurrent_captures_do_not_cross_contaminate():
    """ContextVar is per-task, so the two captured files must each contain
    only their own task's messages — never the other one's."""
    a = job_log_path("job-iso-A")
    b = job_log_path("job-iso-B")
    for p in (a, b):
        if p.exists():
            p.unlink()

    async def runner():
        return await asyncio.gather(
            _async_log_in_capture("job-iso-A", "alpha"),
            _async_log_in_capture("job-iso-B", "bravo"),
        )

    asyncio.run(runner())

    body_a = a.read_text(encoding="utf-8")
    body_b = b.read_text(encoding="utf-8")

    assert "alpha" in body_a and "alpha second" in body_a
    assert "bravo" not in body_a

    assert "bravo" in body_b and "bravo second" in body_b
    assert "alpha" not in body_b


# ---------------------------------------------------------------------------
# _tail_job_log helper
# ---------------------------------------------------------------------------

def test_tail_returns_empty_string_when_log_does_not_exist():
    assert _tail_job_log("job-nonexistent-xyz") == ""


def test_tail_returns_last_n_lines():
    job_id = "job-tail-1"
    target = job_log_path(job_id)
    target.write_text(
        "\n".join(f"line-{i}" for i in range(1, 31)),
        encoding="utf-8",
    )
    tail = _tail_job_log(job_id, lines=5)
    assert tail.splitlines() == [f"line-{i}" for i in range(26, 31)]


def test_tail_handles_log_shorter_than_requested():
    job_id = "job-tail-2"
    target = job_log_path(job_id)
    target.write_text("only-one-line", encoding="utf-8")
    assert _tail_job_log(job_id, lines=10).strip() == "only-one-line"
