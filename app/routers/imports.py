"""Import your collection from a spreadsheet, another app's export, or a pasted list."""

from __future__ import annotations

import json
import secrets
from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from .. import config
from .. import importer as imp
from .. import search as fts
from ..db import get_session
from ..models import InventoryItem
from ..services import place_item, suggest_cells
from ..web import redirect, render

router = APIRouter()


def _dir():
    d = config.DATA_DIR / "imports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load(token: str) -> dict:
    if not token.isalnum():
        raise HTTPException(404)
    p = _dir() / f"{token}.json"
    if not p.exists():
        raise HTTPException(404, "That import has expired. Upload the file again.")
    return json.loads(p.read_text())


def _mapping(request: Request, header: list[str]) -> dict[int, str]:
    q = request.query_params
    if any(k.startswith("map_") for k in q):
        return {i: q.get(f"map_{i}", "") for i in range(len(header)) if q.get(f"map_{i}", "") in imp.FIELDS}
    return imp.guess_mapping(header)


def _parse_date(v: str) -> date | None:
    v = (v or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d.%m.%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


@router.get("/import/collection")
def import_form(request: Request):
    return render(request, "import_collection.html")


@router.post("/import/collection")
async def import_upload(file: UploadFile | None = None, text: str = Form(""), session: Session = Depends(get_session)):
    header, rows, name = [], [], "pasted list"
    try:
        if file is not None and file.filename:
            data = await file.read()
            if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
                return redirect("/import/collection", warn="That file is too big.")
            name = file.filename
            if name.lower().endswith((".xlsx", ".xlsm")):
                header, rows = imp.parse_xlsx(data)
            else:
                header, rows = imp.parse_csv(data)
        elif text.strip():
            header, rows = imp.parse_text(text)
    except Exception as e:  # noqa: BLE001 - any parse failure is reported to the user
        return redirect("/import/collection", warn=f"Couldn't read that file: {e}")
    if not rows:
        return redirect("/import/collection", warn="No rows found. Upload a CSV or Excel file, or paste a list.")
    token = secrets.token_hex(8)
    (_dir() / f"{token}.json").write_text(json.dumps({"header": header, "rows": rows[:5000], "name": name}))
    return redirect(f"/import/collection/{token}")


def _opts(src) -> dict:
    return {
        "skip_owned": src.get("skip_owned", "0") == "1",
        "guesses": src.get("guesses", "1") == "1",
        "place": src.get("place", "file") if src.get("place") in ("file", "auto", "none") else "file",
    }


@router.get("/import/collection/{token}")
def import_preview(request: Request, token: str, session: Session = Depends(get_session)):
    data = _load(token)
    mapping = _mapping(request, data["header"])
    rows = imp.match_rows(session, data["header"], data["rows"], mapping)
    opts = _opts(request.query_params)
    owned = imp.owned_paint_ids(session)
    for r in rows:
        if r.paint and not r.skip:
            if opts["skip_owned"] and r.paint.id in owned:
                r.skip = "already owned"
            elif r.how == "guess" and not opts["guesses"]:
                r.skip = "unsure match"
    ready = [r for r in rows if r.paint and not r.skip]
    return render(
        request, "import_preview.html", token=token, filename=data["name"], header=data["header"], mapping=mapping,
        rows=rows, ready=ready, bottles=sum(r.quantity for r in ready), opts=opts,
        fields=imp.FIELDS, labels=imp.FIELD_LABELS, sample=data["rows"][:3],
        counts={k: sum(1 for r in rows if r.how == k and not r.skip) for k in ("barcode", "code", "name", "guess")},
        unmatched=sum(1 for r in rows if r.skip == "no match"),
    )


@router.post("/import/collection/{token}/confirm")
async def import_confirm(request: Request, token: str, session: Session = Depends(get_session)):
    data = _load(token)
    form = await request.form()
    header = data["header"]
    mapping = {i: form.get(f"map_{i}", "") for i in range(len(header)) if form.get(f"map_{i}", "") in imp.FIELDS}
    opts = _opts(form)
    rows = imp.match_rows(session, header, data["rows"], mapping)
    owned = imp.owned_paint_ids(session)
    chosen = {int(x) for x in form.getlist("row") if str(x).isdigit()}
    overrides = {int(k[4:]): int(v) for k, v in form.items() if k.startswith("alt_") and str(v).isdigit()}
    added, warnings, touched = 0, [], set()
    for r in rows:
        if r.n not in chosen or not r.paint or r.skip:
            continue
        if opts["skip_owned"] and r.paint.id in owned:
            continue
        pid = overrides.get(r.n, r.paint.id)
        for k in range(r.quantity):
            item = InventoryItem(kind="paint", paint_id=pid, acquired_on=_parse_date(r.acquired_on), notes=r.notes[:500])
            session.add(item)
            session.flush()
            session.refresh(item)
            cell = None
            if opts["place"] == "file" and r.storage and k == 0:
                cell = next((c for c in r.storage.cells if c.label.upper() == r.cell_label), None)
                if cell and (cell.disabled or cell.items):
                    cell = None
            if cell is None and opts["place"] in ("file", "auto"):
                cell = (suggest_cells(session, item.effective_format) or [None])[0]
            try:
                w = place_item(item, cell)
                if w:
                    warnings.append(w)
            except ValueError:
                item.cell = None
            session.flush()
            added += 1
        touched.add(pid)
    for pid in touched:
        fts.index_entity(session, "paint", pid)
    session.commit()
    (_dir() / f"{token}.json").unlink(missing_ok=True)
    return redirect("/items", msg=f"Imported {added} bottle{'s' if added != 1 else ''} of {len(touched)} paint{'s' if len(touched) != 1 else ''}.",
                    warn=f"{len(warnings)} bottles went into racks not tagged for their bottle size." if warnings else None)
