"""A distorted pattern never becomes cutting files (owner: "a deformed piece — e.g. the back with a spike — the
model goes straight to MDL-ERR, no cutting file").

The pieces are read from the marker LEVEL7 Nesting made of the model GRAFIS saved (each piece once, in its own
millimetres) and checked, for example but not only:
* the outline crosses itself (a piece folded over itself);
* a spike: a tip whose two sides close in under a narrow angle and open wider further from it (a wedge out of
  the piece or into it) — a real corner of a garment piece is never that sharp. A cut line (a slit of parallel
  sides, e.g. the jabzour opening of the front, about 1 mm wide) is part of the pattern, not a spike;
* a piece with almost no area (collapsed).
"""
from __future__ import annotations

import json
import math
import os
import shutil
import zipfile

# measured on real thobe patterns: the sharpest sound tip is a collar point (about 52°); the deformed back of
# 10041-44499-1_0 folds back in a hook of 26° (its sides 3.7 times further apart twice as far from the tip)
NEEDLE_DEG = 40.0          # a tip sharper than this …
NEEDLE_MM = 50.0           # … along this length on both sides (a notch is shorter, a corner wider) …
WEDGE_MM = 100.0           # … whose sides are further apart here: a wedge (a slit keeps its width)
SLIT_MM = 3.0              # sides this close together at NEEDLE_MM: a cut line of the pattern
MIN_AREA_MM2 = 100.0       # 1 cm²


def pieces_of_marker(path: str) -> list[tuple[str, list[tuple[float, float]]]]:
    """(name, outline) of each piece nested, once per piece and size."""
    with zipfile.ZipFile(path) as z:
        proj = json.loads(z.read("project.json").decode("utf-8"))["project"]
    pieces = {p["uid"]: p for p in proj.get("pieces", [])}
    seen, out = set(), []
    for pl in (proj.get("markers") or [{}])[-1].get("placements", []):
        key = (pl.get("piece_uid"), pl.get("size"))
        pc = pieces.get(pl.get("piece_uid"))
        g = (pc or {}).get("sizes", {}).get(pl.get("size"))
        if key in seen or not g or not g.get("cut"):
            continue
        seen.add(key)
        out.append((pc.get("name", "") or "?", [(float(x), float(y)) for x, y in g["cut"]]))
    return out


def _clean(pts):
    out = []
    for p in pts:
        if not out or math.dist(out[-1], p) > 1e-6:
            out.append(p)
    if len(out) > 1 and math.dist(out[0], out[-1]) <= 1e-6:
        out.pop()
    return out


def _area(pts) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2


def _cross(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _hits(p1, p2, q1, q2) -> bool:
    d1, d2 = _cross(q1, q2, p1), _cross(q1, q2, p2)
    d3, d4 = _cross(p1, p2, q1), _cross(p1, p2, q2)
    return ((d1 > 1e-9 and d2 < -1e-9) or (d1 < -1e-9 and d2 > 1e-9)) and \
        ((d3 > 1e-9 and d4 < -1e-9) or (d3 < -1e-9 and d4 > 1e-9))


def crosses_itself(pts) -> bool:
    n = len(pts)
    segs = [(pts[i], pts[(i + 1) % n]) for i in range(n)]
    boxes = [(min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1])) for a, b in segs]
    order = sorted(range(n), key=lambda i: boxes[i][0])
    for k, i in enumerate(order):                     # sweep on x: only segments that overlap are compared
        for j in order[k + 1:]:
            if boxes[j][0] > boxes[i][1]:
                break
            if abs(i - j) <= 1 or abs(i - j) == n - 1:
                continue                               # neighbours share a point
            if boxes[j][2] > boxes[i][3] or boxes[j][3] < boxes[i][2]:
                continue
            if _hits(*segs[i], *segs[j]):
                return True
    return False


def _along(pts, i, step, length):
    """The point ``length`` mm along the outline from point i (step +1 forwards / -1 backwards)."""
    n, left, cur = len(pts), length, pts[i]
    for k in range(1, n):
        nxt = pts[(i + step * k) % n]
        d = math.dist(cur, nxt)
        if d >= left:
            t = left / d
            return (cur[0] + (nxt[0] - cur[0]) * t, cur[1] + (nxt[1] - cur[1]) * t)
        left -= d
        cur = nxt
    return None


