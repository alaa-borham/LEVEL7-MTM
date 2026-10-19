"""Fully automatic chain after GRAFIS: checked SCB → LEVEL7 Nesting (nest at the order's fabric width and
quantity) → PLT / ISO files for the plotter / cutter. Nobody opens a file.

LEVEL7 Nesting is driven through its own command line (``level7-cli``), which is installed with it:

    level7 nest  FILE.scb --width W [--sizes SIZE=QTY] --time T -o FILE.l7n      (exit 0 = valid marker)
    level7 export FILE.l7n --format plt --plt-preset algotex_grafis -o FILE.plt
    level7 export FILE.l7n --format iso --iso-preset bullmer_grafis -o FILE.iso

Location of the command line (first found): setting ``nesting.cli`` · ``C:\\Program Files\\LEVEL7 Nesting\\level7-cli.exe``
· ``<any drive>:\\LEVEL7 Nesting\\level7-cli.exe``
· source checkout in ``LEVEL7_NESTING_SRC`` (development / tests).
Note from LEVEL7 Nesting: PLT and ISO profiles are compatible with GRAFIS' files but are not yet verified by a
1:1 test cut on the Algotex / Bullmer ("device_tested: false").
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from ..storage.db import now

DEFAULT_CLI = [r"C:\Program Files\LEVEL7 Nesting\level7-cli.exe",
               r"C:\Program Files (x86)\LEVEL7 Nesting\level7-cli.exe"]


class NestingNotConfigured(RuntimeError):
    pass


def nesting_command(ws) -> tuple[list[str], dict]:
    env = dict(os.environ)
    cli = ws.db.get_setting("nesting.cli")
    drives = [f"{d}:\\" for d in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"] if os.name == "nt" else []
    found = [os.path.join(d, sub, "level7-cli.exe") for d in drives
             for sub in ("LEVEL7 Nesting", r"Program Files\LEVEL7 Nesting")]   # installed on another drive
    for c in ([cli] if cli else []) + DEFAULT_CLI + found:
        if c and os.path.isfile(c):
            return [c], env
    src = os.environ.get("LEVEL7_NESTING_SRC") or ws.db.get_setting("nesting.src")
    if src and os.path.isdir(os.path.join(src, "level7")) and not getattr(sys, "frozen", False):
        env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
        return [sys.executable, "-m", "level7.cli"], env
    raise NestingNotConfigured("LEVEL7 Nesting command line (level7-cli.exe) not found — set its path in the "
                               "GRAFIS markers screen")


KNOWN_FORMATS = ["plt", "iso", "dxf"]


def export_formats(ws) -> list[str]:
    """The cutting file formats LEVEL7 Nesting can write (``export --help``: --format {plt,iso,dxf,…}); a newer
    LEVEL7 Nesting with more formats shows them too. Falls back to PLT / ISO / DXF."""
    try:
        cmd, env = nesting_command(ws)
        p = subprocess.run(cmd + ["export", "--help"], env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        m = re.search(r"--format\s+\{([A-Za-z0-9_,]+)\}", p.stdout + p.stderr)
        if m:
            got = [f.strip().lower() for f in m.group(1).split(",") if f.strip()]
            ws.db.set_setting("nesting.formats", got)
            return got
    except (NestingNotConfigured, OSError, subprocess.SubprocessError, ValueError):
        pass
    return ws.db.get_setting("nesting.formats") or list(KNOWN_FORMATS)


def customer_formats(ws, cid: str) -> list[str] | None:
    """The cutting formats chosen for the customer / product (a product without its own: its customer's);
    None = as on the markers page."""
    from .customers import get
    c = get(ws, cid)
    for one in [cid] + ([c["parent"]] if c and c.get("parent") else []):
        got = ws.db.get_setting(f"cutting.formats@{one}")
        if got:
            return list(got)
    return None


def settings(ws, cid: str | None = None) -> dict:
    return {"formats": (customer_formats(ws, cid) if cid else None) or ws.db.get_setting("cutting.formats",
                                                                                          ["plt", "iso"]),
            "plt_preset": ws.db.get_setting("cutting.plt_preset", "algotex_grafis"),
            "iso_preset": ws.db.get_setting("cutting.iso_preset", "bullmer_grafis"),
            "time": float(ws.db.get_setting("cutting.time_s", 60)),
            "out": ws.db.get_setting("cutting.output") or os.path.join(ws.folder, "cutting"),
            "enabled": bool(ws.db.get_setting("cutting.auto", True)),
            "remnant": bool(ws.db.get_setting("cutting.remnant", True))}


def _run(cmd, env, log) -> int:
    log.append("$ " + " ".join(cmd))
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=3600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log.append(p.stdout[-4000:])
    if p.stderr.strip():
        log.append(p.stderr[-2000:])
    return p.returncode


def _info(cmd, env, path) -> dict:
    """``level7-cli info --json`` (newer LEVEL7 Nesting): size names, features; {} for an older one."""
    try:
        p = subprocess.run(cmd + ["info", "--json", path], env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return json.loads(p.stdout) if p.returncode == 0 else {}
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return {}                                   # older LEVEL7 Nesting: no --json


def _size_key(cmd, env, path, guess, log, info=None) -> str:
    """The size key LEVEL7 Nesting uses for this file (``info --json``, newer LEVEL7 Nesting); else our guess."""
    names = (info if info is not None else _info(cmd, env, path)).get("size_names", [])
    key = guess if not names or guess in names else (names[0] if len(names) == 1 else guess)
    if names:
        log.append(f"LEVEL7 Nesting sizes: {names} -> {key}")
    return key


def auto_cut(ws, chk, scb_path: str, force: bool = False) -> dict:
    """Nest the checked SCB and write the cutting files. Returns {status, outputs, log, ...}.
    ``force``: nest again (e.g. with a longer nesting time) although cutting files exist."""
    from .activity import working
    with working([ln["revision_id"] for ln in chk.lines], "nesting", settings(ws)["time"] + 15):
        return _auto_cut(ws, chk, scb_path, force)


def _auto_cut(ws, chk, scb_path: str, force: bool = False) -> dict:
    try:
        return _auto_cut_inner(ws, chk, scb_path, force)
    except (OSError, subprocess.SubprocessError) as e:   # LEVEL7 Nesting could not be started / ran too long
        result = {"status": "failed", "outputs": [], "log": [],
                  "message": f"LEVEL7 Nesting could not run: {e or type(e).__name__}"}
        return _save(ws, chk, scb_path, result)


def _auto_cut_inner(ws, chk, scb_path: str, force: bool = False) -> dict:
    custs = {ws.customer_of(ln["revision_id"]) for ln in chk.lines}
    st = settings(ws, custs.pop() if len(custs) == 1 else None)   # the formats of the order's customer
    dup = ws.db.one("SELECT * FROM cutting_jobs WHERE sha256=? AND status='done'", (chk.sha256,))
    if dup and not force and all(os.path.isfile(p) for p in json.loads(dup["outputs"])):
        return {"status": "duplicate", "outputs": json.loads(dup["outputs"]), "log": []}
    log: list[str] = []
    result = {"status": "failed", "outputs": [], "log": log}
    widths = {ws.revision(ln["revision_id"])["normalized"].get("fabric_width_mm") for ln in chk.lines}
    qty = sum(int(ln["quantity"] or 0) for ln in chk.lines)
    if len(widths) != 1 or None in widths:
        result["message"] = f"order lines have no single fabric width: {sorted(map(str, widths))}"
        return _save(ws, chk, scb_path, result)
    width = widths.pop()
    sizes = chk.info.get("sizes", [])
    try:
        cmd, env = nesting_command(ws)
    except NestingNotConfigured as e:
        result["status"], result["message"] = "not_configured", str(e)
        return _save(ws, chk, scb_path, result)
    os.makedirs(st["out"], exist_ok=True)
    stem = os.path.join(st["out"], os.path.splitext(os.path.basename(scb_path))[0])
    nest = cmd + ["nest", scb_path, "--width", f"{width:g}", "--time", f"{st['time']:g}", "-o", stem + ".l7n"]
    finfo = _info(cmd, env, scb_path)
    if len(sizes) == 1:
        key = _size_key(cmd, env, scb_path, sizes[0], log, finfo)
        nest += ["--sizes", f"{key}={qty}"]              # garments from the order, not from the GRAFIS marker
    if st["remnant"]:
        if "remnant" in finfo.get("features", []):
            nest += ["--remnant"]                       # small pieces together: one large piece of fabric left
        else:
            log.append("LEVEL7 Nesting too old for --remnant (update it): marker as nested")
            result["remnant"] = {"done": False, "why": "old LEVEL7 Nesting"}
    rc = _run(nest, env, log)
    out_text = "\n".join(log)
    m = re.search(r'"length_mm":\s*([\d.]+)', out_text)
    e = re.search(r'"efficiency_total_width_pct":\s*([\d.]+)', out_text)
    placed = re.search(r'"placed":\s*(\d+)', out_text)
    rm = re.search(r"^remnant:\s*(\{.*\})\s*$", out_text, re.M)
    if rm:
        try:
            result["remnant"] = json.loads(rm.group(1))
        except ValueError:
            pass
    result.update({"width_mm": width, "garments": qty, "length_mm": float(m.group(1)) if m else None,
                   "efficiency_pct": float(e.group(1)) if e else None,
                   "placed": int(placed.group(1)) if placed else None, "l7n": stem + ".l7n"})
    if rc != 0 or "UNPLACED:" in out_text or not os.path.exists(stem + ".l7n"):
        result["message"] = "nesting did not produce a valid marker: " + _why(out_text, rc)
        return _save(ws, chk, scb_path, result)
    for fmt in st["formats"]:
        target = f"{stem}.{fmt}"
        args = cmd + ["export", stem + ".l7n", "--format", fmt, "-o", target]
        if fmt == "plt":
            args += ["--plt-preset", st["plt_preset"]]
        elif fmt == "iso":
            args += ["--iso-preset", st["iso_preset"]]
        if _run(args, env, log) != 0 or not os.path.exists(target):
            result["message"] = f"export {fmt} failed: " + _why("\n".join(log), 1)
            return _save(ws, chk, scb_path, result)
        result["outputs"].append(target)
    result["status"] = "done"
    result["device_tested"] = False
    return _save(ws, chk, scb_path, result)


def _why(text: str, rc: int) -> str:
    """The lines of LEVEL7 Nesting's output that say why (pieces not placed, the error), short enough to read."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    unplaced = [ln[len("UNPLACED:"):].strip() for ln in lines if ln.startswith("UNPLACED:")]
    if unplaced:
        return (f"{len(unplaced)} piece(s) not placed — " + "; ".join(unplaced[:4])
                + (" …" if len(unplaced) > 4 else ""))
    err = [ln for ln in lines if ln.lower().startswith(("error", "traceback")) or "Error:" in ln]
    if err:
        return err[-1][:300]
    if rc != 0:
        return f"LEVEL7 Nesting stopped with exit code {rc}" + (f" — {lines[-1][:200]}" if lines else "")
    return "no marker file was written"


def _save(ws, chk, scb_path, result) -> dict:
    with ws.db.tx() as c:
        c.execute("INSERT INTO cutting_jobs(sha256, scb_path, created_at, status, outputs, info, log) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (chk.sha256, scb_path, now(), result["status"], json.dumps(result["outputs"]),
                   json.dumps({k: v for k, v in result.items() if k not in ("log", "outputs")}, ensure_ascii=False),
                   "\n".join(result["log"])[-20000:]))
        if result["status"] == "done":
            for ln in chk.lines:
                c.execute("UPDATE revisions SET status='completed' WHERE id=? AND status NOT IN "
                          "('superseded','cancelled')", (ln["revision_id"],))
        ws.db.audit(c, ws.actor, f"cutting.{result['status']}", "file", os.path.basename(scb_path),
                    {"outputs": result["outputs"], "message": result.get("message", ""),
                     "length_mm": result.get("length_mm")})
    if result["status"] == "done":
        from .done_folder import move_finished
        result["xml_done"] = move_finished(ws, [ln["revision_id"] for ln in chk.lines])
    return result
