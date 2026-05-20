"""Streaming XML importer.

The fixture in ``fixtures/wiki-current.xml`` is the canonical small
synthetic export used throughout this test suite:

  * 5 pages total
  * 1 main-namespace page with categories + file ref (id=10)
  * 1 main-namespace page with TWO revisions (id=11) — must yield only
    the latest (revision id 120)
  * 1 redirect in main namespace (id=12)
  * 1 Diskussion (Talk) namespace page (id=13)
  * 1 Vorlage (Template) namespace page (id=14)
"""

from __future__ import annotations

from pathlib import Path

from app.connectors.mediawiki.xml_importer import iter_pages, read_namespace_map


FIXTURE = Path(__file__).parent / "fixtures" / "wiki-current.xml"


def _pages_by_id():
    return {p.page_id: p for p in iter_pages(FIXTURE)}


def test_yields_every_page_in_export():
    pages = list(iter_pages(FIXTURE))
    assert len(pages) == 5
    ids = sorted(p.page_id for p in pages)
    assert ids == [10, 11, 12, 13, 14]


def test_title_and_namespace_extracted():
    pages = _pages_by_id()
    assert pages[10].title == "Firewall Konzept"
    assert pages[10].namespace_id == 0
    assert pages[13].title == "Diskussion:Firewall Konzept"
    assert pages[13].namespace_id == 1
    assert pages[14].namespace_id == 10


def test_latest_revision_wins_when_page_has_history():
    """Page 11 has revisions 50 and 120 — must keep only 120."""
    pages = _pages_by_id()
    assert pages[11].revision_id == 120
    assert "180 Tagen" in pages[11].raw_text   # text from revision 120
    assert "90 Tagen" not in pages[11].raw_text


def test_redirect_detected_via_xml_element():
    pages = _pages_by_id()
    assert pages[12].is_redirect is True
    assert pages[12].redirect_target == "Firewall Konzept"
    # Non-redirects must not be marked.
    assert pages[10].is_redirect is False


def test_revision_timestamp_preserved():
    pages = _pages_by_id()
    assert pages[10].revision_timestamp == "2026-01-15T09:30:00Z"


def test_namespace_map_extracted():
    """``read_namespace_map`` reads ``<siteinfo>`` once."""
    ns = read_namespace_map(FIXTURE)
    assert ns[0] == ""          # main namespace has no name
    assert ns[1] == "Diskussion"
    assert ns[6] == "Datei"
    assert ns[10] == "Vorlage"
    assert ns[14] == "Kategorie"
