from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from .. import search as fts
from ..colour import normalise_hex
from ..db import get_session
from ..mixsolver import suggest_mixes
from ..models import (
    BarcodeLink,
    NON_MIXING_TYPES,
    PAINT_TYPES,
    BottleFormat,
    Brand,
    InventoryItem,
    MixComponent,
    MixRecipe,
    Paint,
    ProductLine,
    ReferenceColour,
    ReferenceLink,
    StorageCell,
)
from ..seed import ensure_formats, get_or_create_brand, get_or_create_line, import_user_csv, set_oklab
from ..services import log_colour_request, nearest_owned_mixes, nearest_paints, owned_counts
from ..web import is_htmx, redirect, render
from .scan import paint_barcodes

router = APIRouter()
PAGE_SIZE = 60


def _paint_query(brand: int | None, line: int | None, type_: str, finish: str, owned: str, q: str, session: Session):
    stmt = select(Paint).join(ProductLine)
    if brand:
        stmt = stmt.where(ProductLine.brand_id == brand)
    if line:
        stmt = stmt.where(Paint.line_id == line)
    if type_:
        stmt = stmt.where(Paint.type == type_)
    if finish:
        stmt = stmt.where(Paint.finish == finish)
    owned_ids = select(InventoryItem.paint_id).where(InventoryItem.paint_id.is_not(None))
    if owned == "yes":
        stmt = stmt.where(Paint.id.in_(owned_ids))
    elif owned == "no":
        stmt = stmt.where(Paint.id.not_in(owned_ids))
    if q:
        hits = [i for t, i in fts.search(session, q, types=("paint",), limit=1000)]
        stmt = stmt.where(Paint.id.in_(hits))
    return stmt


