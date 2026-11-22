"""Domain logic shared by the routers: storage fit, cell suggestions, mix maths, colour matching."""

from __future__ import annotations

import string
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .colour import delta_e, hex_to_oklab, normalise_hex, spectral_mix
from .models import (
    BottleFormat,
    ColourRequest,
    InventoryItem,
    MixComponent,
    MixRecipe,
    Paint,
    ProductLine,
    StorageCell,
    StorageUnit,
)
from .seed import get_setting

# ---------------------------------------------------------------- storage


def row_label(i: int) -> str:
    letters = string.ascii_uppercase
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = letters[r] + s
    return s


def cell_label(scheme: str, row: int, col: int) -> str:
    if scheme == "1-1":
        return f"{row + 1}-{col + 1}"
    return f"{row_label(row)}{col + 1}"


def build_cells(unit: StorageUnit) -> None:
    """Create any missing cells so the full grid exists; relabel all."""
    existing = {(c.row, c.col): c for c in unit.cells}
    for r in range(unit.rows):
        for c in range(unit.cols):
            cell = existing.get((r, c))
            if cell is None:
                unit.cells.append(StorageCell(row=r, col=c, label=cell_label(unit.label_scheme, r, c)))
            else:
                cell.label = cell_label(unit.label_scheme, r, c)


def cell_free(cell: StorageCell) -> bool:
    return not cell.disabled and len(cell.items) < (cell.capacity or 1)


def unit_fits(unit: StorageUnit, fmt: BottleFormat | None) -> bool | None:
    """True if the unit is tagged for this format, False if tagged for others, None if unknown."""
    if fmt is None or not unit.formats:
        return None
    return any(f.id == fmt.id for f in unit.formats)


def fit_warning(cell: StorageCell | None, item: InventoryItem) -> str | None:
    if cell is None:
        return None
    fmt = item.effective_format
    fits = unit_fits(cell.unit, fmt)
    if fits is False:
        tags = ", ".join(f.name for f in cell.unit.formats)
        return f"{cell.unit.name} is tagged for {tags}; this bottle is a {fmt.name}. It may not fit."
    return None


def all_units(session: Session) -> list[StorageUnit]:
    return list(
        session.scalars(
            select(StorageUnit)
            .options(
                selectinload(StorageUnit.formats),
                selectinload(StorageUnit.cells).selectinload(StorageCell.items),
            )
            .order_by(StorageUnit.sort_order, StorageUnit.id)
        ).all()
    )


def suggest_cells(session: Session, fmt: BottleFormat | None, limit: int = 1) -> list[StorageCell]:
    """Next empty cells, preferring units tagged for the bottle format, then untagged units.
    Units tagged only for other formats are never suggested."""
    tagged, untagged = [], []
    for unit in all_units(session):
        fits = unit_fits(unit, fmt)
        if fits is False:
            continue
        free = [c for c in unit.cells if cell_free(c)]
        (tagged if fits else untagged).extend(free)
    return (tagged + untagged)[:limit]


def place_item(item: InventoryItem, cell: StorageCell | None) -> str | None:
    """Move item into cell (None = unsorted). Returns a fit warning or raises ValueError if full."""
    if cell is not None:
        if cell.disabled:
            raise ValueError(f"{cell.label} is disabled")
        others = [i for i in cell.items if i.id != item.id]
        if len(others) >= (cell.capacity or 1):
            raise ValueError(f"{cell.full_label} is already full")
    item.cell = cell
    return fit_warning(cell, item)


# ---------------------------------------------------------------- mixes


def drops_per_ml(session: Session) -> float:
    try:
        return float(get_setting(session, "drops_per_ml")) or 20.0
    except ValueError:
        return 20.0


def compute_mix_hex(components: list[tuple[str | None, float]]) -> str | None:
    return spectral_mix([(h, a) for h, a in components if h])


def scale_recipe(unit: str, amounts: list[float], total_ml: float, dpm: float) -> list[dict]:
    """Return per-component ml and drops to make total_ml of the mix."""
    total = sum(amounts)
    out = []
    for a in amounts:
        share = a / total if total else 0
        ml = share * total_ml
        out.append({"share": share, "ml": ml, "drops": ml * dpm})
    return out