def needles(pts) -> int:
    """How many spike tips (wedges) the outline has; cut lines (slits of parallel sides) are not counted."""
    n, found, last = len(pts), 0, -10
    for i in range(n):
        a, b = _along(pts, i, -1, NEEDLE_MM), _along(pts, i, 1, NEEDLE_MM)
        if a is None or b is None:
            continue
        v1 = (a[0] - pts[i][0], a[1] - pts[i][1])
        v2 = (b[0] - pts[i][0], b[1] - pts[i][1])
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 < NEEDLE_MM * 0.8 or n2 < NEEDLE_MM * 0.8:
            continue                                   # the outline turns back on a short way: not a long tip
        cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
        if math.degrees(math.acos(cos)) >= NEEDLE_DEG or i - last <= 3:
            continue
        near = max(math.dist(a, b), 0.5)
        fa, fb = _along(pts, i, -1, WEDGE_MM), _along(pts, i, 1, WEDGE_MM)
        if fa is None or fb is None or math.dist(fa, fb) < max(near * 1.2, SLIT_MM):
            continue                                   # sides that keep their width: a cut line of the pattern
            #                                            (e.g. the jabzour slit), not a wedge
        found += 1
        last = i
    return found


def defects(name: str, outline) -> list[str]:
    pts = _clean(outline)
    if len(pts) < 3 or _area(pts) < MIN_AREA_MM2:
        return [f"{name}: the piece has (almost) no area"]
    out = []
    if crosses_itself(pts):
        out.append(f"{name}: its outline crosses itself")
    k = needles(pts)
    if k:
        out.append(f"{name}: {k} needle tip(s) — a spike or a slit in the outline")   # (a wedge, see needles)
    return out


def check_marker(path: str) -> list[str]:
    """The defects of the pieces in a marker; [] = the pattern is sound (or the marker cannot be read)."""
    try:
        pieces = pieces_of_marker(path)
    except (OSError, KeyError, ValueError, TypeError, zipfile.BadZipFile):
        return []
    out = []
    for name, outline in pieces:
        for d in defects(name, outline):
            if d not in out:
                out.append(d)
    return out


def _pts(raw) -> list[tuple[float, float]] | None:
    """[[x, y], …] or [{"x":…, "y":…}, …] as points; None when it is not an outline."""
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        return None
    try:
        return [(float(q["x"]), float(q["y"])) if isinstance(q, dict) else (float(q[0]), float(q[1])) for q in raw]
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _outlines_of_info(info: dict) -> list[tuple[str, list]]:
    """The piece outlines in ``level7-cli info --json`` (when this LEVEL7 Nesting gives them; its "sizes" may be a
    list of names, a list of sizes or a dict of them)."""
    out = []
    pieces = info.get("pieces") if isinstance(info, dict) else None
    for p in pieces if isinstance(pieces, list) else []:
        if not isinstance(p, dict):
            continue
        sizes = p.get("sizes")
        sizes = list(sizes.values()) if isinstance(sizes, dict) else sizes if isinstance(sizes, list) else []
        shapes = [p.get("cut"), p.get("outline")] + [g.get("cut") or g.get("outline") for g in sizes
                                                     if isinstance(g, dict)]
        for raw in shapes:
            pts = _pts(raw)
            if pts:
                out.append((str(p.get("name") or "?"), pts))
                break
    return out


SOUND: set = set()                                # contents found sound (the robot's check, then the nesting's)
BAD: dict = {}                                     # contents found distorted: their defects
LAST = {"text": ""}                                # what the last check_model did (said with the robot's result)


def _sound_kept(ws) -> set:
    try:
        return set(ws.db.get_setting("pattern.sound") or [])
    except Exception:  # noqa: BLE001
        return set()


def known_result(ws, path: str) -> list[str] | None:
    """The check of this content done before: [] sound, the defects, or None (not checked yet)."""
    try:
        from .grafis_robot import file_sha
        sha = file_sha(path)
    except OSError:
        return None
    if sha in BAD:
        return BAD[sha]
    if sha in SOUND or sha in _sound_kept(ws):
        return []
    return None


def check_model(ws, path: str, width_mm: float | None = None) -> list[str]:
    """See _check_model; a failure of the check itself never stops the model (the check after the nesting
    still holds). A content checked before is not checked again (kept over a restart when sound)."""
    LAST["text"] = ""
    if not ws.db.get_setting("cutting.pattern_check", True):
        LAST["text"] = "pattern not checked: the check is off"
        return []
    try:
        from .grafis_robot import file_sha
        sha = file_sha(path)
    except OSError:
        sha = None
    if sha and sha in BAD:
        LAST["text"] = "pattern checked before: distorted"
        return list(BAD[sha])
    if sha and (sha in SOUND or sha in _sound_kept(ws)):
        LAST["text"] = "pattern checked before: sound"
        return []
    try:
        found = _check_model(ws, path, width_mm)
    except Exception as e:  # noqa: BLE001 - only a check
        LAST["text"] = f"pattern not checked: {type(e).__name__}: {e}"
        return []
    if sha and LAST["text"].startswith("pattern checked"):
        if found:
            BAD[sha] = list(found)
        else:
            SOUND.add(sha)                            # the nesting does not check it again
            try:
                kept = list(_sound_kept(ws))[-1999:] + [sha]
                ws.db.set_setting("pattern.sound", kept)
            except Exception:  # noqa: BLE001
                pass
    return found


