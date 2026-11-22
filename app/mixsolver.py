"""Reverse mixing: find 1-, 2- and 3-paint combinations (with ratios) that get close to a target colour.

Uses the same spectral (Kubelka-Munk) model as the mix preview, vectorised with numpy so a few
tens of thousands of candidate mixes take well under a second. Candidates are ranked by OKLab
distance, then the best are re-scored exactly (gamut-mapped, CIEDE2000) for display.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np

from .colour import (
    _BASE,
    _CMF,
    _CONV,
    EPS,
    delta_e,
    hex_to_oklab,
    hex_to_rgb,
    normalise_hex,
    spectral_mix,
    srgb_to_lrgb,
)

_B = {k: np.array(v) for k, v in _BASE.items()}
_CMF_T = np.array(_CMF).T  # (38, 3)
_XYZ_RGB = np.array(_CONV["XYZ_RGB"])
_RGB_XYZ = np.array(_CONV["RGB_XYZ"])
_XYZ_LMS = np.array(_CONV["XYZ_LMS"])
_LMS_LAB = np.array(_CONV["LMS_LAB"])

PAIR_PARTS = [1, 2, 3, 4, 5, 6, 8, 10, 15, 20]
TRIPLE_PARTS = [1, 2, 3, 4, 6]


def _reflectance(hex_value: str) -> np.ndarray:
    r0, g0, b0 = srgb_to_lrgb(hex_to_rgb(hex_value))
    w = min(r0, g0, b0)
    r0, g0, b0 = r0 - w, g0 - w, b0 - w
    c, m, y = min(g0, b0), min(r0, b0), min(r0, g0)
    r = max(0.0, min(r0 - b0, r0 - g0))
    g = max(0.0, min(g0 - b0, g0 - r0))
    b = max(0.0, min(b0 - g0, b0 - r0))
    R = w * _B["W"] + c * _B["C"] + m * _B["M"] + y * _B["Y"] + r * _B["R"] + g * _B["G"] + b * _B["B"]
    return np.maximum(R, EPS)


def _mix_oklab(KS: np.ndarray, lum: np.ndarray, idx: np.ndarray, parts: np.ndarray) -> np.ndarray:
    """KS (n,38), lum (n,), idx (m,k) paint indices, parts (m,k) amounts -> OKLab (m,3)."""
    conc = parts**2 * lum[idx]  # (m,k)
    ks = np.einsum("mk,mkb->mb", conc, KS[idx]) / conc.sum(axis=1, keepdims=True)
    R = 1 + ks - np.sqrt(ks**2 + 2 * ks)
    xyz = R @ _CMF_T
    lrgb = np.clip(xyz @ _XYZ_RGB.T, 0, 1)  # clip for ranking; exact gamut mapping happens later
    lms = np.cbrt((lrgb @ _RGB_XYZ.T) @ _XYZ_LMS.T)
    return lms @ _LMS_LAB.T


@dataclass
class Suggestion:
    paints: list  # list of Paint
    parts: list[float]
    hex: str
    delta_e: float

    @property
    def ratio(self) -> str:
        return " : ".join(f"{p:g}" for p in self.parts)


def _ratios(k: int, values: list[int]) -> np.ndarray:
    out = set()
    for combo in itertools.product(values, repeat=k):
        g = 0
        for v in combo:
            g = math.gcd(g, v)
        out.add(tuple(v // g for v in combo))
    return np.array(sorted(out), dtype=float)


_PAIR_R = _ratios(2, PAIR_PARTS)
_TRIPLE_R = _ratios(3, TRIPLE_PARTS)


def suggest_mixes(paints: list, target_hex: str, limit: int = 8, max_paints: int = 3) -> list[Suggestion]:
    """paints: Paint objects with display_hex. Returns the best distinct combinations."""
    target = normalise_hex(target_hex)
    paints = [p for p in paints if p.display_hex]
    # one bottle per paint id
    seen, uniq = set(), []
    for p in paints:
        if p.id not in seen:
            seen.add(p.id)
            uniq.append(p)
    paints = uniq
    if not target or not paints:
        return []
    t = np.array(hex_to_oklab(target))
    Rs = np.array([_reflectance(p.display_hex) for p in paints])
    KS = (1 - Rs) ** 2 / (2 * Rs)
    lum = np.maximum(Rs @ _CMF_T[:, 1], EPS)
    n = len(paints)

    cands: list[tuple[float, tuple[int, ...], tuple[float, ...]]] = []
    # singles
    single_lab = _mix_oklab(KS, lum, np.arange(n)[:, None], np.ones((n, 1)))
    d1 = np.linalg.norm(single_lab - t, axis=1)
    for i in range(n):
        cands.append((float(d1[i]), (i,), (1.0,)))

    best_pair_members: dict[int, float] = {}
    if n >= 2 and max_paints >= 2:
        pairs = np.array(list(itertools.combinations(range(n), 2)))
        P, Q = len(pairs), len(_PAIR_R)
        idx = np.repeat(pairs, Q, axis=0)
        parts = np.tile(_PAIR_R, (P, 1))
        d = np.linalg.norm(_mix_oklab(KS, lum, idx, parts) - t, axis=1).reshape(P, Q)
        best_q = d.argmin(axis=1)
        best_d = d[np.arange(P), best_q]
        for pi in np.argsort(best_d)[:200]:
            i, j = pairs[pi]
            cands.append((float(best_d[pi]), (int(i), int(j)), tuple(_PAIR_R[best_q[pi]])))
            for m in (int(i), int(j)):
                best_pair_members[m] = min(best_pair_members.get(m, 9), float(best_d[pi]))

    if n >= 3 and max_paints >= 3:
        # Triples from the most promising paints (best pairs + nearest singles)
        pool = sorted(best_pair_members, key=best_pair_members.get)[:9]
        for i in np.argsort(d1)[:4]:
            if int(i) not in pool:
                pool.append(int(i))
        triples = np.array(list(itertools.combinations(pool, 3)))
        if len(triples):
            T, Q = len(triples), len(_TRIPLE_R)
            idx = np.repeat(triples, Q, axis=0)
            parts = np.tile(_TRIPLE_R, (T, 1))
            d = np.linalg.norm(_mix_oklab(KS, lum, idx, parts) - t, axis=1).reshape(T, Q)
            best_q = d.argmin(axis=1)
            best_d = d[np.arange(T), best_q]
            for ti in np.argsort(best_d)[:100]:
                cands.append((float(best_d[ti]), tuple(int(x) for x in triples[ti]), tuple(_TRIPLE_R[best_q[ti]])))

    cands.sort(key=lambda c: c[0])
    out: list[Suggestion] = []
    used_sets: set[frozenset] = set()
    for _, members, parts in cands[: limit * 6]:
        key = frozenset(members)
        if key in used_sets:
            continue
        used_sets.add(key)
        ps = [paints[i] for i in members]
        hx = spectral_mix([(p.display_hex, a) for p, a in zip(ps, parts)])
        de = delta_e(target, hx)
        out.append(Suggestion(ps, [float(a) for a in parts], hx, de))
    # Fewer paints is easier to mix: a slight preference for simpler recipes.
    out.sort(key=lambda s: s.delta_e + 0.4 * (len(s.paints) - 1))
    pruned: list[Suggestion] = []
    for s in out:
        # Drop a mix that isn't meaningfully better than a simpler one using a subset of its paints
        if len(s.paints) > 1 and any(
            {x.id for x in o.paints} < {x.id for x in s.paints} and o.delta_e <= s.delta_e + 0.5 for o in pruned
        ):
            continue
        pruned.append(s)
    return pruned[:limit]
