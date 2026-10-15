import math

import pytest

from level7mtm.pattern.allowance import make_cut
from level7mtm.pattern.geometry import (GeometryError, Path, Segment, bezier_by_tangents, intersect_line_circle,
                                        intersect_lines, mirror, polygon_area, project, rotate, self_intersections)


def test_intersections_projection_mirror():
    assert intersect_lines((0, 0), (10, 0), (5, -5), (5, 5)) == pytest.approx((5, 0))
    with pytest.raises(GeometryError):
        intersect_lines((0, 0), (1, 0), (0, 1), (1, 1))
    assert project((3, 4), (0, 0), (10, 0)) == pytest.approx((3, 0))
    assert mirror((1, 2), (0, 0), (0, 10)) == pytest.approx((-1, 2))
    assert rotate((10, 0), (0, 0), 90) == pytest.approx((0, 10), abs=1e-12)
    assert intersect_line_circle((0, 0), (10, 0), (0, 0), 5) == pytest.approx((5, 0))


def test_curve_length_and_split():
    # quarter circle approximated by a cubic: length ~ pi/2 * r within 0.03 %
    k = 0.5522847498
    s = Segment(((100, 0), (100, 100 * k), (100 * k, 100), (0, 100)))
    assert s.length() == pytest.approx(math.pi * 50, rel=3e-4)
    a, b = s.split(0.3)
    assert a.length() + b.length() == pytest.approx(s.length(), abs=1e-3)
    p = Path([s])
    assert p.point_at_length(p.length()) == pytest.approx((0, 100), abs=1e-6)
    left, right = p.split_at_length(40)
    assert left.length() == pytest.approx(40, abs=0.01)


def test_tangent_curve_keeps_definition():
    c = bezier_by_tangents((0, 0), (100, 0), 90, -90)
    assert c.is_curve and c.pts[0] == (0, 0) and c.pts[3] == (100, 0)
    assert c.flatten(0.5) != c.flatten(0.01)        # display precision is separate from the definition


def _square_edges(a=10.0):
    return [("bottom", [(0, 0), (100, 0)], a), ("right", [(100, 0), (100, 100)], a),
            ("top", [(100, 100), (0, 100)], a), ("left", [(0, 100), (0, 0)], 0.0)]


def test_allowance_per_edge():
    r = make_cut(_square_edges())
    assert r.ok
    xs = [p[0] for p in r.cut]
    ys = [p[1] for p in r.cut]
    assert min(xs) == pytest.approx(0) and max(xs) == pytest.approx(110)    # left edge cut on seam
    assert min(ys) == pytest.approx(-10) and max(ys) == pytest.approx(110)
    assert abs(polygon_area(r.cut)) == pytest.approx(110 * 120)


def test_allowance_not_closed_is_blocking():
    e = _square_edges()
    e[1] = ("right", [(100, 0.5), (100, 100)], 10)
    r = make_cut(e)
    assert not r.ok and r.issues[0].code == "seam.not_closed"


def test_allowance_collapse_is_reported_not_fixed():
    # small concave notch 4 mm wide with 10 mm allowance -> the offset folds
    seam = [("a", [(0, 0), (100, 0)], 10), ("b", [(100, 0), (100, 100)], 10),
            ("c", [(100, 100), (52, 100), (52, 60), (48, 60), (48, 100), (0, 100)], 10),
            ("d", [(0, 100), (0, 0)], 10)]
    r = make_cut(seam)
    assert not r.ok
    assert any(i.code in ("allowance.collapse", "cut.self_intersection") for i in r.issues)


def test_self_intersections():
    assert self_intersections([(0, 0), (10, 10), (10, 0), (0, 10)])
    assert not self_intersections([(0, 0), (10, 0), (10, 10), (0, 10)])
