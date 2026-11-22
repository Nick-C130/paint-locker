"""Things in the collection worth a look: low bottles, key paints with one bottle, duplicates and
near-identical paints, and old bottles or mix batches.

Each alert has a stable key so it can be dismissed (stored in the `alerts_dismissed` setting).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .colour import delta_e
from .models import InventoryItem, MixComponent, Paint, Project, ProjectPaint, RecipeStep, ShoppingItem, type_category
from .seed import get_setting, set_setting

DISMISSED_KEY = "alerts_dismissed"
KINDS = {
    "low": ("🪫", "Running low"),
    "single": ("☝️", "Only one bottle of a paint you use a lot"),
    "duplicate": ("👯", "Several bottles of the same paint"),
    "similar": ("🎭", "Near-identical paints"),
    "age": ("⏳", "Old bottles to check"),
    "mixage": ("🧪", "Old mix batches"),
}


@dataclass
class Alert:
    kind: str
    key: str
    text: str
    hex: str | None = None
    hex2: str | None = None
    links: list[tuple[str, str]] = field(default_factory=list)  # (label, url)
    item_id: int | None = None
    paint_id: int | None = None


def dismissed(session: Session) -> set[str]:
    try:
        return set(json.loads(get_setting(session, DISMISSED_KEY) or "[]"))
    except ValueError:
        return set()


def dismiss(session: Session, key: str) -> None:
    d = dismissed(session)
    d.add(key)
    set_setting(session, DISMISSED_KEY, json.dumps(sorted(d)))


def clear_dismissed(session: Session) -> None:
    set_setting(session, DISMISSED_KEY, "[]")


def _int_setting(session: Session, key: str, default: int) -> int:
    try:
        return int(get_setting(session, key) or default)
    except ValueError:
        return default


def usage_counts(session: Session) -> Counter:
    """How often each paint is used: mix components, recipe steps and active project paints."""
    c: Counter = Counter()
    for pid in session.scalars(select(MixComponent.paint_id).where(MixComponent.paint_id.is_not(None))).all():
        c[pid] += 1
    for pid in session.scalars(select(RecipeStep.paint_id).where(RecipeStep.paint_id.is_not(None))).all():
        c[pid] += 1
    rows = session.execute(
        select(ProjectPaint.paint_id).join(Project).where(ProjectPaint.paint_id.is_not(None), Project.status == "active")
    ).scalars()
    for pid in rows:
        c[pid] += 1
    return c


def compute(session: Session, today: date | None = None, include_dismissed: bool = False) -> list[Alert]:
    today = today or date.today()
    age_years = _int_setting(session, "alert_age_years", 3)
    mix_months = _int_setting(session, "alert_mix_months", 6)
    similar_de = 1.5

    items = session.scalars(
        select(InventoryItem).options(
            selectinload(InventoryItem.paint).selectinload(Paint.line), selectinload(InventoryItem.mix), selectinload(InventoryItem.cell)
        )
    ).all()
    on_list = set(session.scalars(select(ShoppingItem.paint_id).where(ShoppingItem.done.is_(False))).all())
    by_paint: dict[int, list[InventoryItem]] = defaultdict(list)
    for it in items:
        if it.paint_id:
            by_paint[it.paint_id].append(it)
    uses = usage_counts(session)
    out: list[Alert] = []

    for it in items:
        if it.level == "low":
            out.append(Alert("low", f"low:{it.id}", f"{it.full_label} is running low" + (" (already on the shopping list)" if it.paint_id in on_list else ""),
                             it.hex, links=[("Bottle", f"/items/{it.id}")], item_id=it.id, paint_id=it.paint_id if it.paint_id not in on_list else None))

    for pid, its in by_paint.items():
        p = its[0].paint
        if len(its) == 1 and uses[pid] >= 3 and pid not in on_list:
            out.append(Alert("single", f"single:{pid}", f"{p.full_label}: used in {uses[pid]} mixes/recipes/projects, and you have one bottle",
                             p.display_hex, links=[("Paint", f"/paints/{pid}")], paint_id=pid))
        if len(its) >= 2:
            # key includes the count, so buying another re-raises a dismissed alert
            out.append(Alert("duplicate", f"duplicate:{pid}:{len(its)}", f"{len(its)} bottles of {p.full_label}",
                             p.display_hex, links=[("Paint", f"/paints/{pid}")]))

    owned = [its[0].paint for its in by_paint.values() if its[0].paint.ok_l is not None and its[0].paint.display_hex]
    if len(owned) >= 2:
        lab = np.array([[p.ok_l, p.ok_a, p.ok_b] for p in owned])
        d2 = ((lab[:, None, :] - lab[None, :, :]) ** 2).sum(-1)
        ii, jj = np.where(np.triu(d2 < 0.03**2, k=1))
        for i, j in zip(ii.tolist(), jj.tolist()):
            a, b = owned[i], owned[j]
            if type_category(a.type) != type_category(b.type):
                continue
            de = delta_e(a.display_hex, b.display_hex)
            if de < similar_de:
                lo, hi = sorted((a.id, b.id))
                out.append(Alert("similar", f"similar:{lo}:{hi}", f"{a.full_label} and {b.full_label} are almost the same colour (ΔE {de:.1f})",
                                 a.display_hex, b.display_hex, links=[("First", f"/paints/{a.id}"), ("Second", f"/paints/{b.id}")]))

    for it in items:
        started = it.acquired_on or (it.created_at.date() if it.created_at else None)
        if not started:
            continue
        last = max(started, it.checked_on) if it.checked_on else started
        months = (today.year - last.year) * 12 + today.month - last.month
        if it.mix_id and months >= mix_months:
            out.append(Alert("mixage", f"mixage:{it.id}:{last.isoformat()}", f"Batch of {it.mix.name} is {months} months old. Check it hasn't dried out or separated",
                             it.hex, links=[("Bottle", f"/items/{it.id}")], item_id=it.id))
        elif it.paint_id and months >= age_years * 12:
            out.append(Alert("age", f"age:{it.id}:{last.isoformat()}", f"{it.full_label}: {'bought' if it.acquired_on else 'added'} {months // 12} years ago" + (" (last checked)" if it.checked_on else ""),
                             it.hex, links=[("Bottle", f"/items/{it.id}")], item_id=it.id))

    if not include_dismissed:
        d = dismissed(session)
        out = [a for a in out if a.key not in d]
    return out


def grouped(alerts: list[Alert]) -> list[tuple[str, str, str, list[Alert]]]:
    g: dict[str, list[Alert]] = defaultdict(list)
    for a in alerts:
        g[a.kind].append(a)
    return [(k, KINDS[k][0], KINDS[k][1], g[k]) for k in KINDS if g.get(k)]
