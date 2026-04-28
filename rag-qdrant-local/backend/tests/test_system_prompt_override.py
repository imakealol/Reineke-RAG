"""Global system prompt: store override → live → reset → default."""

import pytest

from app.chat_service import DEFAULT_SYSTEM_PROMPT, SYSTEM_PROMPT, ChatService
from app.database import init_db
from app.system_prompt_store import (
    SYSTEM_PROMPT_MAX_CHARS,
    clear_system_prompt,
    get_system_prompt,
    has_override,
    set_system_prompt,
)


@pytest.fixture(autouse=True)
def _ensure_schema():
    """Conftest's sandbox SQLite needs the schema in place."""
    init_db()
    # Start each test with a clean slate.
    clear_system_prompt()
    yield
    clear_system_prompt()


def test_default_when_no_override():
    assert get_system_prompt() == DEFAULT_SYSTEM_PROMPT
    assert SYSTEM_PROMPT == DEFAULT_SYSTEM_PROMPT  # constant alias unchanged
    assert has_override() is False


def test_set_and_reset_override():
    set_system_prompt("Du bist ein Test-Assistent.")
    assert has_override() is True
    assert get_system_prompt() == "Du bist ein Test-Assistent."

    clear_system_prompt()
    assert has_override() is False
    assert get_system_prompt() == DEFAULT_SYSTEM_PROMPT


def test_empty_set_clears_override():
    set_system_prompt("Etwas")
    assert has_override() is True
    set_system_prompt("   \n  ")  # whitespace-only acts as reset
    assert has_override() is False
    assert get_system_prompt() == DEFAULT_SYSTEM_PROMPT


def test_size_cap_enforced():
    with pytest.raises(ValueError, match="zu lang"):
        set_system_prompt("x" * (SYSTEM_PROMPT_MAX_CHARS + 1))


def test_compose_uses_passed_global_prompt():
    """ChatService._compose_system_prompt must honour an override-resolved
    prompt passed in via ``global_prompt``."""
    custom = "CUSTOM PROMPT"
    out = ChatService._compose_system_prompt(
        "t", "p", "Persona", global_prompt=custom
    )
    assert out.startswith(custom)
    assert "Persona" in out
    # Must not silently fall back to the default
    assert DEFAULT_SYSTEM_PROMPT not in out
