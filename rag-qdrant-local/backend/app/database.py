"""SQLite engine, session factory, and schema bootstrap."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base


_BUSY_TIMEOUT_SECONDS = 30


def _build_engine() -> Engine:
    db_path = settings.sqlite_path
    url = f"sqlite:///{db_path}"
    engine = create_engine(
        url,
        future=True,
        echo=False,
        connect_args={
            "check_same_thread": False,
            # Wait up to N seconds for the lock to clear before raising
            # `database is locked`. Concurrent /chat requests that all need
            # to insert a new chat_session would otherwise collide.
            "timeout": _BUSY_TIMEOUT_SECONDS,
        },
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_con, _):  # type: ignore[no-untyped-def]
        cur = dbapi_con.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA foreign_keys=ON;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_SECONDS * 1000};")
        cur.close()

    return engine


engine: Engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """Create all tables if they do not exist yet."""
    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context-managed transactional session."""
    s: Session = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a session."""
    s: Session = SessionLocal()
    try:
        yield s
    finally:
        s.close()
