"""Move everything to another Paint Locker: one .zip with the database and every photo.

Restore replaces this server's data. It always keeps a safety copy of what was here first, refuses
backups from a newer version than this one, and upgrades backups from older versions.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import backup, config, db

log = logging.getLogger("paintlocker.transfer")
DB_NAME = "paintlocker.db"
MANIFEST = "paintlocker-backup.json"
COUNTED = {"paints with bottles": "SELECT COUNT(DISTINCT paint_id) FROM inventory_items", "bottles": "SELECT COUNT(*) FROM inventory_items",
           "mixes": "SELECT COUNT(*) FROM mix_recipes", "racks": "SELECT COUNT(*) FROM storage_units",
           "comments": "SELECT COUNT(*) FROM notes", "photos": "SELECT COUNT(*) FROM attachments"}
OPTIONAL = {"recipes": "SELECT COUNT(*) FROM recipes", "projects": "SELECT COUNT(*) FROM projects"}


class RestoreError(ValueError):
    pass


def _counts(path: Path) -> dict:
    out = {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for k, q in {**COUNTED, **OPTIONAL}.items():
            with contextlib.suppress(sqlite3.Error):
                out[k] = con.execute(q).fetchone()[0]
        try:
            out["_revision"] = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        except sqlite3.Error as e:
            raise RestoreError("That database isn't from Paint Locker (no schema version).") from e
    finally:
        con.close()
    return out


def known_revisions() -> list[str]:
    from alembic.script import ScriptDirectory

    from .migrate import alembic_config

    script = ScriptDirectory.from_config(alembic_config(db.engine))
    return [r.revision for r in script.walk_revisions()]


def current_counts() -> dict:
    return _counts(config.DB_PATH)


def make_backup_zip(dest: Path) -> Path:
    """Database (consistent snapshot) + uploads + a small manifest."""
    with tempfile.TemporaryDirectory() as tmp:
        snap = backup.backup_to(Path(tmp) / DB_NAME)
        counts = _counts(snap)
        manifest = {"app": "paint-locker", "version": config.VERSION, "created": datetime.now().isoformat(timespec="seconds"),
                    "schema": counts.pop("_revision"), "counts": counts}
        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr(MANIFEST, json.dumps(manifest, indent=2))
            z.write(snap, DB_NAME)
            if config.UPLOAD_DIR.exists():
                for f in sorted(config.UPLOAD_DIR.rglob("*")):
                    if f.is_file():
                        z.write(f, "uploads/" + f.relative_to(config.UPLOAD_DIR).as_posix(), compress_type=zipfile.ZIP_STORED)
    return dest


def inspect(zip_path: Path, work: Path) -> dict:
    """Check an uploaded backup and extract its database to `work`. Returns what's in it."""
    try:
        z = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise RestoreError("That isn't a Paint Locker backup (.zip). Download one from Settings → Move to another server.") from e
    with z:
        names = z.namelist()
        if DB_NAME not in names:
            raise RestoreError(f"The .zip has no {DB_NAME}, so it isn't a Paint Locker backup.")
        for n in names:
            if n.startswith("/") or ".." in Path(n).parts:
                raise RestoreError("The backup contains unsafe file paths.")
        manifest = json.loads(z.read(MANIFEST)) if MANIFEST in names else {}
        z.extract(DB_NAME, work)
        photos = sum(1 for n in names if n.startswith("uploads/") and not n.endswith("/"))
    dbf = work / DB_NAME
    with open(dbf, "rb") as f:
        if f.read(16) != b"SQLite format 3\x00":
            raise RestoreError("The database in the backup is damaged or not SQLite.")
    con = sqlite3.connect(dbf)
    try:
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()
    if ok != "ok":
        raise RestoreError(f"The database in the backup failed its integrity check ({ok}).")
    counts = _counts(dbf)
    rev = counts.pop("_revision")
    if rev not in known_revisions():
        raise RestoreError(f"That backup is from a newer Paint Locker (schema {rev}). Update this server first, then restore.")
    return {"manifest": manifest, "counts": counts, "schema": rev, "photos": photos}


def restore(zip_path: Path) -> Path:
    """Replace this server's database and photos with the backup's. Returns the safety copy's path."""
    from . import main as app_main

    with tempfile.TemporaryDirectory() as tmp:
        inspect(zip_path, Path(tmp))
    safety = config.BACKUP_DIR / f"before-restore-{datetime.now():%Y%m%d-%H%M%S}.zip"
    make_backup_zip(safety)
    log.warning("Restoring from backup; the previous data was saved to %s", safety)

    if db.engine is not None:
        db.engine.dispose()
    staging = Path(tempfile.mkdtemp(dir=config.DATA_DIR, prefix=".restore-"))
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extract(DB_NAME, staging)
            for n in z.namelist():
                if n.startswith("uploads/") and not n.endswith("/"):
                    z.extract(n, staging)
        for suffix in ("", "-wal", "-shm"):
            Path(str(config.DB_PATH) + suffix).unlink(missing_ok=True)
        shutil.move(str(staging / DB_NAME), config.DB_PATH)
        if config.UPLOAD_DIR.exists():
            shutil.rmtree(config.UPLOAD_DIR)
        if (staging / "uploads").exists():
            shutil.move(str(staging / "uploads"), config.UPLOAD_DIR)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    # upgrade an older backup's schema, re-check the catalogue and rebuild search
    from . import search, seed

    app_main.startup()
    with db.SessionLocal() as s:
        search.rebuild(s)
        seed.set_setting(s, "search_index_version", search.INDEX_VERSION)
        s.commit()
    log.warning("Restore finished")
    return safety
