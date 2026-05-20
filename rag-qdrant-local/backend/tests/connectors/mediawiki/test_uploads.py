"""MediaWiki upload resolver — hashed + flat layouts, path-traversal rejection."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.connectors.mediawiki.uploads import (
    _hashed_subpath,
    _normalise_filename,
    resolve_upload,
)


# ---------------------------------------------------------------------------
# Filename normalisation
# ---------------------------------------------------------------------------

def test_normalise_first_char_uppercased():
    assert _normalise_filename("netzplan.pdf") == "Netzplan.pdf"


def test_normalise_spaces_to_underscores():
    assert _normalise_filename("notfall handbuch.docx") == "Notfall_handbuch.docx"


def test_normalise_rejects_path_traversal():
    assert _normalise_filename("../etc/passwd") == ""
    assert _normalise_filename("..secret.pdf") == ""
    assert _normalise_filename("sub/folder.pdf") == ""
    assert _normalise_filename("back\\slash.pdf") == ""
    assert _normalise_filename("nul\x00byte.pdf") == ""


def test_normalise_empty_in_empty_out():
    assert _normalise_filename("") == ""
    assert _normalise_filename("   ") == ""


# ---------------------------------------------------------------------------
# Hashed sub-path layout (MediaWiki's default)
# ---------------------------------------------------------------------------

def test_hashed_subpath_matches_md5_prefix():
    """``$wgHashedUploadDirectory`` layout: ``<first>/<first-two>/Filename``."""
    name = "Netzplan-demo.pdf"
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()
    assert _hashed_subpath(name) == f"{digest[0]}/{digest[0:2]}"


# ---------------------------------------------------------------------------
# resolve_upload
# ---------------------------------------------------------------------------

def test_resolve_finds_file_in_hashed_layout(tmp_path: Path):
    name = "Netzplan-demo.pdf"
    sub = _hashed_subpath(name)
    target = tmp_path / sub / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"%PDF-1.4 stub")

    out = resolve_upload(tmp_path, name)
    assert out.exists
    assert out.resolved_path == target.resolve()


def test_resolve_finds_file_in_flat_layout(tmp_path: Path):
    name = "Notfallhandbuch.docx"
    target = tmp_path / name
    target.write_bytes(b"PK\x03\x04 stub")

    out = resolve_upload(tmp_path, name)
    assert out.exists
    assert out.resolved_path == target.resolve()


def test_resolve_normalises_first_char(tmp_path: Path):
    """``[[File:netzplan.pdf]]`` should still find ``Netzplan.pdf``."""
    target = tmp_path / "Netzplan.pdf"
    target.write_bytes(b"x")

    out = resolve_upload(tmp_path, "netzplan.pdf")
    assert out.exists
    assert out.resolved_path == target.resolve()


def test_resolve_missing_file_is_none(tmp_path: Path):
    out = resolve_upload(tmp_path, "Nonexistent.pdf")
    assert not out.exists
    assert out.resolved_path is None


def test_resolve_refuses_path_traversal(tmp_path: Path):
    """A malicious wikitext like ``[[File:../etc/passwd]]`` must not escape
    the uploads root, even if the target file exists outside."""
    outside = tmp_path.parent / "outside.txt"
    outside.write_bytes(b"secret")
    try:
        out = resolve_upload(tmp_path, "../outside.txt")
        assert not out.exists
        assert out.resolved_path is None
    finally:
        if outside.exists():
            outside.unlink()


def test_resolve_refuses_symlink_escaping_uploads_root(tmp_path: Path):
    """Symlink inside uploads pointing outside must be refused."""
    outside_dir = tmp_path.parent / "outside-dir"
    outside_dir.mkdir(exist_ok=True)
    outside_file = outside_dir / "Secret.pdf"
    outside_file.write_bytes(b"secret")
    link = tmp_path / "Secret.pdf"
    try:
        link.symlink_to(outside_file)
        out = resolve_upload(tmp_path, "Secret.pdf")
        assert not out.exists
    finally:
        if link.is_symlink():
            link.unlink()
        if outside_file.exists():
            outside_file.unlink()
        if outside_dir.exists():
            outside_dir.rmdir()
