"""Central application configuration loaded from environment / .env file."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    """Resolve the project root regardless of where uvicorn is started from."""
    return Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Strongly typed runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=str(_project_root() / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    # Per-request keep_alive — passed in /api/chat and /api/embeddings bodies.
    # Format: "5m", "1h", "24h", "-1" (forever), "0" (unload immediately).
    OLLAMA_KEEP_ALIVE: str = "1h"

    # Qdrant
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: Optional[str] = None
    QDRANT_COLLECTION: str = "documents"

    # Models
    EMBEDDING_MODEL: str = "mxbai-embed-large"
    CHAT_MODEL: str = "qwen2.5:14b"

    # Allowed base paths (comma-separated string in env)
    ALLOWED_BASE_PATHS: str = ""

    # SQLite
    SQLITE_DB_PATH: str = "./storage/rag.sqlite"

    # Chunking
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 150
    XLSX_ROWS_PER_CHUNK: int = 40
    # Hard cap on the character length of a single spreadsheet chunk.
    # Rough rule of thumb: 4 chars ≈ 1 token, so 6000 ≈ 1500 tokens — well
    # under the typical 2048/8192 embedder context limit even for tokenizers
    # that don't compact German text well.
    XLSX_MAX_CHARS_PER_CHUNK: int = 6000

    # Retrieval
    RETRIEVAL_TOP_K: int = 6
    MIN_RETRIEVAL_SCORE: float = 0.35

    # Reranker (cross-encoder, runs after the bi-encoder Qdrant search).
    # RERANK_ENABLED is a global killswitch: false = reranker is never
    # invoked, regardless of per-collection settings. Set true on the
    # appliance and let the smart default ("auto-enable when collection
    # has ≥ RERANK_AUTO_ENABLE_MIN_DOCS docs") plus per-collection
    # overrides decide who actually gets reranked.
    RERANK_ENABLED: bool = True
    RERANK_MODEL: str = "BAAI/bge-reranker-v2-m3"
    # Overfetch candidates from Qdrant for the reranker to reorder. Used
    # as a hard floor when the per-collection smart default would pick a
    # smaller value. Higher = more recall, more reranker work per query.
    RERANK_OVERFETCH_K: int = 20
    # Smart-default threshold: collections below this many indexed docs
    # don't auto-enable reranking (the lift is small, the latency cost
    # is the same). Admins can still flip it on per-collection.
    RERANK_AUTO_ENABLE_MIN_DOCS: int = 100

    # Generation
    CHAT_TEMPERATURE: float = 0.1
    CHAT_MAX_TOKENS: int = 1024
    # Number of past user/assistant turn-pairs to include from this session's
    # SQLite history before the current question. 0 = stateless (no memory).
    CHAT_HISTORY_TURNS: int = 4

    # LibreOffice binary
    SOFFICE_BIN: str = "soffice"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    @field_validator(
        "CHUNK_SIZE",
        "CHUNK_OVERLAP",
        "RETRIEVAL_TOP_K",
        "XLSX_ROWS_PER_CHUNK",
        "XLSX_MAX_CHARS_PER_CHUNK",
    )
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be a positive integer")
        return v

    @field_validator("CHAT_HISTORY_TURNS")
    @classmethod
    def _non_negative_int(cls, v: int) -> int:
        if v < 0:
            raise ValueError("must be ≥ 0")
        return v

    @field_validator("OLLAMA_KEEP_ALIVE")
    @classmethod
    def _ollama_keep_alive(cls, v: str) -> str:
        import re
        v = (v or "").strip()
        if not re.fullmatch(r"-1|0|\d+[smh]", v):
            raise ValueError(
                "OLLAMA_KEEP_ALIVE must look like '5m', '1h', '24h', '-1' (forever), or '0'"
            )
        return v

    @field_validator("MIN_RETRIEVAL_SCORE")
    @classmethod
    def _score_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("MIN_RETRIEVAL_SCORE must be in [0, 1]")
        return v

    # ---- Derived helpers --------------------------------------------------

    @property
    def allowed_base_paths(self) -> List[Path]:
        """Parse ALLOWED_BASE_PATHS into a list of absolute Paths."""
        raw = self.ALLOWED_BASE_PATHS or ""
        items = [p.strip() for p in raw.split(",") if p.strip()]
        resolved: List[Path] = []
        for item in items:
            try:
                resolved.append(Path(item).resolve(strict=False))
            except OSError:
                # Path may not exist yet — still keep it as a logical base
                resolved.append(Path(os.path.abspath(item)))
        return resolved

    @property
    def sqlite_path(self) -> Path:
        p = Path(self.SQLITE_DB_PATH)
        if not p.is_absolute():
            p = _project_root() / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def converted_dir(self) -> Path:
        p = _project_root() / "storage" / "converted"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def temp_dir(self) -> Path:
        p = _project_root() / "storage" / "temp"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def job_logs_dir(self) -> Path:
        """One file per ingest job, named ``<job_id>.log``.

        Used by the per-job logging capture so the admin UI can show the log
        tail next to the live progress bar and offer the full file as a
        download. Survives backend restarts (plain filesystem).
        """
        p = _project_root() / "storage" / "job-logs"
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
