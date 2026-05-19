"""Retrieval-quality scorecard for Reineke-RAG.

Runs ``questions.yaml`` against a *live* backend and reports four metrics
plus per-category breakdowns and a diff against the previous run:

    Recall@5         — expected doc among the top-5 sources?
    MRR              — mean reciprocal rank of the first correct doc
    Faithfulness     — answer contains at least one expected keyword
    Refusal-Accuracy — refusal questions actually refused

The eval is **not** a pass/fail gate; it is a report. The test assertion
only checks that the backend was reachable and that every question
produced a result — quality interpretation is up to the reader.

Run locally with the project's venv activated and the backend up::

    pytest -m eval -v -s tests/eval/test_eval_retrieval.py

Override the target with environment variables when needed::

    RAG_EVAL_BACKEND_URL=http://10.1.1.81:8000 \\
    RAG_EVAL_TENANT=ruberg RAG_EVAL_PROJECT=versuchsprotokolle \\
    pytest -m eval -v -s tests/eval/test_eval_retrieval.py
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
import pytest
import yaml


# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

REFUSAL_SUBSTRING = "nicht eindeutig"          # canonical refusal phrase
RECALL_TOP_K = 5
REQUEST_TIMEOUT_SECONDS = 120                  # cold-loaded Ollama can take a while


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _icontains(haystack: str, needle: str) -> bool:
    return needle.lower() in (haystack or "").lower()


def _any_icontains(haystack: str, needles: Iterable[str]) -> bool:
    return any(_icontains(haystack, n) for n in needles)


def _expected_doc_strings(q: Dict[str, Any]) -> List[str]:
    """Return the list of acceptable substrings for the source file_name."""
    if "expected_doc" in q and q["expected_doc"]:
        return [q["expected_doc"]]
    if "expected_docs_any" in q and q["expected_docs_any"]:
        return list(q["expected_docs_any"])
    return []


def _rank_of_first_match(sources: List[Dict[str, Any]], needles: List[str]) -> Optional[int]:
    """1-indexed rank of the first source whose ``file_name`` matches any
    substring in ``needles``. ``None`` if no source matches."""
    for idx, src in enumerate(sources, start=1):
        name = src.get("file_name") or ""
        if _any_icontains(name, needles):
            return idx
    return None


def _query_chat(
    client: httpx.Client,
    backend_url: str,
    tenant: str,
    project: str,
    question: str,
) -> Tuple[str, List[Dict[str, Any]], float]:
    """POST to ``/chat`` and return ``(answer, sources, latency_seconds)``."""
    started = time.monotonic()
    resp = client.post(
        f"{backend_url}/chat",
        json={
            "tenant": tenant,
            "project": project,
            "question": question,
            "session_id": None,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    elapsed = time.monotonic() - started
    resp.raise_for_status()
    data = resp.json()
    return (
        data.get("answer", "") or "",
        data.get("sources") or [],
        elapsed,
    )


def _score_question(
    q: Dict[str, Any],
    answer: str,
    sources: List[Dict[str, Any]],
    latency_s: float,
) -> Dict[str, Any]:
    """Compute per-question scores against the answer + retrieved sources."""
    expected_refusal = bool(q.get("expected_refusal"))
    needles = _expected_doc_strings(q)
    rank = _rank_of_first_match(sources, needles) if needles else None
    recall_at_k = (rank is not None and rank <= RECALL_TOP_K)
    rr = 1.0 / rank if rank else 0.0
    is_refusal = REFUSAL_SUBSTRING in (answer or "").lower()

    expected_keywords = q.get("expected_keywords") or []
    keyword_hit = (
        _any_icontains(answer, expected_keywords) if expected_keywords else None
    )

    if expected_refusal:
        # For refusal questions, "correct" = system refused.
        # Faithfulness is not meaningful here — skipped.
        outcome = is_refusal
    else:
        # For answer questions, "correct" needs both: right doc AND right text.
        outcome = bool(recall_at_k and (keyword_hit if expected_keywords else True))

    return {
        "id": q["id"],
        "category": q.get("category", "uncategorized"),
        "difficulty": q.get("difficulty", "unspecified"),
        "question": q["question"],
        "expected_refusal": expected_refusal,
        "needles": needles,
        "answer": answer,
        "sources": [
            {"file_name": s.get("file_name"), "score": s.get("score")}
            for s in sources[:RECALL_TOP_K]
        ],
        "rank": rank,
        "recall_at_k": recall_at_k,
        "reciprocal_rank": rr,
        "is_refusal": is_refusal,
        "keyword_hit": keyword_hit,
        "outcome": outcome,
        "latency_seconds": round(latency_s, 3),
    }


def _live_line(scored: Dict[str, Any]) -> str:
    """One-line summary for the live-progress output during a run.

    Designed to be appended to a previously-printed "querying... " prefix
    so the reader sees exactly which question finished, in what time, and
    why it failed if it did. Kept short — one line per question.
    """
    latency = f"{scored['latency_seconds']:>5.1f}s"
    if scored["expected_refusal"]:
        if scored["is_refusal"]:
            return f"✓ refused as expected  · {latency}"
        return f"✗ refusal expected but got an answer  · {latency}"
    rank = scored["rank"]
    if rank is None:
        return f"✗ no source matched  · {latency}"
    keyword = scored["keyword_hit"]
    kw_part = ""
    if keyword is False:
        kw_part = "  (answer missing keyword)"
    icon = "✓" if scored["outcome"] else "✗"
    return f"{icon} rank={rank}{kw_part}  · {latency}"


def _aggregate(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the headline + per-category numbers from per-question scores."""
    answer_qs = [r for r in results if not r["expected_refusal"]]
    refusal_qs = [r for r in results if r["expected_refusal"]]
    # Faithfulness only meaningful when expected_keywords were set.
    faith_qs = [r for r in answer_qs if r["keyword_hit"] is not None]

    def _pct(num: int, denom: int) -> float:
        return (num / denom * 100.0) if denom else 0.0

    recall_hits = sum(1 for r in answer_qs if r["recall_at_k"])
    mrr = (
        statistics.mean([r["reciprocal_rank"] for r in answer_qs])
        if answer_qs else 0.0
    )
    faith_hits = sum(1 for r in faith_qs if r["keyword_hit"])
    refusal_hits = sum(1 for r in refusal_qs if r["is_refusal"])

    latencies = [r["latency_seconds"] for r in results]
    p50 = statistics.median(latencies) if latencies else 0.0
    p95 = (
        statistics.quantiles(latencies, n=20, method="inclusive")[18]
        if len(latencies) >= 2 else (latencies[0] if latencies else 0.0)
    )

    by_category: Dict[str, Dict[str, Any]] = {}
    for r in results:
        c = r["category"]
        slot = by_category.setdefault(
            c, {"n": 0, "recall_hits": 0, "rr_sum": 0.0, "answer_qs": 0}
        )
        slot["n"] += 1
        if not r["expected_refusal"]:
            slot["answer_qs"] += 1
            if r["recall_at_k"]:
                slot["recall_hits"] += 1
            slot["rr_sum"] += r["reciprocal_rank"]

    for c, slot in by_category.items():
        slot["recall_at_k_pct"] = _pct(slot["recall_hits"], slot["answer_qs"] or 1)
        slot["mrr"] = (slot["rr_sum"] / slot["answer_qs"]) if slot["answer_qs"] else 0.0

    return {
        "n_total": len(results),
        "n_answer": len(answer_qs),
        "n_refusal": len(refusal_qs),
        "n_with_keywords": len(faith_qs),
        "recall_at_k": _pct(recall_hits, len(answer_qs)),
        "recall_hits": recall_hits,
        "mrr": mrr,
        "faithfulness": _pct(faith_hits, len(faith_qs)),
        "faithfulness_hits": faith_hits,
        "refusal_accuracy": _pct(refusal_hits, len(refusal_qs)),
        "refusal_hits": refusal_hits,
        "latency_p50": p50,
        "latency_p95": p95,
        "by_category": by_category,
    }


