"""CAD workspace of LEVEL7 MTM: ribbon, order browser, marker canvas (with rulers and grid), properties
inspector. The marker shown is the real one LEVEL7 Nesting saved (``.l7n``, read only)."""
from __future__ import annotations

import json
import math
import os
import zipfile

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
                           QTransform)
from PySide6.QtWidgets import (QFrame, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import themes
from .i18n import tr

# ---------------------------------------------------------------- icons (24 x 24, stroked)

ICONS = {
    "import": "M12 3v11m0 0l-4-4m4 4l4-4M4 15v4a1 1 0 001 1h14a1 1 0 001-1v-4",
    "orders": "M4 5h16M4 10h16M4 15h10M4 20h7",
    "ruler": "M3 17L17 3l4 4L7 21zM7 13l2 2M10 10l2 2M13 7l2 2",
    "model": "M12 3a2 2 0 012 2c0 1-1 1.5-2 2v1l9 6v2H3v-2l9-6",
    "robot": "M6 8h12v10H6zM12 4v4M9 12h.01M15 12h.01M9 15h6M3 12v3M21 12v3",
    "xvalues": "M4 4l6 8-6 8M14 20h6",
    "options": "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5",
    "parts": "M3 3h8v8H3zM13 3h8v8h-8zM3 13h8v8H3zM13 13h8v8h-8z",
    "nest": "M3 3h8v8H3zM13 3h8v5h-8zM13 10h8v11h-8zM3 13h8v8H3z",
    "renest": "M4 12a8 8 0 0114-5l2-2v6h-6l2-2a5 5 0 10 1 6",
    "marker": "M3 6h18v12H3zM7 6v12M12 6v12M17 6v12",
    "folder": "M3 6a1 1 0 011-1h5l2 2h9a1 1 0 011 1v10a1 1 0 01-1 1H4a1 1 0 01-1-1z",
    "error": "M12 3l10 18H2zM12 10v5M12 18h.01",
    "setup": "M12 8a4 4 0 110 8 4 4 0 010-8zM12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2",
    "log": "M5 3h11l3 3v15H5zM8 9h8M8 13h8M8 17h5",
    "home": "M3 11l9-8 9 8M5 10v10h14V10",
    "select": "M5 3l14 8-6 2-2 6z",
    "pan": "M12 3v18M3 12h18M12 3l-3 3m3-3l3 3M12 21l-3-3m3 3l3-3M3 12l3-3m-3 3l3 3M21 12l-3-3m3 3l-3 3",
    "zoomin": "M11 4a7 7 0 110 14 7 7 0 010-14zM21 21l-5-5M8 11h6M11 8v6",
    "zoomout": "M11 4a7 7 0 110 14 7 7 0 010-14zM21 21l-5-5M8 11h6",
    "fit": "M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5",
    "theme": "M12 3a9 9 0 100 18 7 7 0 010-18z",
    "curve": "M3 19C5 9 10 5 21 5M3 19h.01M21 5h.01",
}


def icon(name: str, colour: QColor | None = None, size: int = 24) -> QIcon:
    from PySide6.QtSvg import QSvgRenderer
    c = colour or themes.colour("text")
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='{c.name()}' "
           f"stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'><path d='{ICONS[name]}'/></svg>")
    r = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    r.render(p)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- data

def ltr(text) -> str:
    """Keep order numbers, sizes and coordinates left-to-right inside Arabic text (9-53-1_2 stays 9-53-1_2)."""
    return f"\u2066{text}\u2069"


STAGES = ("new", "grafis", "nest", "cut")
STAGE_TEXT = {"new": "New", "grafis": "In GRAFIS", "nest": "Nesting", "cut": "Ready to cut", "err": "Needs attention"}
STAGE_COLOUR = {"new": "muted", "grafis": "warn", "nest": "accent", "cut": "ok", "err": "bad"}


def load_marker(path: str) -> dict | None:
    """The marker LEVEL7 Nesting saved (``.l7n``: ZIP with project.json): fabric width, length and every placed
    piece as a polygon in marker millimetres. None when the file is missing or not readable."""
    try:
        with zipfile.ZipFile(path) as z:
            doc = json.loads(z.read("project.json").decode("utf-8"))
        proj = doc["project"]
        if not proj.get("markers"):
            return None
        m = proj["markers"][-1]
        pieces = {p["uid"]: p for p in proj.get("pieces", [])}
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    out = []
    for pl in m.get("placements", []):
        if not pl.get("placed", True):
            continue
        pc = pieces.get(pl.get("piece_uid"))
        g = (pc or {}).get("sizes", {}).get(pl.get("size"))
        if not g or not g.get("cut"):
            continue
        tf = lambda pts, pl=pl: transform(pts, pl.get("rotation", 0.0), pl.get("mirrored", False), pl.get("x", 0.0),
                                          pl.get("y", 0.0), pl.get("sx", 1.0), pl.get("sy", 1.0))
        grain = (g.get("grain") or {}).get("points") or []
        cut = tf(g["cut"])
        out.append({"name": pc.get("name", ""), "cut": cut, "grain": tf(grain) if len(grain) >= 2 else [],
                    "holes": [tf(h) for h in g.get("holes", [])], "mirrored": bool(pl.get("mirrored")),
                    "corners": tf(g["corners"]) if g.get("corners") else sharp_corners(cut)})
    fab = m.get("fabric", {})
    length = float(m.get("length") or 0.0) or max((x for p in out for x, _ in p["cut"]), default=0.0)
    return {"width": float(fab.get("width") or 0.0), "length": length, "pieces": out, "stats": m.get("stats", {}),
            "time": m.get("compute_time")}


