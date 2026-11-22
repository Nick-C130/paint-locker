import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PAINTLOCKER_DATA_DIR", BASE_DIR / "data")).resolve()
CATALOGUE_DIR = Path(os.environ.get("PAINTLOCKER_CATALOGUE_DIR", BASE_DIR / "catalogues")).resolve()
DB_PATH = DATA_DIR / "paintlocker.db"
UPLOAD_DIR = DATA_DIR / "uploads"
BACKUP_DIR = DATA_DIR / "backups"

PASSWORD = os.environ.get("PAINTLOCKER_PASSWORD") or None
BACKUP_HOUR = int(os.environ.get("PAINTLOCKER_BACKUP_HOUR", "3"))
BACKUP_KEEP = int(os.environ.get("PAINTLOCKER_BACKUP_KEEP", "14"))
BACKUPS_ENABLED = os.environ.get("PAINTLOCKER_BACKUPS", "1") not in ("0", "false", "no")
SEED_ON_STARTUP = os.environ.get("PAINTLOCKER_SEED", "1") not in ("0", "false", "no")
MAX_UPLOAD_MB = int(os.environ.get("PAINTLOCKER_MAX_UPLOAD_MB", "15"))


def ensure_dirs() -> None:
    for d in (DATA_DIR, UPLOAD_DIR, BACKUP_DIR):
        d.mkdir(parents=True, exist_ok=True)

# Server and logging
HOST = os.environ.get("PAINTLOCKER_HOST", "0.0.0.0")
PORT = int(os.environ.get("PAINTLOCKER_PORT", "8080"))
LOG_LEVEL = (os.environ.get("LOG_LEVEL") or os.environ.get("PAINTLOCKER_LOG_LEVEL") or "info").lower()
VERSION = os.environ.get("PAINTLOCKER_VERSION", "dev")
