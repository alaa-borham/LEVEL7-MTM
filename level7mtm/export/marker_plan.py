"""The printed plan of a marker of several orders (owner: the Bullmer ISO file carries no text, so on the cutting
table the pieces of the orders nested together could not be told apart): the marker drawn to scale over as many
pages as it needs, each piece filled with its order's colour and marked with its order, a legend of the orders.
Read only from the ``.l7n`` LEVEL7 Nesting saved."""
from __future__ import annotations

import json
import math
import zipfile

from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .pdf import _font, _shape

# light fills that stay apart also on a grey printout (every piece also carries its order in text)
COLOURS = ["#9ecae1", "#fdae6b", "#a1d99b", "#bcbddc", "#fc9272", "#fdd0a2", "#c7e9c0", "#dadaeb",
           "#6baed6", "#e6550d", "#31a354", "#756bb1"]


def order_of(size: str) -> str:
    """The order a piece of a marker of several orders belongs to: its size is "<order>:<size>" or
    "base:<order>:<size>" (LEVEL7 Nesting ``--with``); "" for a marker of one order."""
    s = size[5:] if size.startswith("base:") else size
    return s.split(":", 1)[0] if ":" in s else ""


def _transform(pts, rotation, mirrored, dx, dy, sx=1.0, sy=1.0):
    a = math.radians(rotation)
    c, s = math.cos(a), math.sin(a)
    if abs(rotation % 90.0) < 1e-12:
        c, s = round(c), round(s)
    return [(sx * (c * (-x if mirrored else x) - s * y) + dx, sy * (s * (-x if mirrored else x) + c * y) + dy)
            for x, y in pts]


def read_plan(path: str) -> dict:
    """{"width", "length", "pieces": [{"order", "name", "cut"}]} of the last marker of the ``.l7n``."""
    with zipfile.ZipFile(path) as z:
        proj = json.loads(z.read("project.json").decode("utf-8"))["project"]
    m = proj["markers"][-1]
    pieces = {p["uid"]: p for p in proj.get("pieces", [])}
    out = []
    for pl in m.get("placements", []):
        if not pl.get("placed", True):
            continue
        pc = pieces.get(pl.get("piece_uid")) or {}
        g = pc.get("sizes", {}).get(pl.get("size"))
        if not g or not g.get("cut"):
            continue
        out.append({"order": order_of(pl.get("size", "")), "name": pc.get("name", ""),
                    "cut": _transform(g["cut"], pl.get("rotation", 0.0), pl.get("mirrored", False),
                                      pl.get("x", 0.0), pl.get("y", 0.0), pl.get("sx", 1.0), pl.get("sy", 1.0))})
    length = float(m.get("length") or 0.0) or max((x for p in out for x, _ in p["cut"]), default=0.0)
    return {"width": float((m.get("fabric") or {}).get("width") or 0.0), "length": length, "pieces": out}


def _centre(pts):
    """A point well inside the piece (the centre of its area; the middle of its box for an odd shape)."""
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        k = x1 * y2 - x2 * y1
        a += k
        cx += (x1 + x2) * k
        cy += (y1 + y2) * k
    if abs(a) < 1e-9:
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    return cx / (3 * a), cy / (3 * a)


def write_plan(l7n: str, out: str, title: str, orders: list[dict] | None = None) -> str:
    """The plan as a PDF (A3 landscape). ``orders``: [{"order", "text"}] for the legend (the order's customer,
    garments …), in the order of the colours; orders found on the marker only are added."""
    plan = read_plan(l7n)
    seen = [o["order"] for o in orders or []]
    for p in plan["pieces"]:
        if p["order"] not in seen:
            seen.append(p["order"])
    colour = {o: COLOURS[i % len(COLOURS)] for i, o in enumerate(seen)}
    text_of = {o["order"]: o.get("text", "") for o in orders or []}
    font = _font()
    pw, ph = landscape(A3)
    margin, head = 12 * mm, 26 * mm
    W, L = max(plan["width"], 1.0), max(plan["length"], 1.0)
    scale = (ph - 2 * margin - head) / W                      # points per mm: the fabric width fills the page
    span = (pw - 2 * margin) / scale                          # fabric millimetres on one page
    pages = max(1, math.ceil(L / span))
    c = canvas.Canvas(out, pagesize=(pw, ph))
    c.setTitle(title)
    for k in range(pages):
        x0 = k * span
        c.setFont(font, 13)
        c.drawString(margin, ph - margin - 12, _shape(f"{title}   —   {k + 1} / {pages}"))
        c.setFont(font, 9)
        c.drawString(margin, ph - margin - 26,
                     f"{W / 1000:.2f} m x {L / 1000:.2f} m   ·   1:{1 / (scale / mm):.0f}   ·   "
                     f"{x0 / 1000:.2f} – {min(L, x0 + span) / 1000:.2f} m")
        lx = pw * 0.45                                        # the legend: one box per order
        for i, o in enumerate(seen):
            bx = lx + (i % 4) * 58 * mm
            by = ph - margin - 10 - (i // 4) * 12
            c.setFillColor(colour[o])
            c.rect(bx, by - 2, 10, 8, fill=1, stroke=1)
            c.setFillColor("black")
            c.drawString(bx + 13, by - 1, _shape(f"{o.replace('_', '/')}  {text_of.get(o, '')}"[:46]))
        oy = margin                                           # fabric: x along the page, y up
        c.setStrokeColor("black")
        c.setLineWidth(0.6)
        c.rect(margin, oy, min(span, L - x0) * scale, W * scale, fill=0, stroke=1)
        for p in plan["pieces"]:
            xs = [x for x, _ in p["cut"]]
            if max(xs) < x0 or min(xs) > x0 + span:
                continue
            path = c.beginPath()
            pts = [(margin + (x - x0) * scale, oy + y * scale) for x, y in p["cut"]]
            path.moveTo(*pts[0])
            for q in pts[1:]:
                path.lineTo(*q)
            path.close()
            c.saveState()
            cl = c.beginPath()                                 # a piece on two pages: each page its part
            cl.rect(margin, oy, span * scale, W * scale)
            c.clipPath(cl, stroke=0, fill=0)
            c.setFillColor(colour.get(p["order"], "#dddddd"))
            c.setLineWidth(0.4)
            c.drawPath(path, fill=1, stroke=1)
            cx, cy = _centre(pts)
            ys = [q[1] for q in pts]
            size = max(5.0, min(11.0, (max(ys) - min(ys)) / 3, (max(q[0] for q in pts) - min(q[0] for q in pts)) / 6))
            c.setFillColor("black")
            c.setFont(font, size)
            c.drawCentredString(cx, cy, p["order"].replace("_", "/") or p["name"])
            if p["order"] and p["name"]:
                c.setFont(font, size * 0.7)
                c.drawCentredString(cx, cy - size, _shape(p["name"])[:24])
            c.restoreState()
        c.showPage()
    c.save()
    return out
