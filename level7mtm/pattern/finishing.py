"""Production finishing applied after the pattern is generated: fabric shrinkage compensation.

Definitions (stored with every generation, see docs/ENGINE.md):

* ``expected_shrinkage`` s %: the fabric loses s % after processing. The pattern is enlarged by
  ``1 / (1 - s/100)`` so that the finished garment reaches the target (adding s % is *not* exact).
* ``compensation`` c %: the pattern is enlarged by ``1 + c/100`` exactly as given.

Length = along the grain line, width = across it. Factors are applied in the grain-aligned frame of each
piece, once, at this stage only. Pieces without a grain line cannot be compensated (blocking issue).
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass

from .allowance import Issue
from .geometry import Path, rotate


@dataclass
class ShrinkSpec:
    material: str
    length_pct: float = 0.0
    width_pct: float = 0.0
    definition: str = "expected_shrinkage"     # or "compensation"
    already_applied_by_source: bool = False

    def factors(self) -> tuple[float, float]:
        if self.already_applied_by_source:
            return 1.0, 1.0
        if self.definition == "expected_shrinkage":
            for v in (self.length_pct, self.width_pct):
                if not 0 <= v < 50:
                    raise ValueError(f"expected shrinkage {v}% outside 0..50%")
            return 1 / (1 - self.length_pct / 100), 1 / (1 - self.width_pct / 100)
        if self.definition == "compensation":
            return 1 + self.length_pct / 100, 1 + self.width_pct / 100
        raise ValueError(f"unknown shrinkage definition {self.definition!r}")


def apply_shrinkage(result, specs: dict[str, ShrinkSpec]):
    """Return a copy of ``result`` with shrinkage applied per material, plus issues and the factors used."""
    res = copy.deepcopy(result)
    used = {}
    issues: list[Issue] = []
    for p in res.pieces:
        spec = specs.get(p.material)
        if spec is None:
            used[p.id] = (1.0, 1.0)
            continue
        fl, fw = spec.factors()
        used[p.id] = (fl, fw)
        if fl == 1.0 and fw == 1.0:
            continue
        if not p.grain:
            issues.append(Issue("blocking", "shrink.no_grain", "piece has no grain line; shrinkage cannot be applied",
                                piece=p.id))
            continue
        (ax, ay), (bx, by) = p.grain
        ang = math.degrees(math.atan2(by - ay, bx - ax))
        c = (ax, ay)

        def fn(pt, ang=ang, c=c, fl=fl, fw=fw):
            q = rotate(pt, c, 90 - ang)            # grain now points along +y
            q = (c[0] + (q[0] - c[0]) * fw, c[1] + (q[1] - c[1]) * fl)
            return rotate(q, c, ang - 90)
        p.seam = [fn(q) for q in p.seam]
        p.cut = [fn(q) for q in p.cut]
        p.seam_edges = [(n, path.transformed(fn)) for n, path in p.seam_edges]
        p.internal = [path.transformed(fn) for path in p.internal]
        for n in p.notches:
            n["position"] = fn(n["position"])
        for lb in p.labels:
            lb["position"] = fn(lb["position"])
        p.grain = (fn(p.grain[0]), fn(p.grain[1]))
        p.edge_lengths = {n: path.length() for n, path in p.seam_edges}
    res.issues = list(res.issues) + issues
    return res, used, issues


def grain_angle(piece) -> float | None:
    if not piece.grain:
        return None
    (ax, ay), (bx, by) = piece.grain
    return math.degrees(math.atan2(by - ay, bx - ax)) % 180.0


def cut_notch(piece, notch_pos, length: float = 5.0):
    """Nearest point on the cut line to a seam notch, and the inward unit direction."""
    best, bestd = None, float("inf")
    pts = piece.cut
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((notch_pos[0] - a[0]) * dx + (notch_pos[1] - a[1]) * dy) / L2))
        q = (a[0] + dx * t, a[1] + dy * t)
        d = math.hypot(q[0] - notch_pos[0], q[1] - notch_pos[1])
        if d < bestd:
            best, bestd = q, d
    if best is None:
        return None, None
    vx, vy = notch_pos[0] - best[0], notch_pos[1] - best[1]
    n = math.hypot(vx, vy)
    if n < 1e-9:
        return best, None
    return best, (vx / n, vy / n)


__all__ = ["ShrinkSpec", "apply_shrinkage", "grain_angle", "cut_notch", "Path"]
