from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from .. import search as fts
from ..db import get_session
from ..models import BottleFormat, InventoryItem, MixRecipe, Paint, ProductLine, StorageCell, StorageUnit
from ..services import build_cells, fit_warning, place_item
from ..web import is_htmx, redirect, render

router = APIRouter()
MAX_DIM = 60


def load_unit(session: Session, unit_id: int) -> StorageUnit:
    unit = session.scalar(
        select(StorageUnit)
        .where(StorageUnit.id == unit_id)
        .options(
            selectinload(StorageUnit.formats),
            selectinload(StorageUnit.cells)
            .selectinload(StorageCell.items)
            .selectinload(InventoryItem.paint)
            .selectinload(Paint.line)
            .selectinload(ProductLine.brand),
            selectinload(StorageUnit.cells).selectinload(StorageCell.items).selectinload(InventoryItem.mix),
        )
    )
    if not unit:
        raise HTTPException(404)
    return unit


def grid(unit: StorageUnit) -> list[list[StorageCell]]:
    rows = [[None] * unit.cols for _ in range(unit.rows)]
    for c in unit.cells:
        if c.row < unit.rows and c.col < unit.cols:
            rows[c.row][c.col] = c
    return rows


@router.get("/storage")
def storage_list(request: Request, session: Session = Depends(get_session)):
    units = session.scalars(
        select(StorageUnit)
        .options(selectinload(StorageUnit.formats), selectinload(StorageUnit.cells).selectinload(StorageCell.items))
        .order_by(StorageUnit.sort_order, StorageUnit.id)
    ).all()
    return render(request, "storage_list.html", units=units)


@router.get("/storage/new")
def storage_new(request: Request, session: Session = Depends(get_session)):
    formats = session.scalars(select(BottleFormat).order_by(BottleFormat.name)).all()
    return render(request, "storage_form.html", unit=None, formats=formats)


def _formats_from(session: Session, ids: list[str]) -> list[BottleFormat]:
    ids_int = [int(i) for i in ids if i]
    return list(session.scalars(select(BottleFormat).where(BottleFormat.id.in_(ids_int))).all()) if ids_int else []


@router.post("/storage")
async def storage_create(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    rows, cols = int(form.get("rows", 0)), int(form.get("cols", 0))
    if not (1 <= rows <= MAX_DIM and 1 <= cols <= MAX_DIM):
        raise HTTPException(400, f"Rows and columns must be 1-{MAX_DIM}")
    unit = StorageUnit(
        name=(form.get("name") or "Rack").strip(),
        rows=rows,
        cols=cols,
        label_scheme=form.get("label_scheme") if form.get("label_scheme") in ("A1", "1-1") else "A1",
        location=(form.get("location") or "").strip(),
    )
    unit.formats = _formats_from(session, form.getlist("format_ids"))
    session.add(unit)
    build_cells(unit)
    session.flush()
    fts.index_entity(session, "unit", unit.id)
    session.commit()
    return redirect(f"/storage/{unit.id}", msg=f"Created {unit.name} with {rows * cols} cells.")


@router.get("/storage/{unit_id}")
def storage_detail(
    request: Request, unit_id: int, highlight: str = "", session: Session = Depends(get_session)
):
    unit = load_unit(session, unit_id)
    hl_items: set[int] = set()
    if highlight.startswith("paint:"):
        pid = int(highlight.split(":")[1])
        hl_items = {it.id for c in unit.cells for it in c.items if it.paint_id == pid}
    elif highlight.startswith("mix:"):
        mid = int(highlight.split(":")[1])
        hl_items = {it.id for c in unit.cells for it in c.items if it.mix_id == mid}
    elif highlight.startswith("item:"):
        hl_items = {int(highlight.split(":")[1])}
    other_units = session.scalars(select(StorageUnit).order_by(StorageUnit.sort_order, StorageUnit.id)).all()
    unsorted = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.cell_id.is_(None))
        .options(selectinload(InventoryItem.paint), selectinload(InventoryItem.mix))
        .order_by(InventoryItem.id.desc())
    ).all()
    used = sum(1 for c in unit.cells if c.items)
    usable = sum(1 for c in unit.cells if not c.disabled)
    tpl = "partials/grid.html" if is_htmx(request) and request.query_params.get("partial") else "storage.html"
    return render(
        request,
        tpl,
        unit=unit,
        grid=grid(unit),
        hl=hl_items,
        other_units=other_units,
        unsorted=unsorted,
        used=used,
        usable=usable,
    )


@router.get("/storage/{unit_id}/edit")
def storage_edit_form(request: Request, unit_id: int, session: Session = Depends(get_session)):
    unit = load_unit(session, unit_id)
    formats = session.scalars(select(BottleFormat).order_by(BottleFormat.name)).all()
    return render(request, "storage_form.html", unit=unit, formats=formats)


@router.post("/storage/{unit_id}/edit")
async def storage_edit(request: Request, unit_id: int, session: Session = Depends(get_session)):
    unit = load_unit(session, unit_id)
    form = await request.form()
    rows, cols = int(form.get("rows", unit.rows)), int(form.get("cols", unit.cols))
    if not (1 <= rows <= MAX_DIM and 1 <= cols <= MAX_DIM):
        raise HTTPException(400, f"Rows and columns must be 1-{MAX_DIM}")
    dropped = [c for c in unit.cells if c.row >= rows or c.col >= cols]
    occupied = [c.label for c in dropped if c.items]
    if occupied:
        return redirect(
            f"/storage/{unit.id}/edit",
            warn=f"Can't shrink: cells {', '.join(occupied[:10])} still hold bottles. Move them first.",
        )
    for c in dropped:
        unit.cells.remove(c)
    unit.name = (form.get("name") or unit.name).strip()
    unit.location = (form.get("location") or "").strip()
    unit.rows, unit.cols = rows, cols
    if form.get("label_scheme") in ("A1", "1-1"):
        unit.label_scheme = form.get("label_scheme")
    try:
        unit.sort_order = int(form.get("sort_order") or 0)
    except ValueError:
        pass
    unit.formats = _formats_from(session, form.getlist("format_ids"))
    build_cells(unit)
    session.flush()
    fts.index_entity(session, "unit", unit.id)
    # cell labels are part of paint search text
    for c in unit.cells:
        for it in c.items:
            if it.paint_id:
                fts.index_entity(session, "paint", it.paint_id)
            if it.mix_id:
                fts.index_entity(session, "mix", it.mix_id)
    session.commit()
    return redirect(f"/storage/{unit.id}", msg="Saved.")


