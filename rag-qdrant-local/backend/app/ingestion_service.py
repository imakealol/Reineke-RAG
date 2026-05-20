"""Coordinate scanning → loading → chunking → embedding → storing."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from .chunker import Chunk, chunk_document
from .config import settings
from .document_loader import (
    DocumentLoadError,
    LoadedDocument,
    RequiresOCRError,
    load_document,
)
from .models import Document, FileSource, IngestionJob
from .ollama_client import OllamaClient, OllamaError
from .path_security import (
    PathSecurityError,
    assert_existing_dir,
    resolve_safe_path,
)
from .qdrant_store import QdrantStore
from .schemas import IngestError, IngestPathResponse
from .source_scanner import scan_directory
from .utils import (
    capture_logs_for_job,
    file_modified_iso,
    get_logger,
    new_id,
    sha256_file,
    utcnow_iso,
)

log = get_logger(__name__)


class IngestionService:
    def __init__(
        self,
        ollama: Optional[OllamaClient] = None,
        store: Optional[QdrantStore] = None,
    ) -> None:
        self.ollama = ollama or OllamaClient()
        self.store = store or QdrantStore()

    # -----------------------------------------------------------------------

    async def _ensure_collection_for_model(self) -> None:
        """Probe the embedding model to learn its dimension and ensure Qdrant
        has a matching collection."""
        probe = await self.ollama.embed("dimension probe")
        dim = len(probe)
        # Qdrant client is sync — run in thread to keep the event loop free
        await asyncio.to_thread(self.store.ensure_collection, dim)

    # -----------------------------------------------------------------------

    def _upsert_file_source(
        self, db: Session, *, tenant: str, project: str, base_path: str, recursive: bool
    ) -> FileSource:
        existing = db.execute(
            select(FileSource).where(
                FileSource.tenant == tenant,
                FileSource.project == project,
                FileSource.base_path == base_path,
            )
        ).scalar_one_or_none()
        if existing:
            existing.recursive = recursive
            existing.last_scan_at = datetime.now(timezone.utc)
            return existing
        fs = FileSource(
            id=new_id(),
            tenant=tenant,
            project=project,
            base_path=base_path,
            recursive=recursive,
            last_scan_at=datetime.now(timezone.utc),
        )
        db.add(fs)
        return fs

    def _find_or_create_document(
        self,
        db: Session,
        *,
        tenant: str,
        project: str,
        path: Path,
        checksum: str,
    ) -> Tuple[Document, bool]:
        """Return (document, is_new). Updates existing doc with the latest checksum."""
        doc = db.execute(
            select(Document).where(
                Document.tenant == tenant,
                Document.project == project,
                Document.source_path == str(path),
            )
        ).scalar_one_or_none()

        is_new = doc is None
        if doc is None:
            doc = Document(
                id=new_id(),
                tenant=tenant,
                project=project,
                source_path=str(path),
                file_name=path.name,
                file_extension=path.suffix.lower(),
                file_size=path.stat().st_size,
                checksum=checksum,
                modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
                status="pending",
            )
            db.add(doc)
        else:
            doc.file_name = path.name
            doc.file_extension = path.suffix.lower()
            doc.file_size = path.stat().st_size
            doc.modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return doc, is_new

    # -----------------------------------------------------------------------

    @staticmethod
    def _update_job_progress(
        db: Session,
        job: IngestionJob,
        *,
        indexed: int,
        skipped: int,
        failed: int,
        chunks_total: int,
    ) -> None:
        """Commit running counters to the job row so the live progress bar
        sees them while the ingest is still in flight. Called once per file."""
        job.files_indexed = indexed
        job.files_skipped = skipped
        job.files_failed = failed
        job.chunks_created = chunks_total
        db.commit()

    # -----------------------------------------------------------------------

    async def ingest_path(
        self,
        db: Session,
        *,
        tenant: str,
        project: str,
        path: str,
        recursive: bool = True,
        reindex_changed_only: bool = True,
        include_extensions: Optional[List[str]] = None,
        job_id: Optional[str] = None,
    ) -> IngestPathResponse:
        """Run the full ingest pipeline.

        When called via the admin wizard, the endpoint pre-creates the
        :class:`IngestionJob` row so it can return a job id immediately for
        the live progress bar; in that case ``job_id`` is passed in. When
        called via the public ``/sources/ingest-path`` API, ``job_id`` is
        ``None`` and we create a fresh job here ourselves.
        """
        # --- security ------------------------------------------------------
        try:
            safe = resolve_safe_path(path)
            assert_existing_dir(safe)
        except PathSecurityError:
            raise

        # --- collection ----------------------------------------------------
        await self._ensure_collection_for_model()

        # --- track job -----------------------------------------------------
        if job_id is not None:
            job = db.execute(
                select(IngestionJob).where(IngestionJob.id == job_id)
            ).scalar_one()
        else:
            job = IngestionJob(
                id=new_id(),
                tenant=tenant,
                project=project,
                source_path=str(safe),
                status="running",
            )
            db.add(job)
        self._upsert_file_source(
            db, tenant=tenant, project=project, base_path=str(safe), recursive=recursive
        )
        db.commit()

        # --- scan ----------------------------------------------------------
        scan = scan_directory(safe, recursive=recursive)

        # --- optional include-extensions filter ---------------------------
        # When the wizard ships an explicit whitelist (one checkbox per type
        # found in the scan), narrow the work-set here so file counts, the
        # job row and the response all reflect what the user asked for.
        if include_extensions is not None:
            scan = scan.filter_to_extensions(include_extensions)

        job.files_found = len(scan.supported)
        db.commit()

        indexed = 0
        skipped = 0
        failed = 0
        chunks_total = 0
        errors: List[IngestError] = []

        for entry in scan.supported:
            file_path = Path(entry.path)

            # Tell the progress bar what we're about to work on. Commit here
            # so the polling endpoint sees the new filename even if the file
            # turns out to be slow (large XLSX with many embedding calls).
            job.current_file = entry.file_name
            db.commit()

            try:
                checksum = sha256_file(file_path)
            except OSError as exc:
                failed += 1
                errors.append(IngestError(file=str(file_path), error=f"hash failed: {exc}"))
                self._update_job_progress(
                    db, job,
                    indexed=indexed, skipped=skipped, failed=failed,
                    chunks_total=chunks_total,
                )
                continue

            doc, is_new = self._find_or_create_document(
                db,
                tenant=tenant,
                project=project,
                path=file_path,
                checksum=checksum,
            )

            unchanged = (
                not is_new
                and doc.checksum == checksum
                and doc.status == "indexed"
            )
            if unchanged and reindex_changed_only:
                skipped += 1
                db.commit()
                self._update_job_progress(
                    db, job,
                    indexed=indexed, skipped=skipped, failed=failed,
                    chunks_total=chunks_total,
                )
                continue

            # --- (Re)index ---------------------------------------------
            try:
                count = await self._index_one(
                    file_path=file_path,
                    document=doc,
                    new_checksum=checksum,
                )
            except Exception as exc:
                log.exception("Failed to ingest %s", file_path)
                doc.status = "failed"
                doc.error_message = str(exc)
                db.commit()
                failed += 1
                errors.append(IngestError(file=str(file_path), error=str(exc)))
                self._update_job_progress(
                    db, job,
                    indexed=indexed, skipped=skipped, failed=failed,
                    chunks_total=chunks_total,
                )
                continue

            doc.checksum = checksum
            doc.chunks_count = count
            doc.status = "indexed"
            doc.error_message = None
            db.commit()

            chunks_total += count
            indexed += 1

            self._update_job_progress(
                db, job,
                indexed=indexed, skipped=skipped, failed=failed,
                chunks_total=chunks_total,
            )

        # --- finalise job --------------------------------------------------
        job.files_indexed = indexed
        job.files_skipped = skipped
        job.files_failed = failed
        job.chunks_created = chunks_total
        # Clear the current_file marker — the UI uses None to know the run
        # has reached a terminal state.
        job.current_file = None
        job.status = "completed" if failed == 0 else "completed_with_errors"
        job.completed_at = datetime.now(timezone.utc)
        # Update file source ingest timestamp
        fs = db.execute(
            select(FileSource).where(
                FileSource.tenant == tenant,
                FileSource.project == project,
                FileSource.base_path == str(safe),
            )
        ).scalar_one_or_none()
        if fs is not None:
            fs.last_ingest_at = datetime.now(timezone.utc)
        db.commit()

        return IngestPathResponse(
            job_id=job.id,
            indexed_files=indexed,
            skipped_unchanged=skipped,
            failed_files=failed,
            chunks_created=chunks_total,
            errors=errors,
        )

    # -----------------------------------------------------------------------

    async def _index_one(
        self,
        *,
        file_path: Path,
        document: Document,
        new_checksum: str,
    ) -> int:
        """File-path entry point: load → delegate to ``_index_loaded_document``.

        Kept thin so connectors that already hold a ``LoadedDocument`` in
        memory (MediaWiki, future Confluence, …) can call the lower-level
        primitive directly without round-tripping through a temp file.
        """
        try:
            loaded: LoadedDocument = await asyncio.to_thread(load_document, file_path)
        except RequiresOCRError as exc:
            # Wipe any prior points for this document so a previously-indexed
            # version can't survive a status change to ``requires_ocr``.
            await asyncio.to_thread(self.store.delete_document, document.id)
            document.status = "requires_ocr"
            document.error_message = str(exc)
            return 0

        return await self._index_loaded_document(
            document=document,
            loaded=loaded,
            checksum=new_checksum,
        )

    async def _index_loaded_document(
        self,
        *,
        document: Document,
        loaded: LoadedDocument,
        checksum: str,
        payload_extras: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Chunk → embed → upsert for a ``LoadedDocument`` already in memory.

        ``payload_extras`` is merged into every per-chunk payload so connectors
        can attach citation fields (``url``, …) without the chunking or
        embedding code knowing about them.
        """
        # Always wipe previous points for this document_id so re-indexing
        # cannot leave orphaned vectors behind.
        await asyncio.to_thread(self.store.delete_document, document.id)

        chunks: List[Chunk] = chunk_document(loaded)
        if not chunks:
            document.status = "empty"
            document.error_message = "No extractable content."
            return 0

        try:
            vectors = await self.ollama.embed_many([c.text for c in chunks])
        except OllamaError:
            raise

        payloads = self._build_payloads(
            document=document,
            chunks=chunks,
            checksum=checksum,
            payload_extras=payload_extras,
        )

        await asyncio.to_thread(
            self.store.upsert_chunks,
            document_id=document.id,
            vectors=vectors,
            payloads=payloads,
        )
        return len(chunks)

    # -----------------------------------------------------------------------

    @staticmethod
    def _build_payloads(
        *,
        document: Document,
        chunks: List[Chunk],
        checksum: str,
        payload_extras: Optional[Dict[str, Any]] = None,
    ) -> List[Dict]:
        modified_at = (
            document.modified_at.isoformat()
            if document.modified_at is not None
            else utcnow_iso()
        )
        created_at = utcnow_iso()
        out = []
        for c in chunks:
            payload: Dict[str, Any] = {
                "text": c.text,
                "tenant": document.tenant,
                "project": document.project,
                "document_id": document.id,
                "file_name": document.file_name,
                "source_path": document.source_path,
                "file_extension": document.file_extension,
                "document_type": c.document_type,
                "page": c.page,
                "sheet": c.sheet,
                "row_start": c.row_start,
                "row_end": c.row_end,
                "chunk_index": c.chunk_index,
                "checksum": checksum,
                "modified_at": modified_at,
                "created_at": created_at,
                # ``source_type`` is always part of the payload so retrieval
                # citations can label rows without an extra SQLite lookup.
                "source_type": document.source_type,
            }
            if payload_extras:
                payload.update(payload_extras)
            out.append(payload)
        return out

    # -----------------------------------------------------------------------

    async def reindex_changed(
        self,
        db: Session,
        *,
        tenant: str,
        project: str,
        path: str,
        recursive: bool = True,
        mark_missing_as_deleted: bool = False,
    ) -> IngestPathResponse:
        """Same as ingest but never re-embeds unchanged files; optionally marks
        files that disappeared from disk as ``deleted``."""
        result = await self.ingest_path(
            db,
            tenant=tenant,
            project=project,
            path=path,
            recursive=recursive,
            reindex_changed_only=True,
        )

        if mark_missing_as_deleted:
            safe = resolve_safe_path(path)
            existing_docs: List[Document] = (
                db.execute(
                    select(Document).where(
                        Document.tenant == tenant,
                        Document.project == project,
                        Document.status != "deleted",
                    )
                )
                .scalars()
                .all()
            )
            for doc in existing_docs:
                src = Path(doc.source_path)
                if not str(src).startswith(str(safe)):
                    continue
                if not src.exists():
                    await asyncio.to_thread(self.store.delete_document, doc.id)
                    doc.status = "deleted"
                    doc.error_message = "File no longer exists on disk."
            db.commit()

        return result

    # -----------------------------------------------------------------------

    async def delete_document(self, db: Session, *, document_id: str) -> int:
        doc = db.get(Document, document_id)
        if doc is None:
            return 0
        deleted = await asyncio.to_thread(self.store.delete_document, document_id)
        doc.status = "deleted"
        db.commit()
        return deleted
