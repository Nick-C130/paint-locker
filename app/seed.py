"""Catalogue importer. Runs on startup; upserts by brand + line + code (or name when there's no code).

Never touches inventory, notes, mixes or user-added paints. A seeded paint's user-edited fields
(measured colour, own format override) are left alone; catalogue fields are refreshed.
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .colour import hex_to_oklab, normalise_hex
from .models import (
    PAINT_TYPES,
    BottleFormat,
    Brand,
    Paint,
    ProductLine,
    ReferenceColour,
    Setting,
)

log = logging.getLogger("paintlocker.seed")

DEFAULT_FORMATS = [
    ("17-18 ml dropper", 18, "Vallejo, The Army Painter, AK 3rd Gen, Scale 75 and similar dropper bottles"),
    ("10 ml dropper", 10, "AK Real Colors and other small droppers"),
    ("30-35 ml dropper", 32, "Vallejo Metal Color and other tall droppers"),
    ("60 ml bottle", 60, "Large primer and varnish bottles"),
    ("Citadel pot", 12, "Citadel 12 ml / 18 ml / 24 ml flip-top pots"),
    ("Tamiya 10 ml jar", 10, "Tamiya Mini acrylic and LP jars"),
    ("Spray can", 400, "Rattle cans"),
    ("Jar / other", None, "Pigment jars, washes, anything odd-shaped"),
]

DEFAULT_SETTINGS = {
    "drops_per_ml": "20",
}

TRUTHY = {"1", "true", "yes", "y", "x"}


def get_setting(session: Session, key: str) -> str:
    s = session.get(Setting, key)
    return s.value if s else DEFAULT_SETTINGS.get(key, "")


def set_setting(session: Session, key: str, value: str) -> None:
    s = session.get(Setting, key)
    if s:
        s.value = value
    else:
        session.add(Setting(key=key, value=value))


def _float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def ensure_formats(session: Session) -> dict[str, BottleFormat]:
    existing = {f.name: f for f in session.scalars(select(BottleFormat)).all()}
    for name, vol, notes in DEFAULT_FORMATS:
        if name not in existing:
            f = BottleFormat(name=name, volume_ml=vol, notes=notes)
            session.add(f)
            existing[name] = f
    session.flush()
    return existing


def get_or_create_brand(session: Session, name: str) -> Brand:
    b = session.scalar(select(Brand).where(Brand.name == name))
    if not b:
        b = Brand(name=name)
        session.add(b)
        session.flush()
    return b


def get_or_create_line(session: Session, brand: Brand, name: str, **defaults) -> ProductLine:
    line = session.scalar(select(ProductLine).where(ProductLine.brand_id == brand.id, ProductLine.name == name))
    if not line:
        line = ProductLine(brand_id=brand.id, name=name, **defaults)
        session.add(line)
        session.flush()
    return line


def set_oklab(p: Paint) -> None:
    h = p.measured_hex or p.hex
    if h:
        p.ok_l, p.ok_a, p.ok_b = hex_to_oklab(h)
    else:
        p.ok_l = p.ok_a = p.ok_b = None


def import_paint_rows(
    session: Session,
    line: ProductLine,
    rows,
    formats: dict[str, BottleFormat],
    source: str = "seed",
) -> tuple[int, int]:
    """Upsert paints from dict rows into a line. Returns (created, updated)."""
    existing = {p.key: p for p in session.scalars(select(Paint).where(Paint.line_id == line.id)).all()}
    created = updated = 0
    for row in rows:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        code = (row.get("code") or "").strip()
        key = code or name.lower()
        ptype = (row.get("type") or line.default_type or "acrylic").strip().lower()
        if ptype not in PAINT_TYPES:
            ptype = line.default_type
        fmt_name = (row.get("format") or "").strip()
        values = dict(
            code=code,
            name=name,
            hex=normalise_hex(row.get("hex")),
            type=ptype,
            finish=(row.get("finish") or line.default_finish or "matt").strip().lower(),
            volume_ml=_float(row.get("volume_ml")) or line.default_volume_ml,
            barcode=(row.get("barcode") or "").strip(),
            discontinued=(str(row.get("discontinued") or "").strip().lower() in TRUTHY) or line.discontinued,
            tags=(row.get("tags") or "").strip(),
        )
        p = existing.get(key)
        if p is None:
            p = Paint(line_id=line.id, key=key, source=source, **values)
            if fmt_name and fmt_name in formats:
                p.format = formats[fmt_name]
            session.add(p)
            existing[key] = p
            created += 1
        else:
            changed = False
            for k, v in values.items():
                if getattr(p, k) != v:
                    setattr(p, k, v)
                    changed = True
            if fmt_name and fmt_name in formats and p.format_id is None:
                p.format = formats[fmt_name]
                changed = True
            updated += int(changed)
        set_oklab(p)
    return created, updated


def import_catalogues(session: Session, catalogue_dir: Path | None = None) -> dict:
    catalogue_dir = catalogue_dir or config.CATALOGUE_DIR
    formats = ensure_formats(session)
    stats = {"lines": 0, "created": 0, "updated": 0, "references": 0}
    # lines.csv is generated from the upstream dataset; lines-extra.csv is hand-maintained.
    for manifest in (catalogue_dir / "lines.csv", catalogue_dir / "lines-extra.csv"):
        if not manifest.exists():
            continue
        with open(manifest, newline="", encoding="utf-8") as fh:
            for entry in csv.DictReader(fh):
                path = catalogue_dir / entry["file"]
                if not path.exists():
                    log.warning("catalogue file missing: %s", path)
                    continue
                brand = get_or_create_brand(session, entry["brand"].strip())
                line = get_or_create_line(session, brand, entry["line"].strip())
                line.default_type = entry.get("default_type") or "acrylic"
                line.default_finish = entry.get("default_finish") or "matt"
                line.default_volume_ml = _float(entry.get("default_volume_ml"))
                fmt = formats.get((entry.get("default_format") or "").strip())
                if fmt:
                    line.default_format = fmt
                line.discontinued = (entry.get("discontinued") or "").strip().lower() in TRUTHY
                line.stocked_by = entry.get("stocked_by") or ""
                line.source = entry.get("source") or ""
                with open(path, newline="", encoding="utf-8") as pf:
                    c, u = import_paint_rows(session, line, csv.DictReader(pf), formats)
                stats["lines"] += 1
                stats["created"] += c
                stats["updated"] += u
    ref_dir = catalogue_dir / "reference"
    if ref_dir.exists():
        for path in sorted(ref_dir.glob("*.csv")):
            with open(path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    system, code = row["system"].strip(), row["code"].strip()
                    r = session.scalar(
                        select(ReferenceColour).where(ReferenceColour.system == system, ReferenceColour.code == code)
                    )
                    if not r:
                        r = ReferenceColour(system=system, code=code, name=row["name"].strip())
                        session.add(r)
                    r.name = row["name"].strip()
                    r.aliases = row.get("aliases", "")
                    r.nation = row.get("nation", "")
                    r.hex = normalise_hex(row.get("hex"))
                    r.source = row.get("source", "")
                    stats["references"] += 1
    for k, v in DEFAULT_SETTINGS.items():
        if session.get(Setting, k) is None:
            session.add(Setting(key=k, value=v))
    session.flush()
    return stats


def import_user_csv(session: Session, brand_name: str, line_name: str, data: bytes) -> tuple[int, int]:
    """Bulk import from an uploaded CSV (same columns as the catalogue files)."""
    formats = ensure_formats(session)
    brand = get_or_create_brand(session, brand_name.strip())
    line = get_or_create_line(session, brand, line_name.strip())
    text = data.decode("utf-8-sig")
    return import_paint_rows(session, line, csv.DictReader(io.StringIO(text)), formats, source="user")
