"""Pydantic schemas used by the FastAPI endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ----- Shared ----------------------------------------------------------------

class TenantProject(BaseModel):
    tenant: str = Field(min_length=1, max_length=128)
    project: str = Field(min_length=1, max_length=128)


class FileEntry(BaseModel):
    path: str
    file_name: str
    extension: str
    size_bytes: int
    modified_at: Optional[str] = None
    supported: bool


# ----- Health ----------------------------------------------------------------

class HealthCheckItem(BaseModel):
    ok: bool
    detail: Optional[str] = None


class HealthResponse(BaseModel):
    ok: bool
    backend: HealthCheckItem
    qdrant: HealthCheckItem
    ollama: HealthCheckItem
    embedding_model: HealthCheckItem
    chat_model: HealthCheckItem
    # Reranker is a *soft* dependency — disabled or not-yet-loaded both
    # report ok=true. ok=false only when enabled but actively broken.
    reranker: HealthCheckItem


# ----- Sources ---------------------------------------------------------------

class ScanPathRequest(TenantProject):
    path: str
    recursive: bool = True


class ScanPathResponse(BaseModel):
    path: str
    supported_files: int
    unsupported_files: int
    file_types: Dict[str, int]
    files: List[FileEntry]


class IngestPathRequest(TenantProject):
    path: str
    recursive: bool = True
    reindex_changed_only: bool = True
    # If set, only files whose extension (lowercased, including the leading
    # dot — e.g. ``".pdf"``) is in this list are ingested. ``None`` (default)
    # means: ingest every supported file type. An empty list means: ingest
    # nothing — useful to dry-run the wizard without writing.
    include_extensions: Optional[List[str]] = None


class IngestError(BaseModel):
    file: str
    error: str


class IngestPathResponse(BaseModel):
    job_id: str
    indexed_files: int
    skipped_unchanged: int
    failed_files: int
    chunks_created: int
    errors: List[IngestError] = Field(default_factory=list)


class ReindexChangedRequest(TenantProject):
    path: str
    recursive: bool = True
    mark_missing_as_deleted: bool = False


# ----- Ingest schedules ------------------------------------------------------

class IngestScheduleIn(TenantProject):
    base_path: str = Field(min_length=1)
    recursive: bool = True
    reindex_changed_only: bool = True
    hour: int = Field(ge=0, le=23, default=23)
    minute: int = Field(ge=0, le=59, default=59)
    enabled: bool = True


PERSONA_PROMPT_MAX_CHARS = 4000


class TenantProjectPromptIn(TenantProject):
    persona_prompt: str = Field(default="", max_length=PERSONA_PROMPT_MAX_CHARS)


class IngestScheduleOut(IngestScheduleIn):
    id: str
    last_run_at: Optional[datetime] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    last_indexed: int = 0
    last_skipped: int = 0
    last_failed: int = 0
    last_chunks: int = 0
    last_job_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ----- Documents -------------------------------------------------------------

class DocumentOut(BaseModel):
    id: str
    tenant: str
    project: str
    source_path: str
    file_name: str
    file_extension: str
    file_size: int
    checksum: str
    modified_at: Optional[datetime] = None
    status: str
    chunks_count: int
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    documents: List[DocumentOut]
    total: int


class DeleteDocumentResponse(BaseModel):
    document_id: str
    deleted_points: int
    status: str


# ----- Chat ------------------------------------------------------------------

class ChatRequest(TenantProject):
    question: str = Field(min_length=1)
    session_id: Optional[str] = None
    top_k: Optional[int] = None


class ChatSource(BaseModel):
    file_name: str
    document_id: str
    sheet: Optional[str] = None
    row_start: Optional[int] = None
    row_end: Optional[int] = None
    page: Optional[int] = None
    chunk_index: int
    score: float
    # Connector-friendly citation extras. ``filesystem`` documents leave
    # both empty; ``mediawiki_page`` / ``mediawiki_upload`` documents fill
    # ``url`` so the UI can render a click-through to the wiki.
    source_type: Optional[str] = None
    url: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    sources: List[ChatSource]
    session_id: str


# ----- Retrieve (LLM-less; used by the eval runner for fast iteration) ------

class HistoryTurn(BaseModel):
    """A single user-or-assistant turn passed to /retrieve so the
    query-rewriter can resolve pronoun follow-ups in eval scenarios. The
    main /chat path loads its history from SQLite by session_id; this
    field exists so the eval harness can simulate multi-turn flows
    without persisting state.
    """
    role: str = Field(min_length=1)  # 'user' or 'assistant'
    content: str = ""


class RetrieveRequest(TenantProject):
    question: str = Field(min_length=1)
    top_k: Optional[int] = None
    # Optional prior turns, oldest-first. Used by the query-rewriter for
    # follow-up resolution. Empty / None = treat as a fresh conversation.
    history: Optional[List[HistoryTurn]] = None


class RetrieveResponse(BaseModel):
    sources: List[ChatSource]


# ----- OpenAI-compatible /v1/chat/completions --------------------------------

class OpenAIMessage(BaseModel):
    role: str = Field(min_length=1)
    content: str = ""
    name: Optional[str] = None


class OpenAIChatCompletionRequest(BaseModel):
    """OpenAI-style chat completions payload.

    Tenant + project are resolved from one of:
      * ``model`` field  ⇒  ``rag:<tenant>:<project>``
      * ``extra_body``   ⇒  ``{"tenant": "...", "project": "..."}``
    """

    model: str
    messages: List[OpenAIMessage]
    temperature: Optional[float] = None
    stream: bool = False

    # OpenWebUI passes tenant/project either inline in the body or under
    # ``extra_body`` (depending on the SDK version).
    tenant: Optional[str] = None
    project: Optional[str] = None
    session_id: Optional[str] = None
    extra_body: Optional[Dict[str, Any]] = None

    # Tolerate any further OpenAI-style fields without erroring out.
    model_config = {"extra": "ignore"}


class OpenAIChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str


class OpenAIChoice(BaseModel):
    index: int = 0
    message: OpenAIChoiceMessage
    finish_reason: str = "stop"


class OpenAIUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class OpenAIChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[OpenAIChoice]
    usage: OpenAIUsage = Field(default_factory=OpenAIUsage)
    # Non-standard, but harmless: keep the structured citations alongside the
    # OpenAI response so OpenWebUI (or any other client) can render them.
    sources: List[ChatSource] = Field(default_factory=list)
    session_id: Optional[str] = None


class OpenAIModelEntry(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str = "rag-qdrant-local"


class OpenAIModelList(BaseModel):
    object: str = "list"
    data: List[OpenAIModelEntry]
