from __future__ import annotations

import csv
import io
import json
import tempfile
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from starlette.background import BackgroundTask

from .. import backup, config
from .. import search as fts
from ..db import get_session
from ..models import (
    Base,
    BottleFormat,
    Brand,
    ColourRequest,
    InventoryItem,
    MixRecipe,
    Paint,
    ProductLine,
    StorageCell,
    StorageUnit,
)
from ..seed import get_setting, import_catalogues, set_setting
from ..web import redirect, render

router = APIRouter()


@router.get("/settings")
def settings_page(request: Request, session: Session = Depends(get_session)):
    formats = session.scalars(select(BottleFormat).order_by(BottleFormat.name)).all()
    usage = {
        f.id: session.scalar(select(func.count()).select_from(StorageUnit).where(StorageUnit.formats.any(BottleFormat.id == f.id)))
        for f in formats
    }
    stats = {
        "brands": session.scalar(select(func.count()).select_from(Brand)),
        "lines": session.scalar(select(func.count()).select_from(ProductLine)),
        "paints": session.scalar(select(func.count()).select_from(Paint)),
        "user_paints": session.scalar(select(func.count()).select_from(Paint).where(Paint.source == "user")),
    }
    return render(
        request,
        "settings.html",
        formats=formats,
        usage=usage,
        dpm=get_setting(session, "drops_per_ml"),
        backups=backup.list_backups(),
        stats=stats,
        password_on=bool(config.PASSWORD),
        backup_hour=config.BACKUP_HOUR,
        backup_keep=config.BACKUP_KEEP,
    )


@router.post("/settings")
def settings_save(drops_per_ml: str = Form("20"), session: Session = Depends(get_session)):
    try:
        v = float(drops_per_ml)
        if v <= 0:
            raise ValueError
    except ValueError:
        return redirect("/settings", warn="Drops per ml must be a positive number.")
    set_setting(session, "drops_per_ml", f"{v:g}")
    session.commit()
    return redirect("/settings", msg="Saved.")


@router.post("/formats")
def format_add(name: str = Form(...), volume_ml: str = Form(""), notes: str = Form(""), session: Session = Depends(get_session)):
    name = name.strip()
    if not name:
        return redirect("/settings")
    if session.scalar(select(BottleFormat).where(BottleFormat.name == name)):
        return redirect("/settings", warn="That format already exists.")
    session.add(BottleFormat(name=name, volume_ml=float(volume_ml) if volume_ml else None, notes=notes))
    session.commit()
    return redirect("/settings", msg=f"Added format {name}.")


@router.post("/formats/{fid}/edit")
def format_edit(fid: int, name: str = Form(...), volume_ml: str = Form(""), notes: str = Form(""), session: Session = Depends(get_session)):
    f = session.get(BottleFormat, fid)
    if not f:
        raise HTTPException(404)
    f.name = name.strip() or f.name
    f.volume_ml = float(volume_ml) if volume_ml else None
    f.notes = notes
    session.commit()
    return redirect("/settings", msg="Saved.")


@router.post("/formats/{fid}/delete")
def format_delete(fid: int, session: Session = Depends(get_session)):
    f = session.get(BottleFormat, fid)
    if not f:
        raise HTTPException(404)
    in_use = (
        session.scalar(select(func.count()).select_from(ProductLine).where(ProductLine.default_format_id == fid))
        or session.scalar(select(func.count()).select_from(Paint).where(Paint.format_id == fid))
        or session.scalar(select(func.count()).select_from(InventoryItem).where(InventoryItem.format_id == fid))
        or session.scalar(select(func.count()).select_from(MixRecipe).where(MixRecipe.format_id == fid))
    )
    if in_use:
        return redirect("/settings", warn=f"{f.name} is used by product lines, paints or bottles, so it can't be deleted.")
    session.delete(f)
    session.commit()
    return redirect("/settings", msg="Format deleted.")


@router.post("/catalogue/reimport")
def catalogue_reimport(session: Session = Depends(get_session)):
    stats = import_catalogues(session)
    fts.rebuild(session)
    session.commit()
    return redirect(
        "/settings", msg=f"Catalogue re-imported: {stats['created']} new, {stats['updated']} updated across {stats['lines']} lines."
    )


