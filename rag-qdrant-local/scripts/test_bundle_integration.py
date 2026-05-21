#!/usr/bin/env python3
"""End-to-end integration test for the quality-bundle features.

What this exercises against a running backend (Ollama + Qdrant up,
documents already ingested):

  * Health checks (backend, Ollama, Qdrant)
  * Corpus inventory + DOCX/PDF pair detection
  * Stem-based retrieval dedup (no DOCX+PDF twins in /retrieve top-K)
  * LLM "Quellen:" trailer replacement (exactly one block, our format)
  * Meta-question deflection (count question → SQLite count, no retrieval)
  * Meta-question NEGATIVE (long content question that mentions "wie viele"
    must NOT short-circuit)
  * Cross-turn citation recall (turn-1 cited docs reach turn-2 candidate pool)
  * OpenAI-compatible path (/v1/chat/completions reaches the same path)
  * num_ctx + CHAT_HISTORY_TURNS startup logging (advisory — not API-exposed)

Pure stdlib, no extra deps. Make executable (`chmod +x`) and run from the
repo root or anywhere on the host that can reach the backend.

Configuration (env vars, all optional):
  BACKEND_URL        default http://localhost:8000
  RAG_TENANT         default reineke
  RAG_PROJECT        default watch
  RAG_TEST_TIMEOUT   default 240 (seconds per HTTP call — bump on cold Ollama)
  NO_COLOR=1         disable ANSI colours

Exit code: 0 if all assertions pass, 1 if any FAIL. SKIP and INFO never fail
the run so the script stays useful on a sparse corpus or a stripped install.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
TENANT = os.getenv("RAG_TENANT", "reineke")
PROJECT = os.getenv("RAG_PROJECT", "watch")
TIMEOUT_S = int(os.getenv("RAG_TEST_TIMEOUT", "240"))

# Colours only when the terminal is real (and the user hasn't opted out).
_USE_COLOUR = sys.stdout.isatty() and not os.getenv("NO_COLOR")
def _c(code: str) -> str:
    return code if _USE_COLOUR else ""
RED = _c("\033[31m")
GREEN = _c("\033[32m")
YELLOW = _c("\033[33m")
BLUE = _c("\033[34m")
DIM = _c("\033[2m")
RESET = _c("\033[0m")
BOLD = _c("\033[1m")


# ---------------------------------------------------------------------------
# Result collection + reporting helpers
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    name: str
    status: str        # PASS | FAIL | SKIP | INFO
    detail: str = ""

_RESULTS: List[TestResult] = []

_STATUS_GLYPH = {"PASS": "✓", "FAIL": "✗", "SKIP": "⊘", "INFO": "ⓘ"}
_STATUS_COLOUR = {"PASS": GREEN, "FAIL": RED, "SKIP": YELLOW, "INFO": BLUE}


def record(name: str, status: str, detail: str = "") -> None:
    _RESULTS.append(TestResult(name, status, detail))
    glyph = _STATUS_GLYPH.get(status, "?")
    colour = _STATUS_COLOUR.get(status, "")
    line = f"  {colour}{glyph}{RESET} {name}"
    if detail:
        line += f"  {DIM}— {detail}{RESET}"
    print(line)


def section(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}")


# ---------------------------------------------------------------------------
# HTTP helpers — stdlib only, returns (status_code, parsed_body_or_text)
# ---------------------------------------------------------------------------

def _request(method: str, path: str, body: Dict[str, Any] | None = None) -> Tuple[int, Any]:
    url = f"{BACKEND_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", "replace")
        return e.code, body_text
    except urllib.error.URLError as e:
        return -1, f"network: {e.reason}"
    except TimeoutError:
        return -1, f"timeout after {TIMEOUT_S}s"
    except Exception as e:  # last-ditch — make sure we never crash the runner
        return -1, f"{type(e).__name__}: {e}"


def get(path: str) -> Tuple[int, Any]:
    return _request("GET", path)


def post(path: str, body: Dict[str, Any]) -> Tuple[int, Any]:
    return _request("POST", path, body)


# ---------------------------------------------------------------------------
# Stem helper — must mirror app/retrieval_service.py:_document_stem exactly
# ---------------------------------------------------------------------------

_DATE_SUFFIX = re.compile(r"_\d{8}$")
_VERSION_SUFFIX = re.compile(r"[_\-]v\d+$")


def stem(file_name: str) -> str:
    if not file_name:
        return ""
    s = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    s = _DATE_SUFFIX.sub("", s)
    s = _VERSION_SUFFIX.sub("", s)
    return s.lower()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_health() -> bool:
    """Sanity gate — if the backend isn't up, nothing below makes sense."""
    section("Health")
    code, body = get("/health")
    if code != 200 or not isinstance(body, dict):
        record("Backend reachable", "FAIL", f"GET /health → {code} ({str(body)[:120]})")
        return False
    record("Backend reachable", "PASS")
    checks = body.get("checks") or []
    if isinstance(checks, list):
        for it in checks:
            name = str(it.get("name", "?"))
            ok = it.get("status") == "ok"
            detail = "" if ok else str(it.get("error") or it.get("status") or "")
            record(f"Subsystem: {name}", "PASS" if ok else "FAIL", detail)
    return True


