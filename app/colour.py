"""Colour maths: hex parsing, OKLab, CIEDE2000 and spectral (Kubelka-Munk) mixing.

The spectral mixing is a straight Python port of spectral.js v3 by Ronald van Wijnen
(MIT licence, see app/static/vendor/spectral.LICENSE), so the server and the browser
preview give the same answer.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

_DATA = json.loads((Path(__file__).parent / "spectral_data.json").read_text())
SIZE = 38
GAMMA = 2.4
EPS = 2.220446049250313e-16
_BASE = _DATA["BASE_SPECTRA"]
_CMF = _DATA["CMF"]
_CONV = _DATA["CONVERSION"]

HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalise_hex(value: str | None) -> str | None:
    """Return '#RRGGBB' (upper case) or None if the value isn't a hex colour."""
    if not value:
        return None
    m = HEX_RE.match(value.strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h.upper()


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = normalise_hex(h) or "#000000"
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def rgb_to_hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, int(round(c)))):02X}" for c in rgb)


def _mul(m, v):
    return [sum(a * b for a, b in zip(row, v)) for row in m]


def _uncompand(x):
    return ((x + 0.055) / 1.055) ** GAMMA if x > 0.04045 else x / 12.92


def _compand(x):
    return 1.055 * x ** (1.0 / GAMMA) - 0.055 if x > 0.0031308 else x * 12.92


def srgb_to_lrgb(rgb):
    return [_uncompand(c / 255) for c in rgb]


def lrgb_to_srgb(lrgb):
    return [round(_compand(c) * 255) for c in lrgb]


def _cbrt(x):
    return math.copysign(abs(x) ** (1 / 3), x)


def xyz_to_oklab(xyz):
    lms = [_cbrt(x) for x in _mul(_CONV["XYZ_LMS"], xyz)]
    return _mul(_CONV["LMS_LAB"], lms)


def oklab_to_xyz(lab):
    lms = [x**3 for x in _mul(_CONV["LAB_LMS"], lab)]
    return _mul(_CONV["LMS_XYZ"], lms)


def lrgb_to_xyz(lrgb):
    return _mul(_CONV["RGB_XYZ"], lrgb)


def xyz_to_lrgb(xyz):
    return _mul(_CONV["XYZ_RGB"], xyz)


@lru_cache(maxsize=20000)
def hex_to_oklab(h: str) -> tuple[float, float, float]:
    return tuple(xyz_to_oklab(lrgb_to_xyz(srgb_to_lrgb(hex_to_rgb(h)))))


# ---------------------------------------------------------------- CIE Lab / ΔE2000

def _xyz_to_lab_d65(xyz):
    # XYZ (D65, Y=1) -> CIE Lab
    wx, wy, wz = 0.95047, 1.0, 1.08883

    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(xyz[0] / wx), f(xyz[1] / wy), f(xyz[2] / wz)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


@lru_cache(maxsize=20000)
def hex_to_lab(h: str) -> tuple[float, float, float]:
    return _xyz_to_lab_d65(lrgb_to_xyz(srgb_to_lrgb(hex_to_rgb(h))))