def sharp_corners(pts, min_turn: float = 25.0) -> list:
    """The corners and end points of a cut line: vertices where it turns by more than ``min_turn`` degrees (the
    many small steps of a curve are not corners)."""
    n = len(pts)
    out = []
    for i in range(n):
        (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[(i + 1) % n]
        a1, a2 = math.atan2(y1 - y0, x1 - x0), math.atan2(y2 - y1, x2 - x1)
        turn = abs((math.degrees(a2 - a1) + 180) % 360 - 180)
        if turn >= min_turn:
            out.append((x1, y1))
    return out


def transform(pts, rotation: float, mirrored: bool, dx: float, dy: float, sx: float = 1.0, sy: float = 1.0):
    """Same as LEVEL7 Nesting: mirror (x -> -x), rotate CCW, shrinkage scale, translate."""
    a = math.radians(rotation)
    c, s = math.cos(a), math.sin(a)
    if abs(rotation % 90.0) < 1e-12:
        c, s = round(c), round(s)
    out = []
    for x, y in pts:
        if mirrored:
            x = -x
        out.append((sx * (c * x - s * y) + dx, sy * (s * x + c * y) + dy))
    return out


def order_rows(ws, limit: int = 300) -> list[dict]:
    """Every open order line with where it is in the chain (XML → GRAFIS model → GRAFIS → nesting → cutting files)."""
    db = ws.db
    results: dict[int, dict] = {}
    for r in db.q("SELECT * FROM grafis_results WHERE archived=0 ORDER BY id"):
        for ln in json.loads(r["lines"]):
            if ln.get("revision_id") is not None:
                results[ln["revision_id"]] = dict(r)          # the newest result of the line wins
    out = []
    for rev in ws.list_revisions()[:limit]:
        if rev["status"] == "cancelled":
            continue
        rid = rev["id"]
        gos = db.q("SELECT * FROM grafis_orders WHERE revision_id=? ORDER BY id DESC", (rid,))
        if gos and all(g["archived"] for g in gos):
            continue                                          # cleaned from the list
        go = next((dict(g) for g in gos if not g["archived"]), None)
        full = ws.revision(rid)
        norm, raw = full["normalized"], full["raw_line"]
        row = {"rid": rid, "line": rev["line_number"], "stage": "new", "problems": [], "imported": rev["imported_at"],
               "fabric": raw.get("fabric_number") or "", "width": norm.get("fabric_width_mm"),
               "quantity": rev["quantity"], "times": {"new": rev["imported_at"]}, "x_rows": [], "mdl": None,
               "result": None, "l7n": None, "outputs": [], "length_mm": None, "efficiency_pct": None,
               "placed": None, "status": rev["status"], "robot_failed": False}
        if go:
            info = json.loads(go["info"])
            row["x_rows"] = info.get("rows", [])
            row["mdl"] = go["path"]
            row["times"]["grafis"] = go["created_at"]
            if go["status"] in ("blocked", "not_configured"):
                row["problems"] += info.get("problems", []) or [tr(go["status"])]
            elif go["status"] in ("written", "duplicate"):
                row["stage"] = "grafis"
                rj = db.one("SELECT status, message, at FROM robot_jobs WHERE path=? ORDER BY id DESC", (go["path"],))
                if rj and rj["status"] == "failed" and rj["at"] >= go["created_at"]:
                    row["robot_failed"] = True                # stays until "Robot: try again" (never silent)
                    row["problems"].append(f"{tr('GRAFIS robot')}: {rj['message']}")
        res = results.get(rid)
        if res:
            row["result"] = res["path"]
            row["times"]["nest"] = res["checked_at"]
            row["stage"] = "nest"
            if res["status"] in ("needs_review", "unmatched"):
                row["problems"] += [m for sv, m in json.loads(res["issues"]) if sv != "info"]
            cj = db.one("SELECT * FROM cutting_jobs WHERE sha256=? ORDER BY id DESC", (res["sha256"],))
            if cj:
                ci = json.loads(cj["info"])
                row.update(l7n=ci.get("l7n"), length_mm=ci.get("length_mm"), efficiency_pct=ci.get("efficiency_pct"),
                           placed=ci.get("placed"))
                if cj["status"] == "done":
                    row["stage"] = "cut"
                    row["outputs"] = json.loads(cj["outputs"])
                    row["times"]["cut"] = cj["created_at"]
                elif cj["status"] in ("failed", "not_configured"):
                    row["problems"].append(ci.get("message") or tr(cj["status"]))
        if res:                                               # GRAFIS saved it: an old robot failure is over
            row["problems"] = [p for p in row["problems"] if not p.startswith(tr("GRAFIS robot") + ":")]
            row["robot_failed"] = False
        if row["problems"] and row["stage"] != "cut":
            row["stage_reached"], row["stage"] = row["stage"], "err"
        out.append(row)
    return out


# ---------------------------------------------------------------- marker canvas

# ---------------------------------------------------------------- measuring (marker millimetres)

def _closed(pts):
    return list(pts) + [pts[0]] if pts and pts[0] != pts[-1] else list(pts)


def contour_length(pts) -> float:
    q = _closed(pts)
    return sum(math.dist(q[i], q[i + 1]) for i in range(len(q) - 1))


def nearest_on_contour(pts, p):
    """(distance, arc position s along the contour, the point on it) of the contour point nearest to p."""
    q = _closed(pts)
    best, s0 = None, 0.0
    for i in range(len(q) - 1):
        (x1, y1), (x2, y2) = q[i], q[i + 1]
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - x1) * dx + (p[1] - y1) * dy) / L2))
        c = (x1 + t * dx, y1 + t * dy)
        d = math.dist(c, p)
        seg = math.sqrt(L2)
        if best is None or d < best[0]:
            best = (d, s0 + t * seg, c)
        s0 += seg
    return best


def point_at(pts, s):
    q = _closed(pts)
    for i in range(len(q) - 1):
        seg = math.dist(q[i], q[i + 1])
        if s <= seg or i == len(q) - 2:
            t = 0.0 if seg == 0 else min(1.0, s / seg)
            return (q[i][0] + t * (q[i + 1][0] - q[i][0]), q[i][1] + t * (q[i + 1][1] - q[i][1]))
        s -= seg
    return q[-1]


def arc_between(pts, sa: float, sb: float):
    """The shorter way along the contour between two arc positions: (length, the points of that way)."""
    total = contour_length(pts)
    a, b = sorted((sa, sb))
    inner = b - a
    q = _closed(pts)
    cum, acc = [0.0], 0.0
    for i in range(len(q) - 1):
        acc += math.dist(q[i], q[i + 1])
        cum.append(acc)
    if inner <= total - inner:
        way = [point_at(pts, a)] + [q[i] for i in range(len(q)) if a < cum[i] < b] + [point_at(pts, b)]
        return inner, way
    way = [point_at(pts, b)] + [q[i] for i in range(len(q)) if cum[i] > b] + \
        [q[i] for i in range(len(q)) if 0 < cum[i] < a] + [point_at(pts, a)]
    return total - inner, way


def arc_options(pts, sa: float, sb: float):
    """Both ways along the contour between two arc positions: [(length, points)] (shorter first)."""
    total = contour_length(pts)
    a, b = sorted((sa, sb))
    q = _closed(pts)
    cum, acc = [0.0], 0.0
    for i in range(len(q) - 1):
        acc += math.dist(q[i], q[i + 1])
        cum.append(acc)
    inner = [point_at(pts, a)] + [q[i] for i in range(len(q)) if a < cum[i] < b] + [point_at(pts, b)]
    outer = [point_at(pts, b)] + [q[i] for i in range(len(q)) if cum[i] > b] + \
        [q[i] for i in range(len(q)) if 0 < cum[i] < a] + [point_at(pts, a)]
    return sorted([(b - a, inner), (total - (b - a), outer)], key=lambda o: o[0])


def curve_between(pts, corners, sa: float, sb: float, tol: float = 0.5):
    """The way along the cut line between two points that passes no corner (one curve), or None."""
    for length, way in arc_options(pts, sa, sb):
        inside = way[1:-1]
        if not any(math.dist(c, w) <= tol for c in corners for w in inside
                   if math.dist(c, way[0]) > tol and math.dist(c, way[-1]) > tol):
            return length, way
    return None


