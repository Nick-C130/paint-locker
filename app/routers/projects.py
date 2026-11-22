"""Projects and armies: what you're painting, its recipes, and every paint it needs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from .. import search as fts
from ..db import get_session
from ..models import PROJECT_KINDS, PROJECT_STATUSES, MixRecipe, Paint, Project, ProjectPaint, Recipe
from ..services import add_missing_to_shopping, locate_url, needs
from ..web import redirect, render

router = APIRouter()


def _id(v: str | None) -> int | None:
    return int(v) if v and str(v).isdigit() else None


def _int(v) -> int:
    try:
        return int(v or 0)
    except ValueError:
        return 0


def load_project(session: Session, pid: int) -> Project:
    p = session.get(Project, pid)
    if not p:
        raise HTTPException(404)
    return p


def project_needs(session: Session, project: Project) -> list[dict]:
    """Paints and mixes for this project and its units: direct ones first, then each recipe's."""
    wanted = []
    for p in project.all_projects():
        prefix = "" if p.id == project.id else f"{p.name}: "
        for e in p.extras:
            use = prefix + (e.note or "direct")
            if e.paint_id:
                wanted.append(("paint", e.paint_id, use))
            if e.mix_id:
                wanted.append(("mix", e.mix_id, use))
        for r in p.recipes:
            for s in r.steps:
                use = prefix + r.name
                if s.paint_id:
                    wanted.append(("paint", s.paint_id, use))
                if s.mix_id:
                    wanted.append(("mix", s.mix_id, use))
    return needs(session, wanted)


def _would_loop(project: Project, parent: Project | None) -> bool:
    while parent is not None:
        if parent.id == project.id:
            return True
        parent = parent.parent
    return False


@router.get("/projects")
def project_list(request: Request, status: str = "", session: Session = Depends(get_session)):
    stmt = select(Project).options(selectinload(Project.children)).order_by(Project.name)
    projects = session.scalars(stmt).all()
    top = [p for p in projects if p.parent_id is None and (not status or p.status == status)]
    return render(request, "projects.html", top=top, status=status, all_projects=projects,
                  kinds=PROJECT_KINDS, statuses=PROJECT_STATUSES)


def _apply(p: Project, session: Session, name, kind, status, parent_id, description, models_total, models_done):
    p.name = name.strip() or p.name or "Untitled"
    p.kind = kind if kind in PROJECT_KINDS else "other"
    p.status = status if status in PROJECT_STATUSES else "active"
    parent = session.get(Project, _id(parent_id)) if _id(parent_id) else None
    if p.id and parent and _would_loop(p, parent):
        parent = None
    p.parent = parent
    p.description = description
    p.models_total = max(0, _int(models_total))
    p.models_done = max(0, _int(models_done))
    if p.models_total:
        p.models_done = min(p.models_done, p.models_total)


@router.post("/projects")
def project_create(
    name: str = Form(...),
    kind: str = Form("unit"),
    status: str = Form("active"),
    parent_id: str = Form(""),
    description: str = Form(""),
    models_total: str = Form("0"),
    models_done: str = Form("0"),
    session: Session = Depends(get_session),
):
    p = Project()
    _apply(p, session, name, kind, status, parent_id, description, models_total, models_done)
    session.add(p)
    session.flush()
    fts.index_entity(session, "project", p.id)
    session.commit()
    return redirect(f"/projects/{p.id}", msg="Project created.")


@router.get("/projects/{pid}")
def project_detail(request: Request, pid: int, session: Session = Depends(get_session)):
    p = load_project(session, pid)
    rows = project_needs(session, p)
    recipes = session.scalars(select(Recipe).order_by(Recipe.name)).all()
    # a valid parent is anything that isn't this project or one of its units
    parents = [x for x in session.scalars(select(Project).order_by(Project.name)).all() if not _would_loop(p, x)]
    done, total = p.progress
    return render(
        request,
        "project.html",
        project=p,
        rows=rows,
        recipes=[r for r in recipes if r not in p.recipes],
        parents=parents,
        kinds=PROJECT_KINDS,
        statuses=PROJECT_STATUSES,
        done=done,
        total=total,
        missing=sum(1 for x in rows if x["kind"] == "paint" and not x["bottles"]),
        locate=locate_url(rows, f"Paints for {p.name}", f"/projects/{p.id}"),
    )