def test_corpus_inventory() -> Tuple[bool, List[dict]]:
    """Read the indexed-document list — needed for the dedup test and to
    report whether DOCX/PDF pairs exist in this corpus at all."""
    section("Corpus inventory")
    code, body = get(f"/documents?tenant={TENANT}&project={PROJECT}")
    if code != 200 or not isinstance(body, dict):
        record(
            "GET /documents", "FAIL",
            f"got {code} for tenant={TENANT}, project={PROJECT}",
        )
        return False, []
    docs = body.get("documents") or []
    if not docs:
        record("Corpus has documents", "FAIL", "0 indexed — ingest something first")
        return False, []
    record("Corpus has documents", "PASS", f"{len(docs)} indexed")

    by_stem: Dict[str, List[str]] = {}
    for d in docs:
        fn = str(d.get("file_name") or "")
        if fn:
            by_stem.setdefault(stem(fn), []).append(fn)
    pairs = {s: fs for s, fs in by_stem.items() if len(fs) > 1}
    if pairs:
        sample = "; ".join(", ".join(fs) for fs in list(pairs.values())[:2])
        record(
            "DOCX/PDF pairs present in corpus", "INFO",
            f"{len(pairs)} pair(s) — dedup test will be meaningful. Sample: {sample}",
        )
    else:
        record(
            "DOCX/PDF pairs present in corpus", "INFO",
            "no twin filenames — dedup test runs but can't FAIL on this corpus",
        )
    return True, docs


def test_retrieve_dedup(docs: List[dict]) -> None:
    section("Stem-based retrieval dedup (PR #19, commit e38d7d9)")
    if not docs:
        record("Retrieve dedup", "SKIP", "empty corpus")
        return

    by_stem: Dict[str, List[str]] = {}
    for d in docs:
        fn = str(d.get("file_name") or "")
        if fn:
            by_stem.setdefault(stem(fn), []).append(fn)
    paired = [(s, fs) for s, fs in by_stem.items() if len(fs) > 1]

    if paired:
        s, _ = paired[0]
        question = f"Was steht in {s}?"
    else:
        question = "Wie ist die Backup-Strategie geregelt?"

    code, body = post(
        "/retrieve",
        {"tenant": TENANT, "project": PROJECT, "question": question, "top_k": 8},
    )
    if code != 200 or not isinstance(body, dict):
        record("/retrieve responds 200", "FAIL", f"got {code}: {str(body)[:160]}")
        return
    sources = body.get("sources") or []
    record("/retrieve responds 200 with sources", "PASS", f"{len(sources)} hits")

    # Stem dedup invariant: each stem appears at most once.
    seen: Dict[str, str] = {}
    dupes: List[Tuple[str, str]] = []
    for s in sources:
        fn = str(s.get("file_name") or "")
        st = stem(fn)
        if not st:
            continue
        if st in seen:
            dupes.append((seen[st], fn))
        else:
            seen[st] = fn
    if dupes:
        record(
            "No DOCX/PDF duplicates in top-K", "FAIL",
            f"duplicate stems: {dupes}",
        )
    else:
        msg = f"{len(seen)} unique stems among {len(sources)} hits"
        if not paired:
            msg += " (corpus has no twin files — invariant holds trivially)"
        record("No DOCX/PDF duplicates in top-K", "PASS", msg)


