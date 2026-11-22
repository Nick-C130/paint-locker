"""Collection statistics."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..alerts import usage_counts
from ..colour import oklab_mix
from ..db import get_session
from ..models import (
    CATEGORY_LABELS,
    InventoryItem,
    MixRecipe,
    Paint,
    ProductLine,
    Project,
    Recipe,
    StorageCell,
    StorageUnit,
)
from ..recommender import _hue_family
from ..web import render

router = APIRouter()

FAMILY_ORDER = [
    "reds", "oranges and browns", "yellows, tans and ochres", "yellow-greens and olives", "greens", "teals",
    "blues", "violets", "purples and magentas", "greys", "darks", "lights",
]


def _bars(counter: Counter | dict, limit: int | None = None, order: list[str] | None = None) -> list[dict]:
    items = [(k, counter[k]) for k in order if counter.get(k)] if order else counter.most_common(limit)
    top = max((v for _, v in items), default=0) or 1
    return [{"label": k, "value": v, "pct": round(100 * v / top, 1)} for k, v in items]


def _mean_hex(hexes: list[str]) -> str:
    return oklab_mix([(h, 1.0) for h in hexes]) if hexes else "#888888"


@router.get("/stats")
def stats(request: Request, session: Session = Depends(get_session)):
    items = session.scalars(
        select(InventoryItem).options(
            selectinload(InventoryItem.paint).selectinload(Paint.line).selectinload(ProductLine.brand), selectinload(InventoryItem.mix)
        )
    ).all()
    paint_items = [it for it in items if it.paint]
    owned = {it.paint_id: it.paint for it in paint_items}

    by_brand = Counter(it.paint.line.brand.name for it in paint_items)
    labels = dict(CATEGORY_LABELS)
    by_cat = Counter(labels[it.category] for it in items)
    fam_hex: dict[str, list[str]] = defaultdict(list)
    for p in owned.values():
        if p.display_hex:
            fam_hex[_hue_family(p.display_hex)].append(p.display_hex)
    families = _bars(Counter({k: len(v) for k, v in fam_hex.items()}), order=FAMILY_ORDER)
    for f in families:
        f["hex"] = _mean_hex(fam_hex[f["label"]])

    uses = usage_counts(session)
    batch_counts = Counter(it.mix_id for it in items if it.mix_id)
    most_used = []
    for pid, n in uses.most_common(12):
        p = session.get(Paint, pid)
        if p:
            most_used.append({"label": f"{p.label} ({p.line.brand.name})", "value": n, "hex": p.display_hex, "url": f"/paints/{p.id}", "owned": pid in owned})
    top = max((m["value"] for m in most_used), default=1)
    for m in most_used:
        m["pct"] = round(100 * m["value"] / top, 1)
    top_mixes = []
    for mid, n in batch_counts.most_common(8):
        m = session.get(MixRecipe, mid)
        if m:
            top_mixes.append({"label": m.name, "value": n, "hex": m.display_hex, "url": f"/mixes/{m.id}"})
    for m in top_mixes:
        m["pct"] = round(100 * m["value"] / top_mixes[0]["value"], 1)

    # bottles added per month, last 12 months
    today = date.today()
    months = []
    for k in range(11, -1, -1):
        y, m = divmod(today.year * 12 + today.month - 1 - k, 12)
        months.append((y, m + 1))
    per_month = Counter()
    for it in items:
        d = it.acquired_on or (it.created_at.date() if it.created_at else None)
        if d:
            per_month[(d.year, d.month)] += 1
    top_m = max((per_month[m] for m in months), default=0) or 1
    timeline = [{"label": date(y, m, 1).strftime("%b %y"), "value": per_month[(y, m)], "pct": round(100 * per_month[(y, m)] / top_m, 1)} for y, m in months]

    racks = []
    for u in session.scalars(select(StorageUnit).options(selectinload(StorageUnit.cells).selectinload(StorageCell.items)).order_by(StorageUnit.sort_order, StorageUnit.id)):
        usable = sum(1 for c in u.cells if not c.disabled)
        used = sum(1 for c in u.cells if c.items)
        racks.append({"label": u.name, "value": used, "of": usable, "pct": round(100 * used / usable, 1) if usable else 0, "url": f"/storage/{u.id}"})

    projects = session.scalars(select(Project).where(Project.parent_id.is_(None))).all()
    done = sum(p.progress[0] for p in projects)
    total = sum(p.progress[1] for p in projects)

    totals = {
        "bottles": len(items),
        "paints": len(owned),
        "brands": len(by_brand),
        "mixes": session.scalar(select(func.count()).select_from(MixRecipe)),
        "recipes": session.scalar(select(func.count()).select_from(Recipe)),
        "batches": sum(batch_counts.values()),
        "models_done": done,
        "models_total": total,
        "ml": round(sum((it.volume_ml or (it.paint.volume_ml if it.paint else 0) or 0) for it in items)),
        "measured": sum(1 for p in owned.values() if p.measured_hex),
    }
    return render(
        request,
        "stats.html",
        t=totals,
        by_brand=_bars(by_brand, 12),
        by_cat=_bars(by_cat),
        families=families,
        most_used=most_used,
        top_mixes=top_mixes,
        timeline=timeline,
        racks=racks,
    )
