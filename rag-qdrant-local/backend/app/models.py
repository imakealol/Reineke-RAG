"""SQLAlchemy ORM models for the metadata store."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class FileSource(Base):
    """A configured root path that has been scanned at least once."""

    __tablename__ = "file_sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(128), index=True)
    project: Mapped[str] = mapped_column(String(128), index=True)
    base_path: Mapped[str] = mapped_column(Text)
    recursive: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_scan_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ingest_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_file_sources_tenant_project", "tenant", "project"),
    )


class Document(Base):
    """A single file that has been (or is being) indexed."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(128), index=True)
    project: Mapped[str] = mapped_column(String(128), index=True)
    source_path: Mapped[str] = mapped_column(Text, index=True)
    file_name: Mapped[str] = mapped_column(String(512))
    file_extension: Mapped[str] = mapped_column(String(16))
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    modified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    chunks_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        Index("ix_documents_tenant_project_path", "tenant", "project", "source_path", unique=True),
    )


class IngestionJob(Base):
    """Audit record for one ingest run."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(128), index=True)
    project: Mapped[str] = mapped_column(String(128), index=True)
    source_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="running")
    files_found: Mapped[int] = mapped_column(Integer, default=0)
    files_indexed: Mapped[int] = mapped_column(Integer, default=0)
    files_skipped: Mapped[int] = mapped_column(Integer, default=0)
    files_failed: Mapped[int] = mapped_column(Integer, default=0)
    chunks_created: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ChatSession(Base):
    """Conversation grouping for a tenant/project."""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(128), index=True)
    project: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | system
    content: Mapped[str] = mapped_column(Text)
    sources_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class RequestLog(Base):
    """One row per HTTP request handled by FastAPI — feeds the admin audit tab."""

    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(512), index=True)
    query_string: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    tenant: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    project: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    client_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_request_logs_created_status", "created_at", "status_code"),
    )


class IngestSchedule(Base):
    """Recurring auto-ingest schedule. One row = one cron-like trigger."""

    __tablename__ = "ingest_schedules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(128), index=True)
    project: Mapped[str] = mapped_column(String(128), index=True)
    base_path: Mapped[str] = mapped_column(Text)
    recursive: Mapped[bool] = mapped_column(Boolean, default=True)
    reindex_changed_only: Mapped[bool] = mapped_column(Boolean, default=True)

    # Trigger — daily HH:MM in local time. Cron-style fields kept open for
    # later expansion (weekday filter, etc.) but for the MVP we run a daily
    # job and just store the time-of-day.
    hour: Mapped[int] = mapped_column(Integer, default=23)
    minute: Mapped[int] = mapped_column(Integer, default=59)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_indexed: Mapped[int] = mapped_column(Integer, default=0)
    last_skipped: Mapped[int] = mapped_column(Integer, default=0)
    last_failed: Mapped[int] = mapped_column(Integer, default=0)
    last_chunks: Mapped[int] = mapped_column(Integer, default=0)
    last_job_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        Index("ix_ingest_schedules_tenant_project", "tenant", "project"),
    )


class TenantProjectPrompt(Base):
    """Persona / domain prompt that gets appended to the global system
    prompt for one ``(tenant, project)`` collection.

    Composite primary key on ``(tenant, project)`` — exactly one persona
    per collection. Empty / missing rows fall back to the global prompt
    only.
    """

    __tablename__ = "tenant_project_prompts"

    tenant: Mapped[str] = mapped_column(String(128), primary_key=True)
    project: Mapped[str] = mapped_column(String(128), primary_key=True)
    persona_prompt: Mapped[str] = mapped_column(Text, default="")
    # Per-agent override of the chat model. Empty / NULL → fall back to
    # ``settings.CHAT_MODEL``. Lets each agent run on a different model
    # (e.g. small fast one for FAQ, big high-quality one for legal review).
    chat_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class SystemPromptOverride(Base):
    """Single-row override for the global system prompt.

    PK is the fixed string ``'global'`` — at most one row exists. If the row
    is missing, the code falls back to the default ``SYSTEM_PROMPT`` constant
    in ``chat_service.py``.
    """

    __tablename__ = "system_prompt_overrides"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default="global")
    prompt: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class SettingsOverride(Base):
    """Runtime override for a Settings field, edited from the admin UI.

    Overrides take precedence over `.env` / environment variables and are
    applied to the live ``settings`` object at startup and on every change.
    """

    __tablename__ = "settings_overrides"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
