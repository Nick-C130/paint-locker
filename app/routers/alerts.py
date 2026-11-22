from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session

from .. import alerts as al
from ..db import get_session
from ..models import BOTTLE_LEVELS, InventoryItem, ShoppingItem
from ..seed import get_setting, set_setting
from ..web import redirect, render

router = APIRouter()


@router.get("/alerts")
def alerts_page(request: Request, session: Session = Depends(get_session)):
    items = al.compute(session)
    return render(
        request,
        "alerts.html",
        groups=al.grouped(items),
        total=len(items),
        dismissed=len(al.dismissed(session)),
        age_years=get_setting(session, "alert_age_years") or "3",
        mix_months=get_setting(session, "alert_mix_months") or "6",
    )


@router.post("/alerts/dismiss")
def alert_dismiss(key: str = Form(...), session: Session = Depends(get_session)):
    al.dismiss(session, key)
    session.commit()
    return redirect("/alerts")


@router.post("/alerts/dismissed/clear")
def alert_clear(session: Session = Depends(get_session)):
    al.clear_dismissed(session)
    session.commit()
    return redirect("/alerts", msg="Dismissed alerts are back.")


@router.post("/alerts/settings")
def alert_settings(age_years: int = Form(3), mix_months: int = Form(6), session: Session = Depends(get_session)):
    set_setting(session, "alert_age_years", str(max(1, min(age_years, 20))))
    set_setting(session, "alert_mix_months", str(max(1, min(mix_months, 60))))
    session.commit()
    return redirect("/alerts", msg="Saved.")


@router.post("/items/{item_id}/checked")
def item_checked(item_id: int, back: str = Form(""), session: Session = Depends(get_session)):
    it = session.get(InventoryItem, item_id)
    if not it:
        raise HTTPException(404)
    it.checked_on = date.today()
    session.commit()
    return redirect(back if back.startswith("/") else f"/items/{it.id}", msg="Marked as checked today.")


@router.post("/items/{item_id}/level")
def item_level(item_id: int, level: str = Form(""), shopping: str = Form(""), back: str = Form(""), session: Session = Depends(get_session)):
    it = session.get(InventoryItem, item_id)
    if not it:
        raise HTTPException(404)
    it.level = level if level in dict(BOTTLE_LEVELS) else None
    msg = "Level saved."
    if shopping and it.paint_id:
        session.add(ShoppingItem(paint_id=it.paint_id))
        msg += " Added to the shopping list."
    session.commit()
    return redirect(back if back.startswith("/") else f"/items/{it.id}", msg=msg)


@router.post("/alerts/shopping")
def alert_to_shopping(paint_id: int = Form(...), session: Session = Depends(get_session)):
    session.add(ShoppingItem(paint_id=paint_id))
    session.commit()
    return redirect("/alerts", msg="Added to the shopping list.")
