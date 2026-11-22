from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import backup, config, db, search, seed
from .migrate import upgrade_db
from .web import STATIC_DIR

log = logging.getLogger("paintlocker")


def catalogue_fingerprint() -> str:
    h = hashlib.sha256()
    for p in sorted(config.CATALOGUE_DIR.rglob("*.csv")):
        h.update(str(p.relative_to(config.CATALOGUE_DIR)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def startup(db_url: str | None = None) -> None:
    config.ensure_dirs()
    db.init_engine(db_url)
    upgrade_db(db.engine)
    with db.SessionLocal() as session:
        if config.SEED_ON_STARTUP:
            fp = catalogue_fingerprint()
            if seed.get_setting(session, "catalogue_fingerprint") != fp:
                stats = seed.import_catalogues(session)
                seed.set_setting(session, "catalogue_fingerprint", fp)
                log.info("catalogue import: %s", stats)
                search.rebuild(session)
        seed.ensure_formats(session)
        session.commit()


async def _backup_loop():
    while True:
        now = datetime.now()
        nxt = now.replace(hour=config.BACKUP_HOUR, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            await asyncio.to_thread(backup.nightly_backup)
        except Exception:  # pragma: no cover
            log.exception("nightly backup failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if db.engine is None:
        startup()
    task = asyncio.create_task(_backup_loop()) if config.BACKUPS_ENABLED else None
    yield
    if task:
        task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(title="Paint Locker", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    if config.PASSWORD:

        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            if request.url.path in ("/healthz",):
                return await call_next(request)
            header = request.headers.get("authorization", "")
            ok = False
            if header.lower().startswith("basic "):
                try:
                    _, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                    ok = secrets.compare_digest(pw, config.PASSWORD)
                except Exception:
                    ok = False
            if not ok:
                return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Paint Locker"'})
            return await call_next(request)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return PlainTextResponse("ok")

    @app.get("/sw.js", include_in_schema=False)
    def sw():
        return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")

    from .routers import admin, catalogue, inventory, mixes, notes, pages, storage

    for r in (pages, catalogue, inventory, storage, mixes, notes, admin):
        app.include_router(r.router)
    return app


app = create_app()