@router.get("/paints")
def paint_list(
    request: Request,
    brand: int | None = None,
    line: int | None = None,
    type: str = "",
    finish: str = "",
    owned: str = "",
    q: str = "",
    page: int = 1,
    session: Session = Depends(get_session),
):
    stmt = _paint_query(brand, line, type, finish, owned, q.strip(), session)
    total = session.scalar(select(func.count()).select_from(stmt.subquery()))
    paints = session.scalars(
        stmt.options(selectinload(Paint.line).selectinload(ProductLine.brand))
        .order_by(ProductLine.name, Paint.code, Paint.name)
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    counts = owned_counts(session, [p.id for p in paints])
    brands = session.scalars(select(Brand).options(selectinload(Brand.lines)).order_by(Brand.name)).all()
    ctx = dict(
        paints=paints,
        counts=counts,
        total=total,
        page=page,
        pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
        brands=brands,
        f=dict(brand=brand, line=line, type=type, finish=finish, owned=owned, q=q),
    )
    tpl = "partials/paint_table.html" if is_htmx(request) else "paints.html"
    return render(request, tpl, **ctx)


@router.get("/paints/new")
def paint_new(request: Request, session: Session = Depends(get_session)):
    brands = session.scalars(select(Brand).options(selectinload(Brand.lines)).order_by(Brand.name)).all()
    formats = session.scalars(select(BottleFormat).order_by(BottleFormat.name)).all()
    return render(request, "paint_form.html", paint=None, brands=brands, formats=formats)


def _apply_paint_form(p: Paint, session: Session, form: dict) -> None:
    p.code = form["code"].strip()
    p.name = form["name"].strip()
    p.hex = normalise_hex(form.get("hex"))
    p.measured_hex = normalise_hex(form.get("measured_hex"))
    p.type = form["type"] if form["type"] in PAINT_TYPES else "acrylic"
    p.finish = form.get("finish") or "matt"
    try:
        p.volume_ml = float(form["volume_ml"]) if form.get("volume_ml") else None
    except ValueError:
        p.volume_ml = None
    p.barcode = (form.get("barcode") or "").strip()
    p.format_id = int(form["format_id"]) if form.get("format_id") else None
    p.discontinued = bool(form.get("discontinued"))
    p.tags = ";".join(t.strip() for t in (form.get("tags") or "").replace(",", ";").split(";") if t.strip())
    set_oklab(p)


@router.post("/paints")
async def paint_create(request: Request, session: Session = Depends(get_session)):
    form = dict(await request.form())
    if not form.get("name", "").strip() or not form.get("brand", "").strip() or not form.get("line", "").strip():
        raise HTTPException(400, "Brand, line and name are required")
    ensure_formats(session)
    brand = get_or_create_brand(session, form["brand"].strip())
    line = get_or_create_line(session, brand, form["line"].strip())
    key = form.get("code", "").strip() or form["name"].strip().lower()
    dup = session.scalar(select(Paint).where(Paint.line_id == line.id, Paint.key == key))
    if dup:
        return redirect(f"/paints/{dup.id}", warn="That paint already exists in this line.")
    p = Paint(line_id=line.id, key=key, source="user")
    p.line = line
    _apply_paint_form(p, session, form)
    session.add(p)
    session.flush()
    fts.index_entity(session, "paint", p.id)
    session.commit()
    return redirect(f"/paints/{p.id}", msg="Paint added to the catalogue.")


@router.get("/paints/{paint_id}")
def paint_detail(request: Request, paint_id: int, session: Session = Depends(get_session)):
    p = session.get(Paint, paint_id)
    if not p:
        raise HTTPException(404)
    items = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.paint_id == p.id)
        .options(selectinload(InventoryItem.cell).selectinload(StorageCell.unit))
    ).all()
    similar = nearest_paints(session, p.display_hex, limit=12, exclude_paint_id=p.id) if p.display_hex else []
    other_brands = (
        [m for m in nearest_paints(session, p.display_hex, limit=40, exclude_paint_id=p.id) if m.obj.line.brand_id != p.line.brand_id][:8]
        if p.display_hex
        else []
    )
    mixes = session.scalars(
        select(MixRecipe).where(MixRecipe.id.in_(select(MixComponent.mix_id).where(MixComponent.paint_id == p.id)))
    ).all()
    links = session.scalars(
        select(ReferenceLink)
        .options(selectinload(ReferenceLink.reference))
        .where(ReferenceLink.entity_type == "paint", ReferenceLink.entity_id == p.id)
    ).all()
    refs_all = session.scalars(select(ReferenceColour).order_by(ReferenceColour.system, ReferenceColour.code)).all()
    formats = session.scalars(select(BottleFormat).order_by(BottleFormat.name)).all()
    return render(
        request,
        "paint.html",
        paint=p,
        items=items,
        similar=similar,
        other_brands=other_brands,
        mixes=mixes,
        links=links,
        refs_all=refs_all,
        formats=formats,
        non_mixing=p.type in NON_MIXING_TYPES,
        barcodes=paint_barcodes(session, p.id),
    )


@router.get("/paints/{paint_id}/edit")
def paint_edit_form(request: Request, paint_id: int, session: Session = Depends(get_session)):
    p = session.get(Paint, paint_id)
    if not p:
        raise HTTPException(404)
    brands = session.scalars(select(Brand).options(selectinload(Brand.lines)).order_by(Brand.name)).all()
    formats = session.scalars(select(BottleFormat).order_by(BottleFormat.name)).all()
    return render(request, "paint_form.html", paint=p, brands=brands, formats=formats)


@router.post("/paints/{paint_id}/edit")
async def paint_edit(request: Request, paint_id: int, session: Session = Depends(get_session)):
    p = session.get(Paint, paint_id)
    if not p:
        raise HTTPException(404)
    form = dict(await request.form())
    _apply_paint_form(p, session, form)
    if p.source == "user":
        p.key = p.code or p.name.lower()
    fts.index_entity(session, "paint", p.id)
    session.commit()
    return redirect(f"/paints/{p.id}", msg="Saved.")


