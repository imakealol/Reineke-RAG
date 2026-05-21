"""ChatService — guard against LLM-fabricated 'Quellen:' trailers.

The model is asked (in the system prompt) to end every answer with a
``Quellen:`` block. Before this guard, our deterministic block was only
appended when the model *forgot* to include one — meaning the user often
saw the LLM's own version with invented filenames, page numbers, and
'Quelle N:' indices that didn't match the actual retrieval. These tests
nail down that we now always replace the LLM's block with ours.
"""

from __future__ import annotations

from app.chat_service import ChatService


def test_strip_trailer_removes_clean_quellen_block():
    answer = (
        "Eine Antwort.\n\nQuellen:\n- foo.pdf (Seite 1)\n- bar.pdf (Seite 2)\n"
    )
    cleaned = ChatService._strip_llm_sources_trailer(answer)
    assert cleaned == "Eine Antwort."


def test_strip_trailer_handles_quelle_N_format_too():
    answer = (
        "Versuch 905 wurde mit dem Aggregat AM400 durchgeführt.\n\n"
        "Quellen:\nQuelle 6: 505 Mars Petcare AM400.doc (—)\n"
        "Quelle 2: 1241 Deutsche Algengenossenschaft.xlsx (Sheet 'Versuch 1')\n"
    )
    cleaned = ChatService._strip_llm_sources_trailer(answer)
    # Body stays, trailer gone — even the "Quelle N:"-prefixed kind.
    assert "Quellen:" not in cleaned
    assert "Quelle 6:" not in cleaned
    assert cleaned.startswith("Versuch 905 wurde mit dem Aggregat AM400")


def test_strip_trailer_leaves_inline_mentions_intact():
    """'siehe die Quellen:' without a newline before is content, not a trailer."""
    answer = "Laut den Quellen: das ist so. Und die Quellen sagen…"
    assert ChatService._strip_llm_sources_trailer(answer) == answer


def test_strip_trailer_handles_indented_label():
    answer = "Antwort.\n  Quellen:\n  - x.pdf\n"
    cleaned = ChatService._strip_llm_sources_trailer(answer)
    assert cleaned == "Antwort."


def test_strip_trailer_handles_markdown_bold_label():
    """Regression: qwen2.5 commonly emits ``**Quellen:**`` instead of plain
    ``Quellen:``. Before the trailer regex tolerated markdown decoration,
    the bold variant slipped through and the user got two Quellen blocks
    side-by-side (LLM's wrong one + ours). Observed via the OpenAI compat
    path on the local reineke/watch test of PR #19's bundle.
    """
    answer = (
        "Die Backup-Strategie ist in mehreren Richtlinien geregelt.\n\n"
        "**Quellen:**\n"
        "- Quelle 1: PL.ISMS010_Backup-Richtlinie.docx\n"
        "- Quelle 2: PL.ISMS013_…\n"
    )
    cleaned = ChatService._strip_llm_sources_trailer(answer)
    assert "Quellen" not in cleaned
    assert "ISMS010" not in cleaned
    assert cleaned.startswith("Die Backup-Strategie")


def test_strip_trailer_handles_markdown_heading_label():
    answer = "Body.\n\n## Quellen:\n- foo.pdf\n"
    cleaned = ChatService._strip_llm_sources_trailer(answer)
    assert cleaned == "Body."


def test_strip_trailer_handles_italic_label():
    answer = "Body.\n\n*Quellen:*\n- foo.pdf\n"
    cleaned = ChatService._strip_llm_sources_trailer(answer)
    assert cleaned == "Body."


def test_ensure_sources_appended_replaces_llm_trailer_with_ours():
    """The full path: LLM wrote its own block, we strip it, append ours."""
    answer = "Hauptantwort.\n\nQuellen:\n- erfunden.pdf (Seite 99)\n"
    ours = "Quellen:\n- echte_datei.pdf, Seite 4, Chunk 0"
    out = ChatService._ensure_sources_appended(answer, ours)
    # LLM's fabricated entry is gone.
    assert "erfunden.pdf" not in out
    # Our trustworthy block is at the end.
    assert out.endswith(ours)
    # Single 'Quellen:' heading — no double block.
    assert out.count("Quellen:") == 1


def test_ensure_sources_appended_appends_when_llm_omitted_block():
    answer = "Antwort ohne Quellenangabe."
    ours = "Quellen:\n- foo.pdf"
    out = ChatService._ensure_sources_appended(answer, ours)
    assert out == "Antwort ohne Quellenangabe.\n\nQuellen:\n- foo.pdf"


def test_ensure_sources_appended_with_empty_block_is_identity():
    answer = "Antwort."
    assert ChatService._ensure_sources_appended(answer, "") == "Antwort."
