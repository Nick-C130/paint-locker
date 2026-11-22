from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from .. import search as fts
from ..colour import delta_e, describe_delta_e, normalise_hex
from ..db import get_session
from ..mixsolver import suggest_mixes
from ..models import (
    ADDITIVE_TYPES,
    BUILTIN_ADDITIVES,
    FINISHES,
    MIX_UNITS,
    NON_MIXING_TYPES,
    BottleFormat,
    InventoryItem,
    MixComponent,
    MixRecipe,
    Paint,
    ProductLine,
    ReferenceColour,
    ReferenceLink,
    StorageCell,
)
from ..services import (
    describe_dilution,
    drops_per_ml,
    log_colour_request,
    mix_contains,
    place_item,
    recompute_mix,
    scale_recipe,
    suggest_cells,
)
from ..web import redirect, render
from .inventory import cell_choices

router = APIRouter()


def _component_json(c: MixComponent) -> dict:
    if c.additive:
        return {"kind": "additive", "id": c.additive, "label": c.label, "hex": None, "type": "additive", "amount": c.amount}
    if c.paint:
        return {
            "kind": "paint",
            "id": c.paint.id,
            "label": c.paint.full_label,
            "hex": c.paint.display_hex,
            "type": c.paint.type,
            "amount": c.amount,
        }
    return {
        "kind": "mix",
        "id": c.sub_mix.id,
        "label": f"Mix: {c.sub_mix.name}",
        "hex": c.sub_mix.display_hex,
        "type": "mix",
        "amount": c.amount,
    }


@router.get("/mixes")
def mix_list(request: Request, session: Session = Depends(get_session)):
    mixes = session.scalars(
        select(MixRecipe)
        .options(selectinload(MixRecipe.components), selectinload(MixRecipe.items))
        .order_by(MixRecipe.name)
    ).all()
    return render(request, "mixes.html", mixes=mixes)


@router.get("/mixes/lab")
def mix_lab(
    request: Request,
    mix_id: int | None = None,
    paint_id: int | None = None,
    target: str = "",
    combo: str = "",
    session: Session = Depends(get_session),
):
    mix = session.get(MixRecipe, mix_id) if mix_id else None
    initial = {
        "id": mix.id if mix else None,
        "name": mix.name if mix else "",
        "description": mix.description if mix else "",
        "intended_use": mix.intended_use if mix else "",
        "finish": mix.finish if mix else "matt",
        "unit": mix.unit if mix else "parts",
        "tags": mix.tags.replace(";", ", ") if mix else "",
        "target_hex": (mix.target_hex if mix else None) or normalise_hex(target) or "",
        "measured_hex": (mix.measured_hex if mix else "") or "",
        "format_id": mix.format_id if mix else None,
        "components": [_component_json(c) for c in mix.components] if mix else [],
    }
    if combo and not mix:
        # "id:parts,id:parts" from a mix suggestion
        for part in combo.split(","):
            pid, _, amt = part.partition(":")
            p = session.get(Paint, int(pid)) if pid.isdigit() else None
            if p:
                try:
                    a = float(amt or 1)
                except ValueError:
                    a = 1.0
                initial["components"].append(
                    {"kind": "paint", "id": p.id, "label": p.full_label, "hex": p.display_hex, "type": p.type, "amount": a}
                )
    if paint_id and not mix:
        p = session.get(Paint, paint_id)
        if p:
            initial["components"].append(
                {"kind": "paint", "id": p.id, "label": p.full_label, "hex": p.display_hex, "type": p.type, "amount": 1}
            )
    formats = session.scalars(select(BottleFormat).order_by(BottleFormat.name)).all()
    refs = session.scalars(select(ReferenceColour).order_by(ReferenceColour.nation, ReferenceColour.system)).all()
    return render(
        request,
        "mix_lab.html",
        initial=initial,
        formats=formats,
        refs=[{"label": r.label, "hex": r.hex} for r in refs if r.hex],
        non_mixing=sorted(NON_MIXING_TYPES - ADDITIVE_TYPES),
        additive_types=sorted(ADDITIVE_TYPES),
        dpm=drops_per_ml(session),
    )


