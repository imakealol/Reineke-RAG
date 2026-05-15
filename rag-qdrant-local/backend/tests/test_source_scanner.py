"""Scan: classifies supported vs unsupported files; respects recursive flag."""

from pathlib import Path

from app.schemas import FileEntry
from app.source_scanner import ScanResult, scan_directory


def test_scan_classifies_extensions(sandbox_root: Path):
    proj = sandbox_root / "scan-test"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "a.pdf").write_bytes(b"%PDF-1.4\n%fake")
    (proj / "b.docx").write_bytes(b"PK\x03\x04fake")
    (proj / "c.xlsx").write_bytes(b"PK\x03\x04fake")
    (proj / "d.txt").write_text("not supported")
    (proj / "e.png").write_bytes(b"\x89PNGfake")
    (proj / "f.html").write_text("<html><body>x</body></html>")
    (proj / "g.htm").write_text("<html><body>y</body></html>")

    result = scan_directory(proj, recursive=True)

    exts = {f.extension for f in result.supported}
    assert exts == {".pdf", ".docx", ".xlsx", ".html", ".htm"}
    assert {f.extension for f in result.unsupported} == {".txt", ".png"}
    assert result.file_types == {
        ".pdf": 1, ".docx": 1, ".xlsx": 1, ".html": 1, ".htm": 1,
    }


def test_scan_non_recursive(sandbox_root: Path):
    proj = sandbox_root / "scan-nonrec"
    (proj / "sub").mkdir(parents=True, exist_ok=True)
    (proj / "top.pdf").write_bytes(b"%PDF")
    (proj / "sub" / "deep.pdf").write_bytes(b"%PDF")

    result = scan_directory(proj, recursive=False)
    assert len(result.supported) == 1
    assert result.supported[0].file_name == "top.pdf"


def test_scan_skips_office_lock_and_junk(sandbox_root: Path):
    """Office owner-lock files, dotfiles, macOS resource forks and zero-byte
    files must never reach the loader."""
    proj = sandbox_root / "scan-junk"
    proj.mkdir(parents=True, exist_ok=True)

    (proj / "real.docx").write_bytes(b"PK\x03\x04fake")
    (proj / "~$real.docx").write_bytes(b"office lock junk")           # Office owner-lock
    (proj / "._real.docx").write_bytes(b"macos resource fork")        # macOS extended attr file
    (proj / ".DS_Store").write_bytes(b"DS junk")                      # dotfile
    (proj / "empty.pdf").write_bytes(b"")                             # zero bytes

    result = scan_directory(proj, recursive=True)
    names = {f.file_name for f in result.supported}
    assert names == {"real.docx"}
    # Junk files should not appear in unsupported either — they're filtered.
    junk = {"~$real.docx", "._real.docx", ".DS_Store", "empty.pdf"}
    assert junk.isdisjoint({f.file_name for f in result.unsupported})


# ---------------------------------------------------------------------------
# filter_to_extensions: post-scan whitelist used by the ingest wizard
# ---------------------------------------------------------------------------

def _fe(name: str, ext: str) -> FileEntry:
    """Tiny FileEntry factory — only the fields the filter touches matter."""
    return FileEntry(
        path=f"/x/{name}",
        file_name=name,
        extension=ext,
        size_bytes=1,
        modified_at="2026-01-01T00:00:00+00:00",
        supported=True,
    )


def _sample_scan() -> ScanResult:
    return ScanResult(
        supported=[
            _fe("a.pdf", ".pdf"),
            _fe("b.docx", ".docx"),
            _fe("c.xlsx", ".xlsx"),
            _fe("d.html", ".html"),
        ],
        unsupported=[_fe("x.txt", ".txt")],
        file_types={".pdf": 1, ".docx": 1, ".xlsx": 1, ".html": 1},
    )


def test_filter_to_extensions_keeps_only_allowed_types():
    result = _sample_scan().filter_to_extensions([".pdf", ".html"])

    assert {f.extension for f in result.supported} == {".pdf", ".html"}
    assert result.file_types == {".pdf": 1, ".html": 1}
    # Filtered-out supported files move into unsupported so the wizard can
    # still report what was found but skipped.
    skipped_exts = {f.extension for f in result.unsupported}
    assert ".docx" in skipped_exts
    assert ".xlsx" in skipped_exts
    # Original unsupported entries are preserved.
    assert ".txt" in skipped_exts


def test_filter_to_extensions_case_and_dot_tolerance():
    # Accept ``PDF`` and ``pdf`` (no dot) just as readily as ``.pdf``.
    result = _sample_scan().filter_to_extensions(["PDF", "html"])

    assert {f.extension for f in result.supported} == {".pdf", ".html"}


def test_filter_to_extensions_empty_whitelist_drops_everything():
    """An empty whitelist means "user unchecked every type" — wizard behaviour
    that must result in zero ingested files (not silently ingest everything)."""
    result = _sample_scan().filter_to_extensions([])

    assert result.supported == []
    assert result.file_types == {}
    # All previously-supported files surface in unsupported so the user still
    # sees that the scan found them.
    skipped_files = {f.file_name for f in result.unsupported}
    assert skipped_files == {"x.txt", "a.pdf", "b.docx", "c.xlsx", "d.html"}


def test_filter_to_extensions_does_not_mutate_original():
    scan = _sample_scan()
    scan.filter_to_extensions([".pdf"])

    # Original must be untouched: filter_to_extensions returns a new ScanResult.
    assert len(scan.supported) == 4
    assert scan.file_types == {".pdf": 1, ".docx": 1, ".xlsx": 1, ".html": 1}
