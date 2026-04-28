"""FastAPI entry point — wires up endpoints and dependencies."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from .admin import RequestLogMiddleware, api_router as admin_api_router, html_router as admin_html_router
from .chat_service import ChatService
from .config import settings
from .database import get_db, init_db
from .ingestion_service import IngestionService
from .models import Document
from .ollama_client import OllamaClient, OllamaError
from .openai_compat import (
    OpenAIRequestError,
    build_openai_response,
    resolve_openai_request,
)
from .scheduler_service import scheduler
from .settings_overrides import apply_overrides
from .path_security import PathSecurityError, assert_existing_dir, resolve_safe_path
from .qdrant_store import QdrantStore, QdrantStoreError
from .schemas import (
    ChatRequest,
    ChatResponse,
    DeleteDocumentResponse,
    DocumentListResponse,
    DocumentOut,
    HealthCheckItem,
    HealthResponse,
    IngestPathRequest,
    IngestPathResponse,
    OpenAIChatCompletionRequest,
    OpenAIChatCompletionResponse,
    OpenAIModelEntry,
    OpenAIModelList,
    ReindexChangedRequest,
    ScanPathRequest,
    ScanPathResponse,
)
from .source_scanner import scan_directory
from .utils import configure_logging, get_logger

log = get_logger("rag.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    init_db()
    apply_overrides()  # load runtime settings overrides from SQLite
    log.info("Backend starting on %s:%s", settings.HOST, settings.PORT)
    log.info("Allowed base paths: %s", [str(p) for p in settings.allowed_base_paths])
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()
        log.info("Backend shutting down.")


app = FastAPI(
    title="rag-qdrant-local",
    version="0.1.0",
    description="Local RAG over server-mounted documents (Ollama + Qdrant + FastAPI).",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLogMiddleware)

# --- Admin / operations console ---------------------------------------------
_ADMIN_STATIC = Path(__file__).parent / "admin" / "static"
app.mount("/admin/static", StaticFiles(directory=str(_ADMIN_STATIC)), name="admin-static")
app.include_router(admin_html_router)
app.include_router(admin_api_router)


# --- Silence noisy browser probes ------------------------------------------

from fastapi.responses import RedirectResponse, Response  # noqa: E402


@app.get("/", include_in_schema=False)
async def _root_redirect() -> RedirectResponse:
    return RedirectResponse("/admin/", status_code=307)


@app.get("/favicon.ico", include_in_schema=False)
async def _favicon() -> Response:
    return Response(status_code=204)


@app.get("/service-worker.js", include_in_schema=False)
async def _service_worker() -> Response:
    # Empty service worker — silences browser PWA probes without registering one.
    return Response(content="// noop\n", media_type="application/javascript")


# --- DI helpers --------------------------------------------------------------

def get_ingestion() -> IngestionService:
    return IngestionService()


def get_chat() -> ChatService:
    return ChatService()


# --- /health -----------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    backend = HealthCheckItem(ok=True, detail="alive")

    ollama = OllamaClient()
    try:
        ollama_ok = await ollama.ping()
        ollama_item = HealthCheckItem(
            ok=ollama_ok,
            detail=f"reachable at {settings.OLLAMA_BASE_URL}" if ollama_ok else "unreachable",
        )
    except Exception as exc:
        ollama_item = HealthCheckItem(ok=False, detail=str(exc))

    if ollama_item.ok:
        try:
            has_emb = await ollama.has_model(settings.EMBEDDING_MODEL)
            emb_item = HealthCheckItem(
                ok=has_emb,
                detail=settings.EMBEDDING_MODEL
                if has_emb
                else f"model '{settings.EMBEDDING_MODEL}' not pulled. Run: ollama pull {settings.EMBEDDING_MODEL}",
            )
        except Exception as exc:
            emb_item = HealthCheckItem(ok=False, detail=str(exc))

        try:
            has_chat = await ollama.has_model(settings.CHAT_MODEL)
            chat_item = HealthCheckItem(
                ok=has_chat,
                detail=settings.CHAT_MODEL
                if has_chat
                else f"model '{settings.CHAT_MODEL}' not pulled. Run: ollama pull {settings.CHAT_MODEL}",
            )
        except Exception as exc:
            chat_item = HealthCheckItem(ok=False, detail=str(exc))
    else:
        emb_item = HealthCheckItem(ok=False, detail="Ollama unreachable")
        chat_item = HealthCheckItem(ok=False, detail="Ollama unreachable")

    store = QdrantStore()
    try:
        qdrant_ok = await asyncio.to_thread(store.ping)
        qdrant_item = HealthCheckItem(
            ok=qdrant_ok,
            detail=f"reachable at {settings.QDRANT_URL}" if qdrant_ok else "unreachable",
        )
    except Exception as exc:
        qdrant_item = HealthCheckItem(ok=False, detail=str(exc))

    overall = all(
        item.ok for item in (backend, ollama_item, qdrant_item, emb_item, chat_item)
    )
    return HealthResponse(
        ok=overall,
        backend=backend,
        qdrant=qdrant_item,
        ollama=ollama_item,
        embedding_model=emb_item,
        chat_model=chat_item,
    )


# --- /sources ---------------------------------------------------------------

@app.post("/sources/scan-path", response_model=ScanPathResponse)
async def sources_scan_path(req: ScanPathRequest) -> ScanPathResponse:
    try:
        safe = resolve_safe_path(req.path)
        assert_existing_dir(safe)
    except PathSecurityError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    result = scan_directory(safe, recursive=req.recursive)
    return ScanPathResponse(
        path=str(safe),
        supported_files=len(result.supported),
        unsupported_files=len(result.unsupported),
        file_types=result.file_types,
        files=result.all_files,
    )


@app.post("/sources/ingest-path", response_model=IngestPathResponse)
async def sources_ingest_path(
    req: IngestPathRequest,
    db: Session = Depends(get_db),
    svc: IngestionService = Depends(get_ingestion),
) -> IngestPathResponse:
    try:
        return await svc.ingest_path(
            db,
            tenant=req.tenant,
            project=req.project,
            path=req.path,
            recursive=req.recursive,
            reindex_changed_only=req.reindex_changed_only,
        )
    except PathSecurityError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except QdrantStoreError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    except OllamaError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Ollama: {exc}")


# --- /documents -------------------------------------------------------------

@app.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    tenant: str = Query(..., min_length=1),
    project: str = Query(..., min_length=1),
    include_deleted: bool = Query(False),
    db: Session = Depends(get_db),
) -> DocumentListResponse:
    stmt = select(Document).where(Document.tenant == tenant, Document.project == project)
    if not include_deleted:
        stmt = stmt.where(Document.status != "deleted")
    docs: List[Document] = db.execute(stmt).scalars().all()
    return DocumentListResponse(
        documents=[DocumentOut.model_validate(d) for d in docs],
        total=len(docs),
    )


@app.post("/documents/reindex-changed", response_model=IngestPathResponse)
async def reindex_changed(
    req: ReindexChangedRequest,
    db: Session = Depends(get_db),
    svc: IngestionService = Depends(get_ingestion),
) -> IngestPathResponse:
    try:
        return await svc.reindex_changed(
            db,
            tenant=req.tenant,
            project=req.project,
            path=req.path,
            recursive=req.recursive,
            mark_missing_as_deleted=req.mark_missing_as_deleted,
        )
    except PathSecurityError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@app.delete("/documents/{document_id}", response_model=DeleteDocumentResponse)
async def delete_document(
    document_id: str,
    db: Session = Depends(get_db),
    svc: IngestionService = Depends(get_ingestion),
) -> DeleteDocumentResponse:
    deleted = await svc.delete_document(db, document_id=document_id)
    return DeleteDocumentResponse(
        document_id=document_id, deleted_points=deleted, status="deleted"
    )


# --- /chat ------------------------------------------------------------------

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    req: ChatRequest,
    svc: ChatService = Depends(get_chat),
) -> ChatResponse:
    # ChatService manages its own short-lived DB sessions — no Depends(get_db)
    # so we don't hold a connection open during the LLM generation.
    try:
        return await svc.chat(
            tenant=req.tenant,
            project=req.project,
            question=req.question,
            session_id=req.session_id,
            top_k=req.top_k,
        )
    except OllamaError as exc:
        log.exception("Chat failed (Ollama): %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Ollama: {exc}")
    except QdrantStoreError as exc:
        log.exception("Chat failed (Qdrant): %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    except ValueError as exc:
        log.warning("Chat rejected (ValueError): %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# --- /v1 (OpenAI-compatible — for OpenWebUI and similar clients) -----------

@app.get("/v1/models", response_model=OpenAIModelList)
async def openai_models(
    tenant: str = Query("", description="Optional tenant filter for the model id"),
    project: str = Query("", description="Optional project filter for the model id"),
    db: Session = Depends(get_db),
) -> OpenAIModelList:
    """Advertise one virtual ``rag:<tenant>:<project>`` model per known
    (tenant, project) tuple. OpenWebUI calls this to populate its model picker.
    If no documents are indexed yet, advertise a generic ``rag:default`` model
    so the user sees something."""
    import time as _t

    stmt = select(Document.tenant, Document.project).where(Document.status != "deleted")
    if tenant:
        stmt = stmt.where(Document.tenant == tenant)
    if project:
        stmt = stmt.where(Document.project == project)
    pairs = sorted({(t, p) for (t, p) in db.execute(stmt).all() if t and p})

    now = int(_t.time())
    if pairs:
        data = [
            OpenAIModelEntry(id=f"rag:{t}:{p}", created=now) for (t, p) in pairs
        ]
    else:
        data = [OpenAIModelEntry(id="rag:default", created=now)]
    return OpenAIModelList(data=data)


@app.post("/v1/chat/completions", response_model=OpenAIChatCompletionResponse)
async def openai_chat_completions(
    req: OpenAIChatCompletionRequest,
    svc: ChatService = Depends(get_chat),
) -> OpenAIChatCompletionResponse:
    try:
        resolved = resolve_openai_request(req)
    except OpenAIRequestError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    try:
        chat_resp = await svc.chat(
            tenant=resolved.tenant,
            project=resolved.project,
            question=resolved.question,
            session_id=resolved.session_id,
        )
    except OllamaError as exc:
        log.exception("OpenAI-compat chat failed (Ollama): %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Ollama: {exc}")
    except QdrantStoreError as exc:
        log.exception("OpenAI-compat chat failed (Qdrant): %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    except ValueError as exc:
        log.warning("OpenAI-compat chat rejected (ValueError): %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return build_openai_response(
        model=resolved.model,
        answer=chat_resp.answer,
        sources=chat_resp.sources,
        session_id=chat_resp.session_id,
    )


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
    )
