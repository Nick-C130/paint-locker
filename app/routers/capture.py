"""Camera colour capture: photograph a paint next to a grey or white card and read its colour.

The maths runs in the browser (static/capture.js); this router serves the page and saves results.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session

from .. import search as fts
from ..colour import normalise_hex
from ..db import get_session
from ..models import MixRecipe, Paint
from ..seed import set_oklab
from ..web import redirect, render

router = APIRouter()


@router.get("/capture")
def capture_page(request: Request, paint_id: int | None = None, mix_id: int | None = None, session: Session = Depends(get_session)):
    paint = session.get(Paint, paint_id) if paint_id else None
    mix = session.get(MixRecipe, mix_id) if mix_id else None
    return render(request, "capture.html", paint=paint, mix=mix)


@router.post("/paints/{paint_id}/measured")
def paint_measured(paint_id: int, measured_hex: str = Form(""), session: Session = Depends(get_session)):
    p = session.get(Paint, paint_id)
    if not p:
        raise HTTPException(404)
    p.measured_hex = normalise_hex(measured_hex)
    set_oklab(p)
    fts.index_entity(session, "paint", p.id)
    session.commit()
    return redirect(f"/paints/{p.id}", msg="Measured colour saved." if p.measured_hex else "Measured colour cleared.")