@router.post("/projects/{pid}/edit")
def project_edit(
    pid: int,
    name: str = Form(...),
    kind: str = Form("unit"),
    status: str = Form("active"),
    parent_id: str = Form(""),
    description: str = Form(""),
    models_total: str = Form("0"),
    models_done: str = Form("0"),
    session: Session = Depends(get_session),
):
    p = load_project(session, pid)
    _apply(p, session, name, kind, status, parent_id, description, models_total, models_done)
    fts.index_entity(session, "project", p.id)
    session.commit()
    return redirect(f"/projects/{p.id}", msg="Saved.")


@router.post("/projects/{pid}/progress")
def project_progress(pid: int, delta: int = Form(1), session: Session = Depends(get_session)):
    p = load_project(session, pid)
    p.models_done = max(0, p.models_done + delta)
    if p.models_total:
        p.models_done = min(p.models_done, p.models_total)
    if p.models_total and p.models_done >= p.models_total and p.status == "active":
        p.status = "done"
    session.commit()
    return redirect(f"/projects/{p.id}")


@router.post("/projects/{pid}/recipes")
def project_add_recipe(pid: int, recipe_id: int = Form(...), session: Session = Depends(get_session)):
    p = load_project(session, pid)
    r = session.get(Recipe, recipe_id)
    if r and r not in p.recipes:
        p.recipes.append(r)
        session.flush()
        fts.index_entity(session, "project", p.id)
        session.commit()
    return redirect(f"/projects/{p.id}", msg=f"Added recipe {r.name}." if r else None)


@router.post("/projects/{pid}/recipes/{recipe_id}/remove")
def project_remove_recipe(pid: int, recipe_id: int, session: Session = Depends(get_session)):
    p = load_project(session, pid)
    p.recipes = [r for r in p.recipes if r.id != recipe_id]
    session.commit()
    return redirect(f"/projects/{p.id}")


@router.post("/projects/{pid}/paints")
def project_add_paint(
    pid: int, paint_id: str = Form(""), mix_id: str = Form(""), note: str = Form(""), session: Session = Depends(get_session)
):
    p = load_project(session, pid)
    paint = session.get(Paint, _id(paint_id)) if _id(paint_id) else None
    mix = session.get(MixRecipe, _id(mix_id)) if _id(mix_id) else None
    if not paint and not mix:
        return redirect(f"/projects/{p.id}", warn="Pick a paint or mix first.")
    p.extras.append(ProjectPaint(paint=paint, mix=mix, note=note.strip()))
    session.commit()
    return redirect(f"/projects/{p.id}", msg="Added.")


@router.post("/project-paints/{eid}/delete")
def project_remove_paint(eid: int, session: Session = Depends(get_session)):
    e = session.get(ProjectPaint, eid)
    if not e:
        raise HTTPException(404)
    pid = e.project_id
    session.delete(e)
    session.commit()
    return redirect(f"/projects/{pid}")


@router.post("/projects/{pid}/shopping")
def project_shopping(pid: int, session: Session = Depends(get_session)):
    p = load_project(session, pid)
    n = add_missing_to_shopping(session, project_needs(session, p))
    session.commit()
    return redirect(f"/projects/{p.id}", msg=f"Added {n} paint{'s' if n != 1 else ''} to the shopping list.")


@router.post("/projects/{pid}/delete")
def project_delete(pid: int, session: Session = Depends(get_session)):
    p = load_project(session, pid)
    for c in list(p.children):
        c.parent = p.parent
    session.execute(text("DELETE FROM notes WHERE entity_type='project' AND entity_id=:i"), {"i": p.id})
    session.execute(text("DELETE FROM search_index WHERE entity_type='project' AND entity_id=:i"), {"i": p.id})
    session.delete(p)
    session.commit()
    return redirect("/projects", msg="Project deleted. Its units were kept.")
