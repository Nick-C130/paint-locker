"""Printable QR labels for rack cells, bottles, mixes, recipes and projects, and the short /q/ links they hold."""

from __future__ import annotations

import segno
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from markupsafe import Markup
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_session
from ..models import InventoryItem, MixRecipe, Project, Recipe, StorageCell, StorageUnit
from ..seed import get_setting, set_setting
from ..web import redirect, render

router = APIRouter()

SIZES = {"small": 25, "medium": 38, "large": 50}  # label width in mm
KINDS = {"c": "cell", "i": "item", "m": "mix", "r": "recipe", "p": "project", "u": "unit"}


def base_url(request: Request, session: Session) -> str:
    return (get_setting(session, "public_url") or str(request.base_url)).rstrip("/")


def _ids(raw: str) -> list[int]:
    return [int(x) for x in raw.split(",") if x.strip().isdigit()]


@router.get("/q/{kind}/{oid}")
def short_link(kind: str, oid: int, session: Session = Depends(get_session)):
    """Where a scanned label goes."""
    if kind == "c":
        cell = session.get(StorageCell, oid)
        if not cell:
            raise HTTPException(404)
        if cell.items:
            return RedirectResponse(f"/storage/{cell.unit_id}?highlight=item:{cell.items[0].id}", 303)
        return RedirectResponse(f"/storage/{cell.unit_id}?msg=Cell+{cell.label}+is+empty", 303)
    targets = {"i": "/items/{}", "m": "/mixes/{}", "r": "/recipes/{}", "p": "/projects/{}", "u": "/storage/{}"}
    if kind not in targets:
        raise HTTPException(404)
    return RedirectResponse(targets[kind].format(oid), 303)


def _label(kind: str, oid: int, title: str, lines: list[str], hex_: str | None = None, cat: str = "") -> dict:
    return {"kind": kind, "id": oid, "title": title, "lines": [x for x in lines if x], "hex": hex_, "cat": cat}


@router.get("/labels")
def labels(
    request: Request,
    unit: str = "",
    cells: str = "all",  # all | filled
    mixes: str = "",
    items: str = "",
    recipe: str = "",
    project: str = "",
    size: str = "medium",
    session: Session = Depends(get_session),
):
    out: list[dict] = []
    for uid in _ids(unit):
        u = session.get(StorageUnit, uid)
        if not u:
            continue
        for c in sorted(u.cells, key=lambda c: (c.row, c.col)):
            if c.disabled or (cells == "filled" and not c.items):
                continue
            it = c.items[0] if c.items else None
            out.append(_label("c", c.id, f"{u.name} · {c.label}",
                              [it.full_label if it else ""],
                              it.hex if it else None, it.category if it else ""))
    mix_ids = _ids(mixes)
    if mixes == "all":
        mix_ids = list(session.scalars(select(MixRecipe.id).order_by(MixRecipe.name)).all())
    for mid in mix_ids:
        m = session.get(MixRecipe, mid)
        if m:
            parts = " + ".join(c.label.split(": ")[-1] for c in m.components)
            out.append(_label("m", m.id, m.name, [m.ratio + ("  " + parts if len(parts) < 70 else ""), m.intended_use], m.display_hex, "mix"))
    for iid in _ids(items):
        it = session.get(InventoryItem, iid)
        if it:
            made = f"{'made' if it.mix_id else 'bought'} {it.acquired_on:%d %b %Y}" if it.acquired_on else ""
            out.append(_label("i", it.id, it.full_label, [it.mix.ratio if it.mix else "", made], it.hex, it.category))
    for rid in _ids(recipe):
        r = session.get(Recipe, rid)
        if r:
            out.append(_label("r", r.id, r.name, [f"{len(r.steps)} steps"], next((s.hex for s in r.steps if s.hex), None)))
    for pid in _ids(project):
        p = session.get(Project, pid)
        if p:
            out.append(_label("p", p.id, p.name, [p.kind], None))
    base = base_url(request, session)
    for lb in out:
        lb["url"] = f"{base}/q/{lb['kind']}/{lb['id']}"
        lb["svg"] = Markup(segno.make(lb["url"], error="m").svg_inline(scale=1, border=1, dark="#000", light="#fff", omitsize=True))
    units = session.scalars(select(StorageUnit).options(selectinload(StorageUnit.cells)).order_by(StorageUnit.sort_order, StorageUnit.id)).all()
    return render(
        request,
        "labels.html",
        labels=out,
        base=base,
        public_url=get_setting(session, "public_url"),
        size=size if size in SIZES else "medium",
        mm=SIZES.get(size, 38),
        sizes=SIZES,
        units=units,
        f={"unit": unit, "cells": cells, "mixes": mixes, "items": items, "recipe": recipe, "project": project},
    )


@router.post("/labels/public-url")
def set_public_url(public_url: str = Form(""), back: str = Form("/labels"), session: Session = Depends(get_session)):
    url = public_url.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        return redirect(back if back.startswith("/") else "/labels", warn="The address must start with http:// or https://")
    set_setting(session, "public_url", url)
    session.commit()
    return redirect(back if back.startswith("/") else "/labels", msg="Label address saved.")
