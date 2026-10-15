"""Seam allowance: cut line from a closed seam line with a separate allowance per edge.

Rules (see docs/ENGINE.md):
* every edge is offset outward by its own allowance (0 = cut on the seam line);
* corners are joined by intersecting the neighbouring offset edges (mitre); a mitre longer than
  ``miter_limit`` x allowance is cut square (bevel) and reported as a *warning* with its position;
* an edge whose offset collapses (curve tighter than the allowance) is an *error*: nothing is repaired silently;
* the finished cut line is checked for self-intersections; any crossing is an *error* with its position.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .geometry import (EPS, Pt, add, dist, intersect_lines, mul, polygon_area, segment_intersection,
                       self_intersections, sub, unit)


@dataclass
class Issue:
    severity: str          # "blocking" / "review" / "info"
    code: str
    message: str
    position: Pt | None = None
    piece: str | None = None
    edge: str | None = None


@dataclass
class AllowanceResult:
    cut: list[Pt]
    issues: list[Issue] = field(default_factory=list)
    edge_ranges: dict[str, tuple[int, int]] = field(default_factory=dict)   # edge -> index range in cut

    @property
    def ok(self) -> bool:
        return not any(i.severity == "blocking" for i in self.issues)


def _offset_polyline(pts: list[Pt], d: float, outward_sign: float) -> tuple[list[Pt], list[int]]:
    """Offset an open polyline by d to the side given by outward_sign (+1 = right of travel direction).
    Returns offset points and indices of segments whose direction reversed (collapse)."""
    if d == 0:
        return list(pts), []
    normals = []
    for i in range(len(pts) - 1):
        t = unit(sub(pts[i + 1], pts[i]))
        normals.append((t[1] * outward_sign, -t[0] * outward_sign))
    out = [add(pts[0], mul(normals[0], d))]
    for i in range(1, len(pts) - 1):
        n1, n2 = normals[i - 1], normals[i]
        b = add(n1, n2)
        bl = math.hypot(*b)
        if bl < 1e-9:
            out.append(add(pts[i], mul(n1, d)))
            continue
        b = (b[0] / bl, b[1] / bl)
        cos_half = b[0] * n1[0] + b[1] * n1[1]
        out.append(add(pts[i], mul(b, d / max(cos_half, 0.2))))
    out.append(add(pts[-1], mul(normals[-1], d)))
    collapsed = []
    for i in range(len(pts) - 1):
        a = sub(pts[i + 1], pts[i])
        b = sub(out[i + 1], out[i])
        if a[0] * b[0] + a[1] * b[1] < -EPS:
            collapsed.append(i)
    return out, collapsed


def make_cut(edges: list[tuple[str, list[Pt], float]], miter_limit: float = 4.0, piece: str = "") -> AllowanceResult:
    """``edges``: ordered (name, flattened seam points, allowance mm) forming a closed seam line."""
    issues: list[Issue] = []
    ring = [p for _, pts, _ in edges for p in pts[:-1]]
    if len(ring) < 3:
        return AllowanceResult([], [Issue("blocking", "seam.too_few_points", "seam line has fewer than 3 points",
                                          piece=piece)])
    for i, (name, pts, _) in enumerate(edges):
        nxt = edges[(i + 1) % len(edges)][1]
        if dist(pts[-1], nxt[0]) > 0.01:
            issues.append(Issue("blocking", "seam.not_closed",
                                f"edge '{name}' ends {dist(pts[-1], nxt[0]):.2f} mm away from the next edge",
                                pts[-1], piece, name))
    if issues:
        return AllowanceResult([], issues)
    area = polygon_area(ring)
    if abs(area) < 1.0:
        return AllowanceResult([], [Issue("blocking", "seam.degenerate", "seam line encloses no area", piece=piece)])
    outward = 1.0 if area > 0 else -1.0       # CCW ring: outside is to the right of travel

    offs = []
    for name, pts, a in edges:
        if a < 0:
            issues.append(Issue("blocking", "allowance.negative", f"negative allowance {a} on edge '{name}'",
                                piece=piece, edge=name))
            a = 0
        clean = [pts[0]]
        for p in pts[1:]:
            if dist(p, clean[-1]) > 1e-6:
                clean.append(p)
        if len(clean) < 2:
            issues.append(Issue("blocking", "seam.zero_edge", f"edge '{name}' has zero length", pts[0], piece, name))
            return AllowanceResult([], issues)
        o, collapsed = _offset_polyline(clean, a, outward)
        for i in collapsed:
            issues.append(Issue("blocking", "allowance.collapse",
                                f"allowance {a} mm on edge '{name}' folds over itself (curve tighter than the "
                                f"allowance)", clean[i], piece, name))
        offs.append((name, o, a, clean))

    cut: list[Pt] = []
    ranges = {}
    n = len(offs)
    joins = []
    for i in range(n):
        name, o, a, clean = offs[i]
        nname, no, na, nclean = offs[(i + 1) % n]
        corner = clean[-1]
        if a == 0 and na == 0:
            joins.append(corner)
            continue
        hit = segment_intersection(o[-2], o[-1], no[0], no[1])
        if hit is None:
            try:
                hit = intersect_lines(o[-2], o[-1], no[0], no[1])
            except ValueError:
                hit = None
        lim = miter_limit * max(a, na, 1e-9)
        if hit is None or dist(hit, corner) > lim:
            issues.append(Issue("review", "allowance.bevel",
                                f"corner between '{name}' and '{nname}' is too sharp for a mitre; "
                                f"cut square (bevel) instead", corner, piece, name))
            joins.append((o[-1], no[0]))
        else:
            joins.append(hit)
    for i in range(n):
        name, o, a, clean = offs[i]
        start_join = joins[i - 1]
        end_join = joins[i]
        s = start_join[1] if isinstance(start_join[0], tuple) else start_join
        e = end_join[0] if isinstance(end_join[0], tuple) else end_join
        body = o[1:-1]
        i0 = len(cut)
        cut.append(s)
        cut.extend(body)
        cut.append(e)
        ranges[name] = (i0, len(cut) - 1)
    # remove consecutive duplicates
    clean_cut = [cut[0]]
    for p in cut[1:]:
        if dist(p, clean_cut[-1]) > 1e-6:
            clean_cut.append(p)
    if dist(clean_cut[0], clean_cut[-1]) < 1e-6:
        clean_cut.pop()
    for p in self_intersections(clean_cut):
        issues.append(Issue("blocking", "cut.self_intersection",
                            "cut line crosses itself (allowance too large for this shape)", p, piece))
    return AllowanceResult(clean_cut, issues, ranges)
