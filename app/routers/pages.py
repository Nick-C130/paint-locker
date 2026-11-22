from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import search as fts
from ..colour import normalise_hex
from ..db import get_session
from ..models import (
    InventoryItem,
    MixRecipe,
    Paint,
    ProductLine,
    ReferenceColour,
    StorageCell,
    StorageUnit,
)
from ..services import all_units, owned_counts
from ..web import is_htmx, redirect, render

router = APIRouter()


def load_results(session: Session, q: str, types=fts.INDEXED, limit: int = 40) -> dict:
    hits = fts.search(session, q, types=types, limit=limit)
    ids: dict[str, list[int]] = {}
    for t, i in hits:
        ids.setdefault(t, []).append(i)
    # Exact code matches first (e.g. "70.950"), even if FTS ranks them lower.
    exact = list(session.scalars(select(Paint.id).where(func.lower(Paint.code) == q.strip().lower())).all())
    from .scan import find_paint_by_barcode

    if q.strip().isdigit() and len(q.strip()) >= 8:
        bp = find_paint_by_barcode(session, q)
        if bp:
            exact.insert(0, bp.id)
    paint_ids = list(dict.fromkeys(list(exact) + ids.get("paint", [])))
    paints = {
        p.id: p
        for p in session.scalars(
            select(Paint)
            .where(Paint.id.in_(paint_ids))
            .options(
                selectinload(Paint.line).selectinload(ProductLine.brand),
                selectinload(Paint.items).selectinload(InventoryItem.cell).selectinload(StorageCell.unit),
            )
        ).all()
    }
    mixes = {
        m.id: m
        for m in session.scalars(
            select(MixRecipe)
            .where(MixRecipe.id.in_(ids.get("mix", [])))
            .options(selectinload(MixRecipe.items).selectinload(InventoryItem.cell).selectinload(StorageCell.unit))
        ).all()
    }
    refs = {r.id: r for r in session.scalars(select(ReferenceColour).where(ReferenceColour.id.in_(ids.get("reference", [])))).all()}
    units = {u.id: u for u in session.scalars(select(StorageUnit).where(StorageUnit.id.in_(ids.get("unit", [])))).all()}
    paint_list = [paints[i] for i in paint_ids if i in paints]
    # Owned paints float to the top
    paint_list.sort(key=lambda p: (0 if p.items else 1))
    return {
        "paints": paint_list,
        "mixes": [mixes[i] for i in ids.get("mix", []) if i in mixes],
        "references": [refs[i] for i in ids.get("reference", []) if i in refs],
        "units": [units[i] for i in ids.get("unit", []) if i in units],
    }


@router.get("/")
def dashboard(request: Request, session: Session = Depends(get_session)):
    units = all_units(session)
    stats = {
        "bottles": session.scalar(select(func.count()).select_from(InventoryItem)),
        "owned_paints": session.scalar(select(func.count(func.distinct(InventoryItem.paint_id)))),
        "mixes": session.scalar(select(func.count()).select_from(MixRecipe)),
        "catalogue": session.scalar(select(func.count()).select_from(Paint)),
        "unsorted": session.scalar(select(func.count()).select_from(InventoryItem).where(InventoryItem.cell_id.is_(None))),
    }
    recent = session.scalars(
        select(InventoryItem)
        .options(
            selectinload(InventoryItem.paint).selectinload(Paint.line).selectinload(ProductLine.brand),
            selectinload(InventoryItem.mix),
            selectinload(InventoryItem.cell).selectinload(StorageCell.unit),
        )
        .order_by(InventoryItem.id.desc())
        .limit(10)
    ).all()
    return render(request, "dashboard.html", units=units, stats=stats, recent=recent)


@router.get("/search")
def search_page(request: Request, q: str = "", session: Session = Depends(get_session)):
    q = q.strip()
    hx = normalise_hex(q) if q.startswith("#") else None
    if q.isdigit() and len(q) >= 8 and not is_htmx(request):
        from .scan import find_paint_by_barcode

        bp = find_paint_by_barcode(session, q)
        if bp:
            return redirect(f"/paints/{bp.id}")
    if hx and not is_htmx(request):
        return redirect("/colours", hex=hx)
    results = load_results(session, q) if q else None
    tpl = "partials/search_results.html" if is_htmx(request) else "search.html"
    return render(request, tpl, q=q, results=results, colour_hex=hx)


@router.get("/api/search")
def api_search(
    q: str = "", kinds: str = "paint,mix", owned: int = 0, ptypes: str = "", session: Session = Depends(get_session)
):
    """JSON search for pickers (mix lab, bulk shelf entry). ptypes filters paints by type (e.g. medium,varnish)."""
    q = q.strip()
    paint_types = [t for t in ptypes.split(",") if t]
    if not q and not paint_types:
        return []
    types = tuple(k for k in kinds.split(",") if k in fts.INDEXED)
    if q:
        res = load_results(session, q, types=types, limit=25 if not paint_types else 200)
    else:
        res = {"paints": [], "mixes": []}
    if paint_types:
        if not q:
            res["paints"] = list(
                session.scalars(
                    select(Paint)
                    .where(Paint.type.in_(paint_types))
                    .options(selectinload(Paint.line).selectinload(ProductLine.brand), selectinload(Paint.items))
                    .order_by(Paint.name)
                ).all()
            )
        res["paints"] = sorted(
            [p for p in res["paints"] if p.type in paint_types], key=lambda p: (
                0 if p.items else 1,
                0 if p.line.name == "Auxiliaries" else 1,
                0 if p.type == "medium" else 1,
                p.line.brand.name,
                p.name,
            )
        )
        res["mixes"] = []
    out = []
    counts = owned_counts(session, [p.id for p in res["paints"]])
    for p in res["paints"]:
        if owned and not counts.get(p.id):
            continue
        out.append(
            {
                "kind": "paint",
                "id": p.id,
                "label": p.full_label,
                "code": p.code,
                "name": p.name,
                "hex": p.display_hex,
                "type": p.type,
                "owned": counts.get(p.id, 0),
                "format": p.effective_format.name if p.effective_format else None,
            }
        )
    for m in res["mixes"]:
        out.append(
            {
                "kind": "mix",
                "id": m.id,
                "label": f"Mix: {m.name}",
                "code": "",
                "name": m.name,
                "hex": m.display_hex,
                "type": "mix",
                "owned": len(m.items),
                "format": m.format.name if m.format else None,
            }
        )
    return out[: 100 if paint_types else 25]