class MeasureBox(QFrame):
    """The measured value in a box the owner can drag anywhere on the canvas; once moved it stays there."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("hud")
        self.setCursor(Qt.OpenHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 5, 10, 5)
        lay.setSpacing(0)
        self.value = QLabel()
        self.value.setStyleSheet(f"color: {themes.colour('warn').name()}; font-size: 13pt; font-weight: 800;")
        self.extra = QLabel()
        self.extra.setObjectName("hudLabel")
        lay.addWidget(self.value)
        lay.addWidget(self.extra)
        self.moved = False
        self._grab = None
        self.hide()

    def show_value(self, text, extra, near):
        self.value.setText(ltr(text))
        self.extra.setText(ltr(extra))
        self.extra.setVisible(bool(extra))
        self.adjustSize()
        if not self.moved:                        # next to the measurement until the owner moves it
            par = self.parentWidget().rect()
            x = min(max(int(near.x()) + 14, 4), par.width() - self.width() - 4)
            y = min(max(int(near.y()) - self.height() - 10, 26), par.height() - self.height() - 4)
            self.move(x, y)
        self.show()
        self.raise_()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._grab = e.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._grab is not None:
            par = self.parentWidget().rect()
            p = self.mapToParent(e.position().toPoint() - self._grab)
            self.move(min(max(p.x(), 0), par.width() - self.width()), min(max(p.y(), 0), par.height() - self.height()))
            self.moved = True

    def mouseReleaseEvent(self, e):
        self._grab = None
        self.setCursor(Qt.OpenHandCursor)


class MarkerView(QGraphicsView):
    """The marker in millimetres: grid, fabric with selvedges, pieces with grain lines and names, the length
    dimension; rulers along the top and the side; wheel = zoom at the cursor, pan tool / middle button = move."""
    cursor_moved = Signal(float, float)
    zoom_changed = Signal(float)
    measured = Signal(str)
    RULER = 22

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.LeftToRight)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMouseTracking(True)
        self.setFrameShape(QFrame.NoFrame)
        self.marker: dict | None = None
        self.message = ""
        self._cursor: QPointF | None = None
        self._pan = None
        self.tool: str | None = None            # None, "line" (straight length) or "curve" (along a piece edge)
        self.meas: dict = {}
        self.box = MeasureBox(self.viewport())
        self._near = None
        self._snapped = None
        self.zooming = False                     # space bar held: left click = zoom out, right click = zoom in
        self.setFocusPolicy(Qt.StrongFocus)

    # -- content
    def show_marker(self, marker: dict | None, message: str = ""):
        sc = self.scene()
        sc.clear()
        self.marker, self.message = marker, message
        self.meas = {}
        if hasattr(self, "box"):
            self.box.hide()
        if not marker:
            sc.setSceneRect(QRectF(-100, -100, 3000, 1700))
            self.resetTransform()
            self.fit()
            return
        c = themes.colours()
        w, length = marker["width"], marker["length"]
        sc.addRect(QRectF(0, 0, length, w), QPen(c["selvedge"], 0), QBrush(c["fabric"]))
        dash = QPen(c["selvedge"], 0, Qt.DashLine)
        for y in (-12, w + 12):
            sc.addLine(0, y, length, y, dash)
        acc = c["piece"]
        fill = QColor(acc)
        fill.setAlpha(70)
        pen = QPen(acc, 0)
        pen.setCosmetic(True)
        pen.setWidthF(1.3)
        gpen = QPen(c["muted"], 0)
        gpen.setCosmetic(True)
        f = QFont(themes.FAMILY["name"], 8)
        f.setBold(True)
        for p in marker["pieces"]:
            path = QPainterPath()
            path.addPolygon(QPolygonF([QPointF(x, y) for x, y in p["cut"]]))
            path.closeSubpath()
            for h in p["holes"]:
                path.addPolygon(QPolygonF([QPointF(x, y) for x, y in h]))
                path.closeSubpath()
            item = sc.addPath(path, pen, QBrush(fill))
            item.setToolTip(p["name"] + (" (M)" if p["mirrored"] else ""))
            if len(p["grain"]) >= 2:
                (x1, y1), (x2, y2) = p["grain"][0], p["grain"][-1]
                sc.addLine(x1, y1, x2, y2, gpen)
                ang = math.atan2(y2 - y1, x2 - x1)
                for (ex, ey), d in (((x2, y2), 1), ((x1, y1), -1)):
                    for s_ in (0.45, -0.45):
                        sc.addLine(ex, ey, ex - d * 22 * math.cos(ang + s_), ey - d * 22 * math.sin(ang + s_), gpen)
            br = path.boundingRect()
            t = QGraphicsSimpleTextItem(p["name"])
            t.setFont(f)
            t.setBrush(c["label"])
            t.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations)
            tb = t.boundingRect()
            t.setTransform(QTransform.fromTranslate(-tb.width() / 2, -tb.height() / 2))   # centred in pixels
            t.setPos(br.center())
            sc.addItem(t)
        dpen = QPen(c["accent"], 0)
        dpen.setCosmetic(True)
        y = w + 70
        sc.addLine(0, y, length, y, dpen)
        for x in (0, length):
            sc.addLine(x, y - 25, x, y + 25, dpen)
        lab = QGraphicsSimpleTextItem(f"{length:,.1f} mm".replace(",", " "))
        lab.setFont(f)
        lab.setBrush(c["accent"])
        lab.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations)
        lab.setPos(length / 2, y + 8)
        sc.addItem(lab)
        sc.setSceneRect(QRectF(-150, -150, length + 300, w + 400))
        self.fit()

    def fit(self):
        r = self.scene().sceneRect()
        if r.width() > 0:
            self.fitInView(r, Qt.KeepAspectRatio)
            self.zoom_changed.emit(self.transform().m11())

    def zoom(self, k: float):
        self.scale(k, k)
        self.zoom_changed.emit(self.transform().m11())

    def set_pan(self, on: bool):
        self.setDragMode(QGraphicsView.ScrollHandDrag if on else QGraphicsView.NoDrag)

    # -- input
    def contextMenuEvent(self, e):
        if self.zooming:
            return
        super().contextMenuEvent(e)

    def wheelEvent(self, e):
        self.zoom(1.18 if e.angleDelta().y() > 0 else 1 / 1.18)

    def set_tool(self, tool: str | None):
        self.tool = tool
        self.meas = {}
        self.setCursor(Qt.CrossCursor if tool else Qt.ArrowCursor)
        self._cursor_shape()
        self.box.moved = False
        self.measured.emit("")
        self.viewport().update()

    def _tol(self) -> float:
        return 12 / (self.transform().m11() or 1.0)          # 12 screen pixels in millimetres

    def _near_edge(self, p):
        """The nearest point on a piece edge to p (scene mm): (distance, piece index, arc position, point)."""
        tol = self._tol() * 3
        near = None
        for i, pc in enumerate((self.marker or {}).get("pieces", [])):
            bb = pc.get("_bb")
            if bb is None:
                xs, ys = [x for x, _ in pc["cut"]], [y for _, y in pc["cut"]]
                bb = pc["_bb"] = (min(xs), min(ys), max(xs), max(ys))
            if not (bb[0] - tol <= p[0] <= bb[2] + tol and bb[1] - tol <= p[1] <= bb[3] + tol):
                continue
            d, s_, c = nearest_on_contour(pc["cut"], p)
            if near is None or d < near[0]:
                near = (d, i, s_, c)
        return near

    SNAP_CORNER, SNAP_EDGE = 18, 9                   # screen pixels

    def snap(self, p):
        """Where a click at p (scene mm) lands: a corner / end point of a piece within SNAP_CORNER pixels, else
        the nearest point on a piece line within SNAP_EDGE pixels, else p. Returns (point, kind)."""
        px = 1 / (self.transform().m11() or 1.0)
        best = None
        for pc in (self.marker or {}).get("pieces", []):
            for v in pc.get("corners") or []:
                d = math.dist(v, p)
                if d <= self.SNAP_CORNER * px and (best is None or d < best[0]):
                    best = (d, v)
        if best:
            return best[1], "corner"
        n = self._near_edge(p)
        if n and n[0] <= self.SNAP_EDGE * px:
            return n[3], "edge"
        return p, None

    def _snap(self, p):
        return self.snap(p)[0]

    def measure_click(self, p):
        """One click of the measuring tools at scene point p (millimetres)."""
        if not self.marker or not self.tool:
            return
        if self.tool == "line":
            pts = self.meas.get("pts", []) if len(self.meas.get("pts", [])) < 2 else []
            pts = pts + [self._snap(p)]
            self.meas = {"kind": "line", "pts": pts}
            if len(pts) == 2:
                d = math.dist(*pts)
                dx, dy = abs(pts[1][0] - pts[0][0]), abs(pts[1][1] - pts[0][1])
                self.meas.update(value=d, text=f"{d / 10:.1f} cm", extra=f"ΔX {dx / 10:.1f}  ΔY {dy / 10:.1f} cm")
                self.measured.emit(self.meas["text"])
        else:
            near = None
            prev = self.meas.get("first") if self.meas.get("kind") == "curve" and not self.meas.get("way") else None
            cands = ([prev[0]] if prev is not None else []) + list(range(len(self.marker["pieces"])))
            for i in cands:                                   # the piece whose line is nearest (the first click's
                pc = self.marker["pieces"][i]                 # piece first, so touching pieces do not mix)
                d, s_, c = nearest_on_contour(pc["cut"], p)
                if d <= self._tol() and (near is None or d < near[0]):
                    near = (d, i, s_, c)
                if prev is not None and i == prev[0] and near is not None:
                    break
            if near is None:
                return                                        # not on a piece edge
            pc = self.marker["pieces"][near[1]]
            px = 1 / (self.transform().m11() or 1.0)
            cs = [v for v in pc.get("corners") or [] if math.dist(v, p) <= self.SNAP_CORNER * px]
            if cs:                                            # a corner of this piece close by: exactly on it
                v = min(cs, key=lambda v: math.dist(v, p))
                d, s_, c = nearest_on_contour(pc["cut"], v)
                near = (d, near[1], s_, c)
            _, i, s_, c = near
            first = self.meas.get("first")
            if self.meas.get("kind") != "curve" or first is None or first[0] != i or self.meas.get("way"):
                self.meas = {"kind": "curve", "first": (i, s_, c), "pts": [c]}
                return
            pc = self.marker["pieces"][i]
            got = curve_between(pc["cut"], pc.get("corners") or [], first[1], s_)
            if got is None:                                # a corner between them both ways: not one curve
                self.meas = {"kind": "curve", "pts": [first[2], c], "text": tr("Not one curve"),
                             "extra": tr("Pick two points on the same curve (between two corners)")}
                self.measured.emit("")
                self.viewport().update()
                return
            length, way = got
            self.meas = {"kind": "curve", "first": first, "pts": [first[2], c], "way": way, "value": length,
                         "text": f"{length / 10:.1f} cm",
                         "extra": f"{pc['name']} — {tr('straight')} {math.dist(first[2], c) / 10:.1f} cm"}
            self.measured.emit(self.meas["text"])
        self.viewport().update()

    def enterEvent(self, e):
        self.setFocus()                          # the space bar works as soon as the mouse is over the marker
        super().enterEvent(e)

    def _cursor_shape(self):
        shape = Qt.SizeAllCursor if self.zooming else (Qt.CrossCursor if self.tool else Qt.ArrowCursor)
        self.viewport().setCursor(shape)

    def zoom_at(self, k: float, pos):
        before = self.mapToScene(pos)
        self.scale(k, k)
        after = self.mapToScene(pos)
        d = after - before
        self.translate(d.x(), d.y())
        self.zoom_changed.emit(self.transform().m11())

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self.zooming = False
            self._cursor_shape()
            return
        super().keyReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Space:
            if not e.isAutoRepeat():
                self.zooming = True
                self._cursor_shape()
            return
        if e.key() == Qt.Key_Escape and self.tool:
            self.meas = {}
            self.measured.emit("")
            self.viewport().update()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._pan = e.position()
            return
        if self.zooming and e.button() in (Qt.LeftButton, Qt.RightButton):
            self.zoom_at(1 / 1.35 if e.button() == Qt.LeftButton else 1.35, e.position().toPoint())
            return
        if e.button() == Qt.LeftButton and self.tool:
            p = self.mapToScene(e.position().toPoint())
            self.measure_click((p.x(), p.y()))
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
        p = self.mapToScene(e.position().toPoint())
        self._cursor = p
        self._near = None
        self._snapped = None
        if self.tool and self.marker:                     # magnifier near a piece line; where a click would land
            n = self._near_edge((p.x(), p.y()))
            if n and n[0] <= self._tol() * 3:
                self._near = n
            q, kind = self.snap((p.x(), p.y()))
            self._snapped = (q, kind) if kind else None
        self.cursor_moved.emit(p.x(), p.y())
        self.viewport().update()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._pan = None
            return
        super().mouseReleaseEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.marker is not None or self.message:
            self.fit()

    # -- painting: grid behind, rulers / centre labels / empty message in front
    def _step(self, min_px: float) -> float:
        k = self.transform().m11() or 1.0
        for s in (5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000):
            if s * k >= min_px:
                return s
        return 10000

    def drawBackground(self, p: QPainter, rect: QRectF):
        c = themes.colours()
        p.fillRect(rect, c["canvas"])
        step = self._step(14)
        big = step * 5
        for colour, s in ((c["grid"], step), (c["grid2"], big)):
            pen = QPen(colour, 0)
            pen.setCosmetic(True)
            p.setPen(pen)
            x = math.floor(rect.left() / s) * s
            while x <= rect.right():
                p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                x += s
            y = math.floor(rect.top() / s) * s
            while y <= rect.bottom():
                p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                y += s

    def _draw_snap(self, p: QPainter, c):
        """The point a click would take: a square on a corner / end point, a circle on a piece line."""
        if not (self.tool and self.marker and self._snapped):
            return
        q, kind = self._snapped
        v = self.mapFromScene(QPointF(*q))
        p.setPen(QPen(c["ok"], 2))
        p.setBrush(Qt.NoBrush)
        if kind == "corner":
            p.drawRect(QRectF(v.x() - 6, v.y() - 6, 12, 12))
        else:
            p.drawEllipse(QPointF(v), 6, 6)

    def _draw_measure(self, p: QPainter, c):
        m = self.meas
        if not m or not self.marker or not m.get("text"):
            self.box.hide()
        if not m or not self.marker:
            return
        v = lambda q: self.mapFromScene(QPointF(*q))
        pen = QPen(c["warn"], 2.2)
        p.setPen(pen)
        way = m.get("way") or m.get("pts", [])
        if m.get("kind") == "line" and len(m["pts"]) == 1 and self._cursor is not None:
            end = self._snapped[0] if self._snapped else (self._cursor.x(), self._cursor.y())
            way = [m["pts"][0], end]                                        # rubber band to where it would land
        for a, b in zip(way, way[1:]):
            p.drawLine(v(a), v(b))
        p.setBrush(c["warn"])
        for q in m.get("pts", []):
            p.drawEllipse(v(q), 4, 4)
        if m.get("text"):
            mid = v(way[len(way) // 2]) if m.get("kind") == "curve" else (v(way[0]) + v(way[-1])) / 2
            self.box.show_value(m["text"], m.get("extra", ""), mid)
        p.setBrush(Qt.NoBrush)

    LOUPE, LOUPE_ZOOM = 150, 5.0

    def _draw_loupe(self, p: QPainter, c):
        """A round magnifier near the cursor while measuring close to a piece line: the spot 5x larger, a cross
        and the exact point the click will take."""
        if not (self.tool and self.marker and self._cursor is not None and self._near):
            return
        cur = self.mapFromScene(self._cursor)
        D = self.LOUPE
        vp = self.viewport().rect()
        x = cur.x() + 24 if cur.x() + 24 + D < vp.width() else cur.x() - 24 - D
        y = cur.y() - 24 - D if cur.y() - 24 - D > self.RULER else cur.y() + 24
        target = QRectF(x, y, D, D)
        k = self.transform().m11() * self.LOUPE_ZOOM              # loupe pixels per mm
        half = D / 2 / k
        src = QRectF(self._cursor.x() - half, self._cursor.y() - half, 2 * half, 2 * half)
        clip = QPainterPath()
        clip.addEllipse(target)
        p.save()
        p.setClipPath(clip)
        p.fillRect(target, c["canvas"])
        self.scene().render(p, target, src, Qt.IgnoreAspectRatio)
        to = lambda q: QPointF(target.center().x() + (q[0] - self._cursor.x()) * k,
                               target.center().y() + (q[1] - self._cursor.y()) * k)
        q, kind = self._snapped or (self._near[3], "edge")
        p.setPen(QPen(c["ok"], 2))
        p.setBrush(Qt.NoBrush)
        if kind == "corner":
            p.drawRect(QRectF(to(q).x() - 6, to(q).y() - 6, 12, 12))
        else:
            p.drawEllipse(to(q), 6, 6)
        p.setPen(QPen(c["text"], 1))
        ctr = target.center()
        p.drawLine(QPointF(ctr.x() - 9, ctr.y()), QPointF(ctr.x() + 9, ctr.y()))
        p.drawLine(QPointF(ctr.x(), ctr.y() - 9), QPointF(ctr.x(), ctr.y() + 9))
        p.restore()
        p.setPen(QPen(c["accent"], 2))
        p.drawEllipse(target)

    def drawForeground(self, p: QPainter, rect: QRectF):
        """In viewport pixels: the empty-state text, the cursor cross and the rulers (top and left edge)."""
        c = themes.colours()
        p.save()
        p.resetTransform()
        vp = self.viewport().rect()
        if not self.marker:
            p.setPen(c["muted"])
            p.setFont(QFont(themes.FAMILY["name"], 11))
            p.drawText(vp, Qt.AlignCenter, self.message or tr("Choose an order line in the browser"))
        self._draw_measure(p, c)
        self._draw_snap(p, c)
        self._draw_loupe(p, c)
        cur = self.mapFromScene(self._cursor) if self._cursor is not None and self.marker else None
        if cur is not None:
            p.setPen(QPen(c["accent"], 1))
            p.drawLine(cur.x() - 10, cur.y(), cur.x() + 10, cur.y())
            p.drawLine(cur.x(), cur.y() - 10, cur.x(), cur.y() + 10)
        R = self.RULER
        p.fillRect(0, 0, vp.width(), R, c["ribbon"])
        p.fillRect(0, 0, R, vp.height(), c["ribbon"])
        p.setPen(c["border"])
        p.drawLine(QPointF(R, R - 1), QPointF(vp.width(), R - 1))
        p.drawLine(R - 1, R, R - 1, vp.height())
        p.setFont(QFont(themes.FAMILY["name"], 7))
        step, lab = self._step(8), self._step(60)
        tl, br = self.mapToScene(0, 0), self.mapToScene(vp.width(), vp.height())
        p.setPen(c["muted"])
        x = math.floor(tl.x() / step) * step
        while x <= br.x():
            vx = self.mapFromScene(QPointF(x, 0)).x()
            big = abs(x / lab - round(x / lab)) < 1e-9
            if vx > R:
                p.drawLine(QPointF(vx, R - (9 if big else 4)), QPointF(vx, R - 1))
                if big:
                    p.drawText(QPointF(vx + 3, 11), ltr(f"{x / 1000:g} m" if lab >= 500 else f"{x:g}"))
            x += step
        y = math.floor(tl.y() / step) * step
        while y <= br.y():
            vy = self.mapFromScene(QPointF(0, y)).y()
            big = abs(y / lab - round(y / lab)) < 1e-9
            if vy > R:
                p.drawLine(QPointF(R - (9 if big else 4), vy), QPointF(R - 1, vy))
                if big:
                    p.save()
                    p.translate(11, vy - 3)
                    p.rotate(-90)
                    p.drawText(QPointF(0, 0), ltr(f"{y:g}"))
                    p.restore()
            y += step
        p.fillRect(0, 0, R, R, c["sidebar"])
        if cur is not None:
            p.setPen(QPen(c["accent"], 1))
            p.drawLine(QPointF(cur.x(), 0), QPointF(cur.x(), R))
            p.drawLine(0, cur.y(), R, cur.y())
        p.restore()


# ---------------------------------------------------------------- small widgets

def hline() -> QFrame:
    f = QFrame()
    f.setObjectName("hline")
    return f


class Ring(QWidget):
    """Efficiency gauge."""

    def __init__(self):
        super().__init__()
        self.value: float | None = None
        self.setFixedSize(70, 70)

    def set_value(self, v):
        self.value = v
        self.update()

    def paintEvent(self, e):
        c = themes.colours()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(6, 6, 58, 58)
        p.setPen(QPen(c["border"], 7))
        p.drawArc(r, 0, 360 * 16)
        if self.value is not None:
            p.setPen(QPen(c["accent"], 7, Qt.SolidLine, Qt.FlatCap))
            p.drawArc(r, 90 * 16, -int(self.value / 100 * 360 * 16))
        p.setPen(c["text"])
        f = QFont(themes.FAMILY["name"], 10)
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, "—" if self.value is None else f"{self.value:.0f}%")
        p.end()


def panel_head(title: str) -> tuple[QFrame, QLabel]:
    h = QFrame()
    h.setObjectName("panelHead")
    h.setFixedHeight(30)
    lay = QHBoxLayout(h)
    lay.setContentsMargins(10, 0, 10, 0)
    t = QLabel(title)
    t.setObjectName("panelTitle")
    lay.addWidget(t)
    lay.addStretch()
    n = QLabel("")
    n.setObjectName("panelCount")
    lay.addWidget(n)
    return h, n


class Ribbon(QFrame):
    """Tool groups in the order of the work: order → model → nesting → output → system. Never squeezes its
    buttons: with less room the labels go (icons only, label in the tooltip), with still less it scrolls."""

    def __init__(self, groups, extra=()):
        super().__init__()
        self.setObjectName("ribbon")
        self.setFixedHeight(96)                                   # room for two-line labels and the group titles
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        outer.addWidget(self.scroll)
        inner = QWidget()
        self.scroll.setWidget(inner)
        lay = QHBoxLayout(inner)
        lay.setContentsMargins(6, 4, 6, 2)
        lay.setSpacing(0)
        self.buttons: dict[str, QToolButton] = {}
        acc = themes.colour("accent")
        brand = QWidget()
        bl = QHBoxLayout(brand)
        bl.setContentsMargins(8, 0, 16, 0)
        bl.setSpacing(9)
        mark = QLabel("L7")
        mark.setObjectName("brandMark")
        mark.setFixedSize(38, 38)
        mark.setAlignment(Qt.AlignCenter)
        bl.addWidget(mark)
        self.brand_text = QWidget()
        col = QVBoxLayout(self.brand_text)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        name = QLabel("LEVEL7")
        name.setObjectName("brand")
        sub = QLabel("MTM CAD")
        sub.setObjectName("brandSub")
        col.addStretch()
        col.addWidget(name)
        col.addWidget(sub)
        col.addStretch()
        bl.addWidget(self.brand_text)
        lay.addWidget(brand)
        for title, items in groups:
            g = QFrame()
            g.setObjectName("ribbonGroup")
            gl = QVBoxLayout(g)
            gl.setContentsMargins(8, 0, 8, 0)
            gl.setSpacing(1)
            row = QHBoxLayout()
            row.setSpacing(2)
            for i, (key, ic, text, fn) in enumerate(items):
                b = QToolButton()
                b.setObjectName("ribbonButton")
                b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                b.setIcon(icon(ic, acc if i == 0 else None))
                b.setIconSize(QSize(24, 24))
                b.setText(text)
                b.setToolTip(text)
                b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)   # never squeezed (no overlapping)
                b.clicked.connect(fn)
                self.buttons[key] = b
                row.addWidget(b)
            gl.addLayout(row, 1)
            t = QLabel(title)
            t.setObjectName("ribbonTitle")
            t.setAlignment(Qt.AlignCenter)
            gl.addWidget(t)
            lay.addWidget(g)
        for title, widget in extra:                          # custom groups (e.g. the ERP countdown)
            g = QFrame()
            g.setObjectName("ribbonGroup")
            gl = QVBoxLayout(g)
            gl.setContentsMargins(8, 0, 8, 0)
            gl.setSpacing(1)
            gl.addWidget(widget, 1)
            t = QLabel(title)
            t.setObjectName("ribbonTitle")
            t.setAlignment(Qt.AlignCenter)
            gl.addWidget(t)
            lay.addWidget(g)
        lay.addStretch()
        self.inner = inner
        self.compact = False
        self.mode = "full"
        self.widths = {}
        self._lay = lay
        self.measure()

    def measure(self):
        """How wide each size is (after the style is applied); measured again when shown."""
        cur = self.mode
        for m in ("small", "icons", "full"):
            self._apply(m)
            for b in self.buttons.values():
                b.ensurePolished()
                b.updateGeometry()
            self._lay.invalidate()
            self._lay.activate()
            self.widths[m] = self._lay.sizeHint().width()
        self.full_width = self.widths["full"]
        self._apply(cur)

    def showEvent(self, e):
        super().showEvent(e)
        self.measure()
        self.resizeEvent(None)

    def _apply(self, mode: str):
        """full: big icons, labels; small: smaller icons, labels in a smaller (still readable) font;
        icons: icons only, label in the tooltip."""
        self.mode = mode
        self.compact = mode == "icons"
        for b in self.buttons.values():
            b.setToolButtonStyle(Qt.ToolButtonIconOnly if mode == "icons" else Qt.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(24, 24) if mode == "full" else QSize(19, 19))
            b.setMinimumWidth(36 if mode == "icons" else 0)
            b.setStyleSheet("" if mode == "full" else "QToolButton { font-size: 8pt; padding: 1px 3px; }")
            full = b.toolTip()
            words = full.split(" ")
            if mode == "small" and len(words) > 1 and len(full) > 9:     # long labels on two lines
                k = max(range(1, len(words)), key=lambda i: -abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
                b.setText(" ".join(words[:k]) + "\n" + " ".join(words[k:]))
            else:
                b.setText(full)
        self.brand_text.setVisible(mode == "full")

    def set_compact(self, on: bool):
        self._apply("icons" if on else "full")

    def resizeEvent(self, e):
        if e is not None:
            super().resizeEvent(e)
        w = self.width()
        want = "full" if w >= self.widths.get("full", 0) else ("small" if w >= self.widths.get("small", 0)
                                                                else "icons")
        if want != self.mode:
            self._apply(want)


class OrderBrowser(QWidget):
    """Order lines as a tree: line → the pieces of its marker; coloured by where the line is in the chain."""
    selected = Signal(object)

    def __init__(self):
        super().__init__()
        self.setObjectName("browser")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head, self.count = panel_head(tr("Order browser"))
        lay.addWidget(head)
        from PySide6.QtWidgets import QLineEdit
        self.find = QLineEdit()
        self.find.setPlaceholderText("⌕  " + tr("Search order…"))
        self.find.textChanged.connect(self._filter)
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(8, 8, 8, 6)
        bl.addWidget(self.find)
        lay.addWidget(box)
        self.tree = QTreeWidget()
        self.tree.setObjectName("orders")
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setIndentation(14)
        self.tree.header().setStretchLastSection(False)
        from PySide6.QtWidgets import QHeaderView
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.currentItemChanged.connect(self._changed)
        lay.addWidget(self.tree, 1)
        self.rows: list[dict] = []

    # three lists, so a line that needs attention never holds up the others
    GROUPS = (("work", "In progress", ("new", "grafis", "nest"), "accent"),
              ("err", "Needs attention", ("err",), "bad"),
              ("cut", "Ready to cut", ("cut",), "ok"))

    def _order_items(self):
        for g in range(self.tree.topLevelItemCount()):
            grp = self.tree.topLevelItem(g)
            for i in range(grp.childCount()):
                yield grp.child(i)

    def set_rows(self, rows: list[dict], keep: int | None = None):
        self.rows = rows
        if not hasattr(self, "closed"):
            self.closed: set[str] = set()
        for g in range(self.tree.topLevelItemCount()):       # remember which lists the owner closed
            grp = self.tree.topLevelItem(g)
            key = grp.data(0, Qt.UserRole + 1)
            (self.closed.discard if grp.isExpanded() else self.closed.add)(key)
        self.tree.blockSignals(True)
        self.tree.clear()
        c = themes.colours()
        current, first = None, None
        for key, title, stages, colour in self.GROUPS:
            mine = [r for r in rows if r["stage"] in stages]
            grp = QTreeWidgetItem([f"{tr(title)}", str(len(mine))])
            grp.setData(0, Qt.UserRole + 1, key)
            grp.setFlags(Qt.ItemIsEnabled)
            f = grp.font(0)
            f.setBold(True)
            f.setPointSizeF(f.pointSizeF() + 0.5)
            for col_ in (0, 1):
                grp.setFont(col_, f)
                grp.setForeground(col_, c[colour])
            grp.setBackground(0, QBrush(QColor(c[colour].red(), c[colour].green(), c[colour].blue(), 28)))
            grp.setBackground(1, QBrush(QColor(c[colour].red(), c[colour].green(), c[colour].blue(), 28)))
            self.tree.addTopLevelItem(grp)
            for r in mine:
                col = c[STAGE_COLOUR[r["stage"]]]
                it = QTreeWidgetItem([ltr(r["line"]), tr(STAGE_TEXT[r["stage"]])])
                it.setData(0, Qt.UserRole, r["rid"])
                pm = QPixmap(10, 10)
                pm.fill(col)
                it.setIcon(0, QIcon(pm))
                it.setForeground(1, col)
                f2 = it.font(1)
                f2.setBold(True)
                it.setFont(1, f2)
                it.setToolTip(0, "\n".join([r["line"]] + r["problems"][:5]))
                grp.addChild(it)
                first = first or it
                if r["rid"] == keep:
                    current = it
            grp.setExpanded(key not in self.closed)
        self.tree.blockSignals(False)
        self.count.setText(str(len(rows)))
        self._filter(self.find.text())
        if current is not None:
            current.parent().setExpanded(True)
            self.tree.setCurrentItem(current)
        elif first is not None and keep is None:
            self.tree.setCurrentItem(first)

    def add_pieces(self, rid, marker):
        """Show the pieces of the line's marker under it."""
        for it in self._order_items():
            if it.data(0, Qt.UserRole) != rid:
                continue
            it.takeChildren()
            if marker:
                counts: dict[str, int] = {}
                for p in marker["pieces"]:
                    counts[p["name"]] = counts.get(p["name"], 0) + 1
                pm = QPixmap(12, 9)
                pm.fill(themes.colour("piece"))
                for name, n in counts.items():
                    ch = QTreeWidgetItem([name, f"{n}×"])
                    ch.setIcon(0, QIcon(pm))
                    ch.setForeground(1, themes.colour("muted"))
                    it.addChild(ch)
                it.setExpanded(True)

    def _filter(self, text):
        t = text.strip().lower()
        for it in self._order_items():
            it.setHidden(bool(t) and t not in it.text(0).strip("\u2066\u2069").lower())

    def _changed(self, cur, _prev):
        while cur is not None and cur.data(0, Qt.UserRole) is None and cur.parent() is not None:
            cur = cur.parent()                                # a piece: its order line
        rid = cur.data(0, Qt.UserRole) if cur is not None else None
        if rid is None:
            return                                           # a list title: keep the selection
        self.selected.emit(next((r for r in self.rows if r["rid"] == rid), None))


