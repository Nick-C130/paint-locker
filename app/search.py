"""Full-text search (SQLite FTS5) across paints, mixes, reference colours and storage units.

Each searchable entity has one row in `search_index`. Notes attached to an entity are
folded into its body, so searching a note's text finds the thing it is attached to.
"""

from __future__ import annotations

import re

from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from .models import (
    BarcodeLink,
    InventoryItem,
    MixRecipe,
    Note,
    Paint,
    ProductLine,
    Project,
    Recipe,
    ReferenceColour,
    ReferenceLink,
    StorageCell,
    StorageUnit,
)

INDEXED = ("paint", "mix", "reference", "unit", "recipe", "project")
INDEX_VERSION = "2"  # bump to force a rebuild on startup when what gets indexed changes


def _notes_text(session: Session, entity_type: str, entity_id: int) -> str:
    rows = session.scalars(
        select(Note.body).where(Note.entity_type == entity_type, Note.entity_id == entity_id)
    ).all()
    return " ".join(rows)


def _refs_text(session: Session, entity_type: str, entity_id: int) -> str:
    links = session.scalars(
        select(ReferenceLink)
        .options(selectinload(ReferenceLink.reference))
        .where(ReferenceLink.entity_type == entity_type, ReferenceLink.entity_id == entity_id)
    ).all()
    return " ".join(f"{l.reference.label} {l.reference.aliases}" for l in links)


def _item_text(session: Session, items: list[InventoryItem]) -> str:
    parts = []
    for it in items:
        if it.cell:
            parts.append(f"{it.cell.unit.name} {it.cell.label}")
        parts.append(it.notes or "")
        parts.append(_notes_text(session, "item", it.id))
    return " ".join(parts)


def _doc_for(session: Session, entity_type: str, entity_id: int) -> tuple[str, str] | None:
    if entity_type == "paint":
        p = session.get(Paint, entity_id)
        if not p:
            return None
        title = f"{p.code} {p.name}"
        body = " ".join(
            [
                p.line.brand.name,
                p.line.name,
                p.type,
                p.finish,
                p.tags.replace(";", " "),
                p.barcode,
                " ".join(session.scalars(select(BarcodeLink.barcode).where(BarcodeLink.paint_id == p.id)).all()),
                _notes_text(session, "paint", p.id),
                _refs_text(session, "paint", p.id),
                _item_text(session, p.items),
            ]
        )
        return title, body
    if entity_type == "mix":
        m = session.get(MixRecipe, entity_id)
        if not m:
            return None
        body = " ".join(
            [
                "mix",
                m.description,
                m.intended_use,
                m.tags.replace(";", " "),
                " ".join(c.label for c in m.components),
                _notes_text(session, "mix", m.id),
                _refs_text(session, "mix", m.id),
                _item_text(session, m.items),
            ]
        )
        return m.name, body
    if entity_type == "reference":
        r = session.get(ReferenceColour, entity_id)
        if not r:
            return None
        return f"{r.system} {r.code} {r.name}", f"{r.aliases} {r.nation} reference {_notes_text(session, 'reference', r.id)}"
    if entity_type == "unit":
        u = session.get(StorageUnit, entity_id)
        if not u:
            return None
        cell_notes = " ".join(
            _notes_text(session, "cell", c.id) for c in u.cells
        )
        return u.name, f"storage {u.location} {_notes_text(session, 'unit', u.id)} {cell_notes}"
    if entity_type == "recipe":
        r = session.get(Recipe, entity_id)
        if not r:
            return None
        steps = " ".join(f"{st.technique} {st.area} {st.dilution} {st.instructions} {st.paint.full_label if st.paint else ''} {st.mix.name if st.mix else ''}" for st in r.steps)
        return r.name, f"recipe {r.description} {r.tags.replace(';', ' ')} {steps} {_notes_text(session, 'recipe', r.id)}"
    if entity_type == "project":
        pr = session.get(Project, entity_id)
        if not pr:
            return None
        return pr.name, f"project {pr.kind} {pr.status} {pr.description} {' '.join(r.name for r in pr.recipes)} {_notes_text(session, 'project', pr.id)}"
    return None


