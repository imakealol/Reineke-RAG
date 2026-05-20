"""End-to-end dry-run of the MediaWiki import service.

Dry-run means: parse XML, filter, normalize, count — but write nothing
to SQLite or Qdrant. That makes it safe to run in CI without Ollama or
Qdrant up, and lets us assert the counter contract against the
synthetic fixture.

The fixture (``fixtures/wiki-current.xml``) contains:

  * 1 main-NS page with categories + 1 file ref       → indexed
  * 1 main-NS page with 2 revisions                   → indexed (latest only)
  * 1 redirect in main NS                             → skipped
  * 1 Diskussion (NS=1) page                          → skipped (namespace)
  * 1 Vorlage   (NS=10) page                          → skipped (namespace)

so a clean run with allowed_namespaces=[0], include_redirects=false
should report 2 indexed pages, 1 redirect skipped, 2 namespace skipped.
"""

from __future__ import annotations

import asyncio
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy.orm import Session

from app.connectors.mediawiki.schemas import MediaWikiWikiConfig
from app.connectors.mediawiki.service import MediaWikiImportService
from app.path_security import resolve_safe_path


FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def staged_export(sandbox_root: Path) -> Path:
    """Copy the fixture XML + images into the test sandbox so the path
    passes ``resolve_safe_path``."""
    dest = sandbox_root / "wiki"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(FIXTURE_DIR, dest)
    return dest


@contextmanager
def _dummy_session() -> Iterator[Session]:
    """Stand-in session — dry-run shouldn't touch it. If anything does,
    pytest will surface an AttributeError."""
    yield None  # type: ignore[misc]


def test_dry_run_against_fixture_counts_correctly(staged_export: Path):
    service = MediaWikiImportService()

    async def run():
        return await service.import_xml(
            None,  # type: ignore[arg-type]  — dry-run never reads db
            tenant="demo",
            project="wiki",
            xml_path=str(staged_export / "wiki-current.xml"),
            uploads_path=str(staged_export / "images"),
            wiki=MediaWikiWikiConfig(base_url="https://wiki.demo.local"),
            allowed_namespaces=[0],
            include_redirects=False,
            include_uploads=True,
            reindex_changed_only=True,
            dry_run=True,
        )

    result = asyncio.run(run())

    assert result.pages_seen == 5
    assert result.pages_indexed == 2          # only NS=0 non-redirect
    assert result.pages_skipped_redirect == 1
    assert result.pages_skipped_namespace == 2  # NS=1 (Diskussion) + NS=10 (Vorlage)

    # One file reference reaches the flat-layout placeholder ``Netzplan-demo.pdf``.
    # The fixture's wikitext also mentions ``notfallhandbuch-demo.docx`` which
    # isn't on disk → counted as unresolved.
    assert result.files_seen >= 1
    assert "notfallhandbuch-demo.docx" in result.unresolved_files

    assert result.errors == []


def test_dry_run_skips_namespace_filter_when_all_allowed(staged_export: Path):
    """allowed_namespaces=[0,1,10] keeps Talk + Template pages too."""
    service = MediaWikiImportService()

    async def run():
        return await service.import_xml(
            None,  # type: ignore[arg-type]
            tenant="demo",
            project="wiki",
            xml_path=str(staged_export / "wiki-current.xml"),
            uploads_path=None,
            wiki=MediaWikiWikiConfig(base_url="https://wiki.demo.local"),
            allowed_namespaces=[0, 1, 10],
            include_redirects=False,
            include_uploads=False,
            reindex_changed_only=True,
            dry_run=True,
        )

    result = asyncio.run(run())
    assert result.pages_seen == 5
    assert result.pages_skipped_namespace == 0
    # 4 non-redirect pages survive (NS 0/1/10) + 1 redirect skipped.
    assert result.pages_indexed == 4
    assert result.pages_skipped_redirect == 1


def test_path_security_rejects_xml_outside_allowed_bases(tmp_path: Path):
    """An XML path outside ALLOWED_BASE_PATHS must be refused before any
    parsing happens — defence in depth."""
    outside = tmp_path / "evil.xml"
    outside.write_text("<mediawiki></mediawiki>")

    from app.path_security import PathSecurityError
    service = MediaWikiImportService()

    async def run():
        return await service.import_xml(
            None,  # type: ignore[arg-type]
            tenant="demo", project="wiki",
            xml_path=str(outside),
            uploads_path=None,
            wiki=MediaWikiWikiConfig(base_url="https://wiki.demo.local"),
            allowed_namespaces=[0],
            include_redirects=False, include_uploads=False,
            reindex_changed_only=True, dry_run=True,
        )

    with pytest.raises(PathSecurityError):
        asyncio.run(run())