def _find_latest_baseline(results_dir: Path) -> Optional[Dict[str, Any]]:
    """Return the most recent JSON scorecard older than the current run, or
    ``None`` when the directory is empty."""
    json_files = sorted(results_dir.glob("eval-*.json"))
    if not json_files:
        return None
    try:
        return json.loads(json_files[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _format_delta(current: float, baseline: Optional[float], *, suffix: str = "%") -> str:
    if baseline is None:
        return ""
    delta = current - baseline
    sign = "+" if delta >= 0 else ""
    return f"   ({sign}{delta:.1f}{suffix})"


def _print_scorecard(scorecard: Dict[str, Any], baseline: Optional[Dict[str, Any]]) -> None:
    """Pretty-print the headline numbers and a list of misses."""
    a = scorecard["aggregate"]
    base = baseline["aggregate"] if baseline else None

    print()
    print("=" * 72)
    print(" Reineke-RAG Retrieval Quality Eval")
    print(f" Run:      {scorecard['run_at']}")
    print(f" Backend:  {scorecard['backend_url']}")
    print(f" Tenant:   {scorecard['tenant']} / {scorecard['project']}")
    print(f" Questions: {a['n_total']} "
          f"({a['n_answer']} answer · {a['n_refusal']} refusal)")
    print("=" * 72)
    print()
    print("Aggregate")
    print("─────────")
    print(f"  Recall@{RECALL_TOP_K:<2}        "
          f"{a['recall_hits']}/{a['n_answer']:<3} "
          f"({a['recall_at_k']:5.1f} %){_format_delta(a['recall_at_k'], base['recall_at_k']) if base else ''}")
    print(f"  MRR              {a['mrr']:.3f}"
          + (f"        {_format_delta(a['mrr'], base['mrr'], suffix='')}" if base else ""))
    if a["n_with_keywords"]:
        print(f"  Faithfulness     {a['faithfulness_hits']}/{a['n_with_keywords']:<3} "
              f"({a['faithfulness']:5.1f} %){_format_delta(a['faithfulness'], base['faithfulness']) if base else ''}")
    if a["n_refusal"]:
        print(f"  Refusal-Accuracy {a['refusal_hits']}/{a['n_refusal']:<3} "
              f"({a['refusal_accuracy']:5.1f} %){_format_delta(a['refusal_accuracy'], base['refusal_accuracy']) if base else ''}")
    print(f"  Latency p50/p95  {a['latency_p50']:.2f}s / {a['latency_p95']:.2f}s")
    print()

    if a["by_category"]:
        print("By category")
        print("───────────")
        for cat, slot in sorted(a["by_category"].items()):
            if slot["answer_qs"]:
                print(f"  {cat:<18} Recall@{RECALL_TOP_K}  "
                      f"{slot['recall_hits']}/{slot['answer_qs']:<2} "
                      f"({slot['recall_at_k_pct']:5.1f} %)   "
                      f"MRR {slot['mrr']:.2f}")
            else:
                print(f"  {cat:<18} (no scoreable questions)")
        print()

    misses = [r for r in scorecard["results"] if not r["outcome"]]
    if misses:
        print("Misses")
        print("──────")
        for r in misses:
            short_q = r["question"]
            if len(short_q) > 60:
                short_q = short_q[:57] + "..."
            if r["expected_refusal"]:
                reason = "did NOT refuse (gave an answer)"
            elif not r["recall_at_k"]:
                reason = (
                    f"expected source not in top-{RECALL_TOP_K}"
                    if r["rank"] is None
                    else f"correct doc only at rank {r['rank']}"
                )
            elif r["keyword_hit"] is False:
                reason = "answer missing expected keyword"
            else:
                reason = "outcome=false (mixed)"
            print(f"  {r['id']:<5} [{r['difficulty']:<7}] {short_q!r:<62} — {reason}")
        print()


# ---------------------------------------------------------------------------
# The actual test
# ---------------------------------------------------------------------------

@pytest.mark.eval
def test_eval_retrieval_scorecard(
    eval_backend_url: str,
    eval_tenant: str,
    eval_project: str,
    eval_questions_path: Path,
    eval_results_dir: Path,
) -> None:
    """Run every question in ``questions.yaml`` against the live backend and
    write a scorecard.

    The test passes when the backend was reachable and produced a result for
    every question. The metrics themselves are not asserted — read the
    printed scorecard (or ``results/eval-*.json``) for that.
    """
    raw_questions = yaml.safe_load(eval_questions_path.read_text(encoding="utf-8"))
    assert isinstance(raw_questions, list) and raw_questions, (
        f"No questions found in {eval_questions_path}"
    )

    # Sanity check: backend is up before we start firing 20 chat requests.
    with httpx.Client(timeout=10) as health_client:
        try:
            r = health_client.get(f"{eval_backend_url}/health")
        except httpx.HTTPError as exc:
            pytest.skip(f"Backend not reachable at {eval_backend_url}: {exc}")
        if r.status_code != 200:
            pytest.skip(
                f"Backend at {eval_backend_url} returned /health "
                f"status {r.status_code}: {r.text[:200]}"
            )

    # Live progress: each /chat call can take 10-60 s on CPU-bound setups,
    # so the user otherwise stares at a blank "collected 1 item" line for
    # several minutes. We print a single line per question: a "querying..."
    # prefix at the start, then complete it in-place with the result. Needs
    # ``pytest -s`` (no output capture) — the eval is documented as such.
    total = len(raw_questions)
    print()  # break away from the pytest "::test_eval_retrieval_scorecard" line
    results: List[Dict[str, Any]] = []
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        for idx, q in enumerate(raw_questions, start=1):
            qid = q.get("id", "?")
            cat = q.get("category", "uncategorized")
            print(
                f"  [{idx:2d}/{total}] {qid:<4} [{cat:<17}] querying... ",
                end="",
                flush=True,
            )
            try:
                answer, sources, latency = _query_chat(
                    client, eval_backend_url, eval_tenant, eval_project, q["question"]
                )
            except httpx.HTTPError as exc:
                print(f"✗ HTTP error: {exc}", flush=True)
                # One failing /chat call must not kill the whole scorecard.
                results.append({
                    "id": qid,
                    "category": cat,
                    "difficulty": q.get("difficulty", "unspecified"),
                    "question": q["question"],
                    "expected_refusal": bool(q.get("expected_refusal")),
                    "needles": _expected_doc_strings(q),
                    "answer": "",
                    "sources": [],
                    "rank": None,
                    "recall_at_k": False,
                    "reciprocal_rank": 0.0,
                    "is_refusal": False,
                    "keyword_hit": None,
                    "outcome": False,
                    "latency_seconds": 0.0,
                    "error": str(exc),
                })
                continue
            scored = _score_question(q, answer, sources, latency)
            results.append(scored)
            print(_live_line(scored), flush=True)

    aggregate = _aggregate(results)
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    scorecard = {
        "run_at": run_at,
        "backend_url": eval_backend_url,
        "tenant": eval_tenant,
        "project": eval_project,
        "questions_path": str(eval_questions_path),
        "aggregate": aggregate,
        "results": results,
    }

    # Diff against the previous run before we save the new one — otherwise
    # the new file would be its own "baseline".
    baseline = _find_latest_baseline(eval_results_dir)
    _print_scorecard(scorecard, baseline)

    out_path = eval_results_dir / f"eval-{run_at.replace(':', '').replace('-', '')}.json"
    out_path.write_text(json.dumps(scorecard, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Scorecard written to {out_path}")

    # Sanity assertion: every question must have produced a result so the
    # scorecard is meaningful.
    assert len(results) == len(raw_questions), (
        f"Mismatch: {len(raw_questions)} questions configured, "
        f"{len(results)} scored."
    )
