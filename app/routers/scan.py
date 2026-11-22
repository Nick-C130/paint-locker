"""Barcode scanning: USB/Bluetooth scanners (keyboard input) and phone cameras."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import search as fts
from ..db import get_session
from ..models import BarcodeLink, InventoryItem, Paint, StorageCell
from ..services import place_item, suggest_cells
from ..web import redirect, render

router = APIRouter()


def clean_barcode(raw: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", raw or "")


def barcode_variants(code: str) -> list[str]:
    """UPC-A (12 digits) and EAN-13 with a leading 0 are the same product."""
    out = [code]
    if code.isdigit():
        if len(code) == 12:
            out.append("0" + code)
        elif len(code) == 13 and code.startswith("0"):
            out.append(code[1:])
    return out


def find_paint_by_barcode(session: Session, raw: str) -> Paint | None:
    code = clean_barcode(raw)
    if not code:
        return None
    variants = barcode_variants(code)
    link = session.scalar(select(BarcodeLink).where(BarcodeLink.barcode.in_(variants)))
    if link:
        return link.paint
    return session.scalar(select(Paint).where(Paint.barcode.in_(variants)).limit(1))


def _paint_json(session: Session, p: Paint) -> dict:
    items = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.paint_id == p.id)
        .options(selectinload(InventoryItem.cell).selectinload(StorageCell.unit))
    ).all()
    fmt = p.effective_format
    suggestion = (suggest_cells(session, fmt) or [None])[0]
    return {
        "id": p.id,
        "label": p.full_label,
        "hex": p.display_hex,
        "url": f"/paints/{p.id}",
        "owned": len(items),
        "locations": [it.cell.full_label if it.cell else "Unsorted" for it in items],
        "format": fmt.name if fmt else None,
        "suggestion": suggestion.full_label if suggestion else None,
    }


@router.get("/scan")
def scan_page(request: Request, link: int | None = None, session: Session = Depends(get_session)):
    """link=<paint id>: the next barcode scanned is linked to that paint."""
    p = session.get(Paint, link) if link else None
    link_paint = {"id": p.id, "label": p.full_label, "hex": p.display_hex} if p else None
    return render(request, "scan.html", link_paint=link_paint)


@router.get("/api/barcode/{raw}")
def api_barcode_lookup(raw: str, session: Session = Depends(get_session)):
    code = clean_barcode(raw)
    if not code:
        raise HTTPException(400, "Empty barcode")
    p = find_paint_by_barcode(session, code)
    return {"barcode": code, "found": bool(p), "paint": _paint_json(session, p) if p else None}


@router.post("/api/barcode/link")
async def api_barcode_link(request: Request, session: Session = Depends(get_session)):
    data = await request.json()
    code = clean_barcode(str(data.get("barcode", "")))
    paint = session.get(Paint, int(data.get("paint_id") or 0))
    if not code or not paint:
        raise HTTPException(400, "Need a barcode and a paint")
    link = session.scalar(select(BarcodeLink).where(BarcodeLink.barcode.in_(barcode_variants(code))))
    if link:
        link.paint_id = paint.id
        link.barcode = code
    else:
        session.add(BarcodeLink(barcode=code, paint_id=paint.id))
    session.flush()
    fts.index_entity(session, "paint", paint.id)
    session.commit()
    return {"ok": True, "barcode": code, "paint": _paint_json(session, paint)}


@router.post("/api/barcode/add")
async def api_barcode_add(request: Request, session: Session = Depends(get_session)):
    """Add one bottle of a paint to the next free fitting cell (scan-to-add)."""
    data = await request.json()
    paint = session.get(Paint, int(data.get("paint_id") or 0))
    if not paint:
        raise HTTPException(404)
    item = InventoryItem(kind="paint", paint_id=paint.id)
    session.add(item)
    session.flush()
    session.refresh(item)
    cell = (suggest_cells(session, item.effective_format) or [None])[0]
    warning = place_item(item, cell)
    session.flush()
    fts.index_entity(session, "paint", paint.id)
    session.commit()
    return {
        "ok": True,
        "item_id": item.id,
        "where": item.cell.full_label if item.cell else "Unsorted",
        "unit_id": item.cell.unit_id if item.cell else None,
        "warning": warning,
        "paint": _paint_json(session, paint),
    }


@router.post("/barcodes/{link_id}/delete")
def barcode_unlink(link_id: int, session: Session = Depends(get_session)):
    link = session.get(BarcodeLink, link_id)
    if not link:
        raise HTTPException(404)
    pid = link.paint_id
    session.delete(link)
    session.flush()
    fts.index_entity(session, "paint", pid)
    session.commit()
    return redirect(f"/paints/{pid}", msg="Barcode unlinked.")


def paint_barcodes(session: Session, paint_id: int) -> list[BarcodeLink]:
    return list(session.scalars(select(BarcodeLink).where(BarcodeLink.paint_id == paint_id)).all())