def delta_e_2000(lab1, lab2) -> float:
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1 = math.hypot(a1, b1)
    C2 = math.hypot(a2, b2)
    Cm = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cm**7 / (Cm**7 + 25**7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if C1p else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if C2p else 0.0
    dLp = L2 - L1
    dCp = C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    else:
        dhp = h2p - h1p
        if dhp > 180:
            dhp -= 360
        elif dhp < -180:
            dhp += 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp) / 2)
    Lmp = (L1 + L2) / 2
    Cmp = (C1p + C2p) / 2
    if C1p * C2p == 0:
        hmp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hmp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hmp = (h1p + h2p + 360) / 2
    else:
        hmp = (h1p + h2p - 360) / 2
    T = (
        1
        - 0.17 * math.cos(math.radians(hmp - 30))
        + 0.24 * math.cos(math.radians(2 * hmp))
        + 0.32 * math.cos(math.radians(3 * hmp + 6))
        - 0.20 * math.cos(math.radians(4 * hmp - 63))
    )
    d_theta = 30 * math.exp(-(((hmp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cmp**7 / (Cmp**7 + 25**7))
    Sl = 1 + (0.015 * (Lmp - 50) ** 2) / math.sqrt(20 + (Lmp - 50) ** 2)
    Sc = 1 + 0.045 * Cmp
    Sh = 1 + 0.015 * Cmp * T
    Rt = -math.sin(math.radians(2 * d_theta)) * Rc
    return math.sqrt(
        (dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh)
    )


def delta_e(hex1: str, hex2: str) -> float:
    return delta_e_2000(hex_to_lab(hex1), hex_to_lab(hex2))


def describe_delta_e(de: float) -> str:
    if de < 1:
        return "indistinguishable"
    if de < 3:
        return "very close"
    if de < 6:
        return "close"
    if de < 12:
        return "noticeably different"
    return "different"


# ---------------------------------------------------------------- spectral mixing

def _lrgb_to_R(lrgb):
    w = min(lrgb)
    r0, g0, b0 = lrgb[0] - w, lrgb[1] - w, lrgb[2] - w
    c = min(g0, b0)
    m = min(r0, b0)
    y = min(r0, g0)
    r = max(0.0, min(r0 - b0, r0 - g0))
    g = max(0.0, min(g0 - b0, g0 - r0))
    b = max(0.0, min(b0 - g0, b0 - r0))
    B = _BASE
    return [
        max(
            EPS,
            w * B["W"][i] + c * B["C"][i] + m * B["M"][i] + y * B["Y"][i] + r * B["R"][i] + g * B["G"][i] + b * B["B"][i],
        )
        for i in range(SIZE)
    ]


def _R_to_xyz(R):
    return _mul(_CMF, R)


def _ks(r):
    return (1 - r) ** 2 / (2 * r)


def _km(ks):
    return 1 + ks - (ks**2 + 2 * ks) ** 0.5


class _SpectralColour:
    __slots__ = ("R", "KS", "luminance")

    def __init__(self, hex_value: str):
        lrgb = srgb_to_lrgb(hex_to_rgb(hex_value))
        self.R = _lrgb_to_R(lrgb)
        self.KS = [_ks(r) for r in self.R]
        self.luminance = max(EPS, _R_to_xyz(self.R)[1])


@lru_cache(maxsize=5000)
def _spectral(hex_value: str) -> _SpectralColour:
    return _SpectralColour(hex_value)


def _in_gamut(lrgb, eps=0.0):
    return all(-eps <= x <= 1 + eps for x in lrgb)


def _oklab_to_oklch(lab):
    L, a, b = lab
    h = math.degrees(math.atan2(b, a))
    return L, math.hypot(a, b), h if h >= 0 else h + 360


def _oklch_to_oklab(lch):
    L, C, h = lch
    return L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h))


def _delta_ok(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _clamp01(x):
    return min(max(x, 0.0), 1.0)


def _gamut_map_xyz(xyz, jnd=0.03, e=0.0001):
    """Port of spectral.js gamutMap; returns sRGB ints."""
    lrgb = xyz_to_lrgb(xyz)
    if _in_gamut(lrgb):
        return lrgb_to_srgb(lrgb)
    oklab = xyz_to_oklab(xyz)
    L, C, h = _oklab_to_oklch(oklab)
    if L >= 1:
        return [255, 255, 255]
    if L <= 0:
        return [0, 0, 0]
    lo, hi, lo_in = 0.0, C, True
    current = lrgb
    clipped = xyz_to_oklab(lrgb_to_xyz([_clamp01(x) for x in current]))
    E = _delta_ok(clipped, xyz_to_oklab(lrgb_to_xyz(current)))
    if E < jnd:
        return lrgb_to_srgb(xyz_to_lrgb(oklab_to_xyz(clipped)))
    while hi - lo > e:
        chroma = (lo + hi) / 2
        lab = _oklch_to_oklab((L, chroma, h))
        current = xyz_to_lrgb(oklab_to_xyz(lab))
        if lo_in and _in_gamut(current):
            lo = chroma
        else:
            clipped = xyz_to_oklab(lrgb_to_xyz([_clamp01(x) for x in current]))
            E = _delta_ok(clipped, lab)
            if E < jnd:
                if jnd - E < e:
                    break
                lo_in = False
                lo = chroma
            else:
                hi = chroma
    return lrgb_to_srgb(xyz_to_lrgb(oklab_to_xyz(clipped)))


def spectral_mix(components: list[tuple[str, float]]) -> str | None:
    """Mix colours like pigments. components: [(hex, amount), ...]. Returns hex."""
    parts = [(normalise_hex(h), float(a)) for h, a in components if normalise_hex(h) and a and float(a) > 0]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0][0]
    cols = [(_spectral(h), a) for h, a in parts]
    R = []
    for i in range(SIZE):
        ks_mix = 0.0
        total = 0.0
        for col, factor in cols:
            conc = factor**2 * col.luminance
            total += conc
            ks_mix += col.KS[i] * conc
        R.append(_km(ks_mix / total))
    return rgb_to_hex(_gamut_map_xyz(_R_to_xyz(R)))


def oklab_mix(components: list[tuple[str, float]]) -> str | None:
    """Fallback: weighted average in OKLab."""
    parts = [(normalise_hex(h), float(a)) for h, a in components if normalise_hex(h) and a and float(a) > 0]
    if not parts:
        return None
    total = sum(a for _, a in parts)
    lab = [sum(hex_to_oklab(h)[k] * a for h, a in parts) / total for k in range(3)]
    return rgb_to_hex(_gamut_map_xyz(oklab_to_xyz(lab)))


def text_colour_for(h: str | None) -> str:
    """Black or white, whichever reads better on the swatch."""
    if not normalise_hex(h):
        return "#000"
    L = hex_to_oklab(normalise_hex(h))[0]
    return "#000" if L > 0.62 else "#fff"