@router.post("/api/mixes")
async def api_mix_save(request: Request, session: Session = Depends(get_session)):
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "A mix needs a name")
    comps = data.get("components") or []
    if not comps:
        raise HTTPException(400, "Add at least one paint")
    if all(c.get("kind") == "additive" for c in comps):
        raise HTTPException(400, "Add at least one paint as well as water")
    mix = session.get(MixRecipe, data["id"]) if data.get("id") else None
    if data.get("id") and not mix:
        raise HTTPException(404)
    if mix is None:
        mix = MixRecipe(name=name)
        session.add(mix)
        session.flush()
    mix.name = name
    mix.description = data.get("description") or ""
    mix.intended_use = data.get("intended_use") or ""
    mix.finish = data.get("finish") if data.get("finish") in FINISHES else "matt"
    mix.unit = data.get("unit") if data.get("unit") in MIX_UNITS else "parts"
    mix.tags = ";".join(t.strip() for t in (data.get("tags") or "").replace(",", ";").split(";") if t.strip())
    mix.target_hex = normalise_hex(data.get("target_hex"))
    mix.measured_hex = normalise_hex(data.get("measured_hex"))
    mix.format_id = int(data["format_id"]) if data.get("format_id") else None
    mix.components.clear()
    session.flush()
    for pos, c in enumerate(comps):
        amount = float(c.get("amount") or 0)
        if amount <= 0:
            continue
        if c.get("kind") == "additive":
            if c.get("id") not in BUILTIN_ADDITIVES:
                raise HTTPException(400, "Unknown additive")
            mix.components.append(MixComponent(additive=c["id"], amount=amount, position=pos))
        elif c.get("kind") == "mix":
            sub_id = int(c["id"])
            if mix_contains(session, sub_id, mix.id):
                raise HTTPException(400, "A mix can't contain itself")
            if not session.get(MixRecipe, sub_id):
                raise HTTPException(404, "Sub-mix not found")
            mix.components.append(MixComponent(sub_mix_id=sub_id, amount=amount, position=pos))
        else:
            if not session.get(Paint, int(c["id"])):
                raise HTTPException(404, "Paint not found")
            mix.components.append(MixComponent(paint_id=int(c["id"]), amount=amount, position=pos))
    session.flush()
    session.refresh(mix)
    recompute_mix(mix)
    if mix.target_hex:
        log_colour_request(session, mix.target_hex, "mix_target", f"Saved mix: {mix.name}")
    session.flush()
    fts.index_entity(session, "mix", mix.id)
    session.commit()
    return {"ok": True, "id": mix.id, "estimated_hex": mix.estimated_hex, "url": f"/mixes/{mix.id}"}


@router.post("/api/colour-requests")
async def api_colour_request(request: Request, session: Session = Depends(get_session)):
    data = await request.json()
    source = data.get("source") if data.get("source") in ("colour_search", "mix_target", "closest_owned", "reference") else "mix_target"
    req = log_colour_request(session, data.get("hex", ""), source, data.get("context", ""))
    if not req:
        raise HTTPException(400, "Bad colour")
    session.commit()
    return {
        "ok": True,
        "best_label": req.best_label,
        "best_delta_e": req.best_delta_e,
    }


@router.get("/api/mix-suggestions")
def api_mix_suggestions(hex: str, max_paints: int = 3, session: Session = Depends(get_session)):
    """Reverse mixing: combinations of paints you own that get close to a target colour."""
    target = normalise_hex(hex)
    if not target:
        raise HTTPException(400, "Bad colour")
    owned = session.scalars(
        select(Paint)
        .where(Paint.id.in_(select(InventoryItem.paint_id).where(InventoryItem.paint_id.is_not(None))))
        .where(Paint.type.not_in(NON_MIXING_TYPES))
        .options(selectinload(Paint.line).selectinload(ProductLine.brand))
    ).all()
    if not owned:
        return {"target": target, "owned_mixable": 0, "suggestions": []}
    sugg = suggest_mixes(list(owned), target, limit=8, max_paints=max(1, min(3, max_paints)))
    return {
        "target": target,
        "owned_mixable": len(owned),
        "suggestions": [
            {
                "hex": s.hex,
                "delta_e": round(s.delta_e, 1),
                "words": describe_delta_e(s.delta_e),
                "ratio": s.ratio,
                "components": [
                    {"kind": "paint", "id": p.id, "label": p.full_label, "hex": p.display_hex, "type": p.type, "amount": a}
                    for p, a in zip(s.paints, s.parts)
                ],
            }
            for s in sugg
        ],
    }