def _size_of(path: str) -> str | None:
    """The size key of a GRAFIS model as LEVEL7 Nesting names it ("base:<size>") — for an older LEVEL7 Nesting whose
    info gives no size names."""
    try:
        from ..grafis.container import read_container
        from ..grafis.textsections import read_text
        sizes = read_text(read_container(path)).sizes
        return f"base:{sizes[0].name}" if sizes else None
    except Exception:  # noqa: BLE001
        return None


def _check_model(ws, path: str, width_mm: float | None = None) -> list[str]:
    """The pattern as CAD saved it, checked before it waits for the nesting (owner: "the files distorted in CAD
    still reach the nesting queue instead of Needs attention"). The pieces are read by LEVEL7 Nesting: from
    ``info --json`` when it gives the outlines, else from a quick marker made for the check only (thrown away).
    [] = sound, or it could not be checked (then the check after the nesting still holds)."""
    import subprocess
    import tempfile

    from .auto_cut import NestingNotConfigured, _info, _run, nesting_command
    if not ws.db.get_setting("cutting.pattern_check", True):
        LAST["text"] = "pattern not checked: the check is off"
        return []
    if not os.path.isfile(path):
        LAST["text"] = "pattern not checked: the model is not there"
        return []
    try:
        cmd, env = nesting_command(ws)
    except NestingNotConfigured:
        LAST["text"] = "pattern not checked: LEVEL7 Nesting not found"
        return []
    info = _info(cmd, env, path)
    pieces = _outlines_of_info(info)
    how = "info"
    if not pieces:
        how = "marker"
        d = tempfile.mkdtemp(prefix="l7check_")
        try:
            out = os.path.join(d, "check.l7n")
            log: list[str] = []
            # one garment of the model's size: without --sizes LEVEL7 Nesting makes no marker ("nothing to nest:
            # set size quantities") and the check never ran
            names = [n for n in (info.get("size_names") or []) if isinstance(n, str)] if isinstance(info, dict) \
                else []
            size = names[0] if names else _size_of(path)
            args = cmd + ["nest", path, "--width", f"{width_mm or 1500:g}", "--time", "1", "-o", out]
            if size:
                args += ["--sizes", f"{size}=1"]
            if _run(args, env, log, 240) != 0 or not os.path.isfile(out):
                tail = " ".join(" ".join(log[1:]).split())[-160:]
                LAST["text"] = f"pattern not checked: the check marker was not made ({tail})"
                return []
            pieces = pieces_of_marker(out)
        except (OSError, subprocess.SubprocessError, KeyError, ValueError, TypeError, zipfile.BadZipFile) as e:
            LAST["text"] = f"pattern not checked: {type(e).__name__}: {e}"
            return []
        finally:
            shutil.rmtree(d, ignore_errors=True)
    found = []
    seen = set()
    for name, outline in pieces:
        if name in seen:
            continue                                  # each piece once (its sizes are the same pattern)
        seen.add(name)
        for dd in defects(name, outline):
            if dd not in found:
                found.append(dd)
    LAST["text"] = f"pattern checked: {len(seen)} pieces ({how})" + (" sound" if not found else "")
    return found


def err_folder(ws) -> str:
    """MDL-ERR: next to the GRAFIS input folder (MDL), or the folder chosen."""
    chosen = ws.db.get_setting("grafis.mdl_err")
    if chosen:
        return chosen
    inp = ws.db.get_setting("grafis.input")
    return os.path.join(os.path.dirname(inp.rstrip("/\\")), "MDL-ERR") if inp else \
        os.path.join(ws.folder, "grafis", "MDL-ERR")


def to_err(ws, model: str, problems: list[str]) -> str | None:
    """The model with a distorted pattern into MDL-ERR, with <name>.txt saying which piece and why."""
    from ..storage.db import now
    d = err_folder(ws)
    try:
        os.makedirs(d, exist_ok=True)
        stem = os.path.splitext(os.path.basename(model))[0]
        with open(os.path.join(d, stem + ".txt"), "w", encoding="utf-8-sig") as fh:
            fh.write(f"LEVEL7 MTM — pattern distorted / تشوه في البترون\n{now()}\n\n")
            for p in problems:
                fh.write(f"- {p}\n")
        if not os.path.isfile(model):
            return None
        target = os.path.join(d, os.path.basename(model))
        shutil.move(model, target)
        return target
    except OSError:
        return None