@router.post("/paints/{paint_id}/delete")
def paint_delete(paint_id: int, session: Session = Depends(get_session)):
    p = session.get(Paint, paint_id)
    if not p:
        raise HTTPException(404)
    if p.items or session.scalar(select(func.count()).select_from(MixComponent).where(MixComponent.paint_id == p.id)):
        return redirect(f"/paints/{p.id}", warn="This paint has bottles or is used in mixes, so it can't be deleted.")
    if p.source != "user":
        return redirect(f"/paints/{p.id}", warn="Seeded paints come back on the next catalogue import; mark it discontinued instead.")
    session.execute(text("DELETE FROM search_index WHERE entity_type='paint' AND entity_id=:i"), {"i": p.id})
    session.delete(p)
    session.commit()
    return redirect("/paints", msg="Paint deleted.")


@router.post("/paints/{paint_id}/merge")
def paint_merge(paint_id: int, into_id: int = Form(...), session: Session = Depends(get_session)):
    """Duplicate merge: move bottles, mix uses and links from this paint to another, then delete it."""
    src, dst = session.get(Paint, paint_id), session.get(Paint, into_id)
    if not src or not dst or src.id == dst.id:
        raise HTTPException(400, "Pick a different paint to merge into")
    if src.source != "user":
        return redirect(f"/paints/{src.id}", warn="Only paints you added yourself can be merged away.")
    for it in session.scalars(select(InventoryItem).where(InventoryItem.paint_id == src.id)).all():
        it.paint_id = dst.id
    for c in session.scalars(select(MixComponent).where(MixComponent.paint_id == src.id)).all():
        c.paint_id = dst.id
    for bl in session.scalars(select(BarcodeLink).where(BarcodeLink.paint_id == src.id)).all():
        bl.paint_id = dst.id
    from ..models import Attachment, Note

    for model in (Note, Attachment, ReferenceLink):
        for row in session.scalars(select(model).where(model.entity_type == "paint", model.entity_id == src.id)).all():
            row.entity_id = dst.id
    session.flush()
    session.execute(text("DELETE FROM search_index WHERE entity_type='paint' AND entity_id=:i"), {"i": src.id})
    session.delete(src)
    session.flush()
    fts.index_entity(session, "paint", dst.id)
    session.commit()
    return redirect(f"/paints/{dst.id}", msg="Merged.")


@router.get("/colours")
def colour_search(
    request: Request,
    hex: str = "",
    owned: int = 0,
    source: str = "colour_search",
    context: str = "",
    session: Session = Depends(get_session),
):
    target = normalise_hex(hex)
    matches, owned_mixes, req, mix_suggestions = [], [], None, []
    if target:
        matches = nearest_paints(session, target, limit=30, owned_only=bool(owned))
        owned_mixes = nearest_owned_mixes(session, target, limit=5)
        if source not in ("colour_search", "closest_owned", "reference", "mix_target"):
            source = "colour_search"
        if owned:
            source = "closest_owned" if source == "colour_search" else source
        req = log_colour_request(session, target, source, context)
        session.commit()
        owned_mixable = session.scalars(
            select(Paint)
            .where(Paint.id.in_(select(InventoryItem.paint_id).where(InventoryItem.paint_id.is_not(None))))
            .where(Paint.type.not_in(NON_MIXING_TYPES))
            .options(selectinload(Paint.line).selectinload(ProductLine.brand))
        ).all()
        mix_suggestions = suggest_mixes(list(owned_mixable), target, limit=3)
    counts = owned_counts(session, [m.obj.id for m in matches])
    return render(
        request,
        "colours.html",
        hex=target or "",
        owned=owned,
        matches=matches,
        owned_mixes=owned_mixes,
        counts=counts,
        req=req,
        mix_suggestions=mix_suggestions,
    )


@router.get("/references")
def reference_list(request: Request, session: Session = Depends(get_session)):
    refs = session.scalars(select(ReferenceColour).order_by(ReferenceColour.nation, ReferenceColour.system, ReferenceColour.code)).all()
    return render(request, "references.html", refs=refs)


