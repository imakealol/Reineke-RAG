"""Runtime-editable overlay on top of ``settings``.

A fixed allow-list of fields can be edited from the admin UI. Edits are
persisted in the ``settings_overrides`` SQLite table and applied to the
live ``settings`` object both at startup and on every change.

Infrastructure / security keys (``ALLOWED_BASE_PATHS``, ``OLLAMA_BASE_URL``,
``QDRANT_*``, ``HOST``, ``PORT``, ``SQLITE_DB_PATH``, ``SOFFICE_BIN``) are
**not** editable — they belong in ``.env`` and require a service restart.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from .config import settings
from .database import session_scope
from .models import SettingsOverride
from .utils import configure_logging, get_logger

log = get_logger("rag.settings_overrides")


@dataclass(frozen=True)
class EditableKey:
    name: str
    type: str         # 'int' | 'float' | 'str' | 'enum'
    label: str
    help: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: Optional[List[str]] = None
    warning: Optional[str] = None  # surfaced in UI ("changing this requires …")


# Order matters — defines the order in the UI.
EDITABLE_KEYS: List[EditableKey] = [
    EditableKey(
        "EMBEDDING_MODEL", "str", "Embedding-Modell",
        help="Name eines in Ollama installierten Embedding-Modells.",
        warning="Modellwechsel erzeugt Vektoren anderer Dimension — bestehende "
                "Qdrant-Collection muss neu angelegt und alle Dokumente neu "
                "ingested werden.",
    ),
    EditableKey(
        "CHAT_MODEL", "str", "Chat-Modell",
        help="Name eines in Ollama installierten Chat-Modells.",
    ),
    EditableKey(
        "CHUNK_SIZE", "int", "Chunk-Größe (Zeichen)",
        help="Maximale Zeichenzahl pro Chunk für PDF/DOCX/DOC.",
        minimum=200, maximum=8000,
    ),
    EditableKey(
        "CHUNK_OVERLAP", "int", "Chunk-Overlap (Zeichen)",
        help="Überlappung zwischen aufeinanderfolgenden Chunks.",
        minimum=0, maximum=2000,
    ),
    EditableKey(
        "XLSX_ROWS_PER_CHUNK", "int", "XLSX-Zeilen pro Chunk",
        help="Wieviele Tabellenzeilen kommen in einen Chunk.",
        minimum=5, maximum=200,
    ),
    EditableKey(
        "XLSX_MAX_CHARS_PER_CHUNK", "int", "XLSX max. Zeichen pro Chunk",
        help="Hard-Cap, schützt das Embedding-Kontextfenster.",
        minimum=500, maximum=20000,
    ),
    EditableKey(
        "RETRIEVAL_TOP_K", "int", "Retrieval Top-K",
        help="Wieviele Chunks werden pro Frage aus Qdrant geholt.",
        minimum=1, maximum=30,
    ),
    EditableKey(
        "MIN_RETRIEVAL_SCORE", "float", "Min. Score-Schwelle",
        help="Treffer unterhalb dieses Cosine-Scores werden verworfen.",
        minimum=0.0, maximum=1.0,
    ),
    EditableKey(
        "CHAT_TEMPERATURE", "float", "Chat-Temperatur",
        help="0 = deterministisch / extrahierend, höher = kreativer.",
        minimum=0.0, maximum=2.0,
    ),
    EditableKey(
        "CHAT_MAX_TOKENS", "int", "Chat max. Tokens",
        help="Maximale Antwortlänge des Chat-Modells.",
        minimum=64, maximum=8192,
    ),
    EditableKey(
        "CHAT_HISTORY_TURNS", "int", "Chat-Historie (Turn-Paare)",
        help="Wieviele frühere Frage/Antwort-Paare aus dieser Session werden "
             "dem Chat-Modell mitgegeben. 0 = stateless.",
        minimum=0, maximum=20,
    ),
    EditableKey(
        "LOG_LEVEL", "enum", "Log-Level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    ),
]

_KEYS_BY_NAME: Dict[str, EditableKey] = {k.name: k for k in EDITABLE_KEYS}


# ---------------------------------------------------------------------------
# Coercion / validation
# ---------------------------------------------------------------------------

def _coerce(meta: EditableKey, raw: str) -> Any:
    raw = raw.strip()
    if meta.type == "int":
        try:
            v = int(raw)
        except ValueError as exc:
            raise ValueError(f"{meta.label}: ganze Zahl erwartet.") from exc
        if meta.minimum is not None and v < meta.minimum:
            raise ValueError(f"{meta.label}: muss ≥ {int(meta.minimum)} sein.")
        if meta.maximum is not None and v > meta.maximum:
            raise ValueError(f"{meta.label}: muss ≤ {int(meta.maximum)} sein.")
        return v
    if meta.type == "float":
        try:
            v_f = float(raw.replace(",", "."))
        except ValueError as exc:
            raise ValueError(f"{meta.label}: Zahl erwartet.") from exc
        if meta.minimum is not None and v_f < meta.minimum:
            raise ValueError(f"{meta.label}: muss ≥ {meta.minimum} sein.")
        if meta.maximum is not None and v_f > meta.maximum:
            raise ValueError(f"{meta.label}: muss ≤ {meta.maximum} sein.")
        return v_f
    if meta.type == "enum":
        if meta.choices and raw not in meta.choices:
            raise ValueError(f"{meta.label}: erlaubt sind {meta.choices}.")
        return raw
    # str
    if not raw:
        raise ValueError(f"{meta.label}: darf nicht leer sein.")
    return raw


# ---------------------------------------------------------------------------
# Persistence / application
# ---------------------------------------------------------------------------

def _apply_to_settings(key: str, value: Any) -> None:
    """Mutate the live settings object and trigger side-effects."""
    setattr(settings, key, value)
    if key == "LOG_LEVEL":
        # Re-configure root logger so the change is visible immediately.
        configure_logging()


def apply_overrides() -> None:
    """Load all overrides from the DB and apply them to the live settings."""
    with session_scope() as db:
        rows = list(db.execute(select(SettingsOverride)).scalars().all())
    for r in rows:
        meta = _KEYS_BY_NAME.get(r.key)
        if meta is None:
            log.warning("Ignoring override for unknown key %s", r.key)
            continue
        try:
            value = _coerce(meta, r.value)
            _apply_to_settings(r.key, value)
            log.info("Applied override %s = %r", r.key, value)
        except Exception as exc:
            log.warning("Could not apply override %s = %r (%s)", r.key, r.value, exc)


def set_override(key: str, raw_value: str) -> Any:
    meta = _KEYS_BY_NAME.get(key)
    if meta is None:
        raise ValueError(f"'{key}' ist nicht editierbar.")
    value = _coerce(meta, raw_value)

    with session_scope() as db:
        existing = db.get(SettingsOverride, key)
        if existing is None:
            db.add(SettingsOverride(key=key, value=str(raw_value).strip()))
        else:
            existing.value = str(raw_value).strip()

    _apply_to_settings(key, value)
    return value


def clear_override(key: str) -> None:
    """Remove the override and revert to the env default."""
    if key not in _KEYS_BY_NAME:
        raise ValueError(f"'{key}' ist nicht editierbar.")
    with session_scope() as db:
        existing = db.get(SettingsOverride, key)
        if existing is not None:
            db.delete(existing)

    # Revert to the value that was loaded from .env at startup. We re-read
    # the env-defined value via a fresh Settings() instance.
    from .config import Settings as _Settings
    fresh = _Settings()
    _apply_to_settings(key, getattr(fresh, key))


def has_override(key: str) -> bool:
    with session_scope() as db:
        return db.get(SettingsOverride, key) is not None


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def overlay_view() -> List[Dict[str, Any]]:
    """Build a UI-friendly description of all editable keys + their values."""
    overridden = set()
    with session_scope() as db:
        overridden = {
            r.key for r in db.execute(select(SettingsOverride)).scalars().all()
        }
    out: List[Dict[str, Any]] = []
    for meta in EDITABLE_KEYS:
        out.append(
            {
                "key": meta.name,
                "label": meta.label,
                "type": meta.type,
                "help": meta.help,
                "warning": meta.warning,
                "minimum": meta.minimum,
                "maximum": meta.maximum,
                "choices": meta.choices,
                "value": getattr(settings, meta.name),
                "overridden": meta.name in overridden,
            }
        )
    return out
