"""Source-scanner auto-detection of MediaWiki exports.

The admin ingest wizard branches its UI on this signal — so the rules
need to be predictable. We verify:

  * a folder with the wiki XML + an images/ dir is detected;
  * a flat ``uploads/`` layout is also recognised;
  * a stray ``.xml`` that isn't a MediaWiki export does NOT trigger;
  * ``LocalSettings*.php`` values flow into the hint when present;
  * when several MediaWiki XMLs exist, the newest wins.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.source_scanner import detect_mediawiki_export, scan_directory


def _write_wiki_xml(p: Path, *, body: str = "") -> None:
    p.write_text(
        '<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.10/">'
        f"{body}</mediawiki>",
        encoding="utf-8",
    )


def test_detects_xml_plus_images_dir(tmp_path: Path):
    _write_wiki_xml(tmp_path / "wiki-current.xml")
    (tmp_path / "images").mkdir()

    hint = detect_mediawiki_export(tmp_path)
    assert hint is not None
    assert hint.xml_path.endswith("wiki-current.xml")
    assert hint.uploads_path and hint.uploads_path.endswith("/images")


def test_detects_xml_plus_uploads_dir(tmp_path: Path):
    """Some installs ship ``uploads/`` instead of ``images/``."""
    _write_wiki_xml(tmp_path / "export.xml")
    (tmp_path / "uploads").mkdir()

    hint = detect_mediawiki_export(tmp_path)
    assert hint is not None
    assert hint.uploads_path and hint.uploads_path.endswith("/uploads")


def test_detects_xml_without_any_uploads_dir(tmp_path: Path):
    """No upload dir → hint still emitted (caller can warn)."""
    _write_wiki_xml(tmp_path / "wiki-current.xml")

    hint = detect_mediawiki_export(tmp_path)
    assert hint is not None
    assert hint.uploads_path is None


def test_ignores_non_mediawiki_xml(tmp_path: Path):
    """A random XML file must not trigger MediaWiki detection."""
    (tmp_path / "config.xml").write_text(
        "<?xml version='1.0'?><configuration><key>value</key></configuration>"
    )
    (tmp_path / "images").mkdir()

    assert detect_mediawiki_export(tmp_path) is None


def test_returns_none_for_empty_dir(tmp_path: Path):
    assert detect_mediawiki_export(tmp_path) is None


def test_returns_none_for_nonexistent_dir(tmp_path: Path):
    assert detect_mediawiki_export(tmp_path / "does-not-exist") is None


def test_picks_newest_xml_when_multiple_present(tmp_path: Path):
    older = tmp_path / "wiki-2024.xml"
    newer = tmp_path / "wiki-2026.xml"
    _write_wiki_xml(older)
    # bump mtime slightly so the older one stays older even on fast CI
    old_time = time.time() - 100
    import os
    os.utime(older, (old_time, old_time))
    _write_wiki_xml(newer)
    (tmp_path / "images").mkdir()

    hint = detect_mediawiki_export(tmp_path)
    assert hint is not None
    assert hint.xml_path.endswith("wiki-2026.xml")


def test_extracts_wiki_config_from_localsettings(tmp_path: Path):
    _write_wiki_xml(tmp_path / "wiki-current.xml")
    (tmp_path / "images").mkdir()
    (tmp_path / "LocalSettings.example.php").write_text(
        '<?php\n'
        '$wgSitename = "Demo";\n'
        '$wgServer = "https://wiki.demo.local";\n'
        '$wgScriptPath = "/w";\n'
        '$wgArticlePath = "/wiki/$1";\n',
        encoding="utf-8",
    )

    hint = detect_mediawiki_export(tmp_path)
    assert hint is not None
    assert hint.base_url == "https://wiki.demo.local"
    assert hint.article_path == "/wiki/$1"
    assert hint.script_path == "/w"
    assert hint.localsettings_path and hint.localsettings_path.endswith(
        "LocalSettings.example.php"
    )


def test_scan_directory_includes_mediawiki_hint_in_result(tmp_path: Path):
    """``scan_directory`` exposes the hint on its ScanResult so the
    admin endpoint can branch its template without a second call."""
    _write_wiki_xml(tmp_path / "wiki-current.xml")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "Netzplan.pdf").write_bytes(b"%PDF-1.4 stub")

    result = scan_directory(tmp_path, recursive=True)
    assert result.mediawiki_hint is not None
    assert result.mediawiki_hint.xml_path.endswith("wiki-current.xml")


def test_scan_directory_no_hint_when_folder_is_just_files(tmp_path: Path):
    (tmp_path / "manual.pdf").write_bytes(b"%PDF-1.4 stub")
    (tmp_path / "data.xlsx").write_bytes(b"PK stub")

    result = scan_directory(tmp_path, recursive=True)
    assert result.mediawiki_hint is None
