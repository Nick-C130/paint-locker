"""Shops and the shopping list grouped by shop."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_session
from ..models import Brand, Paint, ProductLine, Shop, ShoppingItem
from ..web import redirect, render

router = APIRouter()


def all_shops(session: Session) -> list[Shop]:
    return list(session.scalars(select(Shop).options(selectinload(Shop.brands)).order_by(Shop.sort_order, Shop.name)).all())


def group_by_shop(items: list[ShoppingItem], shops: list[Shop]) -> list[dict]:
    """Each open item goes under its chosen shop, else the first shop that stocks it, else 'Anywhere'."""
    groups: dict[int | None, dict] = {s.id: {"shop": s, "rows": []} for s in shops}
    groups[None] = {"shop": None, "rows": []}
    for it in items:
        stockists = [s for s in shops if it.paint and s.stocks(it.paint)]
        shop = it.shop or (stockists[0] if stockists else None)
        others = [s for s in stockists if shop is None or s.id != shop.id]
        groups[shop.id if shop else None]["rows"].append({"item": it, "auto": it.shop_id is None, "others": others})
    return [g for g in groups.values() if g["rows"]]


def as_text(group: dict) -> str:
    lines = []
    for r in group["rows"]:
        it = r["item"]
        q = f"{it.quantity} × " if it.quantity > 1 else ""
        lines.append(f"- {q}{it.paint.full_label if it.paint else it.text}")
    return "\n".join(lines)


@router.get("/shopping")
def shopping(request: Request, shop: str = "", session: Session = Depends(get_session)):
    items = session.scalars(
        select(ShoppingItem)
        .options(selectinload(ShoppingItem.paint).selectinload(Paint.line).selectinload(ProductLine.brand), selectinload(ShoppingItem.shop))
        .order_by(ShoppingItem.id.desc())
    ).all()
    shops = all_shops(session)
    groups = group_by_shop([i for i in items if not i.done], shops)
    if shop:
        groups = [g for g in groups if (str(g["shop"].id) if g["shop"] else "none") == shop]
    for g in groups:
        g["text"] = as_text(g)
    return render(request, "shopping.html", groups=groups, done=[i for i in items if i.done], shops=shops,
                  only=shop, printing=bool(shop))


@router.post("/shopping/{sid}/shop")
def shopping_set_shop(sid: int, shop_id: str = Form(""), session: Session = Depends(get_session)):
    s = session.get(ShoppingItem, sid)
    if not s:
        raise HTTPException(404)
    s.shop_id = int(shop_id) if shop_id.isdigit() and session.get(Shop, int(shop_id)) else None
    session.commit()
    return redirect("/shopping")


@router.post("/shopping/{sid}/quantity")
def shopping_quantity(sid: int, delta: int = Form(1), session: Session = Depends(get_session)):
    s = session.get(ShoppingItem, sid)
    if not s:
        raise HTTPException(404)
    s.quantity = max(1, min(99, (s.quantity or 1) + delta))
    session.commit()
    return redirect("/shopping")


@router.post("/shopping/clear-bought")
def shopping_clear(session: Session = Depends(get_session)):
    for s in session.scalars(select(ShoppingItem).where(ShoppingItem.done.is_(True))).all():
        session.delete(s)
    session.commit()
    return redirect("/shopping", msg="Cleared bought items.")


# ---------------------------------------------------------------- shops


@router.get("/shops")
def shop_list(request: Request, session: Session = Depends(get_session)):
    brands = session.scalars(select(Brand).order_by(Brand.name)).all()
    return render(request, "shops.html", shops=all_shops(session), brands=brands)


async def _apply(request: Request, shop: Shop, session: Session) -> None:
    form = await request.form()
    shop.name = (form.get("name") or shop.name or "Shop").strip()
    shop.url = (form.get("url") or "").strip()
    shop.search_url = (form.get("search_url") or "").strip()
    shop.notes = form.get("notes") or ""
    try:
        shop.sort_order = int(form.get("sort_order") or 0)
    except ValueError:
        shop.sort_order = 0
    ids = [int(x) for x in form.getlist("brand_ids") if str(x).isdigit()]
    shop.brands = list(session.scalars(select(Brand).where(Brand.id.in_(ids))).all()) if ids else []


@router.post("/shops")
async def shop_create(request: Request, session: Session = Depends(get_session)):
    shop = Shop(name="")
    await _apply(request, shop, session)
    if session.scalar(select(Shop).where(Shop.name == shop.name)):
        return redirect("/shops", warn=f"There's already a shop called {shop.name}.")
    session.add(shop)
    session.commit()
    return redirect("/shops", msg=f"Added {shop.name}.")


@router.post("/shops/{shop_id}/edit")
async def shop_edit(request: Request, shop_id: int, session: Session = Depends(get_session)):
    shop = session.get(Shop, shop_id)
    if not shop:
        raise HTTPException(404)
    await _apply(request, shop, session)
    session.commit()
    return redirect("/shops", msg=f"Saved {shop.name}.")


@router.post("/shops/{shop_id}/delete")
def shop_delete(shop_id: int, session: Session = Depends(get_session)):
    shop = session.get(Shop, shop_id)
    if shop:
        session.delete(shop)
        session.commit()
    return redirect("/shops", msg="Shop removed.")
