"""Import a paint collection from a spreadsheet (CSV, TSV, XLSX), another app's export, or a pasted list.

1. parse_*: file or text -> header + rows (list of str lists)
2. guess_mapping: header names -> our fields (brand, line, code, name, …)
3. match_rows: each row -> catalogue paint (barcode, code, name, then full-text search as a guess)
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import Brand, Paint, ProductLine, StorageUnit

FIELDS = ["brand", "line", "code", "name", "barcode", "quantity", "owned", "storage", "cell", "acquired_on", "notes", "item"]
FIELD_LABELS = {
    "brand": "Brand", "line": "Range / line", "code": "Code", "name": "Paint name", "barcode": "Barcode",
    "quantity": "Quantity", "owned": "Owned? (yes/no)", "storage": "Rack / storage", "cell": "Cell / slot",
    "acquired_on": "Date bought", "notes": "Notes", "item": "Code or name (either)",
}
ALIASES = {
    "brand": ["brand", "manufacturer", "maker", "company", "make", "brand name"],
    "line": ["line", "range", "series", "product line", "collection", "product range", "paint range", "set"],
    "code": ["code", "sku", "number", "ref", "reference", "item number", "product code", "paint code", "colour code", "color code", "no", "no."],
    "name": ["name", "paint", "paint name", "colour", "color", "colour name", "color name", "title", "description"],
    "barcode": ["barcode", "ean", "upc", "gtin", "ean13", "ean-13"],
    "quantity": ["quantity", "qty", "count", "bottles", "number owned", "how many", "amount", "stock"],
    "owned": ["owned", "have", "in collection", "own", "status", "inventory"],
    "storage": ["storage_unit", "storage unit", "rack", "storage", "location", "shelf", "box", "drawer"],
    "cell": ["cell", "slot", "position", "spot"],
    "acquired_on": ["acquired_on", "acquired", "bought", "purchased", "date", "date bought", "purchase date"],
    "notes": ["notes", "note", "comment", "comments"],
}
TRUTHY = {"yes", "y", "true", "1", "owned", "have", "x", "✓", "✔", "in stock", "in collection"}
FALSY = {"no", "n", "false", "0", "wishlist", "wanted", "want", "need", "out", "empty", ""}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9.]+", " ", (s or "").lower()).strip()


def _code_key(s: str) -> str:
    return re.sub(r"[\s\-_/]+", "", (s or "").lower())


def parse_csv(data: bytes) -> tuple[list[str], list[list[str]]]:
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    return _split_header(rows)


def parse_xlsx(data: bytes) -> tuple[list[str], list[list[str]]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = []
    for r in ws.iter_rows(values_only=True):
        vals = ["" if v is None else (v.date().isoformat() if hasattr(v, "date") and hasattr(v, "hour") else str(v)) for v in r]
        if any(v.strip() for v in vals):
            rows.append(vals)
    wb.close()
    return _split_header(rows)


def parse_text(text: str) -> tuple[list[str], list[list[str]]]:
    """A pasted list: one paint per line ("70.950", "Vallejo 70.950 Black", "2x Abaddon Black")."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and ("," in lines[0] or "\t" in lines[0]) and len(lines) > 1:
        return parse_csv("\n".join(lines).encode())
    rows = []
    for ln in lines:
        m = re.match(r"^(\d+)\s*[x×]\s+(.*)$", ln, re.I) or re.match(r"^(.*?)\s+[x×]\s*(\d+)$", ln, re.I)
        if m:
            a, b = m.groups()
            qty, item = (a, b) if a.isdigit() else (b, a)
            rows.append([item.strip(" -•*"), qty])
        else:
            rows.append([ln.strip(" -•*"), "1"])
    return ["item", "quantity"], rows


