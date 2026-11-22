# Contributing to Paint Locker

Thanks for helping. Paint Locker is a small, self-hosted app, so the bar is simple:
keep it easy to run (one container, one SQLite file, no JS build step) and keep your data safe.

## Ways to help

* **Catalogue data** is the easiest and most useful contribution. Missing paints, wrong colours,
  barcodes, discontinued flags, whole new brands. See "Catalogue changes" below.
* **Bug reports**: use the bug template and include steps, what you expected and your browser/device.
* **Features**: open a feature request first so we can agree on the shape before you write code.

## Development setup

```bash
git clone https://github.com/Nick-C130/paint-locker.git
cd paint-locker
python -m venv .venv && . .venv/bin/activate     # Python 3.12 or newer
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8080         # data goes to ./data
```

Before you open a pull request:

```bash
pytest -q          # tests
ruff check .       # lint
```

CI runs both on every pull request.

## How the code is laid out

| Path | What it is |
|---|---|
| `app/models.py` | SQLAlchemy schema. Change it, then `alembic revision --autogenerate -m "..."` and check the generated file in `app/migrations/versions` |
| `app/routers/` | FastAPI routes, one file per area (catalogue, storage, mixes, …) |
| `app/templates/` | Jinja2 templates; HTMX for partial updates, Alpine.js for small bits of state |
| `app/static/` | CSS, small JS files, vendored libraries in `vendor/` (no npm at runtime) |
| `app/colour.py` | Colour maths: OKLab, CIEDE2000, spectral (Kubelka-Munk) mixing |
| `app/mixsolver.py`, `app/recommender.py` | Reverse mixing and the "Buy next" recommender (numpy) |
| `app/seed.py`, `catalogues/` | Catalogue importer and the CSV data |
| `tests/` | pytest; `tests/conftest.py` gives each test a fresh database |

Guidelines:

* Server-rendered pages first. Reach for JavaScript only where it clearly helps (drag and drop,
  the mix lab sliders, the camera).
* Migrations must be safe to run on an existing database with real data in it.
* Add a test for new routes and for anything with maths in it.
* Keep pull requests focused; one feature or fix each.

## Catalogue changes

Catalogue CSVs live in `catalogues/<brand>/<line>.csv` and are registered in `catalogues/lines.csv`
(generated from upstream) or `catalogues/lines-extra.csv` (hand-maintained). Columns:

```
code,name,hex,type,finish,volume_ml,barcode,format,discontinued,tags
```

* `type` is one of: acrylic, air, wash, contrast, metallic, primer, ink, medium, varnish, effect, …
  (see `PAINT_TYPES` in `app/models.py`).
* `hex` is an approximate screen colour. Say where it came from in your pull request
  (manufacturer chart, swatch photo under daylight, etc.).
* Most lines come from [Arcturus5404/miniature-paints](https://github.com/Arcturus5404/miniature-paints).
  Fixes to those colours are best sent upstream too, so everyone benefits.

The importer upserts on brand + line + code, so editing a CSV and restarting is enough to test it.

## Licence

By contributing you agree that your contribution is licensed under the [MIT licence](LICENSE).
Please follow the [code of conduct](CODE_OF_CONDUCT.md).
