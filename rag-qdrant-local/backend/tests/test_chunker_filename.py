"""Path A — filename prefix in every chunk.

Each Qdrant chunk text is prepended with a short ``[Datei: <stem>] ``
header so the embedder (and any future BM25 layer) can see file-name
keywords that don't appear in the chunk body — e.g. ``Phishing`` in
``Plasmatreat Maßnahmenplan Phishing.xlsx`` whose row contents are too
generic to retrieve on their own.
"""

from __future__ import annotations

from app.chunker import _filename_prefix, chunk_document
from app.document_loader import LoadedDocument, LoadedSegment


# ---------------------------------------------------------------------------
# Helper: stem extraction
# ---------------------------------------------------------------------------

def test_filename_prefix_stem_only():
    assert _filename_prefix("foo.xlsx") == "[Datei: foo] "
    assert _filename_prefix("Plasmatreat Maßnahmenplan Phishing.xlsx") == (
        "[Datei: Plasmatreat Maßnahmenplan Phishing] "
    )


def test_filename_prefix_strips_iso_date_suffix():
    assert _filename_prefix("Security_Actions_Plasmatreat_20251231.xlsx") == (
        "[Datei: Security_Actions_Plasmatreat] "
    )


def test_filename_prefix_strips_version_suffix():
    assert _filename_prefix("Backup-Policy_v2.docx") == "[Datei: Backup-Policy] "
    assert _filename_prefix("Backup-Policy-v10.docx") == "[Datei: Backup-Policy] "


def test_filename_prefix_strips_both_date_and_version_when_chained():
    """Two-pass design: date suffix is stripped first, then the version
    suffix the date was hiding becomes visible."""
    assert _filename_prefix("Doc_v3_20260101.docx") == "[Datei: Doc] "


def test_filename_prefix_preserves_umlauts():
    """German file names must round-trip — no NFD normalisation, no
    accidental lowercasing, no diacritic stripping."""
    assert _filename_prefix("Maßnahmen_Übersicht.xlsx") == (
        "[Datei: Maßnahmen_Übersicht] "
    )


def test_filename_prefix_handles_dots_inside_name():
    """``PL.ISMS007_Kennwort_Richtlinie.docx`` — the leading ``PL.`` is
    semantic, only the trailing ``.docx`` extension may be stripped."""
    assert _filename_prefix("PL.ISMS007_Kennwort_Richtlinie.docx") == (
        "[Datei: PL.ISMS007_Kennwort_Richtlinie] "
    )


# ---------------------------------------------------------------------------
# Integration: chunk_document threads the prefix through both code paths
# ---------------------------------------------------------------------------

def _text_doc(file_name: str, body: str) -> LoadedDocument:
    return LoadedDocument(
        file_path=__file__,  # type: ignore[arg-type]
        file_name=file_name,
        file_extension=".docx",
        document_type="docx",
        segments=[LoadedSegment(text=body, document_type="docx", page=1)],
    )


def _xlsx_doc(file_name: str, rows: int = 5) -> LoadedDocument:
    header = "A\tB\tC"
    body_rows = "\n".join(f"r{i}\tv{i}\tw{i}" for i in range(rows))
    seg = LoadedSegment(
        text=f"{header}\n{body_rows}",
        document_type="xlsx",
        sheet="Sheet1",
        row_start=2,
        row_end=2 + rows - 1,
        has_header=True,
    )
    return LoadedDocument(
        file_path=__file__,  # type: ignore[arg-type]
        file_name=file_name,
        file_extension=".xlsx",
        document_type="xlsx",
        segments=[seg],
    )


def test_text_chunks_carry_filename_prefix():
    doc = _text_doc(
        "PL.ISMS017_Sicherheitspolitik_für_Lieferanten.docx",
        "Externe Dienstleister müssen sich verpflichten, die Schutzziele "
        "der Informationssicherheit einzuhalten.",
    )
    chunks = chunk_document(doc)
    assert chunks, "expected at least one chunk"
    for c in chunks:
        assert c.text.startswith("[Datei: PL.ISMS017_Sicherheitspolitik_für_Lieferanten] ")


def test_xlsx_chunks_carry_filename_prefix():
    doc = _xlsx_doc("Plasmatreat Maßnahmenplan Phishing.xlsx", rows=3)
    chunks = chunk_document(doc)
    assert chunks, "expected at least one chunk"
    for c in chunks:
        assert c.text.startswith("[Datei: Plasmatreat Maßnahmenplan Phishing] ")
        # Header repetition still works on top of the prefix.
        assert "A\tB\tC" in c.text


def test_filename_prefix_appears_exactly_once_per_chunk():
    doc = _text_doc(
        "Backup-Policy_v2.docx",
        "Die Datensicherung erfolgt täglich um 23:00 Uhr.\n\n"
        "Wiederherstellungstests werden vierteljährlich durchgeführt.",
    )
    chunks = chunk_document(doc)
    for c in chunks:
        # The bracketed token must appear exactly once at the start, never
        # duplicated by a re-application down the chunking pipeline.
        assert c.text.count("[Datei: Backup-Policy] ") == 1
        assert c.text.find("[Datei:") == 0
