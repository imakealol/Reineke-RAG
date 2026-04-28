"""Storage helpers for the global system prompt override.

The default prompt lives in ``chat_service.SYSTEM_PROMPT``. Admins can
override it from the UI; the override is stored in a single-row SQLite
table and applied at runtime by :func:`get_system_prompt`.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from .database import session_scope
from .models import SystemPromptOverride

# Hard cap — keeps prompt overhead bounded. ~2000 tokens.
SYSTEM_PROMPT_MAX_CHARS = 8000

_OVERRIDE_PK = "global"


def get_system_prompt() -> str:
    """Return the active system prompt — DB override if set, else default."""
    # Local import avoids circular import with chat_service (which imports us).
    from .chat_service import DEFAULT_SYSTEM_PROMPT

    with session_scope() as db:
        row: Optional[SystemPromptOverride] = db.get(SystemPromptOverride, _OVERRIDE_PK)
        if row is not None and (row.prompt or "").strip():
            return row.prompt
    return DEFAULT_SYSTEM_PROMPT


def set_system_prompt(text: str) -> None:
    """Persist a custom system prompt. Empty / whitespace-only text resets."""
    text = (text or "").strip()
    if len(text) > SYSTEM_PROMPT_MAX_CHARS:
        raise ValueError(
            f"System-Prompt zu lang ({len(text)} Zeichen, max "
            f"{SYSTEM_PROMPT_MAX_CHARS})."
        )
    with session_scope() as db:
        row = db.get(SystemPromptOverride, _OVERRIDE_PK)
        if not text:
            if row is not None:
                db.delete(row)
            return
        if row is None:
            db.add(SystemPromptOverride(id=_OVERRIDE_PK, prompt=text))
        else:
            row.prompt = text


def clear_system_prompt() -> None:
    """Delete the override and revert to the in-code default."""
    with session_scope() as db:
        row = db.get(SystemPromptOverride, _OVERRIDE_PK)
        if row is not None:
            db.delete(row)


def has_override() -> bool:
    with session_scope() as db:
        return db.get(SystemPromptOverride, _OVERRIDE_PK) is not None
