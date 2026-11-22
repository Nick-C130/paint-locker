"""SQLite backups using the online backup API (safe while the app is running)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from . import config


def backup_to(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(config.DB_PATH)
    try:
        out = sqlite3.connect(dest)
        with out:
            src.backup(out)
        out.close()
    finally:
        src.close()
    return dest


def list_backups() -> list[Path]:
    if not config.BACKUP_DIR.exists():
        return []
    return sorted(config.BACKUP_DIR.glob("paintlocker-*.db"), reverse=True)


def prune(keep: int | None = None) -> None:
    keep = config.BACKUP_KEEP if keep is None else keep
    for old in list_backups()[keep:]:
        old.unlink(missing_ok=True)


def nightly_backup() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = backup_to(config.BACKUP_DIR / f"paintlocker-{stamp}.db")
    prune()
    return path
