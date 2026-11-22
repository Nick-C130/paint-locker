# Paint Locker

A self-hosted web app for a miniature paint collection: a searchable multi-brand paint catalogue,
your bottles and where they live in your racks, mix recipes with a pigment-style colour preview,
and comments on everything. Built for WW2-style miniatures, but nothing stops you painting elves.

Single user, one Docker container, one SQLite file. MIT licensed.

## Quick start

All you need is Docker and one file, [`docker-compose.deploy.yml`](docker-compose.deploy.yml):

```bash
mkdir paint-locker && cd paint-locker
curl -o docker-compose.yml https://raw.githubusercontent.com/Nick-C130/paint-locker/main/docker-compose.deploy.yml
docker compose up -d
```

Open http://your-server:8080. Your data lives in `./data` next to the compose file (database,
photos, backups). Set `TZ` in the file to your time zone so the nightly backup runs when you expect.

> **While the repository is private** the image is private too. Log in once with a GitHub
> [personal access token](https://github.com/settings/tokens) (classic) with the `read:packages` scope:
> `echo <token> | docker login ghcr.io -u <github-username> --password-stdin`. The curl above also
> needs the file copied by hand while private. Neither step is needed once the project is public.

To build from source instead: `git clone https://github.com/Nick-C130/paint-locker.git && cd paint-locker && docker compose up -d --build`.

On first start the app runs its database migrations and imports the paint catalogue (about
4,200 paints, takes a couple of seconds). Later starts only re-import when the catalogue CSVs change.

### Configuration (environment variables)

| Variable | Default | What it does |
|---|---|---|
| `PAINTLOCKER_DATA_DIR` | `/data` in Docker, `./data` otherwise | Database, uploads and backups |
| `PAINTLOCKER_PASSWORD` | unset | Optional single shared password (HTTP basic auth). Off by default; the app assumes it sits behind your own proxy or VPN and doesn't do TLS |
| `PAINTLOCKER_BACKUP_HOUR` | `3` | Hour of the nightly SQLite backup (container local time; set `TZ`) |
| `PAINTLOCKER_BACKUP_KEEP` | `14` | Nightly backups to keep in `data/backups` |
| `PAINTLOCKER_BACKUPS` | `1` | Set to `0` to turn nightly backups off |
| `PAINTLOCKER_SEED` | `1` | Set to `0` to skip the catalogue import on startup |
| `PAINTLOCKER_MAX_UPLOAD_MB` | `15` | Photo upload limit |
| `PUID` / `PGID` | `1000` | User and group the app runs as; `./data` is handed to them on start |
| `TZ` | `UTC` | Container time zone (used for the backup hour) |

### Updating

Published image: `docker compose pull && docker compose up -d`. Built from source:
`git pull && docker compose up -d --build`. Migrations run automatically on startup, and a backup
of `./data` before a big update never hurts.

GitHub Actions builds a multi-arch (amd64 + arm64) image on every push to `main`, tagged `latest`
and with the commit SHA (`ghcr.io/nick-c130/paint-locker:<sha>`) so you can pin or roll back.

### Backups

* Nightly automatic backup into `data/backups` (SQLite online backup, safe while running).
* Settings → Export: full JSON, inventory/mixes/colour-request CSVs, or download the live database.
* Copying the whole `data` folder is a complete backup.

## What's in v1

* **Global search** (press `/`) across paints, mixes, reference colours, notes, tags and rack/cell
  labels. `70.950`, `black`, `dunkelgelb`, `olivgrun` and `Rack 1` all work. Type a hex like
  `#9c8853` to jump to a colour search. Owned paints rank first and show where they are.
* **Catalogue browser** with brand, line, type, finish and owned/not-owned filters. Each paint page
  shows your bottles and their cells, cross-brand equivalents and similar colours (CIEDE2000),
  mixes that use it, linked reference colours, comments and photos.
* **Quick add**: “+ Add bottle” suggests the next free cell in a rack tagged for that bottle's format.
  Add several bottles at once; owned and bottle count fall out of the bottles you add.
* **Storage grids** (with a mini colour map of each rack on the home page): racks as full rows × columns grids (A1 or 1-1 labels), one bottle per cell,
  disabled cells for irregular racks. Cells show the paint's colour and code. Click a cell to fill or
  inspect it, drag a bottle to move it (onto a full cell to swap), and search results link to the
  rack with the bottle highlighted.
* **Bottle formats**: tag racks with the formats they hold (17-18 ml dropper, Citadel pot, Tamiya
  jar, 10 ml dropper, …; manage the list in Settings). Lines set a default format, a paint can
  override it, and a bottle can override it again. Suggestions only use racks that fit; placing a
  bottle in a rack it doesn't fit is allowed but shows a warning (⚠ on the grid).
* **Bulk shelf entry** for the first load of your collection: walks a rack cell by cell; type a
  code, Enter places it and moves on, Enter on an empty box skips the cell.
* **Mix lab**: pick paints (or other mixes), set amounts in parts, drops or ml with sliders, see a
  live spectral (Kubelka-Munk) preview via spectral.js, compare against a target colour or WW2
  reference colour with a ΔE score, scale to “make 10 ml” in ml and drops (drops per ml is a
  setting). Save as a recipe; the server recomputes the estimate with a Python port of the same maths.
* **Mix suggestions (reverse mixing)**: give the mix lab a target colour (or press “Try mixing it”
  on a colour search, or “Mix it” on a WW2 reference) and it suggests 1-, 2- and 3-paint mixes
  from paints you own, with ratios and ΔE; “Use” loads one into the lab. The colour search page shows
  the top three too. Washes, contrast/speed paints, metallics and mediums are left out.
* **Water, thinners and flow improvers** in mixes: “💧 + Water” and “+ Flow improver / thinner /
  medium” in the mix lab. They count towards the ratio (e.g. `2 : 1 : 1`) and the dilution %
  (with a rough consistency: thinned, glaze, wash…) but don't change the estimated colour; a strip
  shows roughly how one coat looks over light and dark primer at that dilution.
* **Mix batches**: “Make a batch” creates a bottle of the mix and stores it in a cell like any paint.
  Record the measured colour after mixing; the mix page shows how far off the estimate was.
* **Colour request log**: every colour search, “closest I own”, reference lookup and mix-lab target
  is logged with the best owned match at the time. Buy next uses this history. See it under Settings → Colour request log.
* **WW2 reference colours**: 62 standards (German RAL and RLM, US FS/ANA and Olive Drab, British
  SCC/BSC and Caunter colours, Soviet 4BO/6K/7K/…), with nearest catalogue paints and what you own.
  Link paints and mixes to them so they show up when you search the reference.
* **Comments and photos on everything**: paints, bottles, mixes, racks, individual cells, brands and
  reference colours. Markdown, pin, edit; comments are searchable. Photo upload uses the phone
  camera on mobile.
* **Barcode scanning** (📷 Scan tab): a USB or Bluetooth scanner types into the scan box; on a
  phone, “Use camera” reads barcodes with the browser's built-in detector (Chrome/Android) or
  ZXing (iPhone and others). The first time a barcode is unknown you pick the paint it belongs to;
  after that it's recognised. Then add a bottle in one tap, or turn on quick mode to add one per scan.
  Links are stored separately from the catalogue, so re-importing never wipes them, and typing a
  barcode into the main search jumps to the paint. **The camera needs HTTPS** (browsers block it
  on plain http, except on localhost), so serve Paint Locker through your HTTPS proxy.
* **Dark mode** follows your device setting; the toggle in the top bar pins it to dark or light.
* **Shopping list**: “Used up → shopping list” on a bottle removes it and adds the paint.
* **Add missing paints**: a quick form with brand/line autocomplete, CSV bulk import, and a merge
  tool for duplicates you added.
* Works on a phone; installable to the home screen (PWA manifest).

### Since v1

* **🛍️ Buy next (purchase recommender)**: ranks paints you don't own by two things, blended with a
  slider: *reach* (how much of the colour space you could newly mix with what you own, using the
  same spectral maths as the mix lab) and *demand* (how much closer it gets you to the colours
  you've actually searched for and mixed, with recent requests counting more). Picks are chosen
  one at a time so they don't overlap, and each says why ("opens up about 6% more of the colour
  range you can mix, mostly greens; would have improved 4 of your past colour requests…"). Limit it
  to brands you already own or one brand, skip discontinued paints; dismiss a pick or send it to the
  shopping list.
* **📍 Find in racks**: from the mix lab or a saved mix, show only the racks holding the paints you
  need, with those cells highlighted and anything missing listed. "Where are my batches?" does the
  same for bottles of a mix.
* **Swatch borders by type** everywhere a rack is drawn (rack grids, the home page mini racks, the
  find-in-racks view): plain acrylic, airbrush-ready, thinner/medium/varnish, wash/contrast/ink,
  metallic, primer and mix batches each get their own border colour and style, with a legend.

Still to come: camera colour capture, painting recipes, printable QR labels. Ideas welcome in the issues.

## The paint catalogue

Catalogue data lives in `catalogues/` as one CSV per product line, listed in `catalogues/lines.csv`:

```
catalogues/lines.csv                  brand, line, file, default type/finish/volume/format, stocked_by
catalogues/vallejo/model-color.csv    code,name,hex,type,finish,volume_ml,barcode,format,discontinued,tags
catalogues/reference/ww2.csv          system,code,name,aliases,nation,hex,source
```

The importer upserts by brand + line + code (or name when a line has no codes, e.g. Citadel), so
editing a CSV and restarting applies cleanly and never touches your bottles, notes or mixes.

Seeded brands and lines (4,214 paints):

* **Vallejo**: Model Color, Model Air, Game Color, Game Air, Xpress Color, Mecha Color, Metal Color,
  Panzer Aces (legacy), Surface Primer, Wash FX, Weathering FX, Premium Airbrush Color
* **The Army Painter**: Warpaints Fanatic (incl. washes), Speedpaint 2.0, Speedpaint 1st edition
  (legacy), Warpaints Air, Warpaints (legacy), Colour Primer
* **Citadel**: Base, Layer, Shade, Contrast, Dry, Technical, Air, Spray, Foundation (legacy)
* **AK Interactive**: 3rd Generation Acrylics, 3rd Gen Air, 3rd Gen Primer, Real Colors, pre-3rd Gen acrylics
* **Scale 75**: Scalecolor, Fantasy & Games, Warfront, Artist, Instant Colors, Metal n' Alchemy, Inktensity, FX, Soil Works, Primers
* **Turbo Dork**, **Tamiya** (Acrylic Mini, LP, TS and AS sprays), and **Ammo by Mig** acrylics

Hand-maintained extras live in `catalogues/lines-extra.csv` (currently Vallejo, Army Painter and
Citadel flow improvers, thinners and mediums, entered by name; add codes and sizes as you go).

**Gaps:** Two Thin Coats and Abteilung 502 aren't seeded because there's no open dataset for them yet.
Add them with Settings → Import CSV, or drop CSV files into `catalogues/` and a row into `lines.csv`.
Barcodes aren't in the source data either.

Hex values are approximate screen colours from community data. Every paint has an optional
measured colour that overrides the catalogue value for matching and mixing.

To refresh from upstream: `python scripts/convert_arcturus.py /path/to/miniature-paints/paints catalogues`.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8080      # data goes to ./data
pytest -q
```

Stack: FastAPI, server-rendered Jinja2 templates with HTMX and a little Alpine.js (no JS build step),
SQLAlchemy 2 + Alembic on SQLite with an FTS5 search index. Schema changes:
`alembic revision --autogenerate -m "what changed"`.

Layout: `app/models.py` (schema), `app/services.py` (storage fit, cell suggestions, colour matching),
`app/colour.py` (OKLab, CIEDE2000, spectral mixing), `app/mixsolver.py` (reverse mixing),
`app/recommender.py` (Buy next), `app/search.py` (FTS5), `app/seed.py` (catalogue importer),
`app/routers/` (pages and JSON endpoints), `app/templates/`. Lint with `ruff check .`.

## Contributing

Bug reports, catalogue fixes and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md)
and the [code of conduct](CODE_OF_CONDUCT.md). Security issues: see [SECURITY.md](SECURITY.md).
Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Credits and licences

* Paint data: [Arcturus5404/miniature-paints](https://github.com/Arcturus5404/miniature-paints) (MIT).
  WW2 reference colour values are approximations taken from AK Real Colors chips in that dataset.
* [spectral.js](https://github.com/rvanwijnen/spectral.js) (MIT), ported to Python in `app/colour.py`.
* Paint Locker itself is [MIT licensed](LICENSE).
* [htmx](https://htmx.org) (0BSD), [Alpine.js](https://alpinejs.dev) (MIT), [Pico CSS](https://picocss.com) (MIT), [ZXing JS](https://github.com/zxing-js/library) (MIT), vendored in `app/static/vendor`.
