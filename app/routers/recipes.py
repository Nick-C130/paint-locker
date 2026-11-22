"""Painting recipes: step-by-step guides that use paints and mixes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from .. import search as fts
from ..db import get_session
from ..models import TECHNIQUES, MixRecipe, Paint, Project, Recipe, RecipeStep
from ..services import add_missing_to_shopping, locate_url, needs
from ..web import redirect, render

router = APIRouter()


def _id(v: str | None) -> int | None:
    return int(v) if v and str(v).isdigit() else None


def load_recipe(session: Session, recipe_id: int) -> Recipe:
    r = session.get(Recipe, recipe_id)
    if not r:
        raise HTTPException(404)
    return r


def recipe_needs(session: Session, recipe: Recipe) -> list[dict]:
    wanted = []
    for s in recipe.steps:
        use = " ".join(x for x in (s.technique, s.area) if x)
        if s.paint_id:
            wanted.append(("paint", s.paint_id, use))
        if s.mix_id:
            wanted.append(("mix", s.mix_id, use))
    return needs(session, wanted)


@router.get("/recipes")
def recipe_list(request: Request, q: str = "", session: Session = Depends(get_session)):
    stmt = select(Recipe).options(selectinload(Recipe.steps)).order_by(Recipe.name)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Recipe.name.ilike(like) | Recipe.tags.ilike(like) | Recipe.description.ilike(like))
    return render(request, "recipes.html", recipes=session.scalars(stmt).all(), q=q)


@router.post("/recipes")
def recipe_create(
    name: str = Form(...), description: str = Form(""), tags: str = Form(""), session: Session = Depends(get_session)
):
    r = Recipe(name=name.strip() or "Untitled recipe", description=description, tags=_tags(tags))
    session.add(r)
    session.flush()
    fts.index_entity(session, "recipe", r.id)
    session.commit()
    return redirect(f"/recipes/{r.id}/edit", msg="Recipe created. Add its steps below.")


def _tags(raw: str) -> str:
    return ";".join(t.strip().lstrip("#") for t in raw.replace(",", ";").split(";") if t.strip())


@router.get("/recipes/{recipe_id}")
def recipe_detail(request: Request, recipe_id: int, session: Session = Depends(get_session)):
    r = load_recipe(session, recipe_id)
    rows = recipe_needs(session, r)
    projects = session.scalars(select(Project).where(Project.recipes.any(Recipe.id == r.id))).all()
    return render(
        request,
        "recipe.html",
        recipe=r,
        rows=rows,
        projects=projects,
        missing=sum(1 for x in rows if x["kind"] == "paint" and not x["bottles"]),
        locate=locate_url(rows, f"Paints for {r.name}", f"/recipes/{r.id}"),
    )


@router.get("/recipes/{recipe_id}/edit")
def recipe_edit_form(request: Request, recipe_id: int, session: Session = Depends(get_session)):
    return render(request, "recipe_edit.html", recipe=load_recipe(session, recipe_id), techniques=TECHNIQUES)


@router.post("/recipes/{recipe_id}/edit")
def recipe_edit(
    recipe_id: int,
    name: str = Form(...),
    description: str = Form(""),
    tags: str = Form(""),
    session: Session = Depends(get_session),
):
    r = load_recipe(session, recipe_id)
    r.name, r.description, r.tags = name.strip() or r.name, description, _tags(tags)
    fts.index_entity(session, "recipe", r.id)
    session.commit()
    return redirect(f"/recipes/{r.id}/edit", msg="Saved.")


def _step_fields(step: RecipeStep, session: Session, technique, area, paint_id, mix_id, dilution, instructions):
    step.technique = technique if technique in TECHNIQUES else "other"
    step.area = area.strip()
    pid, mid = _id(paint_id), _id(mix_id)
    step.paint_id = pid if pid and session.get(Paint, pid) else None
    step.mix_id = mid if mid and session.get(MixRecipe, mid) else None
    step.dilution = dilution.strip()
    step.instructions = instructions


@router.post("/recipes/{recipe_id}/steps")
def step_add(
    recipe_id: int,
    technique: str = Form("layer"),
    area: str = Form(""),
    paint_id: str = Form(""),
    mix_id: str = Form(""),
    dilution: str = Form(""),
    instructions: str = Form(""),
    session: Session = Depends(get_session),
):
    r = load_recipe(session, recipe_id)
    step = RecipeStep(position=(max((s.position for s in r.steps), default=0) + 1))
    _step_fields(step, session, technique, area, paint_id, mix_id, dilution, instructions)
    r.steps.append(step)
    session.flush()
    fts.index_entity(session, "recipe", r.id)
    session.commit()
    return redirect(f"/recipes/{r.id}/edit#add", msg="Step added.")


@router.post("/recipe-steps/{step_id}/edit")
def step_edit(
    step_id: int,
    technique: str = Form("layer"),
    area: str = Form(""),
    paint_id: str = Form(""),
    mix_id: str = Form(""),
    dilution: str = Form(""),
    instructions: str = Form(""),
    session: Session = Depends(get_session),
):
    step = session.get(RecipeStep, step_id)
    if not step:
        raise HTTPException(404)
    _step_fields(step, session, technique, area, paint_id, mix_id, dilution, instructions)
    session.flush()
    fts.index_entity(session, "recipe", step.recipe_id)
    session.commit()
    return redirect(f"/recipes/{step.recipe_id}/edit#step-{step.id}", msg="Step saved.")


@router.post("/recipe-steps/{step_id}/move")
def step_move(step_id: int, direction: str = Form("up"), session: Session = Depends(get_session)):
    step = session.get(RecipeStep, step_id)
    if not step:
        raise HTTPException(404)
    steps = list(step.recipe.steps)
    i = steps.index(step)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(steps):
        steps[i], steps[j] = steps[j], steps[i]
    for n, s in enumerate(steps, 1):
        s.position = n
    session.commit()
    return redirect(f"/recipes/{step.recipe_id}/edit#step-{step.id}")


@router.post("/recipe-steps/{step_id}/delete")
def step_delete(step_id: int, session: Session = Depends(get_session)):
    step = session.get(RecipeStep, step_id)
    if not step:
        raise HTTPException(404)
    rid = step.recipe_id
    session.delete(step)
    session.flush()
    fts.index_entity(session, "recipe", rid)
    session.commit()
    return redirect(f"/recipes/{rid}/edit", msg="Step removed.")


@router.post("/recipes/{recipe_id}/shopping")
def recipe_shopping(recipe_id: int, session: Session = Depends(get_session)):
    r = load_recipe(session, recipe_id)
    n = add_missing_to_shopping(session, recipe_needs(session, r))
    session.commit()
    return redirect(f"/recipes/{r.id}", msg=f"Added {n} paint{'s' if n != 1 else ''} to the shopping list.")


@router.post("/recipes/{recipe_id}/copy")
def recipe_copy(recipe_id: int, session: Session = Depends(get_session)):
    r = load_recipe(session, recipe_id)
    new = Recipe(name=f"{r.name} (copy)", description=r.description, tags=r.tags)
    for s in r.steps:
        new.steps.append(RecipeStep(position=s.position, technique=s.technique, area=s.area, paint_id=s.paint_id,
                                    mix_id=s.mix_id, dilution=s.dilution, instructions=s.instructions))
    session.add(new)
    session.flush()
    fts.index_entity(session, "recipe", new.id)
    session.commit()
    return redirect(f"/recipes/{new.id}/edit", msg="Copied. Rename it and adjust the steps.")


@router.post("/recipes/{recipe_id}/delete")
def recipe_delete(recipe_id: int, session: Session = Depends(get_session)):
    r = load_recipe(session, recipe_id)
    session.execute(text("DELETE FROM notes WHERE entity_type='recipe' AND entity_id=:i"), {"i": r.id})
    session.execute(text("DELETE FROM search_index WHERE entity_type='recipe' AND entity_id=:i"), {"i": r.id})
    session.delete(r)
    session.commit()
    return redirect("/recipes", msg="Recipe deleted.")
