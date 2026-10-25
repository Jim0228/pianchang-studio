from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

engine = None
SessionLocal = sessionmaker(autoflush=False, autocommit=False, expire_on_commit=False)


def init_db(url: str, *, echo: bool = False) -> None:
    global engine
    if engine is not None:
        engine.dispose()
    engine = create_engine(
        url,
        echo=echo,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    SessionLocal.configure(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def media_root(data_dir: Path) -> Path:
    return data_dir
