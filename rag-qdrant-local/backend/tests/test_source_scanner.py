"""Scan: classifies supported vs unsupported files; respects recursive flag."""

from pathlib import Path

from app.source_scanner import scan_directory


def test_scan_classifies_extensions(sandbox_root: Path):
    proj = sandbox_root / "scan-test"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "a.pdf").write_bytes(b"%PDF-1.4\n%fake")
    (proj / "b.docx").write_bytes(b"PK\x03\x04fake")
    (proj / "c.xlsx").write_bytes(b"PK\x03\x04fake")
    (proj / "d.txt").write_text("not supported")
    (proj / "e.png").write_bytes(b"\x89PNGfake")

    result = scan_directory(proj, recursive=True)

    exts = {f.extension for f in result.supported}
    assert exts == {".pdf", ".docx", ".xlsx"}
    assert {f.extension for f in result.unsupported} == {".txt", ".png"}
    assert result.file_types == {".pdf": 1, ".docx": 1, ".xlsx": 1}


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