@router.post("/search/rebuild")
def search_rebuild(session: Session = Depends(get_session)):
    fts.rebuild(session)
    session.commit()
    return redirect("/settings", msg="Search index rebuilt.")


# ---------------------------------------------------------------- export and backup


def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    raise TypeError(type(o))


@router.get("/export/json")
def export_json(session: Session = Depends(get_session)):
    out = {"app": "paint-locker", "exported_at": datetime.now().isoformat(), "tables": {}}
    for table in Base.metadata.sorted_tables:
        rows = session.execute(table.select()).mappings().all()
        out["tables"][table.name] = [dict(r) for r in rows]
    body = json.dumps(out, default=_json_default, indent=1)
    fname = f"paintlocker-{date.today().isoformat()}.json"
    return Response(body, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{fname}"'})


def _csv_response(rows: list[list], header: list[str], fname: str):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{fname}"'}
    )


@router.get("/export/inventory.csv")
def export_inventory(session: Session = Depends(get_session)):
    items = session.scalars(
        select(InventoryItem).options(
            selectinload(InventoryItem.paint).selectinload(Paint.line).selectinload(ProductLine.brand),
            selectinload(InventoryItem.mix),
            selectinload(InventoryItem.cell).selectinload(StorageCell.unit),
        )
    ).all()
    rows = []
    for it in items:
        p = it.paint
        rows.append(
            [
                it.id,
                it.kind,
                p.line.brand.name if p else "",
                p.line.name if p else "",
                p.code if p else "",
                p.name if p else (it.mix.name if it.mix else ""),
                it.hex or "",
                it.cell.unit.name if it.cell else "",
                it.cell.label if it.cell else "",
                it.effective_format.name if it.effective_format else "",
                it.acquired_on or "",
                it.notes,
            ]
        )
    return _csv_response(
        rows,
        ["item_id", "kind", "brand", "line", "code", "name", "hex", "storage_unit", "cell", "format", "acquired_on", "notes"],
        "paintlocker-inventory.csv",
    )


@router.get("/export/mixes.csv")
def export_mixes(session: Session = Depends(get_session)):
    mixes = session.scalars(select(MixRecipe).order_by(MixRecipe.name)).all()
    rows = []
    for m in mixes:
        for c in m.components:
            rows.append([m.id, m.name, m.unit, c.label, c.amount, m.estimated_hex or "", m.measured_hex or ""])
    return _csv_response(
        rows, ["mix_id", "mix", "unit", "component", "amount", "estimated_hex", "measured_hex"], "paintlocker-mixes.csv"
    )


@router.get("/export/colour-requests.csv")
def export_requests(session: Session = Depends(get_session)):
    reqs = session.scalars(select(ColourRequest).order_by(ColourRequest.id)).all()
    rows = [[r.id, r.created_at, r.target_hex, r.source, r.context, r.best_kind or "", r.best_label or "", r.best_delta_e or ""] for r in reqs]
    return _csv_response(
        rows, ["id", "created_at", "target_hex", "source", "context", "best_kind", "best_label", "best_delta_e"], "paintlocker-colour-requests.csv"
    )


@router.get("/export/db")
def export_db():
    tmp = Path(tempfile.mkdtemp()) / f"paintlocker-{date.today().isoformat()}.db"
    backup.backup_to(tmp)

    def cleanup():
        tmp.unlink(missing_ok=True)
        tmp.parent.rmdir()

    return FileResponse(tmp, filename=tmp.name, media_type="application/x-sqlite3", background=BackgroundTask(cleanup))


@router.post("/backup-now")
def backup_now():
    p = backup.nightly_backup()
    return redirect("/settings", msg=f"Backup written: {p.name}")


@router.get("/backups/{name}")
def backup_download(name: str):
    for p in backup.list_backups():
        if p.name == name:
            return FileResponse(p, filename=p.name, media_type="application/x-sqlite3")
    raise HTTPException(404)


@router.get("/colour-requests")
def colour_requests(request: Request, session: Session = Depends(get_session)):
    reqs = session.scalars(select(ColourRequest).order_by(ColourRequest.id.desc()).limit(500)).all()
    return render(request, "colour_requests.html", reqs=reqs)