def mix_contains(session: Session, mix_id: int, target_id: int, depth: int = 0) -> bool:
    """True if mix `mix_id` uses `target_id` anywhere in its tree (cycle guard)."""
    if mix_id == target_id:
        return True
    if depth > 10:
        return True
    subs = session.scalars(
        select(MixComponent.sub_mix_id).where(MixComponent.mix_id == mix_id, MixComponent.sub_mix_id.is_not(None))
    ).all()
    return any(mix_contains(session, s, target_id, depth + 1) for s in subs)


def recompute_mix(mix: MixRecipe) -> None:
    # Water, thinners and mediums dilute the paint but don't change its colour.
    mix.estimated_hex = compute_mix_hex([(c.hex, c.amount) for c in mix.components if not c.is_additive])


def describe_dilution(d: float) -> str:
    """Rough consistency for a given share of water/thinner/medium."""
    if d <= 0:
        return "neat paint"
    if d < 0.2:
        return "lightly thinned"
    if d < 0.4:
        return "thinned (good for layering/airbrushing)"
    if d < 0.6:
        return "glaze consistency"
    if d < 0.85:
        return "wash consistency"
    return "filter / very thin"


# ---------------------------------------------------------------- colour matching


@dataclass
class Match:
    kind: str  # paint | mix | reference
    obj: object
    hex: str
    delta_e: float


def nearest_paints(
    session: Session,
    target_hex: str,
    limit: int = 20,
    owned_only: bool = False,
    exclude_types: set[str] | None = None,
    brand_id: int | None = None,
    exclude_paint_id: int | None = None,
) -> list[Match]:
    target = normalise_hex(target_hex)
    if not target:
        return []
    L, A, B = hex_to_oklab(target)
    dist = (Paint.ok_l - L) * (Paint.ok_l - L) + (Paint.ok_a - A) * (Paint.ok_a - A) + (Paint.ok_b - B) * (Paint.ok_b - B)
    q = select(Paint).where(Paint.ok_l.is_not(None))
    if owned_only:
        q = q.where(Paint.id.in_(select(InventoryItem.paint_id).where(InventoryItem.paint_id.is_not(None))))
    if exclude_types:
        q = q.where(Paint.type.not_in(exclude_types))
    if brand_id:
        q = q.join(ProductLine).where(ProductLine.brand_id == brand_id)
    if exclude_paint_id:
        q = q.where(Paint.id != exclude_paint_id)
    candidates = session.scalars(
        q.options(selectinload(Paint.line).selectinload(ProductLine.brand)).order_by(dist).limit(limit * 4)
    ).all()
    matches = [Match("paint", p, p.display_hex, delta_e(target, p.display_hex)) for p in candidates]
    matches.sort(key=lambda m: m.delta_e)
    return matches[:limit]


def nearest_owned_mixes(session: Session, target_hex: str, limit: int = 5) -> list[Match]:
    target = normalise_hex(target_hex)
    if not target:
        return []
    mixes = session.scalars(
        select(MixRecipe).where(MixRecipe.id.in_(select(InventoryItem.mix_id).where(InventoryItem.mix_id.is_not(None))))
    ).all()
    out = [Match("mix", m, m.display_hex, delta_e(target, m.display_hex)) for m in mixes if m.display_hex]
    out.sort(key=lambda m: m.delta_e)
    return out[:limit]


def best_owned(session: Session, target_hex: str) -> Match | None:
    cands = nearest_paints(session, target_hex, limit=1, owned_only=True) + nearest_owned_mixes(session, target_hex, 1)
    cands.sort(key=lambda m: m.delta_e)
    return cands[0] if cands else None


def log_colour_request(session: Session, target_hex: str, source: str, context: str = "") -> ColourRequest | None:
    target = normalise_hex(target_hex)
    if not target:
        return None
    best = best_owned(session, target)
    req = ColourRequest(
        target_hex=target,
        source=source,
        context=context[:500],
        best_kind=best.kind if best else None,
        best_id=best.obj.id if best else None,
        best_label=(best.obj.full_label if best and best.kind == "paint" else (best.obj.name if best else None)),
        best_delta_e=round(best.delta_e, 2) if best else None,
    )
    session.add(req)
    return req


def owned_counts(session: Session, paint_ids: list[int]) -> dict[int, int]:
    if not paint_ids:
        return {}
    rows = session.execute(
        select(InventoryItem.paint_id, func.count())
        .where(InventoryItem.paint_id.in_(paint_ids))
        .group_by(InventoryItem.paint_id)
    ).all()
    return {pid: n for pid, n in rows}