def index_entity(session: Session, entity_type: str, entity_id: int) -> None:
    if entity_type not in INDEXED:
        return
    session.execute(
        text("DELETE FROM search_index WHERE entity_type = :t AND entity_id = :i"),
        {"t": entity_type, "i": entity_id},
    )
    doc = _doc_for(session, entity_type, entity_id)
    if doc:
        session.execute(
            text("INSERT INTO search_index (entity_type, entity_id, title, body) VALUES (:t, :i, :ti, :b)"),
            {"t": entity_type, "i": entity_id, "ti": doc[0], "b": doc[1]},
        )


def reindex_for_note_target(session: Session, entity_type: str, entity_id: int) -> None:
    """A note/attachment changed; reindex whatever entity carries that text."""
    if entity_type in INDEXED:
        index_entity(session, entity_type, entity_id)
    elif entity_type == "item":
        it = session.get(InventoryItem, entity_id)
        if it and it.paint_id:
            index_entity(session, "paint", it.paint_id)
        if it and it.mix_id:
            index_entity(session, "mix", it.mix_id)
    elif entity_type == "cell":
        c = session.get(StorageCell, entity_id)
        if c:
            index_entity(session, "unit", c.unit_id)


def rebuild(session: Session) -> int:
    session.execute(text("DELETE FROM search_index"))
    rows = []
    paints = session.scalars(
        select(Paint).options(
            selectinload(Paint.line).selectinload(ProductLine.brand),
            selectinload(Paint.items).selectinload(InventoryItem.cell).selectinload(StorageCell.unit),
        )
    ).all()
    notes: dict[tuple[str, int], list[str]] = {}
    for n in session.scalars(select(Note)).all():
        notes.setdefault((n.entity_type, n.entity_id), []).append(n.body)
    refs: dict[tuple[str, int], list[str]] = {}
    for l in session.scalars(select(ReferenceLink).options(selectinload(ReferenceLink.reference))).all():
        refs.setdefault((l.entity_type, l.entity_id), []).append(f"{l.reference.label} {l.reference.aliases}")

    codes: dict[int, list[str]] = {}
    for bl in session.scalars(select(BarcodeLink)).all():
        codes.setdefault(bl.paint_id, []).append(bl.barcode)

    def nt(t, i):
        return " ".join(notes.get((t, i), [])) + " " + " ".join(refs.get((t, i), []))

    for p in paints:
        items_txt = " ".join(
            (f"{it.cell.unit.name} {it.cell.label} " if it.cell else "") + (it.notes or "") + " " + nt("item", it.id)
            for it in p.items
        )
        rows.append(
            {
                "t": "paint",
                "i": p.id,
                "ti": f"{p.code} {p.name}",
                "b": f"{p.line.brand.name} {p.line.name} {p.type} {p.finish} {p.tags.replace(';', ' ')} {p.barcode} {' '.join(codes.get(p.id, []))} {nt('paint', p.id)} {items_txt}",
            }
        )
    if rows:
        session.execute(
            text("INSERT INTO search_index (entity_type, entity_id, title, body) VALUES (:t, :i, :ti, :b)"), rows
        )
    for t, model in (("mix", MixRecipe), ("reference", ReferenceColour), ("unit", StorageUnit), ("recipe", Recipe), ("project", Project)):
        for (eid,) in session.execute(select(model.id)).all():
            index_entity(session, t, eid)
    return len(rows)


_TOKEN_RE = re.compile(r"[\w.\-]+", re.UNICODE)


def fts_query(q: str) -> str | None:
    tokens = [t.strip(".-") for t in _TOKEN_RE.findall(q)]
    tokens = [t for t in tokens if t]
    if not tokens:
        return None
    # every token must match; prefix-match each one
    return " ".join('"' + t.replace('"', "") + '"*' for t in tokens)


def search(session: Session, q: str, types: tuple[str, ...] = INDEXED, limit: int = 50) -> list[tuple[str, int]]:
    """Return [(entity_type, entity_id)] ranked by relevance (title matches first)."""
    fq = fts_query(q)
    if not fq:
        return []
    placeholders = ",".join(f":t{i}" for i in range(len(types)))
    params = {f"t{i}": t for i, t in enumerate(types)}
    params.update({"q": fq, "lim": limit})
    try:
        rows = session.execute(
            text(
                "SELECT entity_type, entity_id FROM search_index "
                f"WHERE search_index MATCH :q AND entity_type IN ({placeholders}) "
                "ORDER BY bm25(search_index, 0, 0, 10.0, 1.0) LIMIT :lim"
            ),
            params,
        ).all()
    except Exception:
        return []
    return [(r[0], int(r[1])) for r in rows]
