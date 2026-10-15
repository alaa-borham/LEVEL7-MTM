"""Command line tools.

    level7mtm import FILE.xml ...            import ERP XML into the workspace
    level7mtm list                           order revisions and their status
    level7mtm calc LINE_NUMBER               calculate the pattern of an order line (e.g. 9-53-1_2)
    level7mtm export LINE_NUMBER dxf|pdf     export the latest calculation
    level7mtm nest LINE_NUMBER ... -o F.l7n [--test]   write a LEVEL7 Nesting file
    level7mtm mdl-report FILE.mdl -o DIR     GRAFIS formula report (Markdown + JSON)
    level7mtm backup                         back up the database

Workspace folder: --data DIR or LEVEL7MTM_DATA (default: %LOCALAPPDATA%\\LEVEL7 MTM).
"""
from __future__ import annotations

import argparse
import os
import sys


def _ws(a):
    from .services.workflow import Workspace
    from .ui.app import default_data_folder
    return Workspace(a.data or os.environ.get("LEVEL7MTM_DATA") or default_data_folder(), actor=a.user)


def _rid(ws, number):
    for r in ws.list_revisions(True):
        if r["line_number"] == number:
            return r["id"]
    raise SystemExit(f"order line {number} not found")


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="level7mtm")
    ap.add_argument("--data")
    ap.add_argument("--user", default=os.environ.get("USERNAME") or os.environ.get("USER") or "cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("import")
    s.add_argument("files", nargs="+")
    sub.add_parser("list")
    s = sub.add_parser("calc")
    s.add_argument("line")
    s = sub.add_parser("export")
    s.add_argument("line")
    s.add_argument("kind", choices=("dxf", "pdf"))
    s.add_argument("-o", "--output")
    s = sub.add_parser("nest")
    s.add_argument("lines", nargs="+")
    s.add_argument("-o", "--output", required=True)
    s.add_argument("--test", action="store_true", help="allow non-approved results; output marked TEST")
    s = sub.add_parser("mdl-report")
    s.add_argument("mdl")
    s.add_argument("-o", "--outdir", default="reports")
    sub.add_parser("backup")
    a = ap.parse_args(argv)
    from .services.workflow import WorkflowError
    try:
        return _run(a)
    except WorkflowError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2


def _run(a) -> int:
    if a.cmd == "mdl-report":
        from .grafis.report import write_report
        print(*write_report(a.mdl, a.outdir), sep="\n")
        return 0
    ws = _ws(a)
    if a.cmd == "import":
        rc = 0
        for f in a.files:
            o = ws.import_file(f)
            print(f"{o.file_name}: {o.status} {o.message}")
            for ln in o.lines:
                print(f"  {ln['line_number']}: {ln['action']} -> {ln['status']}")
            rc |= o.status == "failed"
        return rc
    if a.cmd == "list":
        for r in ws.list_revisions(True):
            print(f"{r['line_number']:<14} rev {r['revision']:<3} {r['status']:<18} qty {r['quantity']}")
        return 0
    if a.cmd == "calc":
        g = ws.generate(_rid(ws, a.line))
        print(f"generation {g['id']} {g['status']} fingerprint {g['fingerprint'][:16]}")
        for i in g["issues"]:
            print(f"  [{i['severity']}] {i['message']}")
        return 0 if g["status"] == "ok" else 2
    if a.cmd == "export":
        g = ws.latest_generation(_rid(ws, a.line))
        if not g:
            raise SystemExit("no calculation")
        print(ws.export(g["id"], a.kind, a.output))
        return 0
    if a.cmd == "nest":
        j = ws.send_to_nesting([_rid(ws, x) for x in a.lines], a.output, test_only=a.test)
        print(j["path"], "(existing job)" if j["duplicate"] else "")
        return 0
    if a.cmd == "backup":
        print(ws.db.backup(os.path.join(ws.folder, "backups")))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