class Inspector(QScrollArea):
    """Properties of the selected order line: order, nesting, X values written, stages, actions."""

    def __init__(self, actions):
        super().__init__()
        self.setObjectName("inspector")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        w = QWidget()
        w.setObjectName("inspector")
        self.setWidget(w)
        self.lay = QVBoxLayout(w)
        self.lay.setContentsMargins(12, 8, 12, 12)
        self.lay.setSpacing(4)
        self.title = QLabel("—")
        self.title.setObjectName("bigTitle")
        self.pill = QLabel()
        self.pill.setObjectName("pill")
        hr = QHBoxLayout()
        hr.addWidget(self.title)
        hr.addStretch()
        hr.addWidget(self.pill)
        self.lay.addLayout(hr)
        self.problems = QLabel()                          # first thing under the title: why it needs attention
        self.problems.setWordWrap(True)
        self.problems.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bad = themes.colour("bad")
        self.problems.setStyleSheet(f"color: {bad.name()}; border: 1px solid {bad.name()}; padding: 6px; "
                                    f"background: rgba({bad.red()},{bad.green()},{bad.blue()},0.10);")
        self.problems.hide()
        self.lay.addWidget(self.problems)
        self.buttons = {}                                 # the actions right under it, never scrolled away
        for key, text, primary in actions:
            b = QPushButton(text)
            if primary:
                b.setObjectName("primary")
            self.buttons[key] = b
            self.lay.addWidget(b)
        self.kv: dict[str, QLabel] = {}
        self._section(tr("The order"), [("fabric", "Fabric"), ("width", "Width"), ("quantity", "Quantity"),
                                    ("imported", "Imported")])
        self.lay.addWidget(self._sec_title(tr("Nesting")))
        nr = QHBoxLayout()
        self.ring = Ring()
        nr.addWidget(self.ring)
        g = QGridLayout()
        for i, (k, t) in enumerate((("length", "Length"), ("placed", "Pieces"))):
            a = QLabel(tr(t))
            a.setObjectName("kvKey")
            b = QLabel("—")
            b.setObjectName("kvVal")
            g.addWidget(a, i, 0)
            g.addWidget(b, i, 1)
            self.kv[k] = b
        nr.addLayout(g, 1)
        self.lay.addLayout(nr)
        self.lay.addWidget(self._sec_title(tr("X values in the model")))
        self.xgrid = QGridLayout()
        self.xgrid.setHorizontalSpacing(8)
        self.xgrid.setVerticalSpacing(1)
        self.lay.addLayout(self.xgrid)
        self.lay.addWidget(self._sec_title(tr("Stages")))
        self.stages = QGridLayout()
        self.stages.setVerticalSpacing(2)
        self.lay.addLayout(self.stages)
        self.lay.addStretch()
        self.show_row(None)

    def _sec_title(self, text):
        t = QLabel(text.upper() if text.isascii() else text)
        t.setObjectName("secTitle")
        return t

    def _section(self, title, items):
        self.lay.addWidget(self._sec_title(title))
        g = QGridLayout()
        g.setVerticalSpacing(2)
        for i, (k, t) in enumerate(items):
            a = QLabel(tr(t))
            a.setObjectName("kvKey")
            b = QLabel("—")
            b.setObjectName("kvVal")
            b.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            g.addWidget(a, i, 0)
            g.addWidget(b, i, 1)
            self.kv[k] = b
        self.lay.addLayout(g)

    @staticmethod
    def _clear(grid):
        while grid.count():
            w = grid.takeAt(0).widget()
            if w:
                w.deleteLater()

    def show_row(self, r: dict | None):
        c = themes.colours()
        self._clear(self.xgrid)
        self._clear(self.stages)
        for b in self.buttons.values():
            b.setEnabled(r is not None)
        if "robot" in self.buttons:
            self.buttons["robot"].setVisible(False)
        if r is None:
            self.title.setText("—")
            self.pill.setText("")
            for v in self.kv.values():
                v.setText("—")
            self.ring.set_value(None)
            self.problems.setText("")
            self.problems.hide()
            return
        self.title.setText(ltr(r["line"]))
        col = c[STAGE_COLOUR[r["stage"]]]
        self.pill.setText(tr(STAGE_TEXT[r["stage"]]))
        self.pill.setStyleSheet(f"color: {col.name()}; border-color: {col.name()};")
        self.kv["fabric"].setText(str(r["fabric"] or "—"))
        self.kv["width"].setText(f"{r['width']:g} mm" if r["width"] else "—")
        self.kv["quantity"].setText(str(r["quantity"] or "—"))
        self.kv["imported"].setText((r["imported"] or "").replace("T", " ")[:16])
        self.kv["length"].setText(f"{r['length_mm'] / 1000:.2f} m" if r["length_mm"] else "—")
        self.kv["placed"].setText(str(r["placed"]) if r["placed"] is not None else "—")
        self.ring.set_value(r["efficiency_pct"])
        for i, x in enumerate(r["x_rows"][:40]):
            a = QLabel(x["x"])
            a.setStyleSheet(f"color: {c['accent'].name()}; font-weight: 700;")
            b = QLabel(x.get("x_name") or "")
            b.setObjectName("kvKey")
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            v = QLabel(f"{x['value_mm']:g}")
            v.setObjectName("kvVal")
            v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.xgrid.addWidget(a, i, 0)
            self.xgrid.addWidget(b, i, 1)
            self.xgrid.addWidget(v, i, 2)
        if not r["x_rows"]:
            n = QLabel("—")
            n.setObjectName("kvKey")
            self.xgrid.addWidget(n, 0, 0)
        reached = r.get("stage_reached", r["stage"])
        order = list(STAGES)
        last = order.index(reached) if reached in order else 0
        for i, (st, label_) in enumerate((("new", "XML received"), ("grafis", "GRAFIS model"),
                                           ("nest", "GRAFIS → nesting"), ("cut", "Cutting files"))):
            done = st in r["times"] and i <= last
            bad = r["stage"] == "err" and i == last + (0 if st in r["times"] else 0) and not done
            colour = c["ok"] if done else (c["bad"] if bad else c["border"])
            d = QLabel("●")
            d.setStyleSheet(f"color: {colour.name()};")
            t = QLabel(tr(label_))
            tm = QLabel((r["times"].get(st) or "").replace("T", " ")[11:16])
            tm.setObjectName("kvKey")
            self.stages.addWidget(d, i, 0)
            self.stages.addWidget(t, i, 1)
            self.stages.addWidget(tm, i, 2)
        self.problems.setText("\n".join("⚠ " + p for p in r["problems"][:6]))
        self.problems.setVisible(bool(r["problems"]))
        self.buttons.get("open_cut") and self.buttons["open_cut"].setEnabled(bool(r["outputs"]))
        self.buttons.get("renest") and self.buttons["renest"].setEnabled(bool(r["result"]))
        self.buttons.get("nesting") and self.buttons["nesting"].setEnabled(bool(r["l7n"]))
        for k in ("m_line", "m_curve"):
            if k in self.buttons:
                self.buttons[k].setEnabled(bool(r["l7n"]))
        if "robot" in self.buttons:
            self.buttons["robot"].setVisible(bool(r.get("robot_failed")))


