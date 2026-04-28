"""Spreadsheet chunking — char budget and row-bound metadata."""

from app.chunker import chunk_document
from app.config import settings
from app.document_loader import LoadedDocument, LoadedSegment


def _make_xlsx_doc(num_rows: int, row_chars: int, header: str = "A\tB\tC") -> LoadedDocument:
    """Synthesize an XLSX-style segment without going through openpyxl."""
    row = "x" * row_chars + "\ty\tz"
    body = [row for _ in range(num_rows)]
    text = "\n".join([header] + body)
    seg = LoadedSegment(
        text=text,
        document_type="xlsx",
        sheet="Sheet1",
        row_start=2,
        row_end=2 + num_rows - 1,
        has_header=True,
    )
    return LoadedDocument(
        file_path=__file__,  # type: ignore[arg-type]
        file_name="synthetic.xlsx",
        file_extension=".xlsx",
        document_type="xlsx",
        segments=[seg],
    )


def test_xlsx_chunks_respect_char_budget(monkeypatch):
    # Force a small budget so a normal block must be split.
    monkeypatch.setattr(settings, "XLSX_MAX_CHARS_PER_CHUNK", 1500, raising=False)
    monkeypatch.setattr(settings, "XLSX_ROWS_PER_CHUNK", 40, raising=False)

    doc = _make_xlsx_doc(num_rows=50, row_chars=200)  # ~10 KB of body
    chunks = chunk_document(doc)

    assert len(chunks) >= 4, "expected the budget to force multiple chunks"
    for c in chunks:
        assert len(c.text) <= settings.XLSX_MAX_CHARS_PER_CHUNK + len(
            "A\tB\tC\n"
        ) + 5, "chunk exceeded budget"
        # row metadata must be sane
        assert c.sheet == "Sheet1"
        assert c.row_start is not None and c.row_end is not None
        assert c.row_start <= c.row_end


def test_xlsx_row_ranges_are_contiguous_and_unique(monkeypatch):
    monkeypatch.setattr(settings, "XLSX_MAX_CHARS_PER_CHUNK", 1500, raising=False)
    monkeypatch.setattr(settings, "XLSX_ROWS_PER_CHUNK", 40, raising=False)

    doc = _make_xlsx_doc(num_rows=30, row_chars=120)
    chunks = chunk_document(doc)

    # Row ranges should cover [2 .. 31] with no gaps and no overlaps.
    ranges = [(c.row_start, c.row_end) for c in chunks]
    ranges.sort()
    assert ranges[0][0] == 2
    assert ranges[-1][1] == 31
    for (a_start, a_end), (b_start, _) in zip(ranges, ranges[1:]):
        assert b_start == a_end + 1, f"gap or overlap between {ranges}"


def test_oversized_single_row_emitted_as_one_chunk(monkeypatch):
    monkeypatch.setattr(settings, "XLSX_MAX_CHARS_PER_CHUNK", 100, raising=False)

    doc = _make_xlsx_doc(num_rows=1, row_chars=500)
    chunks = chunk_document(doc)

    # We don't truncate — better to over-shoot the budget than silently lose data.
    assert len(chunks) == 1
    assert chunks[0].row_start == 2 and chunks[0].row_end == 2
