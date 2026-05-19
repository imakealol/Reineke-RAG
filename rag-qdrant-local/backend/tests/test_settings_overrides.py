"""Regression: ``apply_overrides`` must work when the DB has rows.

A previous version iterated detached ORM instances after the session
closed, raising ``DetachedInstanceError`` and aborting FastAPI startup.
"""

import pytest

from app.config import settings
from app.database import init_db, session_scope
from app.models import SettingsOverride
from app.settings_overrides import apply_overrides, set_override


@pytest.fixture(autouse=True)
def _clean_overrides_table():
    init_db()
    with session_scope() as db:
        for row in list(db.query(SettingsOverride).all()):
            db.delete(row)
    yield
    with session_scope() as db:
        for row in list(db.query(SettingsOverride).all()):
            db.delete(row)


def test_apply_overrides_with_no_rows_is_noop():
    apply_overrides()  # must not raise


def test_apply_overrides_with_persisted_rows_does_not_detach():
    set_override("CHUNK_SIZE", "1234")
    set_override("CHAT_TEMPERATURE", "0.25")

    # Re-load from DB and apply — this is what FastAPI's lifespan does.
    apply_overrides()

    assert settings.CHUNK_SIZE == 1234
    assert settings.CHAT_TEMPERATURE == pytest.approx(0.25)


def test_apply_overrides_skips_unknown_keys():
    with session_scope() as db:
        db.add(SettingsOverride(key="REMOVED_KEY", value="x"))

    apply_overrides()  # must not raise; warning is logged
