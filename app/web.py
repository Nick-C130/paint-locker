from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import urlencode

import markdown as md
from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from .colour import describe_delta_e, text_colour_for
from .models import CATEGORY_LABELS, FINISHES, MIX_UNITS, PAINT_TYPES
from .services import row_label

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
templates = Jinja2Templates(directory=APP_DIR / "templates")


def render_markdown(text: str) -> Markup:
    # Escape raw HTML first so notes can't inject markup, then render Markdown.
    return Markup(md.markdown(html.escape(text or ""), extensions=["nl2br", "sane_lists"]))


templates.env.filters["markdown"] = render_markdown
templates.env.filters["textcol"] = text_colour_for
templates.env.filters["de_words"] = describe_delta_e
templates.env.globals.update(PAINT_TYPES=PAINT_TYPES, FINISHES=FINISHES, MIX_UNITS=MIX_UNITS, row_label=row_label, CATEGORY_LABELS=CATEGORY_LABELS)


def render(request: Request, name: str, **ctx):
    ctx.setdefault("msg", request.query_params.get("msg"))
    ctx.setdefault("warn", request.query_params.get("warn"))
    return templates.TemplateResponse(request, name, ctx)


def redirect(url: str, **params) -> RedirectResponse:
    params = {k: v for k, v in params.items() if v}
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    return RedirectResponse(url, status_code=303)


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"
