"""Regression: the eval matcher must be Unicode-normalisation safe.

macOS HFS+/APFS stores filenames in NFD (``ü`` = ``u`` + combining ¨),
so the ``file_name`` values returned by ``/chat`` come back NFD. Most
editors save the eval YAML as NFC (precomposed ``ü``). Both forms render
identically but are different byte sequences — without NFC-normalising
both sides before comparison, every umlaut in a needle silently produces
a false negative miss.

This regression test runs in the regular (non-eval) CI suite so it
cannot rot if someone tweaks ``_icontains`` later.
"""

from __future__ import annotations

import unicodedata

from app.admin.api import _build_job_progress  # noqa: F401 — sanity import
# Import directly from the eval module — it lives under tests/eval but the
# helper is a pure function with no live-backend dependency.
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR))

from test_eval_retrieval import _icontains  # noqa: E402


def test_icontains_basic_ascii_substring():
    assert _icontains("hello world", "world")
    assert not _icontains("hello world", "foo")


def test_icontains_case_insensitive():
    assert _icontains("Hello World", "WORLD")
    assert _icontains("VERSCHLUESSELUNG", "verschluess")


def test_icontains_nfc_needle_in_nfd_haystack():
    """The case we hit in production: filename came from macOS (NFD),
    YAML editor saved needle as NFC. Without normalising, this is False."""
    haystack_nfd = unicodedata.normalize(
        "NFD", "Richtlinie des Einsatzes von Verschlüsselung.pdf"
    )
    needle_nfc = unicodedata.normalize("NFC", "Verschlüsselung")
    # Sanity: the two forms really do differ at the byte level
    assert haystack_nfd.encode("utf-8") != needle_nfc.encode("utf-8") + b".pdf"
    assert _icontains(haystack_nfd, needle_nfc)


def test_icontains_nfd_needle_in_nfc_haystack():
    """And the reverse direction — symmetric."""
    haystack_nfc = unicodedata.normalize(
        "NFC", "Richtlinie zu Mobilgeräten und Telearbeit"
    )
    needle_nfd = unicodedata.normalize("NFD", "Mobilgeräten")
    assert _icontains(haystack_nfc, needle_nfd)


def test_icontains_empty_inputs():
    assert not _icontains("", "anything")
    assert _icontains("anything", "")  # empty needle matches everything
    assert not _icontains(None, "x")  # type: ignore[arg-type]