def _split_header(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    if not rows:
        return [], []
    header = [h.strip() for h in rows[0]]
    known = sum(1 for h in header if guess_field(h))
    if known == 0:  # no header row: treat the first column as code-or-name
        return ["item"] + [f"column {i + 2}" for i in range(len(rows[0]) - 1)], rows
    width = len(header)
    return header, [(r + [""] * width)[:width] for r in rows[1:]]


def guess_field(header: str) -> str | None:
    h = _norm(header).replace("_", " ")
    if h in FIELDS:
        return h
    for f, names in ALIASES.items():
        if h in names:
            return f
    for f, names in ALIASES.items():
        if any(n in h for n in names if len(n) > 3):
            return f
    return None


def guess_mapping(header: list[str]) -> dict[int, str]:
    out, used = {}, set()
    for i, h in enumerate(header):
        f = guess_field(h)
        if f and f not in used:
            out[i] = f
            used.add(f)
    return out


@dataclass
class Row:
    n: int
    raw: dict
    quantity: int = 1
    paint: Paint | None = None
    how: str = ""  # barcode | code | name | guess
    skip: str = ""  # reason it's skipped
    storage: StorageUnit | None = None
    cell_label: str = ""
    acquired_on: str = ""
    notes: str = ""
    alternatives: list[Paint] = field(default_factory=list)

    @property
    def text(self) -> str:
        r = self.raw
        return " ".join(x for x in (r.get("brand"), r.get("line"), r.get("code"), r.get("name"), r.get("item")) if x)


class Matcher:
    def __init__(self, session: Session):
        self.session = session
        self.paints = session.scalars(select(Paint).options(selectinload(Paint.line).selectinload(ProductLine.brand))).all()
        self.by_code: dict[str, list[Paint]] = {}
        self.by_name: dict[str, list[Paint]] = {}
        for p in self.paints:
            if p.code:
                self.by_code.setdefault(_code_key(p.code), []).append(p)
            self.by_name.setdefault(_norm(p.name), []).append(p)
        self.brands = {b.name.lower(): b for b in session.scalars(select(Brand)).all()}
        self.units = {u.name.lower(): u for u in session.scalars(select(StorageUnit)).all()}

    def _brand_ok(self, p: Paint, brand: str, line: str) -> bool:
        if brand and not (_norm(brand) in _norm(p.line.brand.name) or _norm(p.line.brand.name) in _norm(brand)):
            return False
        return not (line and _norm(line) not in _norm(p.line.name) and _norm(p.line.name) not in _norm(line))

    def _pick(self, cands: list[Paint], brand: str, line: str) -> list[Paint]:
        if not cands:
            return []
        strict = [p for p in cands if self._brand_ok(p, brand, line)]
        if strict:
            return strict
        loose = [p for p in cands if self._brand_ok(p, brand, "")]
        return loose or ([] if brand else cands)

    def match(self, r: dict) -> tuple[Paint | None, str, list[Paint]]:
        brand, line = r.get("brand", ""), r.get("line", "")
        if r.get("barcode"):
            from .routers.scan import find_paint_by_barcode

            p = find_paint_by_barcode(self.session, r["barcode"])
            if p:
                return p, "barcode", []
        code = r.get("code", "")
        item = r.get("item", "")
        if not code and item:
            m = re.search(r"\b(\d{2}\.\d{3}|[A-Z]{1,3}-?\d{1,4}[A-Z]?)\b", item)
            if m:
                code = m.group(1)
        if code:
            found = self._pick(self.by_code.get(_code_key(code), []), brand, line)
            if found:
                return found[0], "code", found[1:4]
        name = r.get("name", "") or (item if not code else "")
        if name:
            found = self._pick(self.by_name.get(_norm(name), []), brand, line)
            if found:
                return found[0], "name", found[1:4]
        q = " ".join(x for x in (brand, code, name or item) if x).strip()
        if q:
            from . import search as fts

            hits = [i for t, i in fts.search(self.session, q, types=("paint",), limit=5)]
            ps = [self.session.get(Paint, i) for i in hits]
            ps = [p for p in ps if p and (not brand or self._brand_ok(p, brand, ""))]
            if ps:
                return ps[0], "guess", ps[1:4]
        return None, "", []


def _qty(v: str) -> int | None:
    m = re.search(r"\d+", v or "")
    return int(m.group()) if m else None


def match_rows(session: Session, header: list[str], rows: list[list[str]], mapping: dict[int, str]) -> list[Row]:
    m = Matcher(session)
    out = []
    for n, raw_row in enumerate(rows, 1):
        r = {f: (raw_row[i].strip() if i < len(raw_row) else "") for i, f in mapping.items() if f}
        row = Row(n=n, raw=r)
        if "owned" in r:
            v = _norm(r["owned"])
            q = _qty(v)
            if v in FALSY or q == 0:
                row.skip = "not owned"
            elif q and "quantity" not in r:
                row.quantity = min(q, 50)
            elif v not in TRUTHY and not q:
                row.skip = f"owned = “{r['owned']}”"
        if "quantity" in r:
            q = _qty(r["quantity"])
            if q == 0:
                row.skip = row.skip or "quantity 0"
            elif q:
                row.quantity = min(q, 50)
        if not row.text.strip():
            row.skip = row.skip or "empty"
        if not row.skip:
            row.paint, row.how, row.alternatives = m.match(r)
            if not row.paint:
                row.skip = "no match"
        row.storage = m.units.get((r.get("storage") or "").lower())
        row.cell_label = (r.get("cell") or "").upper().replace(" ", "")
        row.acquired_on = r.get("acquired_on", "")
        row.notes = r.get("notes", "")
        out.append(row)
    return out


def owned_paint_ids(session: Session) -> set[int]:
    from .models import InventoryItem

    return set(session.scalars(select(InventoryItem.paint_id).where(InventoryItem.paint_id.is_not(None)).group_by(InventoryItem.paint_id)).all())