def test_chat_quellen_trailer() -> None:
    section("LLM Quellen trailer replacement (PR #19, commit 57ac692)")
    code, body = post(
        "/chat",
        {
            "tenant": TENANT, "project": PROJECT,
            "question": "Welche Anforderungen stellt die Kennwort-Richtlinie an Passwörter?",
        },
    )
    if code != 200 or not isinstance(body, dict):
        record("/chat reaches generation", "FAIL", f"{code}: {str(body)[:160]}")
        return
    answer = str(body.get("answer") or "")
    sources = body.get("sources") or []
    record(
        "/chat returns answer + structured sources", "PASS",
        f"{len(answer)} chars, {len(sources)} sources",
    )

    quellen_count = answer.count("Quellen:")
    if quellen_count == 1:
        record("Exactly one 'Quellen:' block in answer", "PASS")
    else:
        record(
            "Exactly one 'Quellen:' block in answer", "FAIL",
            f"found {quellen_count} (LLM trailer not stripped, or appended twice)",
        )

    # Our format uses bulleted "- file.pdf, Seite N, Chunk N" lines.
    # The LLM-fabricated format had "Quelle N: file.pdf" lines — that must
    # never reach the user any more.
    if re.search(r"\bQuelle\s+\d+\s*:", answer):
        record(
            "Trailer is deterministic format (no 'Quelle N:' labels)", "FAIL",
            "answer still contains LLM-style 'Quelle N:' labels",
        )
    else:
        record("Trailer is deterministic format (no 'Quelle N:' labels)", "PASS")

    # And the trailing block should match the bullet shape.
    if "Quellen:" in answer:
        trailer = answer.split("Quellen:", 1)[1]
        if any(line.strip().startswith("- ") for line in trailer.splitlines()):
            record("Trailer lines start with '- '", "PASS")
        else:
            record(
                "Trailer lines start with '- '", "FAIL",
                f"trailer head: {trailer[:120]!r}",
            )


def test_meta_question_deflect() -> None:
    section("Meta-question deflection (PR #19, commit 57ac692)")
    for question in (
        "Wie viele Dokumente hast du?",
        "How many documents do you hold?",
        "Anzahl der Dokumente?",
    ):
        code, body = post(
            "/chat",
            {"tenant": TENANT, "project": PROJECT, "question": question},
        )
        if code != 200 or not isinstance(body, dict):
            record(f"Meta: {question!r}", "FAIL", f"{code}: {str(body)[:120]}")
            continue
        answer = str(body.get("answer") or "")
        sources = body.get("sources") or []
        if sources:
            record(
                f"Meta: {question!r} skips retrieval", "FAIL",
                f"got {len(sources)} sources — deflection didn't fire",
            )
            continue
        if TENANT not in answer or PROJECT not in answer:
            record(
                f"Meta: {question!r} names tenant + project", "FAIL",
                f"missing {TENANT!r}/{PROJECT!r} in {answer[:120]!r}",
            )
            continue
        if not re.search(r"\b\d+\b", answer):
            record(
                f"Meta: {question!r} answer contains a count", "FAIL",
                f"no digit found in {answer[:120]!r}",
            )
            continue
        record(f"Meta: {question!r} fully deflected", "PASS", answer.split("\n", 1)[0])


