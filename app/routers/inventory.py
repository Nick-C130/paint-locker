from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from .. import search as fts
from ..db import get_session
from ..models import BottleFormat, InventoryItem, MixRecipe, Paint, ProductLine, ShoppingItem, StorageCell
from ..services import all_units, cell_free, fit_warning, place_item, suggest_cells, unit_fits
from ..web import redirect, render

router = APIRouter()


def _reindex_item_owner(session: Session, item: InventoryItem) -> None:
    if item.paint_id:
        fts.index_entity(session, "paint", item.paint_id)
    if item.mix_id:
        fts.index_entity(session, "mix", item.mix_id)


def cell_choices(session: Session, fmt: BottleFormat | None, current: StorageCell | None = None):
    """Free cells grouped by unit for a <select>, with fit info."""
    groups = []
    for unit in all_units(session):
        free = [c for c in unit.cells if cell_free(c) or (current and c.id == current.id)]
        if free:
            groups.append({"unit": unit, "fits": unit_fits(unit, fmt), "cells": free})
    # fitting units first, then untagged, then non-fitting
    order = {True: 0, None: 1, False: 2}
    groups.sort(key=lambda g: order[g["fits"]])
    return groups


@router.get("/add")
def quick_add_form(
    request: Request,
    paint_id: int | None = None,
    mix_id: int | None = None,
    session: Session = Depends(get_session),
):
    paint = session.get(Paint, paint_id) if paint_id else None
    mix = session.get(MixRecipe, mix_id) if mix_id else None
    if not paint and not mix:
        raise HTTPException(400, "Pick a paint or mix")
    fmt = paint.effective_format if paint else (mix.format if mix else None)
    suggestion = (suggest_cells(session, fmt) or [None])[0]
    formats = session.scalars(select(BottleFormat).order_by(BottleFormat.name)).all()
    return render(
        request,
        "add_item.html",
        paint=paint,
        mix=mix,
        fmt=fmt,
        suggestion=suggestion,
        groups=cell_choices(session, fmt),
        formats=formats,
        today=date.today().isoformat(),
    )


@router.post("/items")
def item_create(
    paint_id: int | None = Form(None),
    mix_id: int | None = Form(None),
    cell_id: str = Form("auto"),
    format_id: str = Form(""),
    volume_ml: str = Form(""),
    acquired_on: str = Form(""),
    quantity: int = Form(1),
    notes: str = Form(""),
    back: str = Form(""),
    session: Session = Depends(get_session),
):
    if not paint_id and not mix_id:
        raise HTTPException(400, "paint_id or mix_id required")
    if paint_id and not session.get(Paint, paint_id):
        raise HTTPException(404)
    if mix_id and not session.get(MixRecipe, mix_id):
        raise HTTPException(404)
    warnings, placed = [], []
    created = []
    for n in range(max(1, min(quantity, 50))):
        item = InventoryItem(
            kind="mix" if mix_id else "paint",
            paint_id=paint_id,
            mix_id=mix_id,
            format_id=int(format_id) if format_id else None,
            volume_ml=float(volume_ml) if volume_ml else None,
            acquired_on=date.fromisoformat(acquired_on) if acquired_on else None,
            notes=notes,
        )
        session.add(item)
        session.flush()
        session.refresh(item)
        cell = None
        if cell_id == "auto":
            cell = (suggest_cells(session, item.effective_format) or [None])[0]
        elif cell_id and n == 0:
            cell = session.get(StorageCell, int(cell_id))
        elif cell_id:
            # extra bottles beyond the first: next suggestion
            cell = (suggest_cells(session, item.effective_format) or [None])[0]
        try:
            w = place_item(item, cell)
        except ValueError as e:
            warnings.append(str(e))
            w = None
            item.cell = None
        session.flush()
        if w:
            warnings.append(w)
        placed.append(item.cell.full_label if item.cell else "Unsorted")
        created.append(item)
    session.flush()
    _reindex_item_owner(session, created[0])
    session.commit()
    msg = f"Added {len(created)} bottle{'s' if len(created) > 1 else ''}: {', '.join(placed)}."
    target = back if back.startswith("/") else f"/items/{created[0].id}"
    return redirect(target, msg=msg, warn=" ".join(dict.fromkeys(warnings)) or None)


@router.get("/items")
def item_list(request: Request, unsorted: int = 0, session: Session = Depends(get_session)):
    q = (
        select(InventoryItem)
        .options(
            selectinload(InventoryItem.paint).selectinload(Paint.line).selectinload(ProductLine.brand),
            selectinload(InventoryItem.mix),
            selectinload(InventoryItem.cell).selectinload(StorageCell.unit),
        )
        .order_by(InventoryItem.id.desc())
    )
    if unsorted:
        q = q.where(InventoryItem.cell_id.is_(None))
    items = session.scalars(q).all()
    return render(request, "items.html", items=items, unsorted=unsorted)


