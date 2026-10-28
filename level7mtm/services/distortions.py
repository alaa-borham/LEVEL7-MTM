"""The distortion log (owner: "which part or option is distorted most — fix the model once in GRAFIS instead of
each order"): every distorted piece found, from what the program kept — AI TOOL's check after CAD saved, the
check before the nesting and the check of the marker — counted by piece, by the order's design options and by
base pattern, over a period."""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta

# "<piece>: <defect>" as pattern_check writes it
_DEFECT = re.compile(r"^\s*(?P<piece>[^:;]{1,60}?):\s*(?P<what>its outline crosses itself|\d+ needle tip|"
                     r"the piece has \(almost\) no area|its outline is far from the base pattern)")
KINDS = {"its outline crosses itself": "crosses", "needle tip": "needle", "the piece has (almost) no area": "no_area",
         "its outline is far from the base pattern": "shape"}


def _kind(what: str) -> str:
    return "needle" if "needle tip" in what else KINDS.get(what, "shape")


def defects(text: str) -> list[tuple[str, str]]:
    """[(piece, kind)] in a message ("pattern distorted in CAD, not nested: back: … ; front1: …")."""
    body = text.split("not nested:", 1)[-1].split("no cutting file:", 1)[-1]
    out = []
    for part in re.split(r";\s*", body):
        m = _DEFECT.match(part)
        if m:
            out.append((m.group("piece").strip(), _kind(m.group("what"))))
    return out


def _found(ws, since: str):
    """(time, model file name, message) of every distortion the program kept since ``since``."""
    for r in ws.db.q("SELECT at, path, message FROM robot_jobs WHERE status='failed' AND at>=? AND message LIKE "
                     "'%pattern distorted%'", (since,)):
        yield r["at"], r["path"], r["message"]
    for r in ws.db.q("SELECT checked_at, path, issues FROM grafis_results WHERE checked_at>=? AND issues LIKE "
                     "'%pattern distorted%'", (since,)):
        for _, msg in json.loads(r["issues"] or "[]"):
            if "pattern distorted" in msg:
                yield r["checked_at"], r["path"], msg
    for r in ws.db.q("SELECT created_at, scb_path, info FROM cutting_jobs WHERE created_at>=? AND info LIKE "
                     "'%pattern_errors%'", (since,)):
        bad = json.loads(r["info"] or "{}").get("pattern_errors") or []
        if bad:
            yield r["created_at"], r["scb_path"], "pattern distorted, no cutting file: " + "; ".join(bad)


def distortion_log(ws, days: int | None = 30, cid: str | None = None) -> dict:
    """{"orders", "checked", "pieces": [...], "options": [...], "patterns": [...]} — each distorted piece of an
    order counted once (found by several checks or several times: once)."""
    from .customers import names
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds") if days else ""
    by_name: dict[str, int] = {}
    for g in ws.db.q("SELECT path, revision_id, info FROM grafis_orders WHERE path IS NOT NULL ORDER BY id"):
        by_name[os.path.basename(g["path"]).lower()] = g["revision_id"]
    template = {}
    for g in ws.db.q("SELECT revision_id, info FROM grafis_orders ORDER BY id"):
        try:
            template[g["revision_id"]] = os.path.basename(json.loads(g["info"] or "{}").get("template") or "")
        except ValueError:
            pass
    seen: dict[tuple[int, str], dict] = {}
    for at, path, msg in _found(ws, since):
        rid = by_name.get(os.path.basename(path or "").lower())
        if rid is None:
            continue
        for piece, kind in defects(msg):
            k = (rid, piece)
            if k not in seen or at > seen[k]["at"]:
                seen[k] = {"rid": rid, "piece": piece, "kind": kind, "at": at}
    revs = {}
    for rid in {k[0] for k in seen}:
        r = ws.db.one("SELECT line_number, normalized FROM revisions WHERE id=?", (rid,))
        if r:
            n = json.loads(r["normalized"])
            revs[rid] = {"line": r["line_number"], "customer": n.get("customer") or "level7",
                         "options": n.get("options") or {}}
    rows = [v for v in seen.values() if v["rid"] in revs and (cid is None or revs[v["rid"]]["customer"] == cid)]
    bad_orders = {v["rid"] for v in rows}
    pieces: dict[str, dict] = {}
    for v in rows:
        p = pieces.setdefault(v["piece"], {"piece": v["piece"], "orders": set(), "kinds": Counter(), "last": "",
                                           "examples": []})
        p["orders"].add(v["rid"])
        p["kinds"][v["kind"]] += 1
        p["last"] = max(p["last"], v["at"])
        if len(p["examples"]) < 5 and revs[v["rid"]]["line"] not in p["examples"]:
            p["examples"].append(revs[v["rid"]]["line"])
    # every order line made in the period (same customer filter): how often each option value goes with a
    # distortion, against how often the orders have it at all
    total = Counter()
    made = 0
    for r in ws.db.q("SELECT r.id, r.normalized FROM revisions r WHERE r.imported_at>=? AND r.status <> "
                     "'superseded' AND EXISTS (SELECT 1 FROM grafis_orders g WHERE g.revision_id=r.id)", (since,)):
        n = json.loads(r["normalized"])
        if cid is not None and (n.get("customer") or "level7") != cid:
            continue
        made += 1
        for g, v in (n.get("options") or {}).items():
            total[(g, v)] += 1
    bad_opt = Counter()
    for rid in bad_orders:
        for g, v in revs[rid]["options"].items():
            bad_opt[(g, v)] += 1
    rate = len(bad_orders) / made if made else 0.0
    options = sorted(({"group": g, "value": v, "distorted": n, "orders": max(total[(g, v)], n),
                       "rate": n / max(total[(g, v)], n)} for (g, v), n in bad_opt.items()),
                     key=lambda o: (-o["rate"], -o["distorted"]))
    pats = Counter(template.get(rid, "") or "—" for rid in bad_orders)
    cust = names(ws)
    return {"days": days, "orders": len(bad_orders), "checked": made, "rate": rate,
            "pieces": sorted(({**p, "orders": len(p["orders"]), "kinds": dict(p["kinds"])} for p in pieces.values()),
                             key=lambda p: (-p["orders"], p["piece"])),
            "options": options,
            "patterns": [{"pattern": k, "orders": n} for k, n in pats.most_common()],
            "customers": Counter(cust.get(revs[r]["customer"], revs[r]["customer"]) for r in bad_orders)}