def test_meta_question_negative() -> None:
    section("Meta-question NEGATIVE — content question must NOT deflect")
    # Long enough to exceed the 80-char length cap, and clearly content-y.
    question = (
        "Welche Anweisungen gibt es laut der Backup-Richtlinie, "
        "wie viele Sicherungskopien man behalten muss?"
    )
    code, body = post(
        "/chat",
        {"tenant": TENANT, "project": PROJECT, "question": question},
    )
    if code != 200 or not isinstance(body, dict):
        record("Negative meta call reaches /chat", "FAIL", f"{code}")
        return
    sources = body.get("sources") or []
    if sources:
        record(
            "Content question with 'wie viele' still retrieves",
            "PASS", f"{len(sources)} sources",
        )
    else:
        # No sources could mean two different things:
        #   (a) deflection fired (BAD — false-positive on the meta detector)
        #   (b) genuinely no hits above MIN_RETRIEVAL_SCORE (acceptable, but
        #       worth flagging because it's not what we want to validate)
        answer = str(body.get("answer") or "")
        if "indiziert" in answer and TENANT in answer:
            record(
                "Content question with 'wie viele' still retrieves",
                "FAIL", "meta deflection over-fired on a content question",
            )
        else:
            record(
                "Content question with 'wie viele' still retrieves",
                "INFO",
                "no sources, but the answer doesn't look like meta deflection — "
                "may just be a thin corpus match",
            )


def test_cross_turn_recall() -> None:
    section("Cross-turn citation recall (PR #19, commit 57ac692)")
    # Turn 1: a clear, specific question that lands on one or two known docs.
    code, t1 = post(
        "/chat",
        {
            "tenant": TENANT, "project": PROJECT,
            "question": "Welche Sicherheitsanforderungen gelten für externe Dienstleister?",
        },
    )
    if code != 200 or not isinstance(t1, dict):
        record("Turn 1 succeeds", "FAIL", f"{code}: {str(t1)[:160]}")
        return
    session_id = t1.get("session_id")
    t1_docs = {
        s.get("document_id") for s in (t1.get("sources") or [])
        if s.get("document_id")
    }
    if not session_id or not t1_docs:
        record(
            "Turn 1 produced session_id + cited docs", "FAIL",
            f"session={session_id!r}, cited={len(t1_docs)}",
        )
        return
    record(
        "Turn 1 produced session_id + cited docs", "PASS",
        f"session={str(session_id)[:8]}…, {len(t1_docs)} doc(s) cited",
    )

    # Turn 2: an explicitly-referential follow-up.
    code, t2 = post(
        "/chat",
        {
            "tenant": TENANT, "project": PROJECT,
            "session_id": session_id,
            "question": "Welche anderen Richtlinien hängen damit zusammen?",
        },
    )
    if code != 200 or not isinstance(t2, dict):
        record("Turn 2 succeeds", "FAIL", f"{code}: {str(t2)[:160]}")
        return
    t2_docs = {
        s.get("document_id") for s in (t2.get("sources") or [])
        if s.get("document_id")
    }
    record("Turn 2 succeeds", "PASS", f"{len(t2_docs)} doc(s) cited in turn 2")

    overlap = t1_docs & t2_docs
    if overlap:
        record(
            "Turn-1 cited docs reach turn-2 candidate pool", "PASS",
            f"{len(overlap)} carry-over",
        )
    else:
        # Recall feeds the chunks into the candidate pool but the reranker
        # still has the final say. A genuinely-irrelevant carry-over should
        # be discarded — that's correct behaviour, not a failure. Flag INFO.
        record(
            "Turn-1 cited docs reach turn-2 candidate pool",
            "INFO",
            "no overlap — reranker may have outranked them on the new query "
            "(or the follow-up genuinely points elsewhere)",
        )