class MarkerPanel(QWidget):
    """Canvas + floating tool palette + HUD (length, efficiency, pieces)."""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = MarkerView()
        lay.addWidget(self.view)
        self.tools = QFrame(self)
        self.tools.setObjectName("tools")
        tl = QVBoxLayout(self.tools)
        tl.setContentsMargins(3, 3, 3, 3)
        tl.setSpacing(2)
        self.tool_buttons = {}
        for key, ic, tip in (("select", "select", "Select"), ("pan", "pan", "Move the view"),
                             ("line", "ruler", "Measure lengths"), ("curve", "curve", "Measure curves"),
                             ("zoomin", "zoomin", "Zoom in"), ("zoomout", "zoomout", "Zoom out"),
                             ("fit", "fit", "Whole marker")):
            b = QToolButton()
            b.setObjectName("tool")
            b.setIcon(icon(ic))
            b.setIconSize(QSize(18, 18))
            b.setToolTip(tr(tip))
            b.setCheckable(key in ("select", "pan", "line", "curve"))
            self.tool_buttons[key] = b
            tl.addWidget(b)
        tb = self.tool_buttons
        tb["select"].setChecked(True)
        for key in ("select", "pan", "line", "curve"):
            tb[key].clicked.connect(lambda _=False, k=key: self.use_tool(k))
        tb["zoomin"].clicked.connect(lambda: self.view.zoom(1.25))
        tb["zoomout"].clicked.connect(lambda: self.view.zoom(0.8))
        tb["fit"].clicked.connect(self.view.fit)
        self.hud = QFrame(self)
        self.hud.setObjectName("hud")
        hl = QHBoxLayout(self.hud)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.setSpacing(16)
        self.hud_values = {}
        for key, t in (("measure", "Measured"), ("length", "Length"), ("eff", "Efficiency"), ("pieces", "Pieces")):
            col = QVBoxLayout()
            col.setSpacing(0)
            v = QLabel("—")
            v.setObjectName("hudValueAcc" if key == "length" else "hudValue")
            lab = QLabel(tr(t))
            lab.setObjectName("hudLabel")
            col.addWidget(v)
            col.addWidget(lab)
            hl.addLayout(col)
            self.hud_values[key] = v
            if key == "measure":
                v.setStyleSheet(f"color: {themes.colour('warn').name()}; font-size: 13pt; font-weight: 800;")
                self.hud_measure = (v, lab)
                v.hide()
                lab.hide()
        self.view.measured.connect(self._measured)

    def use_tool(self, key: str):
        """select / pan / line (measure lengths) / curve (measure curves): one at a time."""
        for k in ("select", "pan", "line", "curve"):
            self.tool_buttons[k].setChecked(k == key)
        self.view.set_pan(key == "pan")
        self.view.set_tool(key if key in ("line", "curve") else None)

    def _measured(self, text: str):
        v, lab = self.hud_measure
        v.setText(ltr(text))
        v.setVisible(bool(text))
        lab.setVisible(bool(text))
        self.hud.adjustSize()
        self._place()

    def set_marker(self, marker, row=None, message=""):
        self.view.show_marker(marker, message)
        length = (marker or {}).get("length") or (row or {}).get("length_mm")
        eff = (row or {}).get("efficiency_pct")
        self.hud_values["length"].setText(f"{length / 1000:.2f} m" if length else "—")
        self.hud_values["eff"].setText(f"{eff:.1f}%" if eff else "—")
        self.hud_values["pieces"].setText(str(len(marker["pieces"])) if marker else "—")
        self.hud.adjustSize()
        self._place()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def _place(self):
        r = MarkerView.RULER
        self.tools.adjustSize()
        self.tools.move(r + 10, r + 10)
        self.hud.adjustSize()
        self.hud.move(self.width() - self.hud.width() - 14, self.height() - self.hud.height() - 14)
