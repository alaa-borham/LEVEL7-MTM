"""Geometric primitives for the parametric pattern engine. Units: millimetres, y axis up.

Curves keep their definition (cubic Bézier control points); flattening for display, length measurement and
export is done on demand with an explicit tolerance and never replaces the definition.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

Pt = tuple[float, float]
EPS = 1e-9


class GeometryError(ValueError):
    pass


def add(a: Pt, b: Pt) -> Pt:
    return (a[0] + b[0], a[1] + b[1])


def sub(a: Pt, b: Pt) -> Pt:
    return (a[0] - b[0], a[1] - b[1])


def mul(a: Pt, k: float) -> Pt:
    return (a[0] * k, a[1] * k)


def dist(a: Pt, b: Pt) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def lerp(a: Pt, b: Pt, t: float) -> Pt:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def unit(v: Pt) -> Pt:
    n = math.hypot(*v)
    if n < EPS:
        raise GeometryError("zero-length direction")
    return (v[0] / n, v[1] / n)


def polar(origin: Pt, angle_deg: float, length: float) -> Pt:
    a = math.radians(angle_deg)
    return (origin[0] + length * math.cos(a), origin[1] + length * math.sin(a))


def angle_of(a: Pt, b: Pt) -> float:
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def rotate(p: Pt, center: Pt, angle_deg: float) -> Pt:
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    x, y = p[0] - center[0], p[1] - center[1]
    return (center[0] + c * x - s * y, center[1] + s * x + c * y)


def mirror(p: Pt, a: Pt, b: Pt) -> Pt:
    """Reflect p about the line through a and b."""
    d = unit(sub(b, a))
    v = sub(p, a)
    proj = mul(d, v[0] * d[0] + v[1] * d[1])
    return add(a, sub(mul(proj, 2), v))


def project(p: Pt, a: Pt, b: Pt) -> Pt:
    """Foot of the perpendicular from p onto the (infinite) line a-b."""
    d = sub(b, a)
    L2 = d[0] ** 2 + d[1] ** 2
    if L2 < EPS:
        raise GeometryError("projection onto a degenerate line")
    t = ((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) / L2
    return add(a, mul(d, t))


def intersect_lines(a1: Pt, a2: Pt, b1: Pt, b2: Pt) -> Pt:
    """Intersection of the infinite lines a1-a2 and b1-b2."""
    d1, d2 = sub(a2, a1), sub(b2, b1)
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        raise GeometryError("lines are parallel; no intersection")
    t = ((b1[0] - a1[0]) * d2[1] - (b1[1] - a1[1]) * d2[0]) / den
    return add(a1, mul(d1, t))


def segment_intersection(p1: Pt, p2: Pt, q1: Pt, q2: Pt) -> Pt | None:
    d1, d2 = sub(p2, p1), sub(q2, q1)
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        return None
    t = ((q1[0] - p1[0]) * d2[1] - (q1[1] - p1[1]) * d2[0]) / den
    u = ((q1[0] - p1[0]) * d1[1] - (q1[1] - p1[1]) * d1[0]) / den
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return add(p1, mul(d1, t))
    return None


def intersect_line_circle(a: Pt, b: Pt, c: Pt, r: float, pick: str = "far") -> Pt:
    """Point on the line a->b at distance r from c ('near'/'far' relative to a)."""
    d = unit(sub(b, a))
    f = sub(a, c)
    B = 2 * (f[0] * d[0] + f[1] * d[1])
    C = f[0] ** 2 + f[1] ** 2 - r * r
    disc = B * B - 4 * C
    if disc < 0:
        raise GeometryError("line does not reach the circle")
    s = math.sqrt(disc)
    ts = sorted([(-B - s) / 2, (-B + s) / 2])
    t = ts[1] if pick == "far" else ts[0]
    return add(a, mul(d, t))


# ---------------------------------------------------------------- segments

@dataclass
class Segment:
    """A path element: straight line (2 points) or cubic Bézier (4 points)."""
    pts: tuple[Pt, ...]
    name: str = ""

    @property
    def start(self) -> Pt:
        return self.pts[0]

    @property
    def end(self) -> Pt:
        return self.pts[-1]

    @property
    def is_curve(self) -> bool:
        return len(self.pts) == 4

    def point_at(self, t: float) -> Pt:
        if not self.is_curve:
            return lerp(self.pts[0], self.pts[1], t)
        p0, p1, p2, p3 = self.pts
        mt = 1 - t
        a, b, c, d = mt ** 3, 3 * mt * mt * t, 3 * mt * t * t, t ** 3
        return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])

    def flatten(self, tol: float = 0.05) -> list[Pt]:
        if not self.is_curve:
            return [self.pts[0], self.pts[1]]
        out = [self.pts[0]]
        _flat_bezier(*self.pts, tol, out, 0)
        return out

    def length(self, tol: float = 0.005) -> float:
        pts = self.flatten(tol)
        return sum(dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))

    def reversed(self) -> "Segment":
        return Segment(tuple(reversed(self.pts)), self.name)

    def split(self, t: float) -> tuple["Segment", "Segment"]:
        if not self.is_curve:
            m = self.point_at(t)
            return Segment((self.pts[0], m), self.name), Segment((m, self.pts[1]), self.name)
        p0, p1, p2, p3 = self.pts
        a, b, c = lerp(p0, p1, t), lerp(p1, p2, t), lerp(p2, p3, t)
        d, e = lerp(a, b, t), lerp(b, c, t)
        f = lerp(d, e, t)
        return Segment((p0, a, d, f), self.name), Segment((f, e, c, p3), self.name)

    def transformed(self, fn) -> "Segment":
        return Segment(tuple(fn(p) for p in self.pts), self.name)


def _flat_bezier(p0, p1, p2, p3, tol, out, depth):
    # flatness: max distance of control points from the chord
    d = max(_dist_to_line(p1, p0, p3), _dist_to_line(p2, p0, p3))
    if d <= tol or depth > 16:
        out.append(p3)
        return
    a, b, c = lerp(p0, p1, .5), lerp(p1, p2, .5), lerp(p2, p3, .5)
    e, f = lerp(a, b, .5), lerp(b, c, .5)
    g = lerp(e, f, .5)
    _flat_bezier(p0, a, e, g, tol, out, depth + 1)
    _flat_bezier(g, f, c, p3, tol, out, depth + 1)


def _dist_to_line(p, a, b):
    L = dist(a, b)
    if L < EPS:
        return dist(p, a)
    return abs((b[0] - a[0]) * (a[1] - p[1]) - (a[0] - p[0]) * (b[1] - a[1])) / L


def bezier_by_tangents(a: Pt, b: Pt, angle_a: float, angle_b: float, fa: float = 1 / 3, fb: float = 1 / 3) -> Segment:
    """Cubic curve from a to b leaving a at ``angle_a`` and arriving at b with direction ``angle_b``
    (degrees). ``fa``/``fb`` are control lengths as fractions of the chord."""
    L = dist(a, b)
    c1 = polar(a, angle_a, L * fa)
    c2 = polar(b, angle_b + 180.0, L * fb)
    return Segment((a, c1, c2, b))


# ---------------------------------------------------------------- paths

@dataclass
class Path:
    segments: list[Segment]
    name: str = ""

    def flatten(self, tol: float = 0.05) -> list[Pt]:
        out: list[Pt] = []
        for s in self.segments:
            pts = s.flatten(tol)
            if out and dist(out[-1], pts[0]) < 1e-7:
                pts = pts[1:]
            out.extend(pts)
        return out

    def length(self, tol: float = 0.005) -> float:
        return sum(s.length(tol) for s in self.segments)

    @property
    def start(self) -> Pt:
        return self.segments[0].start

    @property
    def end(self) -> Pt:
        return self.segments[-1].end

    def reversed(self) -> "Path":
        return Path([s.reversed() for s in reversed(self.segments)], self.name)

    def point_at_length(self, d: float, tol: float = 0.005) -> Pt:
        """Point at arc length d from the start (negative d counts from the end)."""
        pts = self.flatten(tol)
        total = sum(dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        if d < 0:
            d = total + d
        if d < -1e-6 or d > total + 1e-6:
            raise GeometryError(f"distance {d:.2f} outside path length {total:.2f}")
        if d >= total:
            return pts[-1]
        acc = 0.0
        for i in range(len(pts) - 1):
            L = dist(pts[i], pts[i + 1])
            if acc + L >= d - 1e-9:
                return lerp(pts[i], pts[i + 1], (d - acc) / L if L > EPS else 0.0)
            acc += L
        return pts[-1]

    def split_at_length(self, d: float) -> tuple["Path", "Path"]:
        acc = 0.0
        for i, s in enumerate(self.segments):
            L = s.length()
            if acc + L >= d:
                # find t by bisection on length
                lo, hi = 0.0, 1.0
                for _ in range(60):
                    mid = (lo + hi) / 2
                    if s.split(mid)[0].length() + acc < d:
                        lo = mid
                    else:
                        hi = mid
                a, b = s.split((lo + hi) / 2)
                return Path(self.segments[:i] + [a], self.name), Path([b] + self.segments[i + 1:], self.name)
            acc += L
        raise GeometryError("split distance beyond path end")

    def transformed(self, fn) -> "Path":
        return Path([s.transformed(fn) for s in self.segments], self.name)


def polygon_area(pts: list[Pt]) -> float:
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return a / 2


def self_intersections(pts: list[Pt], closed: bool = True, tol: float = 1e-6) -> list[Pt]:
    """Crossing points between non-adjacent edges of a polyline (O(n^2), fine for pattern pieces)."""
    n = len(pts)
    edges = [(pts[i], pts[(i + 1) % n]) for i in range(n if closed else n - 1)]
    out = []
    m = len(edges)
    for i in range(m):
        for j in range(i + 2, m):
            if closed and i == 0 and j == m - 1:
                continue
            p = segment_intersection(*edges[i], *edges[j])
            if p is not None:
                out.append(p)
    return out


def bbox(pts: list[Pt]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)