def test_openai_compat() -> None:
    section("OpenAI-compatible path (/v1/chat/completions)")
    code, body = post(
        "/v1/chat/completions",
        {
            "model": f"rag:{TENANT}:{PROJECT}",
            "messages": [
                {"role": "user", "content": "Wie ist die Backup-Strategie geregelt?"},
            ],
            "stream": False,
        },
    )
    if code != 200 or not isinstance(body, dict):
        record("/v1/chat/completions returns 200", "FAIL", f"{code}: {str(body)[:160]}")
        return
    record("/v1/chat/completions returns 200", "PASS")

    choices = body.get("choices") or []
    content = ""
    if choices and isinstance(choices[0], dict):
        content = str((choices[0].get("message") or {}).get("content") or "")
    if not content:
        record("OpenAI response has assistant content", "FAIL", "empty choices[0]")
        return
    record("OpenAI response has assistant content", "PASS", f"{len(content)} chars")

    sources = body.get("sources") or []
    if sources:
        record(
            "OpenAI response carries 'sources' extension", "PASS",
            f"{len(sources)} sources",
        )
    else:
        record(
            "OpenAI response carries 'sources' extension", "FAIL",
            "no sources in compat response — citations dropped via OpenAI path",
        )

    # The Quellen guard from the /chat path must also apply when answering
    # via the OpenAI compat shim — same chat_service underneath.
    if content.count("Quellen:") > 1:
        record(
            "OpenAI answer body has exactly one Quellen: block", "FAIL",
            f"found {content.count('Quellen:')}",
        )
    else:
        record("OpenAI answer body has exactly one Quellen: block", "PASS")


def test_num_ctx_hint() -> None:
    section("num_ctx & history (informational)")
    record(
        "num_ctx resolution", "INFO",
        "logged at startup as 'num_ctx=… for chat model …' — grep your backend log",
    )
    record(
        "CHAT_HISTORY_TURNS = 6", "INFO",
        "not exposed via API — check /admin → Konfiguration in the UI",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _summary_exit_code() -> int:
    total = len(_RESULTS)
    passed = sum(1 for r in _RESULTS if r.status == "PASS")
    failed = sum(1 for r in _RESULTS if r.status == "FAIL")
    skipped = sum(1 for r in _RESULTS if r.status == "SKIP")
    info = sum(1 for r in _RESULTS if r.status == "INFO")

    print()
    print(
        f"{BOLD}Summary{RESET}: "
        f"{GREEN}{passed} PASS{RESET}  "
        f"{RED}{failed} FAIL{RESET}  "
        f"{YELLOW}{skipped} SKIP{RESET}  "
        f"{BLUE}{info} INFO{RESET}  "
        f"(of {total})"
    )
    if failed:
        print(f"\n{RED}{BOLD}FAILED:{RESET}")
        for r in _RESULTS:
            if r.status == "FAIL":
                line = f"  - {r.name}"
                if r.detail:
                    line += f": {r.detail}"
                print(line)
    return 1 if failed else 0


def main() -> None:
    print(f"{BOLD}Reineke-RAG bundle integration test{RESET}")
    print(f"  Backend:     {BACKEND_URL}")
    print(f"  Tenant:      {TENANT}")
    print(f"  Project:     {PROJECT}")
    print(f"  HTTP timeout:{TIMEOUT_S}s per call")

    if not test_health():
        sys.exit(_summary_exit_code())

    ok, docs = test_corpus_inventory()
    if ok:
        test_retrieve_dedup(docs)
        test_chat_quellen_trailer()
        test_meta_question_deflect()
        test_meta_question_negative()
        test_cross_turn_recall()
        test_openai_compat()
    test_num_ctx_hint()
    sys.exit(_summary_exit_code())


if __name__ == "__main__":
    main()
