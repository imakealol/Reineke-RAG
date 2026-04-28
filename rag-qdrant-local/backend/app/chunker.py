"""Chunking layer.

Two strategies live here:

* **Text chunking** — used for PDF / DOC / DOCX. Splits on natural
  boundaries (paragraph → sentence → fixed window) with a configurable
  character overlap.
* **Spreadsheet chunking** — used for XLS / XLSX. Splits each sheet into
  blocks of N rows, repeating the header row at the top of every block so
  semantic context isn't lost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from .config import settings
from .document_loader import LoadedDocument, LoadedSegment


@dataclass
class Chunk:
    text: str
    chunk_index: int
    document_type: str
    page: Optional[int] = None
    sheet: Optional[str] = None
    row_start: Optional[int] = None
    row_end: Optional[int] = None


# ---------------------------------------------------------------------------
# Text chunking (paragraph- and sentence-aware sliding window)
# ---------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_paragraphs(text: str) -> List[str]:
    paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    return paras or ([text] if text.strip() else [])


def _split_long_paragraph(p: str, size: int) -> List[str]:
    if len(p) <= size:
        return [p]
    sentences = _SENT_SPLIT.split(p)
    out: List[str] = []
    cur = ""
    for s in sentences:
        if not s:
            continue
        if len(cur) + len(s) + 1 <= size:
            cur = (cur + " " + s).strip() if cur else s
        else:
            if cur:
                out.append(cur)
            if len(s) > size:
                # Hard wrap a giant sentence
                for i in range(0, len(s), size):
                    out.append(s[i : i + size])
                cur = ""
            else:
                cur = s
    if cur:
        out.append(cur)
    return out


def _chunk_text(text: str, size: int, overlap: int) -> List[str]:
    """Sliding window over paragraphs/sentences, capped at `size` chars."""
    if size <= 0:
        return [text]

    paragraphs = _split_paragraphs(text)
    chunks: List[str] = []
    cur = ""

    for para in paragraphs:
        for piece in _split_long_paragraph(para, size):
            if not cur:
                cur = piece
            elif len(cur) + len(piece) + 2 <= size:
                cur = cur + "\n\n" + piece
            else:
                chunks.append(cur)
                # carry overlap from the tail of the previous chunk
                if overlap > 0 and len(cur) > overlap:
                    cur = cur[-overlap:] + "\n\n" + piece
                else:
                    cur = piece

    if cur:
        chunks.append(cur)

    # If we somehow produced nothing meaningful, fall back to the raw text
    if not chunks and text.strip():
        chunks = [text.strip()]

    return chunks


def _chunk_text_segment(seg: LoadedSegment, start_index: int) -> List[Chunk]:
    pieces = _chunk_text(seg.text, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP)
    return [
        Chunk(
            text=piece,
            chunk_index=start_index + i,
            document_type=seg.document_type,
            page=seg.page,
        )
        for i, piece in enumerate(pieces)
    ]


# ---------------------------------------------------------------------------
# Spreadsheet chunking
# ---------------------------------------------------------------------------

def _xlsx_rows_per_chunk(num_columns: int) -> int:
    """Adapt block size to column count: wide tables ⇒ fewer rows per chunk."""
    base = settings.XLSX_ROWS_PER_CHUNK
    if num_columns <= 4:
        return max(25, base + 10)
    if num_columns <= 10:
        return base
    if num_columns <= 20:
        return max(15, base - 10)
    return max(10, base - 20)


def _chunk_spreadsheet_segment(seg: LoadedSegment, start_index: int) -> List[Chunk]:
    """Re-chunk an XLSX segment into row-bounded blocks with header repetition."""
    lines = seg.text.split("\n")
    if not lines:
        return []

    header_line: Optional[str] = None
    data_lines = lines
    if seg.has_header and lines:
        header_line = lines[0]
        data_lines = lines[1:]

    if not data_lines:
        return [
            Chunk(
                text=seg.text,
                chunk_index=start_index,
                document_type=seg.document_type,
                sheet=seg.sheet,
                row_start=seg.row_start,
                row_end=seg.row_end,
            )
        ]

    num_columns = len(header_line.split("\t")) if header_line else (
        len(data_lines[0].split("\t")) if data_lines else 1
    )
    rows_per_chunk = _xlsx_rows_per_chunk(num_columns)

    base_row = seg.row_start or 1  # spreadsheet row of data_lines[0]

    chunks: List[Chunk] = []
    for i in range(0, len(data_lines), rows_per_chunk):
        block = data_lines[i : i + rows_per_chunk]
        for sub_block, sub_offset in _enforce_char_budget(
            block, header_line, settings.XLSX_MAX_CHARS_PER_CHUNK
        ):
            text_lines = [header_line] + sub_block if header_line else sub_block
            row_start = base_row + i + sub_offset
            row_end = row_start + len(sub_block) - 1
            chunks.append(
                Chunk(
                    text="\n".join(text_lines),
                    chunk_index=start_index + len(chunks),
                    document_type=seg.document_type,
                    sheet=seg.sheet,
                    row_start=row_start,
                    row_end=row_end,
                )
            )

    return chunks


def _enforce_char_budget(
    block: List[str],
    header_line: Optional[str],
    budget: int,
) -> List[tuple[List[str], int]]:
    """Split a row block into sub-blocks so that each sub-block (including the
    repeated header) fits inside ``budget`` characters.

    Returns a list of ``(sub_block, offset_within_block)`` tuples so that the
    caller can compute correct ``row_start`` / ``row_end`` for every chunk.

    Rows that on their own already exceed the budget are emitted as a single
    chunk — truncating row content silently would lose data, and a single
    oversized row is rare and a much smaller embedder problem than an
    oversized block of dozens of rows.
    """
    if budget <= 0:
        return [(block, 0)]

    header_len = (len(header_line) + 1) if header_line else 0  # +1 for newline
    out: List[tuple[List[str], int]] = []

    cur: List[str] = []
    cur_chars = header_len
    start_offset = 0

    for idx, row in enumerate(block):
        row_chars = len(row) + 1  # newline
        if cur and (cur_chars + row_chars) > budget:
            out.append((cur, start_offset))
            cur = []
            cur_chars = header_len
            start_offset = idx
        cur.append(row)
        cur_chars += row_chars

    if cur:
        out.append((cur, start_offset))

    return out or [(block, 0)]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def chunk_document(doc: LoadedDocument) -> List[Chunk]:
    chunks: List[Chunk] = []
    for seg in doc.segments:
        if seg.document_type == "xlsx":
            chunks.extend(_chunk_spreadsheet_segment(seg, len(chunks)))
        else:
            chunks.extend(_chunk_text_segment(seg, len(chunks)))
    return chunks
