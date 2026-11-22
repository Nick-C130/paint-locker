from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from . import config



def make_engine(url: str | None = None):
    url = url or f"sqlite:///{config.DB_PATH}"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return engine


engine = None
SessionLocal: sessionmaker[Session] | None = None


def init_engine(url: str | None = None):
    global engine, SessionLocal
    engine = make_engine(url)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return engine


def get_session() -> Iterator[Session]:
    assert SessionLocal is not None, "init_engine() not called"
    with SessionLocal() as session:
        yield session