@router.get("/items/{item_id}")
def item_detail(request: Request, item_id: int, session: Session = Depends(get_session)):
    item = session.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404)
    formats = session.scalars(select(BottleFormat).order_by(BottleFormat.name)).all()
    return render(
        request,
        "item.html",
        item=item,
        groups=cell_choices(session, item.effective_format, item.cell),
        formats=formats,
        warning=fit_warning(item.cell, item),
    )


@router.post("/items/{item_id}/edit")
def item_edit(
    item_id: int,
    format_id: str = Form(""),
    volume_ml: str = Form(""),
    acquired_on: str = Form(""),
    notes: str = Form(""),
    session: Session = Depends(get_session),
):
    item = session.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404)
    item.format_id = int(format_id) if format_id else None
    item.volume_ml = float(volume_ml) if volume_ml else None
    item.acquired_on = date.fromisoformat(acquired_on) if acquired_on else None
    item.notes = notes
    session.flush()
    session.refresh(item)
    _reindex_item_owner(session, item)
    session.commit()
    return redirect(f"/items/{item.id}", msg="Saved.", warn=fit_warning(item.cell, item))


@router.post("/items/{item_id}/move")
async def item_move(request: Request, item_id: int, session: Session = Depends(get_session)):
    """Form post or JSON (drag and drop). cell_id empty = unsorted."""
    item = session.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404)
    is_json = request.headers.get("content-type", "").startswith("application/json")
    data = await (request.json() if is_json else request.form())
    raw = str(data.get("cell_id") or "")
    swap = str(data.get("swap") or "") in ("1", "true", "True")
    cell = session.get(StorageCell, int(raw)) if raw else None
    if raw and not cell:
        raise HTTPException(404, "cell not found")
    old_cell = item.cell
    try:
        if swap and cell and cell.items and old_cell is not None:
            other = cell.items[0]
            other.cell = None
            session.flush()
            item.cell = cell
            session.flush()
            other.cell = old_cell
            warning = fit_warning(cell, item) or fit_warning(old_cell, other)
        else:
            warning = place_item(item, cell)
    except ValueError as e:
        if is_json:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=409)
        return redirect(f"/items/{item.id}", warn=str(e))
    session.flush()
    _reindex_item_owner(session, item)
    session.commit()
    where = cell.full_label if cell else "Unsorted"
    if is_json:
        return {"ok": True, "where": where, "warning": warning}
    back = str(data.get("back") or "")
    return redirect(back if back.startswith("/") else f"/items/{item.id}", msg=f"Moved to {where}.", warn=warning)


@router.post("/items/{item_id}/used-up")
def item_used_up(item_id: int, shopping: str = Form(""), session: Session = Depends(get_session)):
    item = session.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404)
    paint_id, mix_id = item.paint_id, item.mix_id
    label = item.full_label
    session.execute(
        text("DELETE FROM notes WHERE entity_type='item' AND entity_id=:i"), {"i": item.id}
    )
    session.delete(item)
    if shopping and paint_id:
        session.add(ShoppingItem(paint_id=paint_id, text=""))
    session.flush()
    if paint_id:
        fts.index_entity(session, "paint", paint_id)
    if mix_id:
        fts.index_entity(session, "mix", mix_id)
    session.commit()
    back = f"/paints/{paint_id}" if paint_id else f"/mixes/{mix_id}"
    return redirect(back, msg=f"Removed {label}." + (" Added to the shopping list." if shopping and paint_id else ""))


# ---------------------------------------------------------------- shopping list


@router.post("/shopping")
def shopping_add(paint_id: int | None = Form(None), text_: str = Form("", alias="text"), session: Session = Depends(get_session)):
    if not paint_id and not text_.strip():
        return redirect("/shopping")
    session.add(ShoppingItem(paint_id=paint_id, text=text_.strip()))
    session.commit()
    return redirect("/shopping", msg="Added to the shopping list.")


@router.post("/shopping/{sid}/toggle")
def shopping_toggle(sid: int, session: Session = Depends(get_session)):
    s = session.get(ShoppingItem, sid)
    if s:
        s.done = not s.done
        session.commit()
    return redirect("/shopping")


@router.post("/shopping/{sid}/delete")
def shopping_delete(sid: int, session: Session = Depends(get_session)):
    s = session.get(ShoppingItem, sid)
    if s:
        session.delete(s)
        session.commit()
    return redirect("/shopping")
