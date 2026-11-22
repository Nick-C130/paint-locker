"""Convert the Arcturus5404/miniature-paints dataset (MIT) into Paint Locker catalogue CSVs.

Usage:
    git clone --depth 1 https://github.com/Arcturus5404/miniature-paints /tmp/miniature-paints
    python scripts/convert_arcturus.py /tmp/miniature-paints/paints catalogues

Writes one CSV per product line under catalogues/<brand-slug>/ and rewrites
catalogues/lines.csv. Hand-edited CSVs are safe as long as you re-run this only
when you want to refresh from upstream (it overwrites the files it generates).
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROW_RE = re.compile(r"^\|(.+)\|\s*$")
HEX_RE = re.compile(r"`#([0-9A-Fa-f]{6})`")

# Bottle format names (must match app/seed.py DEFAULT_FORMATS)
DROPPER = "17-18 ml dropper"
DROPPER_LARGE = "30-35 ml dropper"
CITADEL_POT = "Citadel pot"
SMALL_DROPPER = "10 ml dropper"
TAMIYA_JAR = "Tamiya 10 ml jar"
SPRAY = "Spray can"
OTHER_JAR = "Jar / other"

FRONTLINE = "Frontline Hobbies"

# brand file -> (brand name, has code column, {set name: line key or None to skip}, {line key: line defaults})
# line defaults: (line name, type, finish, volume_ml, format, discontinued, stocked_by)
BRANDS: dict[str, dict] = {
    "Vallejo.md": {
        "brand": "Vallejo",
        "has_code": True,
        "sets": {
            "Model Color": "mc",
            "Model Air": "ma",
            "Game Color": "gc",
            "Game Color Wash": ("gc", "wash"),
            "Game Color Special FX": "gc",
            "Game Air": "ga",
            "Xpress Color": "xp",
            "Xpress Color Intense": "xp",
            "Mecha Color": "mecha",
            "Metal Color": "metal",
            "Panzer Aces": "pa",
            "Surface Primer": "primer",
            "Wash FX": "washfx",
            "Weathering FX": "wfx",
            "Premium Airbrush Color": "premium",
        },
        "lines": {
            "mc": ("Model Color", "acrylic", "matt", 17, DROPPER, False, FRONTLINE),
            "ma": ("Model Air", "air", "matt", 17, DROPPER, False, FRONTLINE),
            "gc": ("Game Color", "acrylic", "matt", 18, DROPPER, False, FRONTLINE),
            "ga": ("Game Air", "air", "matt", 18, DROPPER, False, FRONTLINE),
            "xp": ("Xpress Color", "contrast", "satin", 18, DROPPER, False, FRONTLINE),
            "mecha": ("Mecha Color", "acrylic", "satin", 17, DROPPER, False, FRONTLINE),
            "metal": ("Metal Color", "metallic", "metallic", 32, DROPPER_LARGE, False, FRONTLINE),
            "pa": ("Panzer Aces", "acrylic", "matt", 17, DROPPER, True, ""),
            "primer": ("Surface Primer", "primer", "matt", 17, DROPPER, False, FRONTLINE),
            "washfx": ("Wash FX", "wash", "matt", 35, OTHER_JAR, False, FRONTLINE),
            "wfx": ("Weathering FX", "pigment", "matt", 35, OTHER_JAR, False, FRONTLINE),
            "premium": ("Premium Airbrush Color", "air", "satin", 60, OTHER_JAR, False, ""),
        },
    },
    "Army_Painter.md": {
        "brand": "The Army Painter",
        "has_code": True,
        "sets": {
            "Warpaints Fanatic": "fan",
            "Warpaints Fanatic Wash": ("fan", "wash"),
            "Speedpaint Set 2.0": "sp2",
            "Speedpaint Set": "sp1",
            "Warpaints Air": "air",
            "Warpaints": "wp",
            "Warpaints Tone": ("wp", "wash"),
            "Warpaints Wash": ("wp", "wash"),
            "Quickshade Washes Set": ("wp", "wash"),
            "Warpaints Primer": "primer",
        },
        "lines": {
            "fan": ("Warpaints Fanatic", "acrylic", "matt", 18, DROPPER, False, FRONTLINE),
            "sp2": ("Speedpaint 2.0", "contrast", "satin", 18, DROPPER, False, FRONTLINE),
            "sp1": ("Speedpaint (1st edition)", "contrast", "satin", 18, DROPPER, True, ""),
            "air": ("Warpaints Air", "air", "matt", 18, DROPPER, False, FRONTLINE),
            "wp": ("Warpaints (legacy)", "acrylic", "matt", 18, DROPPER, True, ""),
            "primer": ("Colour Primer", "primer", "matt", 400, SPRAY, False, FRONTLINE),
        },
    },
    "Citadel_Colour.md": {
        "brand": "Citadel",
        "has_code": False,
        "sets": {
            "Base": "base",
            "Layer": "layer",
            "Shade": "shade",
            "Contrast": "contrast",
            "Dry": "dry",
            "Technical": "tech",
            "Air": "air",
            "Spray": "spray",
            "Glaze": ("layer", "wash"),
            "Foundation (discontinued)": "foundation",
            "Foundation Wash (discontinued)": ("foundation", "wash"),
        },
        "lines": {
            "base": ("Base", "acrylic", "matt", 12, CITADEL_POT, False, FRONTLINE),
            "layer": ("Layer", "acrylic", "matt", 12, CITADEL_POT, False, FRONTLINE),
            "shade": ("Shade", "wash", "matt", 18, CITADEL_POT, False, FRONTLINE),
            "contrast": ("Contrast", "contrast", "satin", 18, CITADEL_POT, False, FRONTLINE),
            "dry": ("Dry", "acrylic", "matt", 12, CITADEL_POT, False, FRONTLINE),
            "tech": ("Technical", "acrylic", "matt", 12, CITADEL_POT, False, FRONTLINE),
            "air": ("Air", "air", "matt", 24, CITADEL_POT, False, FRONTLINE),
            "spray": ("Spray", "primer", "matt", 400, SPRAY, False, FRONTLINE),
            "foundation": ("Foundation (legacy)", "acrylic", "matt", 12, CITADEL_POT, True, ""),
        },
    },
    "AK.md": {
        "brand": "AK Interactive",
        "has_code": True,
        "sets": {
            "Standard (3rd Gen)": ("g3", None, "Standard"),
            "AFV (3rd Gen)": ("g3", None, "AFV"),
            "Figures (3rd Gen)": ("g3", None, "Figures"),
            "Air (3rd Gen)": ("g3air", None),
            "Metallic (3rd Gen)": ("g3", "metallic", "Metallic"),
            "Intense (3rd Gen)": ("g3", None, "Intense"),
            "Pastel (3rd Gen)": ("g3", None, "Pastel"),
            "Ink (3rd Gen)": ("g3", "ink", "Ink"),
            "Auxiliary (3rd Gen)": ("g3", None, "Auxiliary"),
            "Primer (3rd Gen)": ("g3primer", None),
            "AFV": ("legacy", None, "AFV"),
            "Air": ("legacy", "air", "Air"),
            "Figures": ("legacy", None, "Figures"),
            "General": ("legacy", None, "General"),
            "Naval": ("legacy", None, "Naval"),
        },
        "lines": {
            "g3": ("3rd Generation Acrylics", "acrylic", "matt", 17, DROPPER, False, FRONTLINE),
            "g3air": ("3rd Generation Air", "air", "matt", 17, DROPPER, False, FRONTLINE),
            "g3primer": ("3rd Generation Primer", "primer", "matt", 60, OTHER_JAR, False, FRONTLINE),
            "legacy": ("Acrylics (pre-3rd Gen)", "acrylic", "matt", 17, DROPPER, True, ""),
        },
    },
    "AKRC.md": {
        "brand": "AK Interactive",
        "has_code": True,
        "sets": {
            "Real Colors - WWII": ("rc", None, "WWII"),
            "Real Colors - Air": ("rc", None, "Air"),
            "Real Colors - AFV": ("rc", None, "AFV"),
            "Real Colors - Standard": ("rc", None, "Standard"),
            "Real Colors - Modern": ("rc", None, "Modern"),
            "Real Colors - Clear": ("rc", None, "Clear"),
        },
        "lines": {
            "rc": ("Real Colors", "lacquer", "matt", 10, SMALL_DROPPER, False, FRONTLINE),
        },
    },
    "Scale75.md": {
        "brand": "Scale 75",
        "has_code": False,
        "sets": {
            "Scale Color Range": "sc",
            "Fantasy & Games Range": "fg",
            "Warfront  Range": "wf",
            "Artist Range": "art",
            "Instant Colors Range": "inst",
            "Metal N Alchemy Range": "metal",
            "Inktensity Range": "ink",
            "FX Range": "fx",
            "Soil Works": "soil",
            "Primers": "primer",
        },
        "lines": {
            "sc": ("Scalecolor", "acrylic", "matt", 17, DROPPER, False, FRONTLINE),
            "fg": ("Fantasy & Games", "acrylic", "matt", 17, DROPPER, False, FRONTLINE),
            "wf": ("Warfront", "acrylic", "matt", 17, DROPPER, False, FRONTLINE),
            "art": ("Scalecolor Artist", "acrylic", "matt", 20, DROPPER, False, FRONTLINE),
            "inst": ("Instant Colors", "contrast", "satin", 17, DROPPER, False, FRONTLINE),
            "metal": ("Metal n' Alchemy", "metallic", "metallic", 17, DROPPER, False, FRONTLINE),
            "ink": ("Inktensity", "ink", "satin", 17, DROPPER, False, FRONTLINE),
            "fx": ("FX", "acrylic", "matt", 17, DROPPER, False, FRONTLINE),
            "soil": ("Soil Works", "pigment", "matt", 35, OTHER_JAR, False, FRONTLINE),
            "primer": ("Primers", "primer", "matt", 60, OTHER_JAR, False, FRONTLINE),
        },
    },
    "TurboDork.md": {
        "brand": "Turbo Dork",
        "has_code": False,
        "sets": {"Turbo Dork": "td"},
        "lines": {
            "td": ("Turboshift & Metallics", "metallic", "metallic", 20, DROPPER, False, FRONTLINE),
        },
    },
    "Tamiya.md": {
        "brand": "Tamiya",
        "has_code": True,
        "sets": {
            "Acrylics Mini Flat": ("mini", None, "Flat"),
            "Acrylics Mini Gloss": ("mini", None, "Gloss"),
            "Lacquer Paint": "lp",
            "Tamiya Spray": "ts",
            "Aircraft Spray": "as",
        },
        "lines": {
            "mini": ("Acrylic Mini", "acrylic", "matt", 10, TAMIYA_JAR, False, FRONTLINE),
            "lp": ("Lacquer Paint (LP)", "lacquer", "gloss", 10, TAMIYA_JAR, False, FRONTLINE),
            "ts": ("Spray (TS)", "lacquer", "gloss", 100, SPRAY, False, FRONTLINE),
            "as": ("Aircraft Spray (AS)", "lacquer", "matt", 100, SPRAY, False, ""),
        },
    },
    "Mig.md": {
        "brand": "Ammo by Mig",
        "has_code": True,
        "sets": {
            "Acrylics": "acr",
            "Washes": ("acr", "wash"),
            "Primers": "primer",
        },
        "lines": {
            "acr": ("Acrylics", "acrylic", "matt", 17, DROPPER, False, ""),
            "primer": ("One Shot Primer", "primer", "matt", 60, OTHER_JAR, False, ""),
        },
    },
}


def slugify(s: str) -> str:
    s = s.lower().replace("&", "and").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


MEDIUM_RE = re.compile(
    r"\b(thinner|retarder|flow improver|cleaner|mask|maskol|glue|crackle)\b"
    r"|\bmedium\b\s*(-\d+-)?\s*$"  # "Glaze Medium", "Lahmian Medium" but not "Medium Green"
    r"|^(airbrush|speedpaint|contrast|glaze|matte?|gloss|satin|metal|drying) medium\b",
    re.IGNORECASE,
)
VARNISH_RE = re.compile(r"\bvarnish\b|'ardcoat|^(flat |gloss |matt?e? )?clear$", re.IGNORECASE)


def infer_type(name: str, default: str) -> str:
    n = name.lower()
    if MEDIUM_RE.search(n):
        return "medium"
    if VARNISH_RE.search(n.strip()):
        return "varnish"
    if re.search(r"\bprimer\b", n):
        return "primer"
    if re.search(r"\b(wash|shade|tone|quickshade)\b", n) and default not in ("primer",):
        return "wash"
    if re.search(r"\bink\b", n) and default == "acrylic":
        return "ink"
    if default in ("acrylic", "air") and re.search(
        r"\b(metal|metallic|gold|silver|steel|bronze|copper|brass|chrome|gunmetal|aluminium|aluminum|iron|tin|pewter)\b", n
    ):
        return "metallic"
    return default


def infer_finish(ptype: str, name: str, default: str) -> str:
    n = name.lower()
    if ptype == "metallic":
        return "metallic"
    if "gloss" in n:
        return "gloss"
    if "satin" in n or "semi-gloss" in n:
        return "satin"
    return default


def clean_name(name: str) -> str:
    name = re.sub(r"\s+10ml\b", "", name.strip())
    name = re.sub(r"\s+–\s*$", "", name)
    return re.sub(r"\s{2,}", " ", name).strip()


def parse_md(path: Path) -> list[dict]:
    rows = []
    header = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if set(cells[0]) <= {"-"}:
            continue
        rec = dict(zip(header, cells))
        hm = HEX_RE.search(rec.get("hex", ""))
        rec["hex"] = ("#" + hm.group(1).upper()) if hm else ""
        rows.append(rec)
    return rows


def main(src: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for fname, cfg in BRANDS.items():
        brand = cfg["brand"]
        bslug = slugify(brand)
        rows = parse_md(src / fname)
        per_line: dict[str, dict[str, dict]] = {k: {} for k in cfg["lines"]}
        for rec in rows:
            target = cfg["sets"].get(rec.get("set", ""))
            if target is None:
                continue
            if isinstance(target, str):
                target = (target, None)
            line_key, type_override = target[0], target[1]
            tag = target[2] if len(target) > 2 else ""
            lname, ltype, lfinish, lvol, lfmt, ldisc, _ = cfg["lines"][line_key]
            name = clean_name(rec["name"])
            code = rec.get("code", "").strip() if cfg["has_code"] else ""
            if code.lower() in ("null", "none", "-", "n/a"):
                code = ""
            ptype = type_override or infer_type(name, ltype)
            key = code or name.lower()
            existing = per_line[line_key].get(key)
            if existing:
                if tag and tag not in existing["tags"].split(";"):
                    existing["tags"] = ";".join(t for t in (existing["tags"], tag) if t)
                continue
            per_line[line_key][key] = {
                "code": code,
                "name": name,
                "hex": rec["hex"],
                "type": ptype,
                "finish": infer_finish(ptype, name, lfinish),
                "volume_ml": "",
                "barcode": "",
                "format": "",
                "discontinued": "",
                "tags": tag,
            }
        for line_key, paints in per_line.items():
            if not paints:
                continue
            lname, ltype, lfinish, lvol, lfmt, ldisc, stocked = cfg["lines"][line_key]
            rel = f"{bslug}/{slugify(lname)}.csv"
            (out / bslug).mkdir(exist_ok=True)
            with open(out / rel, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(
                    fh,
                    fieldnames=["code", "name", "hex", "type", "finish", "volume_ml", "barcode", "format", "discontinued", "tags"],
                )
                w.writeheader()
                for p in sorted(paints.values(), key=lambda p: (p["code"] or "~", p["name"].lower())):
                    w.writerow(p)
            manifest.append(
                {
                    "brand": brand,
                    "line": lname,
                    "file": rel,
                    "default_type": ltype,
                    "default_finish": lfinish,
                    "default_volume_ml": lvol,
                    "default_format": lfmt,
                    "discontinued": "1" if ldisc else "",
                    "stocked_by": stocked,
                    "source": "Arcturus5404/miniature-paints (MIT)",
                }
            )
            print(f"{rel}: {len(paints)} paints")
    with open(out / "lines.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(manifest[0].keys()))
        w.writeheader()
        w.writerows(manifest)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
