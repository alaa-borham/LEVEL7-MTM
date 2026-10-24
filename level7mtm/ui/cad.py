"""CAD workspace of LEVEL7 MTM: ribbon, order browser, marker canvas (with rulers and grid), properties
inspector. The marker shown is the real one LEVEL7 Nesting saved (``.l7n``, read only)."""
from __future__ import annotations

import json
import math
import zipfile

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
                           QTransform)
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import themes
from .i18n import tr, tr_msg

# ---------------------------------------------------------------- icons (24 x 24, stroked)

ICONS = {
    "cut": "M6 9a3 3 0 100-6 3 3 0 000 6zM6 21a3 3 0 100-6 3 3 0 000 6zM20 4L8.1 15.9M14.5 14.5L20 20M8.1 8.1L12 12",
    "info": "M12 22a10 10 0 100-20 10 10 0 000 20zM12 16v-5M12 8h.01",
    "import": "M12 3v11m0 0l-4-4m4 4l4-4M4 15v4a1 1 0 001 1h14a1 1 0 001-1v-4",
    "orders": "M4 5h16M4 10h16M4 15h10M4 20h7",
    "customers": "M9 11a4 4 0 100-8 4 4 0 000 8zM2 21v-1a6 6 0 0112 0v1M17 11a3 3 0 100-6M22 21v-1a5 5 0 00-4-4.9",
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
    "sum": "M18 5V4H6l7 8-7 8h12v-1",
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
    except (OSError, KeyError, ValueError, TypeError, AttributeError, zipfile.BadZipFile):   # damaged file
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


def pattern_sheet(marker: dict | None) -> dict | None:
    """The order's pattern as GRAFIS made it: one of each piece of the marker (a mirrored copy only when there
    is no other), standing upright (grain line vertical) in rows that fill a screen. Shown without fabric."""
    if not marker or not marker.get("pieces"):
        return None
    first: dict[str, dict] = {}
    for p in marker["pieces"]:
        if p["name"] not in first or (first[p["name"]]["mirrored"] and not p["mirrored"]):
            first[p["name"]] = p
    upright = []
    for p in first.values():
        if len(p["grain"]) >= 2:
            (x1, y1), (x2, y2) = p["grain"][0], p["grain"][-1]
            a = math.atan2(y2 - y1, x2 - x1)
        else:                                            # no grain line: the long side upright
            xs, ys = [q[0] for q in p["cut"]], [q[1] for q in p["cut"]]
            a = 0.0 if max(xs) - min(xs) >= max(ys) - min(ys) else math.pi / 2
        t = math.pi / 2 - a
        ct, st = math.cos(t), math.sin(t)
        rot = lambda pts: [(x * ct - y * st, x * st + y * ct) for x, y in pts]
        q = {**p, "cut": rot(p["cut"]), "grain": rot(p["grain"]), "holes": [rot(h) for h in p["holes"]],
             "corners": rot(p.get("corners") or [])}
        q.pop("_bb", None)
        xs, ys = [v[0] for v in q["cut"]], [v[1] for v in q["cut"]]
        upright.append((q, min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
    gap = 80.0
    area = sum((w + gap) * (h + gap) for _, _, _, w, h in upright)
    row_w = max(max(w for *_, w, _ in upright), math.sqrt(area * 2.6))      # about the shape of the view
    out, x, y, row_h, length = [], 0.0, 0.0, 0.0, 0.0
    for q, x0, y0, w, h in sorted(upright, key=lambda u: -u[4]):            # tall pieces first
        if x > 0 and x + w > row_w:
            x, y, row_h = 0.0, y + row_h + gap, 0.0
        dx, dy = x - x0, y - y0
        mv = lambda pts: [(a + dx, b + dy) for a, b in pts]
        out.append({**q, "cut": mv(q["cut"]), "grain": mv(q["grain"]), "holes": [mv(hh) for hh in q["holes"]],
                    "corners": mv(q["corners"])})
        x += w + gap
        row_h = max(row_h, h)
        length = max(length, x - gap)
    return {"width": y + row_h, "length": length, "pieces": out, "stats": {}, "sheet": True}


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


def _grafis_wait(db, go, rj, ahead=None, robot_on=None) -> str:
    """Why an order model is still in GRAFIS: the robot off, its place in the robot's queue, or saved by GRAFIS
    and waiting to be read from the MDL folder."""
    if rj and rj["status"] == "done" and rj["at"] >= go["created_at"]:
        return tr("Saved by GRAFIS: waiting for nesting")
    from ..services.grafis_robot import file_sha
    try:
        changed = file_sha(go["path"]) != json.loads(go["info"]).get("sha256")
    except OSError:
        return tr("The order model is not in the MDL folder any more (moved or deleted?): write it again")
    if changed:
        return tr("Saved by GRAFIS: waiting for nesting")
    if not (db.get_setting("robot.enabled", False) if robot_on is None else robot_on):
        return tr("The GRAFIS robot is off: switch it on (GRAFIS page → Robot) or open the model in GRAFIS, run "
                  "Test Run All Parts and save")
    if ahead is not None:
        return tr("Waiting for the GRAFIS robot") + (f" — {ahead} {tr('models before it')}" if ahead else "")
    ahead = db.one("SELECT COUNT(*) AS n FROM grafis_orders g WHERE g.status='written' AND g.archived=0 AND g.id<? "
                   "AND g.id IN (SELECT MAX(id) FROM grafis_orders GROUP BY revision_id) AND NOT EXISTS "
                   "(SELECT 1 FROM robot_jobs j WHERE j.path=g.path AND j.at>=g.created_at)", (go["id"],))["n"]
    return tr("Waiting for the GRAFIS robot") + (f" — {ahead} {tr('models before it')}" if ahead else "")


def order_rows(ws, limit: int = 2000) -> list[dict]:
    """Every open order line with where it is in the chain (XML → GRAFIS model → GRAFIS → nesting → cutting files)."""
    db = ws.db
    results: dict[int, dict] = {}
    for r in db.q("SELECT * FROM grafis_results WHERE archived=0 ORDER BY id"):
        for ln in json.loads(r["lines"]):
            if ln.get("revision_id") is not None:
                results[ln["revision_id"]] = dict(r)          # the newest result of the line wins
    out = []
    from ..services.activity import snapshot
    from ..services.customers import names as customer_names
    names = customer_names(ws)
    nesting_now = {r for r, m in snapshot().items() if m.get("what") == "nesting"}
    paused = bool(db.get_setting("nesting.paused", False))
    from ..services.mdl_intake import queue as nesting_queue
    try:
        qpos = {p: i for i, p in enumerate(nesting_queue(ws))}   # the order the nesting takes them (owner moves)
    except Exception:  # noqa: BLE001 - the list is shown anyway
        qpos = {}
    from ..services.shifts import current as current_shift
    shift_start = current_shift(ws)["started_at"]            # "ready to cut": this shift's only (owner)
    auto_on = bool(db.get_setting("cutting.auto", True))
    poll_err = db.get_setting("grafis.last_poll_error") or ""
    from ..services.mdl_intake import intake_now
    now_ = intake_now()                                    # what the nesting worker is on, and since when
    busy_note = (tr("the nesting has been on {f} for {m} min").format(f=now_[0], m=now_[1]) if now_ and now_[1] >= 10
                 else "")
    # everything in a few queries (thousands of lines: one query per line held the database for seconds)
    gos_of: dict[int, list] = {}
    for g in db.q("SELECT * FROM grafis_orders ORDER BY id DESC"):
        gos_of.setdefault(g["revision_id"], []).append(g)
    rj_of: dict[str, object] = {}
    for j in db.q("SELECT path, status, message, at FROM robot_jobs ORDER BY id"):
        rj_of[j["path"]] = j                                  # the newest try of each model
    cj_of: dict[str, object] = {}
    for j in db.q("SELECT * FROM cutting_jobs ORDER BY id"):
        cj_of[j["sha256"]] = j                                # the newest nesting of each saved model
    data_of = {r["id"]: r for r in db.q("SELECT id, raw_line, normalized FROM revisions WHERE status <> 'superseded'")}
    robot_on = bool(db.get_setting("robot.enabled", False))
    tried = {}                                                # the robot's queue: written, not tried since
    for g in db.q("SELECT id, path, created_at FROM grafis_orders WHERE status='written' AND archived=0 AND id IN "
                  "(SELECT MAX(id) FROM grafis_orders GROUP BY revision_id) ORDER BY id"):
        j = rj_of.get(g["path"])
        if not (j and j["at"] >= g["created_at"]):
            tried[g["id"]] = len(tried)
    for rev in ws.list_revisions():
        if len(out) >= limit:                                 # the newest open lines (cleaned / cancelled ones
            break                                             # do not count)
        if rev["status"] == "cancelled":
            continue
        rid = rev["id"]
        gos = gos_of.get(rid, [])
        if gos and all(g["archived"] for g in gos):
            continue                                          # cleaned from the list
        go = next((dict(g) for g in gos if not g["archived"]), None)
        d = data_of.get(rid)
        norm = json.loads(d["normalized"]) if d else {}
        raw = json.loads(d["raw_line"]) if d else {}
        cust = norm.get("customer") or "level7"
        row = {"rid": rid, "line": rev["line_number"], "stage": "new", "problems": [], "imported": rev["imported_at"],
               "customer": names.get(cust, cust),
               "fabric": raw.get("fabric_number") or "", "width": norm.get("fabric_width_mm"),
               "basic_size": raw.get("basic_size") or "", "table": raw.get("measurement_table") or "",
               "quantity": rev["quantity"], "times": {"new": rev["imported_at"]}, "x_rows": [], "mdl": None,
               "result": None, "l7n": None, "outputs": [], "length_mm": None, "efficiency_pct": None,
               "placed": None, "status": rev["status"], "robot_failed": False, "waiting": ""}
        if go:
            info = json.loads(go["info"])
            row["x_rows"] = info.get("rows", [])
            row["mdl"] = go["path"]
            row["times"]["grafis"] = go["created_at"]
            if go["status"] in ("blocked", "not_configured"):
                probs = info.get("problems", []) or [tr(go["status"])]
                if rev["status"] == "needs_review":       # the reasons of the XML first (as in the order details),
                    held = [p["message"] for p in norm.get("problems", [])   # not only "needs review"
                            if p.get("severity") in ("review", "error")]
                    probs = held + [p for p in probs if not p.startswith("order line needs review")]
                row["problems"] += probs
            elif go["status"] in ("written", "duplicate"):
                row["stage"] = "grafis"
                rj = rj_of.get(go["path"])
                if rj and rj["status"] == "failed" and rj["at"] >= go["created_at"]:
                    row["robot_failed"] = True                # stays until "Robot: try again" (never silent)
                    row["problems"].append(f"{tr('GRAFIS robot')}: {rj['message']}")
                else:
                    row["waiting"] = _grafis_wait(db, go, rj, tried.get(go["id"], 0), robot_on)   # never silent
                    row["nest_queue"] = row["waiting"] == tr("Saved by GRAFIS: waiting for nesting")
        if rev["status"] == "needs_review" and not row["problems"]:   # held when read, no model tried yet
            row["problems"] = [p["message"] for p in norm.get("problems", []) if p.get("severity") in ("review", "error")]
        res = results.get(rid)
        if res and go and go["status"] == "written" and res["checked_at"] < go["created_at"]:
            res = None                                        # a model written again since: CAD, then nested again
        if res:
            row["result"] = res["path"]
            row["times"]["nest"] = res["checked_at"]
            row["stage"] = "nest"
            if res["status"] in ("needs_review", "unmatched"):
                row["problems"] += [m for sv, m in json.loads(res["issues"]) if sv != "info"]
            cj = cj_of.get(res["sha256"])
            row["nest_queue"] = False
            if not cj and res["status"] == "handed_over" and rid not in nesting_now:
                row["waiting"] = tr("Waiting for nesting")           # saved by GRAFIS: in the nesting queue
                row["nest_queue"] = True
            if cj:
                ci = json.loads(cj["info"])
                row.update(l7n=ci.get("l7n"), length_mm=ci.get("length_mm"), efficiency_pct=ci.get("efficiency_pct"),
                           placed=ci.get("placed"), job=cj["id"])          # a new nesting: the canvas reloads
                if cj["status"] == "done":
                    row["stage"] = "cut"
                    row["outputs"] = json.loads(cj["outputs"])
                    row["times"]["cut"] = cj["created_at"]
                elif cj["status"] in ("failed", "not_configured"):
                    row["problems"].append(ci.get("message") or tr(cj["status"]))
        if res:                                               # GRAFIS saved it: an old robot failure is over
            row["problems"] = [p for p in row["problems"] if not p.startswith(tr("GRAFIS robot") + ":")]
            row["robot_failed"] = False
        if row.get("nest_queue"):
            row["queue_path"] = row["result"] or row["mdl"]
            row["queue_pos"] = qpos.get(row["queue_path"])
        if row.get("nest_queue"):                         # never silent: why the queue does not move
            why = tr("the nesting is stopped: press «Start» on the nesting queue") if paused else \
                tr("«Nest and write cutting files» is off in Setup: press «Start» on the nesting queue") \
                if not auto_on else (tr("the nesting stopped on an error") + ": " + tr_msg(poll_err[:160])) \
                if poll_err else busy_note
            if why:
                row["waiting"] = (row["waiting"] or tr("Waiting for nesting")) + " — " + why
        if row["problems"] and row["stage"] != "cut":
            row["stage_reached"], row["stage"] = row["stage"], "err"
            if row.get("mdl") and any("pattern distorted" in p for p in row["problems"]):
                from ..services.pattern_check import picture_of
                row["defect_svg"] = picture_of(ws, row["mdl"])   # the distorted pieces, each defect circled
        if row["stage"] == "cut" and (row["times"].get("cut") or "") < shift_start:
            continue                                          # cut in a shift before: off the list, files kept
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
    shortcut = Signal(str)                      # a tool key pressed over the marker: select / pan / line / …
    RULER = 22
    # one letter per tool; Windows virtual keys, so they work with the Arabic keyboard layout too
    KEYS = {"V": "select", "H": "pan", "L": "line", "C": "curve", "S": "sum", "F": "fit"}
    ZOOM_KEYS = {0xBB: "zoomin", 0x6B: "zoomin", 0xBD: "zoomout", 0x6D: "zoomout"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.LeftToRight)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMouseTracking(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # moved with the pan tool / middle button
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._content = QRectF(-100, -100, 3000, 1700)             # what "whole marker" shows
        self.marker: dict | None = None
        self.selected: int | None = None
        self._items: list = []
        self.message = ""
        self._cursor: QPointF | None = None
        self._pan = None
        self.tool: str | None = None            # None, "line" (straight length) or "curve" (along a piece edge)
        self.meas: dict = {}
        self.adding = False                     # "add up": each finished measurement is kept and summed
        self.parts: list[dict] = []
        self.box = MeasureBox(self.viewport())
        self._near = None
        self._snapped = None
        self.zooming = False                     # space bar held: left click = zoom out, right click = zoom in
        self.setFocusPolicy(Qt.StrongFocus)
        from PySide6.QtWidgets import QApplication
        if QApplication.instance() is not None:   # the space bar wherever the keyboard focus is (a button
            QApplication.instance().installEventFilter(self)   # would otherwise be "pressed" by it)

    # -- content
    def show_marker(self, marker: dict | None, message: str = ""):
        sc = self.scene()
        sc.clear()
        self.marker, self.message = marker, message
        self.meas = {}
        self.parts = []
        self._items = []                                 # the piece outlines, in the order of marker["pieces"]
        self.selected: int | None = None
        if hasattr(self, "box"):
            self.box.hide()
        if not marker:
            self._set_content(QRectF(-100, -100, 3000, 1700))
            self.resetTransform()
            self.fit()
            return
        c = themes.colours()
        w, length = marker["width"], marker["length"]
        if not marker.get("sheet"):                          # a pattern sheet has no fabric
            sc.addRect(QRectF(0, 0, length, w), QPen(c["selvedge"], 0), QBrush(c["fabric"]))
        dash = QPen(c["selvedge"], 0, Qt.DashLine)
        for y in (() if marker.get("sheet") else (-12, w + 12)):     # selvedges: on a marker only
            sc.addLine(0, y, length, y, dash)
        acc = c["piece"]
        fill = QColor(acc)
        fill.setAlpha(70)
        pen = QPen(acc, 0)
        pen.setCosmetic(True)
        pen.setWidthF(1.3)
        gpen = QPen(c["muted"], 0)
        gpen.setCosmetic(True)
        f = QFont(themes.FAMILY["name"], 10 if marker.get("sheet") else 8)
        f.setBold(True)
        for p in marker["pieces"]:
            path = QPainterPath()
            path.addPolygon(QPolygonF([QPointF(x, y) for x, y in p["cut"]]))
            path.closeSubpath()
            for h in p["holes"]:
                path.addPolygon(QPolygonF([QPointF(x, y) for x, y in h]))
                path.closeSubpath()
            item = sc.addPath(path, pen, QBrush(fill))
            self._items.append(item)
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
            if marker.get("sheet"):                          # pattern sheet: the name in millimetres, as wide
                tb = t.boundingRect()                        # as the piece at most, so names never overlap
                k = min(60 / max(tb.height(), 1), 0.92 * br.width() / max(tb.width(), 1))
                t.setTransform(QTransform.fromScale(k, k) * QTransform.fromTranslate(
                    br.center().x() - tb.width() * k / 2, br.center().y() - tb.height() * k / 2))
                sc.addItem(t)
                continue
            t.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations)
            tb = t.boundingRect()
            t.setTransform(QTransform.fromTranslate(-tb.width() / 2, -tb.height() / 2))   # centred in pixels
            t.setPos(br.center())
            sc.addItem(t)
        if marker.get("sheet"):
            self._set_content(QRectF(-60, -60, length + 120, w + 120))
            self.fit()
            return
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
        self._set_content(QRectF(-150, -150, length + 300, w + 400))
        self.fit()

    def _set_content(self, r: QRectF):
        """The marker and a margin; the scene is much larger so the view can be moved past the edges (also
        when the whole marker is shown)."""
        self._content = r
        m = max(r.width(), r.height())
        self.scene().setSceneRect(r.adjusted(-m, -m, m, m))

    def fit(self):
        r = self._content
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
        if getattr(self, "selected", None) is not None:
            self.select_piece(None)
        self.tool = tool
        self.meas = {}
        if tool is None:
            self.parts = []
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

    def select_at(self, p):
        """Select tool: the piece under p (scene mm) is highlighted and its name and size shown; elsewhere the
        selection goes."""
        hit = None
        for i, item in enumerate(getattr(self, "_items", [])):
            if item.path().contains(QPointF(*p)):
                hit = i
        self.select_piece(hit)

    def select_piece(self, i: int | None):
        c = themes.colours()
        if self.selected is not None and self.selected < len(self._items):
            it = self._items[self.selected]
            it.setPen(self._piece_pen)
            it.setBrush(self._piece_brush)
        self.selected = i
        if i is None:
            self.box.hide()
            self.measured.emit("")
            return
        it = self._items[i]
        self._piece_pen, self._piece_brush = it.pen(), it.brush()
        pen = QPen(c["warn"], 0)
        pen.setCosmetic(True)
        pen.setWidthF(2.4)
        fill = QColor(c["warn"])
        fill.setAlpha(60)
        it.setPen(pen)
        it.setBrush(QBrush(fill))
        pc = self.marker["pieces"][i]
        xs, ys = [x for x, _ in pc["cut"]], [y for _, y in pc["cut"]]
        pts = pc["cut"]
        area = abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2 / 1e6
        self.box.moved = False
        self.box.show_value(pc["name"] + (" (M)" if pc.get("mirrored") else ""),
                            f"{(max(xs) - min(xs)) / 10:.1f} × {(max(ys) - min(ys)) / 10:.1f} cm · {area:.3f} m²",
                            self.mapFromScene(QPointF((min(xs) + max(xs)) / 2, min(ys))))
        self.measured.emit(pc["name"])

    def set_adding(self, on: bool):
        """Add up several measurements (lines and curves, also on different pieces): each one finished is kept
        on the canvas and the box shows the total."""
        self.adding = on
        self.parts = []
        self.meas = {}
        self.box.moved = False
        self.measured.emit("")
        self.viewport().update()

    def total(self) -> float:
        return sum(m["value"] for m in self.parts)

    def _finished(self):
        """A measurement is complete: shown alone, or (adding up) kept as one more part of the total."""
        if self.adding:
            m = self.meas
            self.parts.append({"way": m.get("way") or m["pts"], "pts": m["pts"], "value": m["value"]})
            self.meas = {}
            self.measured.emit(self._sum_text()[0])
        else:
            self.measured.emit(self.meas["text"])

    def _sum_text(self) -> tuple[str, str]:
        vals = [f"{m['value'] / 10:.1f}" for m in self.parts]
        extra = " + ".join(vals) if len(vals) <= 6 else " + ".join(vals[:5]) + f" + … ({len(vals)})"
        return f"Σ {self.total() / 10:.1f} cm", extra

    def undo_part(self):
        if self.parts:
            self.parts.pop()
            self.measured.emit(self._sum_text()[0] if self.parts else "")
            self.viewport().update()

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
                self.meas.update(value=d, text=f"{d / 10:.1f} cm", extra="")
                self._finished()
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
            self._finished()
        self.viewport().update()

    def _mouse_over(self) -> bool:
        from PySide6.QtGui import QCursor
        return self.isVisible() and self.viewport().rect().contains(self.viewport().mapFromGlobal(QCursor.pos()))

    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        t = e.type()
        if t == QEvent.KeyPress and e.key() != Qt.Key_Space and self.marker and self._mouse_over():
            from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QLineEdit, QPlainTextEdit, QTextEdit
            typing = isinstance(QApplication.focusWidget(), (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox))
            act = None if typing or e.modifiers() & (Qt.ControlModifier | Qt.AltModifier) else self.key_action(e)
            if act:
                if not e.isAutoRepeat():
                    self.shortcut.emit(act)
                return True
        if t in (QEvent.KeyPress, QEvent.KeyRelease) and e.key() == Qt.Key_Space:
            from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QLineEdit, QPlainTextEdit, QTextEdit
            typing = isinstance(QApplication.focusWidget(), (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox))
            if t == QEvent.KeyRelease and self.zooming:
                if not e.isAutoRepeat():
                    self.zooming = False
                    self._cursor_shape()
                return True
            if t == QEvent.KeyPress and self.marker and self._mouse_over() and not typing:
                if not e.isAutoRepeat() and not self.zooming:
                    self.zooming = True
                    self._cursor_shape()
                return True                                   # never "presses" a focused button
        return False

    def key_action(self, e) -> str | None:
        """The tool of a key: by the key's place on the keyboard (Windows), so the layout does not matter."""
        vk, k = e.nativeVirtualKey(), e.key()
        if vk in self.ZOOM_KEYS:
            return self.ZOOM_KEYS[vk]
        if k in (Qt.Key_Plus, Qt.Key_Equal):
            return "zoomin"
        if k in (Qt.Key_Minus, Qt.Key_Underscore):
            return "zoomout"
        letter = chr(vk) if 0x41 <= vk <= 0x5A else (chr(k) if Qt.Key_A <= k <= Qt.Key_Z else None)
        return self.KEYS.get(letter)

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
        if e.key() == Qt.Key_Escape and self.selected is not None:
            self.select_piece(None)
            return
        if e.key() == Qt.Key_Escape and self.tool:
            if self.meas:                                     # the one being measured, then the parts added up
                self.meas = {}
            else:
                self.parts = []
            self.measured.emit(self._sum_text()[0] if self.parts else "")
            self.viewport().update()
            return
        if e.key() == Qt.Key_Backspace and self.parts:        # the last part added out of the total
            self.undo_part()
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
        if e.button() == Qt.LeftButton and self.marker and self.dragMode() == QGraphicsView.NoDrag:
            p = self.mapToScene(e.position().toPoint())       # select tool: the piece clicked
            self.select_at((p.x(), p.y()))
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
        p.fillRect(rect, c["canvas"])                        # plain: no grid lines behind the marker

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
        if not self.marker or (not m.get("text") and not self.parts and self.selected is None):
            self.box.hide()
        if not self.marker or (not m and not self.parts):
            return
        v = lambda q: self.mapFromScene(QPointF(*q))
        pen = QPen(c["warn"], 2.2)
        p.setPen(pen)
        for part in self.parts:                                  # added up so far
            w = part["way"]
            for a, b in zip(w, w[1:]):
                p.drawLine(v(a), v(b))
            p.setBrush(c["warn"])
            for q in part["pts"]:
                p.drawEllipse(v(q), 3, 3)
            p.setBrush(Qt.NoBrush)
        if self.parts and not m.get("text"):
            last = self.parts[-1]["way"]
            self.box.show_value(*self._sum_text(), v(last[len(last) // 2]))
        if not m:
            return
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


class MenuButton(QToolButton):
    """A ribbon button that opens a list of choices when clicked: a small arrow in the accent colour under the
    label says there is more (no separate arrow part to aim at)."""

    def __init__(self, *a):
        super().__init__(*a)
        self.setProperty("hasMenu", True)
        self.setPopupMode(QToolButton.InstantPopup)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(themes.colour("accent") if self.isEnabled() else themes.colour("muted"))
        k = 5 if self.iconSize().width() >= 24 else 4          # smaller with the smaller ribbon
        cx, y = self.width() / 2, self.height() - k - 2
        p.drawPolygon(QPolygonF([QPointF(cx - k, y), QPointF(cx + k, y), QPointF(cx, y + k)]))
        p.end()


class Ribbon(QFrame):
    """Tool groups in the order of the work: order → model → nesting → output → system. Never squeezes its
    buttons: with less room the labels go (icons only, label in the tooltip), with still less it scrolls.
    A click on the L7 mark emits ``home`` (the day's overview)."""
    home = Signal()

    def __init__(self, groups, extra=()):
        super().__init__()
        self.setObjectName("ribbon")
        self.setFixedHeight(106)                                  # room for two-line labels and the group titles
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)    # never a scroll bar: a smaller size
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
        mark.setCursor(Qt.PointingHandCursor)
        mark.setToolTip(tr("Today"))
        mark.mousePressEvent = lambda e: self.home.emit() if e.button() == Qt.LeftButton else None
        self.brand_mark = mark
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
                b = MenuButton() if isinstance(fn, (dict, list, tuple)) else QToolButton()
                b.setObjectName("ribbonButton")
                b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                b.setIcon(icon(ic, acc if i == 0 else None))
                b.setIconSize(QSize(24, 24))
                b.setText(text)
                b.setToolTip(text)
                b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)   # never squeezed (no overlapping)
                if isinstance(fn, (dict, list, tuple)):      # several actions under one button: a click lists them
                    from PySide6.QtWidgets import QMenu
                    m = QMenu(b)
                    for sub_text, sub_fn in (fn["menu"] if isinstance(fn, dict) else fn):
                        m.addAction(sub_text, sub_fn)
                    b.setMenu(m)
                else:
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
        for m in ("small", "tiny", "icons", "full"):
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
            b.setIconSize(QSize(24, 24) if mode == "full" else QSize(16, 16) if mode == "tiny" else QSize(19, 19))
            b.setMinimumWidth(36 if mode == "icons" else 0)
            pad = " padding-bottom: 5px;" if isinstance(b, MenuButton) else ""     # room for its arrow
            b.setStyleSheet("" if mode == "full" else
                            f"QToolButton {{ font-size: 7.5pt; padding: 0px 1px;{pad} }}" if mode == "tiny" else
                            f"QToolButton {{ font-size: 8pt; padding: 1px 3px;{pad} }}")
            full = b.toolTip()
            words = full.split(" ")
            # labels of two words or more on two lines (Arabic labels are wider: they stay readable, no icons only)
            if mode in ("small", "tiny") and len(words) > 1 and (len(full) > 9 or mode == "tiny" or
                                                                 any("\u0600" <= ch <= "\u06ff" for ch in full)):
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
        w = self.scroll.viewport().width() - 12            # the room really there, with a little to spare
        order = ("full", "small", "tiny", "icons")
        want = next((m for m in order if w >= self.widths.get(m, 0)), "icons")
        if want != self.mode:
            self._apply(want)
        QTimer.singleShot(0, self._verify)

    def _verify(self):
        """After the new size is drawn: its real width (this PC's fonts); too wide after all -> the next smaller
        size, so there is never a scroll bar."""
        order = ("full", "small", "tiny", "icons")
        real = self._lay.sizeHint().width()
        self.widths[self.mode] = real
        too_wide = real > self.scroll.viewport().width() - 12
        if too_wide and self.mode != "icons":
            self._apply(order[order.index(self.mode) + 1])
            QTimer.singleShot(0, self._verify)
            return
        # icons only and still too wide (a very small window): a scroll bar, so nothing is out of reach
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded if too_wide else Qt.ScrollBarAlwaysOff)


class OrderBrowser(QWidget):
    """Order lines as a tree: line → the pieces of its marker; coloured by where the line is in the chain."""
    selected = Signal(object)
    remove = Signal(list)                              # right click → remove: the order lines (revision ids)
    reread = Signal(list)                              # right click → read their XML again with today's settings
    nesting_toggle = Signal()                          # the button on the nesting queue: start / stop the nesting
    queue_move = Signal(list, str)                     # right click on the queue: (revision ids, top/up/down/bottom)
    nesting_paused = False

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
        self.find.textChanged.connect(self._find_changed)
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(8, 8, 8, 6)
        bl.addWidget(self.find)
        lay.addWidget(box)
        self.heads_box = QWidget()                     # the lists' titles stay on top; only the list scrolls
        self.heads = QVBoxLayout(self.heads_box)
        self.heads.setContentsMargins(0, 0, 0, 4)
        self.heads.setSpacing(1)
        lay.addWidget(self.heads_box)
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
        self.tree.itemExpanded.connect(self._expanded)          # a closed list is filled when opened
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)   # Ctrl / Shift: several orders
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree, 1)
        self.rest = QWidget()                          # no list open: the titles stay at the top
        lay.addWidget(self.rest, 1)
        self.rows: list[dict] = []

    # separate lists, so a line that needs attention never holds up the others; work in GRAFIS and in the nesting
    # each in its own list (they run side by side)
    GROUPS = (("grafis", "In progress — GRAFIS", ("new", "grafis"), "warn"),
              ("queue", "In the nesting queue", ("queue",), "accent"),     # saved by GRAFIS, waiting their turn
              ("nest", "In progress — Nesting", ("nest",), "accent"),
              ("err", "Needs attention", ("err",), "bad"),
              ("cut", "Ready to cut", ("cut",), "ok"))

    def _queue_button(self, c):
        from PySide6.QtWidgets import QToolButton
        b = QToolButton()
        paused = self.nesting_paused
        col = c["ok"] if paused else c["bad"]
        b.setText(("▶ " + tr("Start nesting")) if paused else ("■ " + tr("Stop nesting")))
        b.setToolTip(tr("The nesting is stopped: the models saved by CAD wait here") if paused else
                     tr("Stop the nesting after the model being nested now"))
        b.setStyleSheet(f"QToolButton {{ color: {col.name()}; border: 1px solid {col.name()}; border-radius: 4px; "
                        f"padding: 1px 6px; font-weight: 700; }}")
        b.clicked.connect(self.nesting_toggle.emit)
        self.queue_button = b
        return b

    def resizeEvent(self, e):
        super().resizeEvent(e)
        narrow = self.width() < 250
        if getattr(self, "_narrow", None) not in (None, narrow) and getattr(self, "_counts", None) is not None:
            self._heads(self._counts, themes.colours())   # the queue button: full text / the sign only
        self._narrow = narrow

    def _heads(self, counts: dict, c):
        """The lists' titles above the list (always in sight): a click opens that list below, again closes it."""
        self._counts = counts
        while self.heads.count():
            w = self.heads.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for key, title, _, colour in self.GROUPS:
            col = c[colour]
            on = self.opened == {key}
            f = QFrame()
            f.setObjectName("listHead")
            f.setCursor(Qt.PointingHandCursor)
            rgb = f"{col.red()},{col.green()},{col.blue()}"
            f.setStyleSheet(f"QFrame#listHead {{ background: rgba({rgb},{95 if on else 40}); border-radius: 3px; "
                            f"border: 1px solid rgba({rgb},{230 if on else 110}); }} QLabel {{ background: "
                            f"transparent; border: none; color: {col.name()}; font-weight: 800; "
                            f"{'font-size: 8pt; ' if self.width() < 250 else ''}}}")
            h = QHBoxLayout(f)
            h.setContentsMargins(6, 5, 6, 5)
            h.setSpacing(4)
            t_ = QLabel(("▾ " if on else "▸ ") + tr(title))
            t_.setToolTip(tr(title))
            t_.setMinimumWidth(0)
            t_.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # cut, never pushing the count away
            h.addWidget(t_, 1)
            if key == "queue":                            # start / stop the nesting right on its list
                qb = self._queue_button(c)
                if self.width() < 250:                    # a narrow list: the sign only, the title stays readable
                    qb.setText(qb.text().split(" ")[0])
                h.addWidget(qb)
            h.addWidget(QLabel(str(counts.get(key, 0))))
            f.mousePressEvent = lambda e, k=key: self.open_list(k)
            self.heads.addWidget(f)

    def open_list(self, key: str):
        """A title clicked: its list opens below (the one open before closes); clicked again it closes."""
        if not hasattr(self, "opened"):
            self.opened = set()
        self.opened = set() if self.opened == {key} else {key}
        cur = self.tree.currentItem()
        for g in range(self.tree.topLevelItemCount()):    # set_rows reads which lists are open from the tree
            grp = self.tree.topLevelItem(g)
            self.tree.blockSignals(True)
            grp.setExpanded(grp.data(0, Qt.UserRole + 1) in self.opened)
            self.tree.blockSignals(False)
        self.set_rows(self.rows, keep=cur.data(0, Qt.UserRole) if cur else None)

    def _order_items(self):
        for g in range(self.tree.topLevelItemCount()):
            grp = self.tree.topLevelItem(g)
            for i in range(grp.childCount()):
                yield grp.child(i)

    def set_rows(self, rows: list[dict], keep: int | None = None):
        self.rows = rows
        if not hasattr(self, "opened"):
            self.opened: set[str] = set()                     # every list closed until the owner opens it
        for g in range(self.tree.topLevelItemCount()):       # remember which lists the owner opened
            grp = self.tree.topLevelItem(g)
            key = grp.data(0, Qt.UserRole + 1)
            (self.opened.add if grp.isExpanded() else self.opened.discard)(key)
        self.tree.blockSignals(True)
        self.tree.clear()
        self._bars = {}
        c = themes.colours()
        current, first = None, None
        counts, items = {}, {}
        for key, title, stages, colour in self.GROUPS:
            mine = [r for r in rows if ("queue" if r.get("nest_queue") and r["stage"] in ("grafis", "nest")
                                        else r["stage"]) in stages]
            if key == "queue":                            # in the order the nesting takes them
                mine.sort(key=lambda r: (r.get("queue_pos") is None, r.get("queue_pos") or 0))
            grp = QTreeWidgetItem([f"{tr(title)}", str(len(mine))])
            counts[key], items[key] = len(mine), grp
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
            if key not in self.opened and not self.find.text().strip():
                grp.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)   # filled when opened (thousands)
                if any(r["rid"] == keep for r in mine):
                    current = None
                continue
            for r in mine:
                col = c[STAGE_COLOUR[r["stage"]]]
                it = QTreeWidgetItem([(f"{r['queue_pos'] + 1}.  " if key == "queue" and r.get("queue_pos") is not None
                                       else "") + ltr(r["line"]),
                                      tr("Waiting for nesting") if r["stage"] == "nest" and
                                      r.get("waiting") else tr(STAGE_TEXT[r["stage"]])])
                it.setData(0, Qt.UserRole, r["rid"])
                pm = QPixmap(10, 10)
                pm.fill(col)
                it.setIcon(0, QIcon(pm))
                it.setForeground(1, col)
                f2 = it.font(1)
                f2.setBold(True)
                it.setFont(1, f2)
                it.setToolTip(0, "\n".join([r["line"]] + [tr_msg(m) for m in r["problems"][:5]]))
                grp.addChild(it)
                if key in self.opened:
                    first = first or it
                if r["rid"] == keep:
                    current = it
            grp.setExpanded(key in self.opened)
        self.tree.blockSignals(False)
        self._heads(counts, c)
        finding = bool(self.find.text().strip())
        one = next(iter(self.opened)) if len(self.opened) == 1 and not finding else None
        # one list open: the tree shows only its lines (its title is above); searching: every list with its title
        from PySide6.QtCore import QModelIndex
        self.tree.setRootIndex(self.tree.indexFromItem(items[one]) if one else QModelIndex())
        shown = bool(self.opened) or finding
        self.tree.setVisible(shown)
        self.rest.setVisible(not shown)
        self.count.setText(str(len(rows)))
        self._filter(self.find.text())
        if current is not None and current.parent().isExpanded():      # a closed list stays closed
            self.tree.setCurrentItem(current)
        elif first is not None and keep is None:
            self.tree.setCurrentItem(first)

    BUSY_TEXT = {"model": "Writing the model…", "robot": "Robot in GRAFIS…", "nesting": "Nesting…"}

    def update_busy(self, busy: dict):
        """A small progress bar under each order line the program is working on (services/activity.py)."""
        from PySide6.QtWidgets import QProgressBar
        bars = getattr(self, "_bars", {})
        for it in list(self._order_items()):
            rid = it.data(0, Qt.UserRole)
            b = busy.get(rid)
            have = bars.get(rid)
            if have is not None:
                try:
                    alive = have[0].parent() is it and have[1].isVisible() is not None
                except RuntimeError:                       # the row was rebuilt
                    alive = False
                if not alive:
                    bars.pop(rid, None)
                    have = None
            if b is None:
                if have is not None:
                    it.removeChild(have[0])
                    bars.pop(rid, None)
                continue
            if have is None:
                ch = QTreeWidgetItem()
                it.insertChild(0, ch)
                ch.setFirstColumnSpanned(True)
                w = QWidget()
                lay = QHBoxLayout(w)
                lay.setContentsMargins(4, 1, 8, 1)
                lay.setSpacing(6)
                lab = QLabel()
                lab.setObjectName("busyText")
                bar = QProgressBar()
                bar.setObjectName("busyBar")
                bar.setTextVisible(False)
                bar.setFixedHeight(6)
                lay.addWidget(lab)
                lay.addWidget(bar, 1)
                self.tree.setItemWidget(ch, 0, w)
                it.setExpanded(True)
                have = bars[rid] = (ch, bar, lab)
            _, bar, lab = have
            if b["what"] == "nesting":                     # it is nested now, not waiting any more
                it.setText(1, tr(STAGE_TEXT["nest"]))
            text = tr(self.BUSY_TEXT.get(b["what"], "Working…"))
            if b["progress"] is None:
                bar.setRange(0, 0)                         # moving: no estimate
                lab.setText(text)
            else:
                bar.setRange(0, 100)
                bar.setValue(int(b["progress"] * 100))
                lab.setText(f"{text} {int(b['progress'] * 100)}%")
        self._bars = bars

    def selected_rids(self) -> list[int]:
        return [it.data(0, Qt.UserRole) for it in self.tree.selectedItems() if it.data(0, Qt.UserRole) is not None]

    def _menu(self, pos):
        from PySide6.QtWidgets import QMenu
        it = self.tree.itemAt(pos)
        if it is not None and it.data(0, Qt.UserRole) is not None and not it.isSelected():
            self.tree.setCurrentItem(it)
        rids = self.selected_rids()
        if not rids:
            return
        m = QMenu(self)
        queued = {r["rid"] for r in self.rows if r.get("nest_queue")}
        if rids and all(r in queued for r in rids):      # the nesting queue: its order
            for text, how in (("To the top", "top"), ("One step up", "up"), ("One step down", "down"),
                              ("To the bottom", "bottom")):
                m.addAction(tr(text), lambda h=how: self.queue_move.emit(rids, h))
            m.addSeparator()
        m.addAction(tr("Read again with the current settings"), lambda: self.reread.emit(rids))
        m.addSeparator()
        m.addAction(tr("Delete order…") if len(rids) == 1 else f"{tr('Delete orders…')} ({len(rids)})",
                    lambda: self.remove.emit(rids))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def open_lists(self, keys=None):
        """Open these lists (all when None) and fill them now."""
        self.opened |= set(keys or [g[0] for g in self.GROUPS])
        for g in range(self.tree.topLevelItemCount()):
            grp = self.tree.topLevelItem(g)
            if grp.data(0, Qt.UserRole + 1) in self.opened:
                self.tree.blockSignals(True)
                grp.setExpanded(True)
                self.tree.blockSignals(False)
        cur = self.tree.currentItem()
        self.set_rows(self.rows, keep=cur.data(0, Qt.UserRole) if cur else None)

    def _find_changed(self, text):
        if bool(text.strip()) != getattr(self, "_finding", False):
            self._finding = bool(text.strip())         # searching: every list filled; done: closed lists empty
            self.set_rows(self.rows)
        self._filter(text)

    def _expanded(self, grp):
        key = grp.data(0, Qt.UserRole + 1)
        if key and key not in getattr(self, "opened", set()) and grp.childCount() == 0:
            self.opened.add(key)
            cur = self.tree.currentItem()
            QTimer.singleShot(0, lambda: self.set_rows(self.rows, keep=cur.data(0, Qt.UserRole) if cur else None))

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
        self.lay.setContentsMargins(8, 8, 8, 10)
        self.lay.setSpacing(4)
        from PySide6.QtWidgets import QLayout
        self.lay.setSizeConstraint(QLayout.SetMinimumSize)   # a short window: it scrolls, rows never overlap
        self.title = QLabel("—")
        self.title.setObjectName("bigTitle")
        self.pill = QLabel()
        self.pill.setObjectName("pill")
        hr = QHBoxLayout()
        hr.addWidget(self.title)
        hr.addStretch()
        hr.addWidget(self.pill)
        self.lay.addLayout(hr)
        self.buttons = {}                                 # the actions right under the title (owner: above the notes)
        self._full: list = []
        row = QHBoxLayout()                               # owner: the actions as one row of icons
        row.setSpacing(6)
        for key, text, primary, *ic in actions:
            if ic:                                        # an icon (its name on hovering)
                b = QToolButton()
                b.setIcon(icon(ic[0], themes.colour("accent") if primary else None))
                b.setIconSize(QSize(22, 22))
                b.setFixedHeight(38)
                b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                b.setMinimumWidth(30)
                b.setToolTip(text)
                b.setAccessibleName(text)
                b.setObjectName("inspectorIcon")
                row.addWidget(b)
            else:
                b = QPushButton(text)
                if primary:
                    b.setObjectName("primary")
                b.setToolTip(text)
                b.setMinimumWidth(0)                      # a narrow panel: the text is cut, never the panel held wide
                b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                self._full.append(b)
            self.buttons[key] = b
        if row.count():
            self.lay.addLayout(row)
        self.problems = QLabel()                          # first thing under the title: why it needs attention
        self.problems.setWordWrap(True)
        self.problems.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bad = themes.colour("bad")
        self.problems.setStyleSheet(f"color: {bad.name()}; border: 1px solid {bad.name()}; padding: 6px; "
                                    f"background: rgba({bad.red()},{bad.green()},{bad.blue()},0.10);")
        self.problems.hide()
        self.lay.addWidget(self.problems)
        self.defect = QLabel()                            # the distorted pieces, each defect circled in red
        self.defect.setAlignment(Qt.AlignCenter)
        self.defect.setCursor(Qt.PointingHandCursor)
        self.defect.setToolTip(tr("The distorted pieces — click to open the picture larger"))
        self.defect.setMinimumWidth(0)
        self.defect.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.defect.mousePressEvent = lambda _e: self._open_defect()
        self.defect.hide()
        self._defect_path = None
        self.lay.addWidget(self.defect)
        self.waiting = QLabel()                           # not an error: why the order waits (e.g. robot off)
        self.waiting.setWordWrap(True)
        warn = themes.colour("warn")
        self.waiting.setStyleSheet(f"color: {warn.name()}; border: 1px solid {warn.name()}; padding: 6px;")
        self.waiting.hide()
        self.lay.addWidget(self.waiting)
        for b in self._full:                              # "Robot: try again": under the reason it answers
            self.lay.addWidget(b)
        self.kv: dict[str, QLabel] = {}
        self._section(tr("The order"), [("customer", "Customer"), ("width", "Width"), ("quantity", "Quantity"),
                                         ("imported", "Imported")])                # owner: no Basic-Size, no fabric
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
        self.lay.addWidget(self._sec_title(tr("Stages")))
        self.stages = QGridLayout()
        self.stages.setVerticalSpacing(2)
        self.lay.addLayout(self.stages)
        self.lay.addWidget(self._sec_title(tr("X values in the model")))
        self.xgrid = QGridLayout()
        self.xgrid.setHorizontalSpacing(8)
        self.xgrid.setVerticalSpacing(1)
        self.lay.addLayout(self.xgrid)
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

    def _show_defect(self, path: str | None):
        self._defect_path = path
        if not path:
            self.defect.clear()
            self.defect.hide()
            return
        from PySide6.QtSvg import QSvgRenderer
        r = QSvgRenderer(path)
        if not r.isValid():
            self.defect.hide()
            return
        sz = r.defaultSize()
        w = max(120, min(self.viewport().width() - 24, 420))
        h = max(1, int(sz.height() * w / max(sz.width(), 1)))
        pm = QPixmap(w * 2, h * 2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        r.render(p)
        p.end()
        pm.setDevicePixelRatio(2.0)
        self.defect.setPixmap(pm)
        self.defect.setFixedHeight(h + 4)
        self.defect.show()

    def _open_defect(self):
        if self._defect_path:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._defect_path))

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
            self._show_defect(None)
            self.waiting.hide()
            return
        self.title.setText(ltr(r["line"]))
        col = c[STAGE_COLOUR[r["stage"]]]
        self.pill.setText(tr(STAGE_TEXT[r["stage"]]))
        self.pill.setStyleSheet(f"color: {col.name()}; border-color: {col.name()};")
        self.kv["customer"].setText(r.get("customer") or "—")

        self.kv["width"].setText(f"{r['width']:g} mm" if r["width"] else "—")
        self.kv["quantity"].setText(str(r["quantity"] or "—"))
        self.kv["imported"].setText((r["imported"] or "").replace("T", " ")[:16])
        self.kv["length"].setText(f"{r['length_mm'] / 1000:.2f} m" if r["length_mm"] else "—")
        self.kv["placed"].setText(str(r["placed"]) if r["placed"] is not None else "—")
        self.ring.set_value(r["efficiency_pct"])
        def order(x):                                     # part by part, X1, X2 … in order
            num = str(x.get("x", "")).lstrip("Xx")
            return (str(x.get("part", "")), int(num) if num.isdigit() else 10 ** 6, str(x.get("x", "")))
        xs = sorted(r["x_rows"], key=order)
        several = len({str(x.get("part", "")) for x in xs}) > 1
        for i, x in enumerate(xs[:60]):                   # owner: the name and the value only (X in the tooltip)
            xl = f"{x.get('part')}/{x['x']}" if several and x.get("part") else x["x"]
            name = (x.get("x_name") or "").strip()
            b = QLabel(name if name and name != "0" else xl)
            b.setObjectName("kvKey")
            b.setToolTip(xl)
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            v = QLabel(f"{x['value_mm']:g}")
            v.setObjectName("kvVal")
            v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.xgrid.addWidget(b, i, 0)
            self.xgrid.addWidget(v, i, 1)
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
        self.problems.setText("\n".join("⚠ " + tr_msg(p) for p in r["problems"][:6]))
        self.problems.setVisible(bool(r["problems"]))
        self._show_defect(r.get("defect_svg"))
        wait = r.get("waiting") if r["stage"] in ("grafis", "nest") else ""
        self.waiting.setText("⏳ " + wait if wait else "")
        self.waiting.setVisible(bool(wait))
        self.buttons.get("open_cut") and self.buttons["open_cut"].setEnabled(bool(r["outputs"]))
        self.buttons.get("renest") and self.buttons["renest"].setEnabled(True)   # from the base pattern
        self.buttons.get("nesting") and self.buttons["nesting"].setEnabled(bool(r["l7n"]))
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
        keys = {"select": "V", "pan": "H", "line": "L", "curve": "C", "sum": "S", "zoomin": "+", "zoomout": "−",
                "fit": "F"}
        for key, ic, tip in (("select", "select", "Select"), ("pan", "pan", "Move the view"),
                             ("line", "ruler", "Measure lengths"), ("curve", "curve", "Measure curves"),
                             ("sum", "sum", "Add measurements together (Backspace: remove the last, Esc: clear)"),
                             ("zoomin", "zoomin", "Zoom in"), ("zoomout", "zoomout", "Zoom out"),
                             ("fit", "fit", "Whole marker")):
            tip = tr(tip) + f"   [{keys[key]}]"
            b = QToolButton()
            b.setObjectName("tool")
            b.setIcon(icon(ic))
            b.setIconSize(QSize(18, 18))
            b.setToolTip(tip)
            b.setCheckable(key in ("select", "pan", "line", "curve", "sum"))
            self.tool_buttons[key] = b
            tl.addWidget(b)
        tb = self.tool_buttons
        tb["select"].setChecked(True)
        for key in ("select", "pan", "line", "curve"):
            tb[key].clicked.connect(lambda _=False, k=key: self.use_tool(k))
        tb["sum"].toggled.connect(self._adding)
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
        self.view.shortcut.connect(self._shortcut)

    def _shortcut(self, act: str):
        if act == "sum":
            self.tool_buttons["sum"].toggle()
        else:
            self.tool_buttons[act].click()

    def use_tool(self, key: str):
        """select / pan / line (measure lengths) / curve (measure curves): one at a time."""
        for k in ("select", "pan", "line", "curve"):
            self.tool_buttons[k].setChecked(k == key)
        self.view.set_pan(key == "pan")
        self.view.set_tool(key if key in ("line", "curve") else None)
        if key not in ("line", "curve"):
            self.tool_buttons["sum"].setChecked(False)

    def _adding(self, on: bool):
        """Add up: lines and curves measured one after the other are summed (switching tool keeps the total)."""
        self.view.set_adding(on)
        if on and self.view.tool is None:
            self.use_tool("line")
            self.tool_buttons["sum"].setChecked(True)

    def _measured(self, text: str):
        v, lab = self.hud_measure
        v.setText(ltr(text))
        v.setVisible(bool(text))
        lab.setVisible(bool(text))
        self.hud.adjustSize()
        self._place()

    def set_marker(self, marker, row=None, message=""):
        self.view.show_marker(marker, message)
        sheet = bool((marker or {}).get("sheet"))
        length = None if sheet else (marker or {}).get("length") or (row or {}).get("length_mm")
        eff = None if sheet else (row or {}).get("efficiency_pct")
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
