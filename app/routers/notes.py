"""Comments (notes) and photo attachments on any entity."""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from .. import search as fts
from ..db import get_session
from ..models import ENTITY_TYPES, Attachment, Note
from ..web import is_htmx, redirect, render

router = APIRouter()
ALLOWED_IMAGE = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "image/heic": ".heic"}


def _check(entity_type: str) -> None:
    if entity_type not in ENTITY_TYPES:
        raise HTTPException(400, "Unknown entity type")


def _block(request: Request, session: Session, entity_type: str, entity_id: int):
    notes = session.scalars(
        select(Note)
        .where(Note.entity_type == entity_type, Note.entity_id == entity_id)
        .order_by(Note.pinned.desc(), Note.created_at.desc(), Note.id.desc())
    ).all()
    attachments = session.scalars(
        select(Attachment)
        .where(Attachment.entity_type == entity_type, Attachment.entity_id == entity_id)
        .order_by(Attachment.id.desc())
    ).all()
    return render(
        request,
        "partials/notes.html",
        notes=notes,
        attachments=attachments,
        entity_type=entity_type,
        entity_id=entity_id,
    )


def _back(request: Request, entity_type: str, entity_id: int):
    if is_htmx(request):
        return None
    ref = request.headers.get("referer") or "/"
    return redirect(ref if ref.startswith(("http://", "https://", "/")) else "/")


@router.get("/notes/{entity_type}/{entity_id}")
def notes_block(request: Request, entity_type: str, entity_id: int, session: Session = Depends(get_session)):
    _check(entity_type)
    return _block(request, session, entity_type, entity_id)


@router.post("/notes/{entity_type}/{entity_id}")
def note_add(
    request: Request,
    entity_type: str,
    entity_id: int,
    body: str = Form(...),
    pinned: str = Form(""),
    session: Session = Depends(get_session),
):
    _check(entity_type)
    if body.strip():
        session.add(Note(entity_type=entity_type, entity_id=entity_id, body=body.strip(), pinned=bool(pinned)))
        session.flush()
        fts.reindex_for_note_target(session, entity_type, entity_id)
        session.commit()
    return _back(request, entity_type, entity_id) or _block(request, session, entity_type, entity_id)


@router.post("/notes/{note_id}/edit")
def note_edit(request: Request, note_id: int, body: str = Form(...), session: Session = Depends(get_session)):
    n = session.get(Note, note_id)
    if not n:
        raise HTTPException(404)
    n.body = body.strip() or n.body
    session.flush()
    fts.reindex_for_note_target(session, n.entity_type, n.entity_id)
    session.commit()
    return _back(request, n.entity_type, n.entity_id) or _block(request, session, n.entity_type, n.entity_id)


@router.post("/notes/{note_id}/pin")
def note_pin(request: Request, note_id: int, session: Session = Depends(get_session)):
    n = session.get(Note, note_id)
    if not n:
        raise HTTPException(404)
    n.pinned = not n.pinned
    session.commit()
    return _back(request, n.entity_type, n.entity_id) or _block(request, session, n.entity_type, n.entity_id)


@router.post("/notes/{note_id}/delete")
def note_delete(request: Request, note_id: int, session: Session = Depends(get_session)):
    n = session.get(Note, note_id)
    if not n:
        raise HTTPException(404)
    t, i = n.entity_type, n.entity_id
    session.delete(n)
    session.flush()
    fts.reindex_for_note_target(session, t, i)
    session.commit()
    return _back(request, t, i) or _block(request, session, t, i)


@router.post("/attachments/{entity_type}/{entity_id}")
async def attachment_add(
    request: Request,
    entity_type: str,
    entity_id: int,
    file: UploadFile,
    caption: str = Form(""),
    session: Session = Depends(get_session),
):
    _check(entity_type)
    ext = ALLOWED_IMAGE.get(file.content_type or "")
    if not ext:
        raise HTTPException(400, "Only images can be attached")
    data = await file.read()
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, "File too large")
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}{ext}"
    (config.UPLOAD_DIR / fname).write_bytes(data)
    session.add(
        Attachment(
            entity_type=entity_type,
            entity_id=entity_id,
            filename=fname,
            original_name=(file.filename or "")[:200],
            content_type=file.content_type,
            caption=caption.strip(),
        )
    )
    session.commit()
    return _back(request, entity_type, entity_id) or _block(request, session, entity_type, entity_id)


@router.post("/attachments/{att_id}/delete")
def attachment_delete(request: Request, att_id: int, session: Session = Depends(get_session)):
    a = session.get(Attachment, att_id)
    if not a:
        raise HTTPException(404)
    t, i = a.entity_type, a.entity_id
    (config.UPLOAD_DIR / a.filename).unlink(missing_ok=True)
    session.delete(a)
    session.commit()
    return _back(request, t, i) or _block(request, session, t, i)


@router.get("/uploads/{filename}")
def upload_file(filename: str):
    if not re.fullmatch(r"[0-9a-f]{32}\.[a-z]{3,4}", filename):
        raise HTTPException(404)
    path = config.UPLOAD_DIR / filename
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)