@router.post("/storage/{unit_id}/delete")
def storage_delete(unit_id: int, session: Session = Depends(get_session)):
    unit = load_unit(session, unit_id)
    owners = set()
    for c in unit.cells:
        for it in c.items:
            it.cell = None
            owners.add(("paint", it.paint_id) if it.paint_id else ("mix", it.mix_id))
    session.flush()
    session.execute(text("DELETE FROM search_index WHERE entity_type='unit' AND entity_id=:i"), {"i": unit.id})
    session.delete(unit)
    session.flush()
    for t, i in owners:
        fts.index_entity(session, t, i)
    session.commit()
    return redirect("/storage", msg="Storage unit deleted; its bottles are now unsorted.")


@router.get("/cells/{cell_id}")
def cell_panel(request: Request, cell_id: int, session: Session = Depends(get_session)):
    cell = session.get(StorageCell, cell_id)
    if not cell:
        raise HTTPException(404)
    unsorted = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.cell_id.is_(None))
        .options(selectinload(InventoryItem.paint), selectinload(InventoryItem.mix))
        .order_by(InventoryItem.id.desc())
        .limit(50)
    ).all()
    warnings = [w for w in (fit_warning(cell, it) for it in cell.items) if w]
    tpl = "partials/cell_panel.html" if is_htmx(request) else "cell.html"
    return render(request, tpl, cell=cell, unsorted=unsorted, warnings=warnings)


@router.post("/cells/{cell_id}/assign")
def cell_assign(
    cell_id: int,
    paint_id: int | None = Form(None),
    mix_id: int | None = Form(None),
    item_id: int | None = Form(None),
    session: Session = Depends(get_session),
):
    cell = session.get(StorageCell, cell_id)
    if not cell:
        raise HTTPException(404)
    if item_id:
        item = session.get(InventoryItem, item_id)
        if not item:
            raise HTTPException(404)
    elif paint_id or mix_id:
        if paint_id and not session.get(Paint, paint_id):
            raise HTTPException(404)
        if mix_id and not session.get(MixRecipe, mix_id):
            raise HTTPException(404)
        item = InventoryItem(kind="mix" if mix_id else "paint", paint_id=paint_id, mix_id=mix_id)
        session.add(item)
        session.flush()
        session.refresh(item)
    else:
        raise HTTPException(400)
    try:
        warning = place_item(item, cell)
    except ValueError as e:
        session.rollback()
        return redirect(f"/storage/{cell.unit_id}", warn=str(e))
    session.flush()
    if item.paint_id:
        fts.index_entity(session, "paint", item.paint_id)
    if item.mix_id:
        fts.index_entity(session, "mix", item.mix_id)
    session.commit()
    return redirect(f"/storage/{cell.unit_id}", msg=f"{item.label} placed in {cell.label}.", warn=warning)


@router.post("/cells/{cell_id}/toggle-disabled")
def cell_toggle(cell_id: int, session: Session = Depends(get_session)):
    cell = session.get(StorageCell, cell_id)
    if not cell:
        raise HTTPException(404)
    if not cell.disabled and cell.items:
        return redirect(f"/storage/{cell.unit_id}", warn="Empty the cell before disabling it.")
    cell.disabled = not cell.disabled
    session.commit()
    return redirect(f"/storage/{cell.unit_id}")


@router.get("/storage/{unit_id}/bulk")
def bulk_entry(request: Request, unit_id: int, start: str = "", session: Session = Depends(get_session)):
    unit = load_unit(session, unit_id)
    cells = [
        {
            "id": c.id,
            "label": c.label,
            "disabled": c.disabled,
            "filled": [{"label": it.label, "hex": it.hex} for it in c.items],
            "capacity": c.capacity or 1,
        }
        for c in sorted(unit.cells, key=lambda c: (c.row, c.col))
    ]
    return render(request, "bulk.html", unit=unit, cells=cells, start=start)


@router.post("/api/cells/{cell_id}/fill")
async def api_cell_fill(request: Request, cell_id: int, session: Session = Depends(get_session)):
    data = await request.json()
    cell = session.get(StorageCell, cell_id)
    if not cell:
        raise HTTPException(404)
    paint_id = data.get("paint_id")
    mix_id = data.get("mix_id")
    if not paint_id and not mix_id:
        raise HTTPException(400)
    item = InventoryItem(kind="mix" if mix_id else "paint", paint_id=paint_id, mix_id=mix_id)
    session.add(item)
    session.flush()
    session.refresh(item)
    try:
        warning = place_item(item, cell)
    except ValueError as e:
        session.rollback()
        return {"ok": False, "error": str(e)}
    session.flush()
    fts.index_entity(session, "paint" if paint_id else "mix", paint_id or mix_id)
    session.commit()
    return {"ok": True, "item_id": item.id, "label": item.label, "hex": item.hex, "warning": warning}
