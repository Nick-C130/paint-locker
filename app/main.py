from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
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


def _check_writable(path) -> None:
    probe = path / ".write-test"
    try:
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        log.error(
            "Can't write to the data folder %s (%s). Running as uid %s. Fix the folder's owner/permissions on the "
            "host, or set PUID/PGID to a user that can write it.", path, e, os.getuid() if hasattr(os, "getuid") else "?"
        )
        raise


def startup(db_url: str | None = None) -> None:
    t0 = time.monotonic()
    log.info("Data folder: %s (database %s)", config.DATA_DIR, config.DB_PATH.name)
    log.info("Password protection: %s · nightly backups: %s · catalogue import on start: %s",
             "ON" if config.PASSWORD else "off",
             f"on at {config.BACKUP_HOUR:02d}:00, keeping {config.BACKUP_KEEP}" if config.BACKUPS_ENABLED else "off",
             "on" if config.SEED_ON_STARTUP else "off")
    config.ensure_dirs()
    _check_writable(config.DATA_DIR)
    db.init_engine(db_url)
    from alembic.migration import MigrationContext

    with db.engine.connect() as conn:
        before = MigrationContext.configure(conn).get_current_revision()
    upgrade_db(db.engine)
    with db.engine.connect() as conn:
        after = MigrationContext.configure(conn).get_current_revision()
    if before != after:
        log.info("Database migrated: %s -> %s", before or "new database", after)
    else:
        log.info("Database schema up to date (revision %s)", after)
    with db.SessionLocal() as session:
        if config.SEED_ON_STARTUP:
            fp = catalogue_fingerprint()
            if seed.get_setting(session, "catalogue_fingerprint") != fp:
                log.info("Catalogue CSVs changed (or first start): importing paints, this takes a few seconds...")
                stats = seed.import_catalogues(session)
                seed.set_setting(session, "catalogue_fingerprint", fp)
                log.info("Catalogue import: %s", stats)
                search.rebuild(session)
                seed.set_setting(session, "search_index_version", search.INDEX_VERSION)
        if seed.get_setting(session, "search_index_version") != search.INDEX_VERSION:
            search.rebuild(session)
            seed.set_setting(session, "search_index_version", search.INDEX_VERSION)
        seed.ensure_formats(session)
        seed.ensure_shops(session)
        session.commit()
        from .models import InventoryItem, Paint

        log.info("Ready in %.1fs: %d paints in the catalogue, %d bottles", time.monotonic() - t0,
                 session.query(Paint).count(), session.query(InventoryItem).count())


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
    app = FastAPI(title="Paint Locker", lifespan=lifespan, version=config.VERSION)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    if config.PASSWORD:

        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            if request.url.path in ("/healthz", "/health"):
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

    # Added last so it wraps everything, including the password check (401s are logged too).
    req_log = logging.getLogger("paintlocker.request")
    quiet = ("/static/", "/health", "/healthz", "/sw.js")

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        t0 = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            req_log.exception("%s %s failed", request.method, request.url.path)
            raise
        ms = (time.monotonic() - t0) * 1000
        client = request.client.host if request.client else "-"
        lvl = logging.DEBUG if request.url.path.startswith(quiet) and response.status_code < 400 else logging.INFO
        if response.status_code >= 500:
            lvl = logging.ERROR
        req_log.log(lvl, "%s %s %s %d %.0fms", client, request.method, request.url.path, response.status_code, ms)
        if req_log.isEnabledFor(logging.DEBUG) and request.url.query:
            req_log.debug("  query: %s", request.url.query)
        return response

    started = time.time()

    @app.get("/health", include_in_schema=False)
    def health():
        """For Docker's HEALTHCHECK and for you: is the app up and can it read the database?"""
        from sqlalchemy import text as sql

        info = {"status": "ok", "version": config.VERSION, "uptime_s": round(time.time() - started), "data_dir": str(config.DATA_DIR)}
        try:
            with db.engine.connect() as conn:
                conn.execute(sql("SELECT 1"))
            info["database"] = "ok"
        except Exception as e:  # noqa: BLE001
            info.update(status="error", database=str(e))
            return JSONResponse(info, status_code=503)
        return info

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return PlainTextResponse("ok")

    @app.get("/sw.js", include_in_schema=False)
    def sw():
        return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")

    from .routers import (
        admin,
        alerts,
        capture,
        catalogue,
        imports,
        inventory,
        labels,
        mixes,
        notes,
        pages,
        projects,
        recipes,
        recommend,
        scan,
        shops,
        stats,
        storage,
    )

    for r in (pages, catalogue, inventory, storage, mixes, notes, admin, scan, recommend,
              recipes, projects, labels, capture, alerts, shops, stats, imports):
        app.include_router(r.router)
    return app


app = create_app()
