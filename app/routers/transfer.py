"""Full backup download and restore (move to another server)."""

from __future__ import annotations

import secrets
import shutil
import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .. import config
from .. import transfer as tr
from ..web import redirect, render

router = APIRouter()


def _upload_path(token: str) -> Path:
    if not token.isalnum():
        raise HTTPException(404)
    return config.DATA_DIR / f".restore-upload-{token}.zip"


@router.get("/backup/full")
def full_backup():
    tmpdir = Path(tempfile.mkdtemp())
    out = tr.make_backup_zip(tmpdir / f"paint-locker-backup-{date.today().isoformat()}.zip")
    return FileResponse(out, filename=out.name, media_type="application/zip",
                        background=BackgroundTask(shutil.rmtree, tmpdir, ignore_errors=True))


@router.post("/restore/upload")
async def restore_upload(file: UploadFile):
    token = secrets.token_hex(8)
    dest = _upload_path(token)
    size, limit = 0, config.MAX_RESTORE_MB * 1024 * 1024
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                f.close()
                dest.unlink(missing_ok=True)
                return redirect("/settings#move", warn=f"That file is over {config.MAX_RESTORE_MB} MB (PAINTLOCKER_MAX_RESTORE_MB).")
            f.write(chunk)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            tr.inspect(dest, Path(tmp))
        except tr.RestoreError as e:
            dest.unlink(missing_ok=True)
            return redirect("/settings#move", warn=str(e))
    return redirect(f"/restore/{token}")


@router.get("/restore/{token}")
def restore_confirm(request: Request, token: str):
    path = _upload_path(token)
    if not path.exists():
        return redirect("/settings#move", warn="That upload has gone. Upload the backup again.")
    with tempfile.TemporaryDirectory() as tmp:
        info = tr.inspect(path, Path(tmp))
    current = tr.current_counts()
    current.pop("_revision", None)
    return render(request, "restore.html", token=token, info=info, current=current, size_mb=path.stat().st_size / 1e6)


@router.post("/restore/{token}")
def restore_do(token: str, confirm: str = Form("")):
    path = _upload_path(token)
    if not path.exists():
        return redirect("/settings#move", warn="That upload has gone. Upload the backup again.")
    if confirm.strip().upper() != "REPLACE":
        return redirect(f"/restore/{token}", warn="Type REPLACE to confirm.")
    try:
        safety = tr.restore(path)
    except tr.RestoreError as e:
        return redirect("/settings#move", warn=str(e))
    finally:
        path.unlink(missing_ok=True)
    return redirect("/", msg=f"Restored. Your previous data was saved as data/backups/{safety.name}.")


@router.post("/restore/{token}/cancel")
def restore_cancel(token: str):
    _upload_path(token).unlink(missing_ok=True)
    return redirect("/settings#move", msg="Restore cancelled. Nothing was changed.")
