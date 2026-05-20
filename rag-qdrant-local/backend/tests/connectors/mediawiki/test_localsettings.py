"""LocalSettings.php parser — strict regex, no PHP evaluation."""

from __future__ import annotations

from pathlib import Path

from app.connectors.mediawiki.localsettings import (
    parse_localsettings_file,
    parse_localsettings_text,
)


FIXTURE = Path(__file__).parent / "fixtures" / "LocalSettings.example.php"


def test_extracts_server_article_and_script_path():
    cfg = parse_localsettings_file(FIXTURE)
    assert cfg.server == "https://wiki.demo.local"
    assert cfg.article_path == "/wiki/$1"
    assert cfg.script_path == "/w"


def test_server_trailing_slash_is_stripped():
    cfg = parse_localsettings_text('$wgServer = "https://wiki.example.com/";')
    assert cfg.server == "https://wiki.example.com"


def test_double_quoted_and_single_quoted_both_supported():
    text = """
    $wgServer    = "https://double.example.com";
    $wgArticlePath = '/seite/$1';
    """
    cfg = parse_localsettings_text(text)
    assert cfg.server == "https://double.example.com"
    assert cfg.article_path == "/seite/$1"


def test_missing_file_yields_empty_record():
    cfg = parse_localsettings_file(Path("/nonexistent/LocalSettings.php"))
    assert cfg.server is None
    assert cfg.article_path is None
    assert cfg.script_path is None


def test_missing_field_in_text_returns_none_for_that_field():
    """Only $wgServer present — other fields should resolve to None."""
    cfg = parse_localsettings_text('$wgServer = "https://only-server.local";')
    assert cfg.server == "https://only-server.local"
    assert cfg.article_path is None
    assert cfg.script_path is None