@router.get("/mixes/{mix_id}")
def mix_detail(request: Request, mix_id: int, ml: float = 10, session: Session = Depends(get_session)):
    mix = session.get(MixRecipe, mix_id)
    if not mix:
        raise HTTPException(404)
    dpm = drops_per_ml(session)
    amounts = [c.amount for c in mix.components]
    # Convert stored amounts to a common basis (drops and ml are physical amounts; parts are ratios)
    if mix.unit == "drops":
        basis = [a / dpm for a in amounts]
    else:
        basis = amounts
    scaled = scale_recipe(mix.unit, basis, ml, dpm)
    items = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.mix_id == mix.id)
        .options(selectinload(InventoryItem.cell).selectinload(StorageCell.unit))
    ).all()
    used_in = session.scalars(
        select(MixRecipe).where(MixRecipe.id.in_(select(MixComponent.mix_id).where(MixComponent.sub_mix_id == mix.id)))
    ).all()
    links = session.scalars(
        select(ReferenceLink)
        .options(selectinload(ReferenceLink.reference))
        .where(ReferenceLink.entity_type == "mix", ReferenceLink.entity_id == mix.id)
    ).all()
    refs_all = session.scalars(select(ReferenceColour).order_by(ReferenceColour.system, ReferenceColour.code)).all()
    formats = session.scalars(select(BottleFormat).order_by(BottleFormat.name)).all()
    target_de = delta_e(mix.target_hex, mix.display_hex) if mix.target_hex and mix.display_hex else None
    est_vs_measured = delta_e(mix.estimated_hex, mix.measured_hex) if mix.estimated_hex and mix.measured_hex else None
    warnings = [c.label for c in mix.components if c.paint and c.paint.type in NON_MIXING_TYPES and not c.is_additive]
    return render(
        request,
        "mix.html",
        mix=mix,
        rows=list(zip(mix.components, scaled)),
        ml=ml,
        dpm=dpm,
        items=items,
        used_in=used_in,
        links=links,
        refs_all=refs_all,
        formats=formats,
        groups=cell_choices(session, mix.format),
        suggestion=(suggest_cells(session, mix.format) or [None])[0],
        target_de=target_de,
        est_vs_measured=est_vs_measured,
        warnings=warnings,
        dilution=mix.dilution,
        dilution_words=describe_dilution(mix.dilution),
        today=date.today().isoformat(),
    )


@router.post("/mixes/{mix_id}/measured")
def mix_measured(mix_id: int, measured_hex: str = Form(""), session: Session = Depends(get_session)):
    mix = session.get(MixRecipe, mix_id)
    if not mix:
        raise HTTPException(404)
    mix.measured_hex = normalise_hex(measured_hex)
    session.commit()
    return redirect(f"/mixes/{mix.id}", msg="Measured colour saved." if mix.measured_hex else "Measured colour cleared.")


@router.post("/mixes/{mix_id}/batch")
def mix_batch(
    mix_id: int,
    cell_id: str = Form("auto"),
    volume_ml: str = Form(""),
    format_id: str = Form(""),
    made_on: str = Form(""),
    notes: str = Form(""),
    session: Session = Depends(get_session),
):
    mix = session.get(MixRecipe, mix_id)
    if not mix:
        raise HTTPException(404)
    item = InventoryItem(
        kind="mix",
        mix_id=mix.id,
        volume_ml=float(volume_ml) if volume_ml else None,
        format_id=int(format_id) if format_id else None,
        acquired_on=date.fromisoformat(made_on) if made_on else date.today(),
        notes=notes,
    )
    session.add(item)
    session.flush()
    session.refresh(item)
    if cell_id == "auto":
        cell = (suggest_cells(session, item.effective_format) or [None])[0]
    else:
        cell = session.get(StorageCell, int(cell_id)) if cell_id else None
    try:
        warning = place_item(item, cell)
    except ValueError as e:
        warning = str(e)
        item.cell = None
    session.flush()
    fts.index_entity(session, "mix", mix.id)
    session.commit()
    where = item.cell.full_label if item.cell else "Unsorted"
    return redirect(f"/mixes/{mix.id}", msg=f"Batch made and stored in {where}.", warn=warning)


@router.post("/mixes/{mix_id}/delete")
def mix_delete(mix_id: int, session: Session = Depends(get_session)):
    mix = session.get(MixRecipe, mix_id)
    if not mix:
        raise HTTPException(404)
    if mix.items:
        return redirect(f"/mixes/{mix.id}", warn="Remove its batches first.")
    if session.scalar(select(MixComponent.id).where(MixComponent.sub_mix_id == mix.id).limit(1)):
        return redirect(f"/mixes/{mix.id}", warn="Another mix uses this one, so it can't be deleted.")
    session.execute(text("DELETE FROM search_index WHERE entity_type='mix' AND entity_id=:i"), {"i": mix.id})
    session.execute(text("DELETE FROM reference_links WHERE entity_type='mix' AND entity_id=:i"), {"i": mix.id})
    session.delete(mix)
    session.commit()
    return redirect("/mixes", msg="Mix deleted.")