@router.get("/references/{ref_id}")
def reference_detail(request: Request, ref_id: int, session: Session = Depends(get_session)):
    r = session.get(ReferenceColour, ref_id)
    if not r:
        raise HTTPException(404)
    links = session.scalars(select(ReferenceLink).where(ReferenceLink.reference_id == r.id)).all()
    linked_paints = [session.get(Paint, l.entity_id) for l in links if l.entity_type == "paint"]
    linked_mixes = [session.get(MixRecipe, l.entity_id) for l in links if l.entity_type == "mix"]
    nearest = nearest_paints(session, r.hex, limit=15) if r.hex else []
    nearest_owned = nearest_paints(session, r.hex, limit=5, owned_only=True) if r.hex else []
    counts = owned_counts(session, [m.obj.id for m in nearest])
    return render(
        request,
        "reference.html",
        ref=r,
        linked_paints=[p for p in linked_paints if p],
        linked_mixes=[m for m in linked_mixes if m],
        nearest=nearest,
        nearest_owned=nearest_owned,
        counts=counts,
    )


@router.post("/references/link")
def reference_link(
    reference_id: int = Form(...),
    entity_type: str = Form(...),
    entity_id: int = Form(...),
    back: str = Form("/"),
    session: Session = Depends(get_session),
):
    if entity_type not in ("paint", "mix"):
        raise HTTPException(400)
    exists = session.scalar(
        select(ReferenceLink).where(
            ReferenceLink.reference_id == reference_id,
            ReferenceLink.entity_type == entity_type,
            ReferenceLink.entity_id == entity_id,
        )
    )
    if not exists:
        session.add(ReferenceLink(reference_id=reference_id, entity_type=entity_type, entity_id=entity_id))
        session.flush()
        fts.index_entity(session, entity_type, entity_id)
        session.commit()
    return redirect(back if back.startswith("/") else "/")


@router.post("/references/unlink/{link_id}")
def reference_unlink(link_id: int, back: str = Form("/"), session: Session = Depends(get_session)):
    l = session.get(ReferenceLink, link_id)
    if l:
        t, i = l.entity_type, l.entity_id
        session.delete(l)
        session.flush()
        fts.index_entity(session, t, i)
        session.commit()
    return redirect(back if back.startswith("/") else "/")


@router.get("/brands/{brand_id}")
def brand_detail(request: Request, brand_id: int, session: Session = Depends(get_session)):
    b = session.get(Brand, brand_id)
    if not b:
        raise HTTPException(404)
    line_counts = dict(
        session.execute(
            select(Paint.line_id, func.count()).join(ProductLine).where(ProductLine.brand_id == b.id).group_by(Paint.line_id)
        ).all()
    )
    owned = dict(
        session.execute(
            select(Paint.line_id, func.count(InventoryItem.id))
            .join(InventoryItem, InventoryItem.paint_id == Paint.id)
            .join(ProductLine)
            .where(ProductLine.brand_id == b.id)
            .group_by(Paint.line_id)
        ).all()
    )
    return render(request, "brand.html", brand=b, line_counts=line_counts, owned=owned)


@router.get("/import")
def import_form(request: Request, session: Session = Depends(get_session)):
    brands = session.scalars(select(Brand).options(selectinload(Brand.lines)).order_by(Brand.name)).all()
    return render(request, "import.html", brands=brands)


@router.post("/import")
async def import_csv(
    brand: str = Form(...), line: str = Form(...), file: UploadFile = None, session: Session = Depends(get_session)
):
    if not file:
        raise HTTPException(400, "No file")
    data = await file.read()
    try:
        created, updated = import_user_csv(session, brand, line, data)
    except (KeyError, UnicodeDecodeError) as e:
        return redirect("/import", warn=f"Couldn't read that CSV: {e}")
    session.flush()
    for (pid,) in session.execute(
        select(Paint.id).join(ProductLine).join(Brand).where(Brand.name == brand.strip(), ProductLine.name == line.strip())
    ).all():
        fts.index_entity(session, "paint", pid)
    session.commit()
    return redirect("/import", msg=f"Imported {created} new and updated {updated} paints.")
