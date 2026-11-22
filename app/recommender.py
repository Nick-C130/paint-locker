"""Purchase recommender: "what should I buy next?"

Follows the plan's design:

* **Mixing reach.** Build the set of colours you can reach now: each owned paint, plus every pair of
  owned paints at common ratios (spectral mixing, OKLab). Sample points spread across the sRGB
  gamut count as "covered" when something reachable is within a small colour difference
  (≈ ΔE 3). A candidate's reach score is how many uncovered sample points it would cover, alone or
  mixed with one of your paints.
* **Demand.** Every logged colour request is replayed: how much closer would your best option get
  with this paint? Requests you matched badly count most; recent ones count more (half weight after
  about six months).
* Both scores are scaled 0-1 and blended with a weight you choose. Picks are made greedily: take
  the best, assume you bought it, re-score, take the next, so the list isn't five near-identical
  olive drabs.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from .colour import hex_to_oklab
from .mixsolver import _CMF_T, EPS, _mix_oklab, _reflectance

REACH_RATIOS = np.array([[1, 1], [2, 1], [1, 2], [3, 1], [1, 3]], dtype=float)
COVER_THRESHOLD = 0.03  # OKLab distance; roughly ΔE 3
HALF_LIFE_DAYS = 182
GRID_STEPS = 9  # 9^3 = 729 sample colours across sRGB


def _sample_points() -> tuple[np.ndarray, list[str]]:
    vals = np.linspace(0, 255, GRID_STEPS).round().astype(int)
    hexes = [f"#{r:02X}{g:02X}{b:02X}" for r in vals for g in vals for b in vals]
    return np.array([hex_to_oklab(h) for h in hexes]), hexes


_SAMPLES, _SAMPLE_HEX = _sample_points()


def _hue_family(hex_value: str) -> str:
    r, g, b = (int(hex_value[i : i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if s < 0.15:
        return "greys" if 0.15 < l < 0.85 else ("darks" if l <= 0.15 else "lights")
    deg = h * 360
    names = [
        (15, "reds"), (40, "oranges and browns"), (65, "yellows, tans and ochres"), (90, "yellow-greens and olives"),
        (160, "greens"), (200, "teals"), (250, "blues"), (290, "violets"), (335, "purples and magentas"), (360, "reds"),
    ]
    for limit, name in names:
        if deg <= limit:
            return name
    return "reds"


class _Model:
    """Spectral data for a list of paints, for fast vectorised mixing."""

    def __init__(self, hexes: list[str]):
        if hexes:
            R = np.array([_reflectance(h) for h in hexes])
            self.KS = (1 - R) ** 2 / (2 * R)
            self.lum = np.maximum(R @ _CMF_T[:, 1], EPS)
            self.lab = _mix_oklab(self.KS, self.lum, np.arange(len(hexes))[:, None], np.ones((len(hexes), 1)))
        else:
            self.KS = np.zeros((0, 38))
            self.lum = np.zeros(0)
            self.lab = np.zeros((0, 3))


def _reachable(owned: _Model) -> np.ndarray:
    n = len(owned.lum)
    if n == 0:
        return np.zeros((0, 3))
    labs = [owned.lab]
    if n >= 2:
        i, j = np.triu_indices(n, 1)
        pairs = np.stack([i, j], axis=1)
        idx = np.repeat(pairs, len(REACH_RATIOS), axis=0)
        parts = np.tile(REACH_RATIOS, (len(pairs), 1))
        labs.append(_mix_oklab(owned.KS, owned.lum, idx, parts))
    return np.concatenate(labs)


def _sqdist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise squared distances (len(a), len(b)) via the matmul identity (fast)."""
    return np.maximum((a * a).sum(1)[:, None] + (b * b).sum(1)[None, :] - 2 * a @ b.T, 0)


def _min_dist(points: np.ndarray, reach: np.ndarray, chunk: int = 20000) -> np.ndarray:
    if len(reach) == 0:
        return np.full(len(points), 1.0)
    best = np.full(len(points), np.inf)
    for s in range(0, len(reach), chunk):
        best = np.minimum(best, _sqdist(points, reach[s : s + chunk]).min(axis=1))
    return np.sqrt(best)


def _all_candidate_reach(cand: _Model, owned: _Model, cis: list[int]) -> np.ndarray:
    """(len(cis), M, 3): colours newly reachable with each candidate, alone and mixed with each owned paint."""
    n = len(owned.lum)
    singles = cand.lab[cis][:, None, :]
    if not n:
        return singles
    KS = np.vstack([owned.KS, cand.KS[cis]])
    lum = np.concatenate([owned.lum, cand.lum[cis]])
    C, Q = len(cis), len(REACH_RATIOS)
    cpos = n + np.arange(C)
    pairs = np.stack([np.tile(np.arange(n), C), np.repeat(cpos, n)], axis=1)  # (C*n, 2)
    idx = np.repeat(pairs, Q, axis=0)
    parts = np.tile(REACH_RATIOS, (C * n, 1))
    mixed = _mix_oklab(KS, lum, idx, parts).reshape(C, n * Q, 3)
    return np.concatenate([singles, mixed], axis=1)


def _min_dist_batched(points: np.ndarray, reach: np.ndarray) -> np.ndarray:
    """points (P,3), reach (C,M,3) -> (C,P) min distance from each point to each candidate's set."""
    C, M, _ = reach.shape
    d = _sqdist(reach.reshape(C * M, 3), points).reshape(C, M, len(points)).min(axis=1)
    return np.sqrt(d)


