"""MediaWiki XML import orchestrator.

Wires the XML importer + normalizer + uploads resolver together and
plays them through the existing :class:`IngestionService`. The same
contract will work for the SQL importer in Mode B — only the page
source changes, everything from this service downward is shared.

Responsibilities:

  * validate XML and uploads paths against ALLOWED_BASE_PATHS;
  * stream-parse the XML, applying namespace + redirect filters;
  * normalize each page's wikitext, collect categories and linked files;
  * create/update one :class:`Document` row per page with full metadata
    in ``source_metadata_json``;
  * call :meth:`IngestionService._index_loaded_document` for the prose;
  * second pass: ingest unique uploaded files via the existing file-path
    pipeline, attaching ``mediawiki_upload`` metadata;
  * support dry-run (parse + count, no writes).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

# select is also used below for the IngestionJob lookup.

from ...chunker import Chunk  # noqa: F401  — re-exported usage clarity
from ...document_loader import LoadedDocument, LoadedSegment
from ...ingestion_service import IngestionService
from ...models import Document, IngestionJob
from ...path_security import assert_existing_dir, resolve_safe_path
from ...source_scanner import SUPPORTED_EXTENSIONS
from ...utils import get_logger, new_id, sha256_file
from .errors import MediaWikiConfigError, MediaWikiUploadsError
from .schemas import (
    MediaWikiFileRef,
    MediaWikiPage,
    MediaWikiWikiConfig,
    NormalizedPage,
)
from .normalizer import normalize_wikitext
from .uploads import ResolvedUpload, resolve_upload
from .xml_importer import iter_pages, read_namespace_map

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result accumulators
# ---------------------------------------------------------------------------

@dataclass
class ImportResult:
    """Mutable counters / lists filled in during the run, snapshotted into
    the API response at the end."""

    pages_seen: int = 0
    pages_indexed: int = 0
    pages_skipped_namespace: int = 0
    pages_skipped_redirect: int = 0
    pages_skipped_unchanged: int = 0
    pages_failed: int = 0
    files_seen: int = 0
    files_indexed: int = 0
    files_skipped_unsupported: int = 0
    files_skipped_unchanged: int = 0
    unresolved_files: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _title_to_url_segment(title: str) -> str:
    """MediaWiki URL conversion: spaces → underscores, then percent-encode
    everything except a small safe set (letters/digits/underscore/dot/hyphen/colon)."""
    return quote(title.replace(" ", "_"), safe="_.-:/")


def _build_page_url(wiki: MediaWikiWikiConfig, title: str) -> str:
    """Compose the canonical URL of a wiki page."""
    if "$1" not in wiki.article_path:
        raise MediaWikiConfigError(
            f"article_path must contain '$1' placeholder, got {wiki.article_path!r}"
        )
    return wiki.base_url.rstrip("/") + wiki.article_path.replace(
        "$1", _title_to_url_segment(title)
    )


def _build_file_page_url(wiki: MediaWikiWikiConfig, file_title: str) -> str:
    """File-page URL (e.g. ``/wiki/File:Netzplan.pdf``). ``file_title``
    must already include the namespace prefix."""
    return _build_page_url(wiki, file_title)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _checksum_for_page(revision_id: int, normalised_text: str) -> str:
    """Change-detection key for a wiki page. Combining the revision id
    with the content hash means an old revision being re-exported is
    detected as unchanged even if our normalizer evolves in a way that
    leaves the prose semantically equivalent."""
    return f"rev:{revision_id}:{_content_hash(normalised_text)[:16]}"


def _parse_iso_timestamp(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # MediaWiki uses RFC 3339 with trailing 'Z'.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class MediaWikiImportService:
    """Orchestrate XML → existing ingestion pipeline for one collection."""

    def __init__(
        self,
        ingestion: Optional[IngestionService] = None,
    ) -> None:
        # Lazy default so dry-run paths don't try to construct an
        # OllamaClient / QdrantStore they will never call.
        self._ingestion = ingestion

    @property
    def ingestion(self) -> IngestionService:
        if self._ingestion is None:
            self._ingestion = IngestionService()
        return self._ingestion

    # -----------------------------------------------------------------------

    async def import_xml(
        self,
        db: Session,
        *,
        tenant: str,
        project: str,
        xml_path: str,
        uploads_path: Optional[str],
        wiki: MediaWikiWikiConfig,
        allowed_namespaces: List[int],
        include_redirects: bool,
        include_uploads: bool,
        reindex_changed_only: bool,
        dry_run: bool,
        job_id: Optional[str] = None,
    ) -> ImportResult:
        """Top-level entry point for ``POST /sources/mediawiki/import-xml``.

        When ``job_id`` is supplied, the corresponding :class:`IngestionJob`
        is loaded and updated as pages / uploads progress — the existing
        admin live-progress UI then works for wiki imports too. The
        bookkeeping fields are mapped from wiki to file shape:
        ``files_found = pages_total + uploads_total``,
        ``files_indexed = pages_indexed + uploads_indexed``,
        ``files_skipped`` aggregates every skip reason,
        ``current_file`` shows the page title / upload filename in flight.
        """
        result = ImportResult()
        job: Optional[IngestionJob] = None
        if job_id is not None:
            job = db.execute(
                select(IngestionJob).where(IngestionJob.id == job_id)
            ).scalar_one_or_none()

        # ---- path validation ------------------------------------------------
        safe_xml = resolve_safe_path(xml_path)
        if not safe_xml.exists() or not safe_xml.is_file():
            raise MediaWikiUploadsError(f"XML export not found: {safe_xml}")

        safe_uploads: Optional[Path] = None
        if uploads_path:
            safe_uploads = resolve_safe_path(uploads_path)
            assert_existing_dir(safe_uploads)
        elif include_uploads:
            result.warnings.append(
                "include_uploads=true but uploads_path is empty — file references "
                "will be recorded as metadata only."
            )

        # ---- namespace map (best-effort) -----------------------------------
        try:
            namespace_map = read_namespace_map(safe_xml)
        except Exception as exc:
            result.warnings.append(f"Could not read namespace map: {exc}")
            namespace_map = {}

        allowed_ns = set(allowed_namespaces)

        # ---- collection: ensure Qdrant has the right vector size -----------
        if not dry_run:
            await self.ingestion._ensure_collection_for_model()

        # ---- pre-count pages so the progress bar has a denominator. Cheap
        # ---- compared to the per-page chunk+embed+upsert below.
        if job is not None and not dry_run:
            try:
                total_pages = sum(1 for _ in iter_pages(safe_xml))
            except Exception:
                total_pages = 0
            job.files_found = total_pages
            db.commit()

        # ---- pages: pass 1 — filter, normalize, ingest, collect file refs --
        # We track file refs across pages because the *same* upload may be
        # referenced by many pages; the ingest happens once and the
        # ``referenced_by_pages`` list records the cross-link.
        file_refs: Dict[str, _UploadAggregate] = {}

        for page in iter_pages(safe_xml):
            result.pages_seen += 1
            if job is not None:
                job.current_file = page.title
                db.commit()

            if page.namespace_id not in allowed_ns:
                result.pages_skipped_namespace += 1
                self._update_job_counters(db, job, result)
                continue
            if page.is_redirect and not include_redirects:
                result.pages_skipped_redirect += 1
                self._update_job_counters(db, job, result)
                continue

            normalised = normalize_wikitext(page.raw_text)
            for fref in normalised.linked_files:
                agg = file_refs.setdefault(
                    fref.bare_filename,
                    _UploadAggregate(title=fref.title, bare_filename=fref.bare_filename),
                )
                if page.page_id not in agg.referenced_by_pages:
                    agg.referenced_by_pages.append(page.page_id)

            page_url = _build_page_url(wiki, page.title)
            checksum = _checksum_for_page(page.revision_id, normalised.text)

            if dry_run:
                # Counted as "would be indexed" — exact unchanged-skip
                # accounting requires a DB lookup we deliberately skip in dry-run.
                result.pages_indexed += 1
                continue

            try:
                await self._ingest_page(
                    db,
                    tenant=tenant,
                    project=project,
                    page=page,
                    normalised=normalised,
                    namespace_map=namespace_map,
                    page_url=page_url,
                    checksum=checksum,
                    reindex_changed_only=reindex_changed_only,
                    wiki=wiki,
                    original_export_file=str(safe_xml),
                    result=result,
                )
            except Exception as exc:
                result.pages_failed += 1
                result.errors.append(
                    f"page '{page.title}' (id={page.page_id}): {exc}"
                )
            self._update_job_counters(db, job, result)

        # ---- pass 2 — uploads (if requested + uploads_path set) ------------
        if file_refs and safe_uploads is not None:
            # Now that we know how many uploads there are, lift the total
            # so the progress bar can reach 100 instead of capping early.
            if job is not None and not dry_run:
                job.files_found = (job.files_found or 0) + len(file_refs)
                db.commit()
            for bare_filename, agg in file_refs.items():
                if job is not None:
                    job.current_file = bare_filename
                    db.commit()
                result.files_seen += 1
                resolved = resolve_upload(safe_uploads, bare_filename)
                if not resolved.exists:
                    result.unresolved_files.append(bare_filename)
                    continue
                if not include_uploads:
                    continue
                ext = (resolved.resolved_path.suffix or "").lower()
                if ext not in SUPPORTED_EXTENSIONS:
                    result.files_skipped_unsupported += 1
                    result.warnings.append(
                        f"upload '{bare_filename}': extension '{ext}' not in supported "
                        f"set — metadata-only."
                    )
                    continue

                if dry_run:
                    result.files_indexed += 1
                    continue

                try:
                    await self._ingest_upload(
                        db,
                        tenant=tenant,
                        project=project,
                        agg=agg,
                        resolved=resolved,
                        wiki=wiki,
                        reindex_changed_only=reindex_changed_only,
                        result=result,
                    )
                except Exception as exc:
                    result.errors.append(
                        f"upload '{bare_filename}': {exc}"
                    )
                self._update_job_counters(db, job, result)

        if job is not None:
            job.current_file = None
            db.commit()
        return result

    @staticmethod
    def _update_job_counters(
        db: Session,
        job: Optional[IngestionJob],
        result: "ImportResult",
    ) -> None:
        """Map the wiki-specific counters onto the file-shaped columns the
        progress UI already understands. Called after each unit so the
        bar advances visibly during long imports."""
        if job is None:
            return
        job.files_indexed = result.pages_indexed + result.files_indexed
        job.files_skipped = (
            result.pages_skipped_namespace
            + result.pages_skipped_redirect
            + result.pages_skipped_unchanged
            + result.files_skipped_unsupported
            + result.files_skipped_unchanged
        )
        job.files_failed = result.pages_failed + len(result.errors)
        db.commit()

    # -----------------------------------------------------------------------

    async def _ingest_page(
        self,
        db: Session,
        *,
        tenant: str,
        project: str,
        page: MediaWikiPage,
        normalised: NormalizedPage,
        namespace_map: Dict[int, str],
        page_url: str,
        checksum: str,
        reindex_changed_only: bool,
        wiki: MediaWikiWikiConfig,
        original_export_file: str,
        result: ImportResult,
    ) -> None:
        # Resolve or create the Document row keyed by URL.
        doc = db.execute(
            select(Document).where(
                Document.tenant == tenant,
                Document.project == project,
                Document.source_path == page_url,
            )
        ).scalar_one_or_none()

        is_new = doc is None
        if doc is None:
            doc = Document(
                id=new_id(),
                tenant=tenant,
                project=project,
                source_path=page_url,
                file_name=page.title,
                file_extension=".wiki",
                file_size=len(page.raw_text.encode("utf-8")),
                checksum="",
                modified_at=_parse_iso_timestamp(page.revision_timestamp),
                status="pending",
                source_type="mediawiki_page",
            )
            db.add(doc)
            db.flush()  # need doc.id stable for Qdrant point ids
        else:
            doc.file_name = page.title
            doc.file_extension = ".wiki"
            doc.file_size = len(page.raw_text.encode("utf-8"))
            doc.modified_at = (
                _parse_iso_timestamp(page.revision_timestamp) or doc.modified_at
            )
            doc.source_type = "mediawiki_page"

        unchanged = (
            not is_new
            and reindex_changed_only
            and doc.checksum == checksum
            and doc.status == "indexed"
        )
        if unchanged:
            result.pages_skipped_unchanged += 1
            return

        # Build the in-memory LoadedDocument and feed it to the existing pipeline.
        loaded = LoadedDocument(
            file_path=Path(""),
            file_name=page.title,
            file_extension=".wiki",
            document_type="mediawiki_page",
            segments=[
                LoadedSegment(
                    text=normalised.text,
                    document_type="mediawiki_page",
                )
            ],
        )

        # Metadata payload — both attached to the Document row (for
        # auditing) and forwarded into the per-chunk Qdrant payload (so
        # retrieval can render click-through links).
        metadata = {
            "page_id": page.page_id,
            "revision_id": page.revision_id,
            "namespace_id": page.namespace_id,
            "namespace_name": namespace_map.get(page.namespace_id),
            "categories": normalised.categories,
            "linked_files": [
                {
                    "title": f.title,
                    "bare_filename": f.bare_filename,
                }
                for f in normalised.linked_files
            ],
            "wiki_base_url": wiki.base_url.rstrip("/"),
            "article_path": wiki.article_path,
            "script_path": wiki.script_path,
            "original_export_file": original_export_file,
            "page_url": page_url,
        }
        doc.source_metadata_json = json.dumps(metadata, ensure_ascii=False)

        # Persist the row before the indexing call so the Qdrant payload's
        # ``document_id`` actually exists in SQLite if anyone reads it later.
        db.commit()

        try:
            chunks_count = await self.ingestion._index_loaded_document(
                document=doc,
                loaded=loaded,
                checksum=checksum,
                payload_extras={
                    "url": page_url,
                    "page_id": page.page_id,
                    "revision_id": page.revision_id,
                    "namespace_id": page.namespace_id,
                },
            )
        except Exception:
            doc.status = "failed"
            db.commit()
            raise

        doc.checksum = checksum
        doc.chunks_count = chunks_count
        doc.status = "indexed" if chunks_count > 0 else "empty"
        doc.error_message = None
        db.commit()

        if chunks_count > 0:
            result.pages_indexed += 1

    # -----------------------------------------------------------------------

    async def _ingest_upload(
        self,
        db: Session,
        *,
        tenant: str,
        project: str,
        agg: "_UploadAggregate",
        resolved: ResolvedUpload,
        wiki: MediaWikiWikiConfig,
        reindex_changed_only: bool,
        result: ImportResult,
    ) -> None:
        assert resolved.resolved_path is not None
        file_path = resolved.resolved_path

        try:
            file_sha = await asyncio.to_thread(sha256_file, file_path)
        except OSError as exc:
            result.errors.append(f"upload '{agg.bare_filename}': hash failed: {exc}")
            return

        existing = db.execute(
            select(Document).where(
                Document.tenant == tenant,
                Document.project == project,
                Document.source_path == str(file_path),
            )
        ).scalar_one_or_none()

        is_new = existing is None
        if existing is None:
            doc = Document(
                id=new_id(),
                tenant=tenant,
                project=project,
                source_path=str(file_path),
                file_name=file_path.name,
                file_extension=(file_path.suffix or "").lower(),
                file_size=file_path.stat().st_size,
                checksum="",
                modified_at=datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc),
                status="pending",
                source_type="mediawiki_upload",
            )
            db.add(doc)
            db.flush()
        else:
            doc = existing
            doc.file_name = file_path.name
            doc.file_size = file_path.stat().st_size
            doc.modified_at = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
            doc.source_type = "mediawiki_upload"

        unchanged = (
            not is_new
            and reindex_changed_only
            and doc.checksum == file_sha
            and doc.status == "indexed"
        )
        if unchanged:
            result.files_skipped_unchanged += 1
            return

        file_page_url = _build_file_page_url(wiki, agg.title)
        metadata = {
            "mediawiki_file_title": agg.title,
            "bare_filename": agg.bare_filename,
            "referenced_by_pages": agg.referenced_by_pages,
            "page_url": file_page_url,
            "wiki_base_url": wiki.base_url.rstrip("/"),
        }
        doc.source_metadata_json = json.dumps(metadata, ensure_ascii=False)
        db.commit()

        try:
            chunks_count = await self.ingestion._index_one(
                file_path=file_path,
                document=doc,
                new_checksum=file_sha,
            )
        except Exception:
            doc.status = "failed"
            db.commit()
            raise

        doc.checksum = file_sha
        doc.chunks_count = chunks_count
        if doc.status not in {"requires_ocr", "empty"}:
            doc.status = "indexed" if chunks_count > 0 else "empty"
        doc.error_message = None
        db.commit()

        if chunks_count > 0:
            result.files_indexed += 1


@dataclass
class _UploadAggregate:
    """Per-bare-filename accumulator used during the page pass to remember
    which pages referenced each upload."""
    title: str
    bare_filename: str
    referenced_by_pages: List[int] = field(default_factory=list)
