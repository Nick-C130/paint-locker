"""Purchase recommender page: what to buy next."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_session
from ..models import NON_MIXING_TYPES, ColourRequest, InventoryItem, Paint, ProductLine, ShoppingItem
from ..recommender import coverage, recommend
from ..seed import get_setting, set_setting
from ..web import redirect, render

router = APIRouter()
DISMISSED_KEY = "recommender_dismissed"


def _dismissed(session: Session) -> set[int]:
    try:
        return set(json.loads(get_setting(session, DISMISSED_KEY) or "[]"))
    except ValueError:
        return set()


@router.get("/recommend")
def recommend_page(
    request: Request,
    count: int = 5,
    weight: int = 50,
    scope: str = "lines",
    stocked: int = 0,
    discontinued: int = 0,
    novelty: int = 0,
    brand: int | None = None,
    run: int = 0,
    session: Session = Depends(get_session),
):
    owned_items = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.paint_id.is_not(None))
        .options(selectinload(InventoryItem.paint).selectinload(Paint.line).selectinload(ProductLine.brand))
    ).all()
    owned = {it.paint.id: it.paint for it in owned_items}
    mixable_owned = [p for p in owned.values() if p.type not in NON_MIXING_TYPES]
    owned_lines = {p.line_id for p in owned.values()}
    owned_brands = {p.line.brand_id for p in owned.values()}
    brands = sorted({p.line.brand for p in owned.values()}, key=lambda b: b.name)
    requests = session.scalars(select(ColourRequest).order_by(ColourRequest.id)).all()
    ctx = dict(
        f=dict(count=count, weight=weight, scope=scope, stocked=stocked, discontinued=discontinued, novelty=novelty, brand=brand),
        owned_count=len(owned),
        mixable_count=len(mixable_owned),
        request_count=len(requests),
        brands=brands,
        picks=None,
        cover=None,
        candidate_count=0,
        dismissed_count=len(_dismissed(session)),
    )
    if not run:
        return render(request, "recommend.html", **ctx)

    q = select(Paint).join(ProductLine).where(Paint.type.not_in(NON_MIXING_TYPES), Paint.hex.is_not(None))
    if scope == "lines":
        q = q.where(Paint.line_id.in_(owned_lines or {-1}))
    elif scope == "brands":
        q = q.where(ProductLine.brand_id.in_(owned_brands or {-1}))
    if brand:
        q = q.where(ProductLine.brand_id == brand)
    if not discontinued:
        q = q.where(Paint.discontinued.is_(False))
    if stocked:
        q = q.where(ProductLine.stocked_by != "")
    planned = set(session.scalars(select(ShoppingItem.paint_id).where(ShoppingItem.paint_id.is_not(None))).all())
    skip = set(owned) | _dismissed(session) | planned
    cands = [
        p
        for p in session.scalars(q.options(selectinload(Paint.line).selectinload(ProductLine.brand))).all()
        if p.id not in skip
        and (novelty or not any(w in p.name.lower() for w in ("fluorescent", "fluo", "transparent", "glow")))
    ]
    picks = recommend(mixable_owned, cands, list(requests), count=max(1, min(count, 15)), weight_reach=weight / 100)
    ctx.update(picks=picks, cover=coverage(mixable_owned), candidate_count=len(cands))
    return render(request, "recommend.html", **ctx)


@router.post("/recommend/{paint_id}/dismiss")
def recommend_dismiss(paint_id: int, back: str = Form("/recommend"), session: Session = Depends(get_session)):
    d = _dismissed(session)
    d.add(paint_id)
    set_setting(session, DISMISSED_KEY, json.dumps(sorted(d)))
    session.commit()
    return redirect(back if back.startswith("/recommend") else "/recommend", msg="Dismissed; it won't be suggested again.")


@router.post("/recommend/dismissed/clear")
def recommend_clear(session: Session = Depends(get_session)):
    set_setting(session, DISMISSED_KEY, "[]")
    session.commit()
    return redirect("/recommend", msg="Dismissed paints can be suggested again.")


@router.post("/recommend/{paint_id}/shopping")
def recommend_shopping(
    paint_id: int, ordered: str = Form(""), back: str = Form("/recommend"), session: Session = Depends(get_session)
):
    if not session.get(Paint, paint_id):
        raise HTTPException(404)
    session.add(ShoppingItem(paint_id=paint_id, text="ordered" if ordered else "", done=bool(ordered)))
    session.commit()
    msg = "Marked as ordered." if ordered else "Added to the shopping list."
    return redirect(back if back.startswith("/recommend") else "/recommend", msg=msg)
