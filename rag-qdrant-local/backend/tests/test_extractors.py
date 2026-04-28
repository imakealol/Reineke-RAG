"""Extractor smoke tests for DOCX and XLSX (legacy .doc/.xls require LibreOffice)."""

from pathlib import Path

import pytest

from app.document_loader import DocumentLoadError, load_document


def _build_xlsx(path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Server"
    ws.append(["Hostname", "IP", "Role"])
    ws.append(["srv-01", "10.0.0.1", "DB"])
    ws.append(["srv-02", "10.0.0.2", "App"])
    ws.append(["srv-03", "10.0.0.3", "Web"])
    wb.create_sheet("Empty")
    wb.save(path)


def _build_docx(path: Path) -> None:
    from docx import Document

    doc = Document()
    doc.add_heading("Test", level=1)
    doc.add_paragraph("Dies ist ein Absatz mit Inhalt.")
    doc.add_paragraph("Zweiter Absatz mit weiteren Informationen.")
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Spalte A"
    table.rows[0].cells[1].text = "Spalte B"
    table.rows[1].cells[0].text = "Wert 1"
    table.rows[1].cells[1].text = "Wert 2"
    doc.save(path)


def test_xlsx_extraction(sandbox_root: Path):
    p = sandbox_root / "test.xlsx"
    _build_xlsx(p)

    loaded = load_document(p)
    assert loaded.document_type == "xlsx"

    server_seg = next(s for s in loaded.segments if s.sheet == "Server")
    assert server_seg.has_header is True
    assert server_seg.row_start == 2
    assert server_seg.row_end == 4
    assert "srv-01" in server_seg.text
    assert "Hostname" in server_seg.text


def test_docx_extraction(sandbox_root: Path):
    p = sandbox_root / "test.docx"
    _build_docx(p)

    loaded = load_document(p)
    assert loaded.document_type == "docx"
    full = "\n".join(s.text for s in loaded.segments)
    assert "Absatz" in full
    assert "Spalte A" in full
    assert "Wert 1" in full


def test_unsupported_extension_raises(sandbox_root: Path):
    p = sandbox_root / "foo.txt"
    p.write_text("hello")
    with pytest.raises(DocumentLoadError):
        load_document(p)
