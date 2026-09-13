from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _connect_args(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


settings = get_settings()
engine = create_engine(settings.database_url, echo=False, future=True, connect_args=_connect_args(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    from . import models  # noqa: F401

    get_settings().ensure_dirs()
    Base.metadata.create_all(engine)
    _ensure_schema_columns(engine)
    _repair_invalid_enums()


# Columns added after the 0.3.0 baseline. create_all only helps new databases;
# existing ones need an explicit ADD COLUMN. Plain String columns keep the
# migration to a simple ALTER TABLE (no enum CHECK rebuild).
_SCHEMA_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "publish_jobs": (
        ("error_category", "VARCHAR(64)"),
        ("request_key", "VARCHAR(64)"),
    ),
}


def _ensure_schema_columns(target_engine) -> None:
    if target_engine.url.get_backend_name().startswith("sqlite"):
        with target_engine.connect() as conn:
            for table, columns in _SCHEMA_COLUMNS.items():
                existing = {row[1] for row in conn.execute(__import__("sqlalchemy").text(f"PRAGMA table_info({table})")).fetchall()}
                if not existing:
                    continue
                for column_name, column_type in columns:
                    if column_name not in existing:
                        conn.execute(
                            __import__("sqlalchemy").text(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_type}")
                        )
            conn.commit()


def _repair_invalid_enums() -> None:
    from .models import ArticleStatus

    valid = {e.value for e in ArticleStatus}
    with engine.connect() as conn:
        rows = conn.execute(
            __import__("sqlalchemy").text("SELECT rowid, status FROM articles")
        ).fetchall()
        for rowid, status in rows:
            if status not in valid:
                conn.execute(
                    __import__("sqlalchemy").text(
                        "UPDATE articles SET status='created' WHERE rowid=:rid"
                    ),
                    {"rid": rowid},
                )
        conn.commit()


@contextmanager
def db_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
