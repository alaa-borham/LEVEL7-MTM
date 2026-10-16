"""Fully automatic chain after GRAFIS: checked SCB → LEVEL7 Nesting (nest at the order's fabric width and
quantity) → PLT / ISO files for the plotter / cutter. Nobody opens a file.

LEVEL7 Nesting is driven through its own command line (``level7-cli``), which is installed with it:

    level7 nest  FILE.scb --width W [--sizes SIZE=QTY] --time T -o FILE.l7n      (exit 0 = valid marker)
    level7 export FILE.l7n --format plt --plt-preset algotex_grafis -o FILE.plt
    level7 export FILE.l7n --format iso --iso-preset bullmer_grafis -o FILE.iso

Location of the command line (first found): setting ``nesting.cli`` · ``C:\\Program Files\\LEVEL7 Nesting\\level7-cli.exe``
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
    for c in ([cli] if cli else []) + DEFAULT_CLI:
        if c and os.path.isfile(c):
            return [c], env
    src = os.environ.get("LEVEL7_NESTING_SRC") or ws.db.get_setting("nesting.src")
    if src and os.path.isdir(os.path.join(src, "level7")) and not getattr(sys, "frozen", False):
        env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
        return [sys.executable, "-m", "level7.cli"], env
    raise NestingNotConfigured("LEVEL7 Nesting command line (level7-cli.exe) not found — set its path in the "
                               "GRAFIS markers screen")


def settings(ws) -> dict:
    return {"formats": ws.db.get_setting("cutting.formats", ["plt", "iso"]),
            "plt_preset": ws.db.get_setting("cutting.plt_preset", "algotex_grafis"),
            "iso_preset": ws.db.get_setting("cutting.iso_preset", "bullmer_grafis"),
            "time": float(ws.db.get_setting("cutting.time_s", 60)),
            "out": ws.db.get_setting("cutting.output") or os.path.join(ws.folder, "cutting"),
            "enabled": bool(ws.db.get_setting("cutting.auto", True))}


def _run(cmd, env, log) -> int:
    log.append("$ " + " ".join(cmd))
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=3600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log.append(p.stdout[-4000:])
    if p.stderr.strip():
        log.append(p.stderr[-2000:])
    return p.returncode


def auto_cut(ws, chk, scb_path: str) -> dict:
    """Nest the checked SCB and write the cutting files. Returns {status, outputs, log, ...}."""
    st = settings(ws)
    dup = ws.db.one("SELECT * FROM cutting_jobs WHERE sha256=? AND status='done'", (chk.sha256,))
    if dup:
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
    if len(sizes) == 1:
        nest += ["--sizes", f"{sizes[0]}={qty}"]          # garments from the order, not from the GRAFIS marker
    rc = _run(nest, env, log)
    out_text = "\n".join(log)
    m = re.search(r'"length_mm":\s*([\d.]+)', out_text)
    e = re.search(r'"efficiency_total_width_pct":\s*([\d.]+)', out_text)
    placed = re.search(r'"placed":\s*(\d+)', out_text)
    result.update({"width_mm": width, "garments": qty, "length_mm": float(m.group(1)) if m else None,
                   "efficiency_pct": float(e.group(1)) if e else None,
                   "placed": int(placed.group(1)) if placed else None, "l7n": stem + ".l7n"})
    if rc != 0 or "UNPLACED:" in out_text or not os.path.exists(stem + ".l7n"):
        result["message"] = "nesting did not produce a valid marker (see log)"
        return _save(ws, chk, scb_path, result)
    for fmt in st["formats"]:
        target = f"{stem}.{fmt}"
        args = cmd + ["export", stem + ".l7n", "--format", fmt, "-o", target]
        if fmt == "plt":
            args += ["--plt-preset", st["plt_preset"]]
        elif fmt == "iso":
            args += ["--iso-preset", st["iso_preset"]]
        if _run(args, env, log) != 0 or not os.path.exists(target):
            result["message"] = f"export {fmt} failed (see log)"
            return _save(ws, chk, scb_path, result)
        result["outputs"].append(target)
    result["status"] = "done"
    result["device_tested"] = False
    return _save(ws, chk, scb_path, result)


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
    return result