@dataclass
class Pick:
    paint: object
    score: float
    reach_gain: int
    reach_pct: float
    demand_gain: float
    families: list[str]
    helped: list[dict] = field(default_factory=list)

    def explain(self) -> str:
        bits = []
        if self.reach_gain:
            fam = ", ".join(self.families[:2])
            pct = f"about {self.reach_pct:.0f}%" if self.reach_pct >= 1 else "a little"
            bits.append(f"opens up {pct} more of the colour range you can mix" + (f", mostly {fam}" if fam else ""))
        if self.helped:
            top = self.helped[0]
            bits.append(
                f"would have improved {len(self.helped)} of your past colour requests, including "
                f"{top['label']} (best match ≈ΔE {top['before']:.0f} → {top['after']:.0f})"
            )
        if not bits:
            return "small improvement overall"
        s = "; ".join(bits)
        return s[0].upper() + s[1:]


def recommend(
    owned_paints: list,
    candidates: list,
    requests: list,
    count: int = 5,
    weight_reach: float = 0.5,
    now: datetime | None = None,
) -> list[Pick]:
    """owned_paints / candidates: Paint objects (display_hex). requests: ColourRequest objects."""
    now = now or datetime.now()
    candidates = [c for c in candidates if c.display_hex]
    if not candidates:
        return []
    owned_hex = [p.display_hex for p in owned_paints if p.display_hex]
    cand = _Model([c.display_hex for c in candidates])
    owned = _Model(owned_hex)

    req_lab = np.array([hex_to_oklab(r.target_hex) for r in requests]) if requests else np.zeros((0, 3))
    req_w = np.array(
        [0.5 ** (max(0.0, (now - (r.created_at or now)).days) / HALF_LIFE_DAYS) for r in requests]
    ) if requests else np.zeros(0)

    picks: list[Pick] = []
    available = list(range(len(candidates)))
    for _ in range(count):
        reach = _reachable(owned)
        d_samples = _min_dist(_SAMPLES, reach)
        uncovered = d_samples > COVER_THRESHOLD
        d_req = _min_dist(req_lab, reach) if len(req_lab) else np.zeros(0)
        # Requests matched badly matter most: weight by how far off the current best is.
        req_need = req_w * np.clip(d_req / COVER_THRESHOLD, 0, 4)

        unc_pts = _SAMPLES[uncovered]
        unc_idx = np.nonzero(uncovered)[0]
        reach_gain = np.zeros(len(candidates))
        demand_gain = np.zeros(len(candidates))
        new_cover: dict[int, np.ndarray] = {}
        req_after: dict[int, np.ndarray] = {}
        for s0 in range(0, len(available), 64):
            cis = available[s0 : s0 + 64]
            new = _all_candidate_reach(cand, owned, cis)
            if len(unc_pts):
                covered = _min_dist_batched(unc_pts, new) <= COVER_THRESHOLD
                reach_gain[cis] = covered.sum(axis=1)
                for k, ci in enumerate(cis):
                    new_cover[ci] = unc_idx[covered[k]]
            if len(req_lab):
                after = np.minimum(d_req[None, :], _min_dist_batched(req_lab, new))
                improvement = np.clip(d_req[None, :] - np.maximum(after, COVER_THRESHOLD), 0, None) / COVER_THRESHOLD
                demand_gain[cis] = (improvement * req_need[None, :]).sum(axis=1)
                for k, ci in enumerate(cis):
                    req_after[ci] = after[k]

        r_max = reach_gain.max() or 1
        # Absolute floor so a trivial demand gain doesn't get scaled up to "1.0"
        d_max = max(demand_gain.max(), 1.0)
        w = min(1.0, max(0.0, weight_reach))
        if not len(req_lab) or demand_gain.max() < 1e-6:
            w = 1.0  # nothing to go on for demand yet
        score = w * reach_gain / r_max + (1 - w) * demand_gain / d_max
        best = max(available, key=lambda i: score[i]) if available else None
        if best is None or score[best] <= 0:
            break
        fams = [_hue_family(_SAMPLE_HEX[i]) for i in new_cover.get(best, [])]
        fam_counts: dict[str, int] = {}
        for f in fams:
            fam_counts[f] = fam_counts.get(f, 0) + 1
        helped = []
        if best in req_after:
            for k, r in enumerate(requests):
                before, after = d_req[k] * 100, req_after[best][k] * 100
                if before - after >= 1.0 and before > COVER_THRESHOLD * 100:
                    helped.append({"label": r.context or r.target_hex, "hex": r.target_hex, "before": before, "after": after})
            helped.sort(key=lambda h: h["before"] - h["after"], reverse=True)
        picks.append(
            Pick(
                paint=candidates[best],
                score=float(score[best]),
                reach_gain=int(reach_gain[best]),
                reach_pct=100 * reach_gain[best] / len(_SAMPLES),
                demand_gain=float(demand_gain[best]),
                families=sorted(fam_counts, key=fam_counts.get, reverse=True),
                helped=helped,
            )
        )
        # Assume it's bought; rescore the rest against the bigger collection.
        owned = _Model(owned_hex + [candidates[best].display_hex])
        owned_hex = owned_hex + [candidates[best].display_hex]
        available.remove(best)
    return picks


def coverage(owned_paints: list) -> float:
    """Share of sample colours you can reach (within ≈ΔE 3) with what you own."""
    owned = _Model([p.display_hex for p in owned_paints if p.display_hex])
    d = _min_dist(_SAMPLES, _reachable(owned))
    return float((d <= COVER_THRESHOLD).mean())

