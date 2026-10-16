"""Error folder ("ER"): every file that stops the automatic chain lands here with a text file saying why.

* XML that failed to import, or whose order line got no GRAFIS model (blocked / not configured): the XML is
  copied (the ERP original is never moved) and ``<name>.txt`` explains the reason;
* SCB from GRAFIS that is not handed over (no matching order, findings to review) or whose nesting / cutting
  failed: the SCB is moved out of the GRAFIS output folder into ER, next to ``<name>.txt``.

Without an error folder set nothing is moved (the screens still show the reason).
"""
from __future__ import annotations

import os
import shutil

from ..storage.db import now


def folder(ws) -> str | None:
    f = ws.db.get_setting("errors.folder")
    return f or None


def _free(path: str) -> str:
    base, ext = os.path.splitext(path)
    k, out = 1, path
    while os.path.exists(out):
        out = f"{base}_{k}{ext}"
        k += 1
    return out


def report(ws, name: str, problems: list[str], kind: str, copy_file: str | None = None,
           move_file: str | None = None) -> str | None:
    """Write ``<ER>/<name>.txt`` and copy / move the file. Returns the file's new path in ER (or None)."""
    er = folder(ws)
    if not er:
        return None
    os.makedirs(er, exist_ok=True)
    moved = None
    src = move_file or copy_file
    if src and os.path.isfile(src):
        moved = _free(os.path.join(er, os.path.basename(src)))
        if move_file:
            shutil.move(src, moved)
        else:
            shutil.copy2(src, moved)
    txt = _free(os.path.join(er, f"{name}.txt"))
    with open(txt, "w", encoding="utf-8-sig") as fh:
        fh.write(f"LEVEL7 MTM — {kind}\n{now()}\n")
        if moved:
            fh.write(f"الملف / file: {os.path.basename(moved)}\n")
        fh.write("\nالسبب / reason:\n")
        for p in problems or ["(no detail)"]:
            fh.write(f"- {p}\n")
    with ws.db.tx() as c:
        ws.db.audit(c, ws.actor, "errors.file", "file", os.path.basename(src or name),
                    {"folder": er, "file": moved, "report": txt, "problems": problems})
    return moved
