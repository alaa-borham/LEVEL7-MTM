"""Shifts (owner): the work is counted per shift; when a shift ends the "ready to cut" list starts again from zero
— nothing is deleted (the cutting files stay in the customers' folders, the orders in the program) — and the
dashboard keeps every shift with its dates and what was done in it.

A shift ends by the owner's button, or by itself at the start time of the next shift of the day's schedule
(e.g. morning 08:00, evening 20:00). Without a schedule a shift lasts until the button is pressed.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta

from ..storage.db import now

FIRST = "2000-01-01T00:00:00"                             # the shift running since the program was installed


def schedule(ws) -> list[dict]:
    """[{name, start "HH:MM"}] in the order of the day."""
    rows = [r for r in ws.db.get_setting("shifts.list") or [] if r.get("start")]
    return sorted(rows, key=lambda r: r["start"])


def set_schedule(ws, rows: list[dict]) -> None:
    clean = []
    for r in rows:
        name, start = (r.get("name") or "").strip(), (r.get("start") or "").strip()
        if not start:
            continue
        datetime.strptime(start, "%H:%M")                  # "8:00" / "20:30"; anything else: refused
        clean.append({"name": name or start, "start": datetime.strptime(start, "%H:%M").strftime("%H:%M")})
    ws.db.set_setting("shifts.list", clean)


def _due(ws, at: datetime) -> tuple[str, datetime] | None:
    """The shift of the schedule running at ``at``: (name, its start)."""
    sch = schedule(ws)
    if not sch:
        return None
    best = None
    for back in (0, 1):
        day = (at - timedelta(days=back)).date()
        for r in sch:
            h, m = map(int, r["start"].split(":"))
            t = datetime(day.year, day.month, day.day, h, m)
            if t <= at and (best is None or t > best[1]):
                best = (r["name"], t)
    return best


_LOCK = threading.RLock()                                 # the order list and the dashboard look together


def current(ws, at: datetime | None = None) -> dict:
    """The shift running now: {name, started_at}. A shift of the schedule that began since: the one before is
    ended (counted) at its start, and it begins."""
    with _LOCK:
        return _current(ws, at)


def _current(ws, at: datetime | None = None) -> dict:
    at = at or datetime.now()
    name = ws.db.get_setting("shifts.current") or ""
    started = ws.db.get_setting("shifts.started_at")
    if not started:                                   # the first look (just updated): nothing leaves the list
        started, name = FIRST, ""                          # until the owner ends a shift / the schedule does
        ws.db.set_setting("shifts.started_at", started)
        ws.db.set_setting("shifts.current", name)
    due = _due(ws, at)
    if due and due[1].isoformat(timespec="seconds") > started:
        _close(ws, name, started, due[1].isoformat(timespec="seconds"))
        started, name = due[1].isoformat(timespec="seconds"), due[0]
        ws.db.set_setting("shifts.started_at", started)
        ws.db.set_setting("shifts.current", name)
    return {"name": name, "started_at": started}


def end_shift(ws, next_name: str = "") -> dict:
    """The owner ends the shift now: counted into the history; the next one begins (the "ready to cut" list from
    zero — nothing deleted). Returns the shift ended."""
    with _LOCK:
        return _end_shift(ws, next_name)


def _end_shift(ws, next_name: str = "") -> dict:
    cur = _current(ws)
    end = now()
    rec = _close(ws, cur["name"], cur["started_at"], end)
    ws.db.set_setting("shifts.started_at", end)
    due = _due(ws, datetime.now())
    ws.db.set_setting("shifts.current", next_name or (due[0] if due else ""))
    return rec


def _close(ws, name: str, start: str, end: str) -> dict:
    c = counts(ws, start, end)
    with ws.db.tx() as tx:
        tx.execute("INSERT INTO shifts(name, started_at, ended_at, counts) VALUES (?,?,?,?)",
                   (name or "", start, end, json.dumps(c)))
        ws.db.audit(tx, ws.actor, "shift.ended", "shift", name or start, c)
    return {"name": name, "started_at": start, "ended_at": end, "counts": c}


def counts(ws, start: str, end: str | None = None) -> dict:
    """What was done between ``start`` and ``end`` (now when None): order lines received, models written for
    CAD, saved by CAD, nested, cutting files and metres, problems."""
    end = end or "9999"
    q = lambda sql: ws.db.one(sql, (start, end))["n"]   # noqa: E731
    jobs = ws.db.q("SELECT outputs, info FROM cutting_jobs WHERE status='done' AND created_at>=? AND created_at<?",
                   (start, end))
    metres = 0.0
    files = 0
    for j in jobs:
        files += len(json.loads(j["outputs"] or "[]"))
        metres += (json.loads(j["info"] or "{}").get("length_mm") or 0) / 1000
    return {"orders": q("SELECT COUNT(*) AS n FROM revisions WHERE imported_at>=? AND imported_at<?"),
            "models": q("SELECT COUNT(*) AS n FROM grafis_orders WHERE status='written' AND created_at>=? "
                        "AND created_at<?"),
            "cad_saved": q("SELECT COUNT(*) AS n FROM robot_jobs WHERE status='done' AND at>=? AND at<?"),
            "nested": len(jobs),
            "cut_files": files,
            "metres": round(metres, 2),
            "problems": q("SELECT COUNT(*) AS n FROM grafis_orders WHERE status IN ('blocked','not_configured') "
                          "AND created_at>=? AND created_at<?")
                        + q("SELECT COUNT(*) AS n FROM robot_jobs WHERE status='failed' AND at>=? AND at<?")
                        + q("SELECT COUNT(*) AS n FROM cutting_jobs WHERE status NOT IN ('done','duplicate') "
                            "AND created_at>=? AND created_at<?")}


def history(ws, limit: int = 200) -> list[dict]:
    """The shift running now first, then the ended ones (newest first)."""
    cur = current(ws)
    out = [{"name": cur["name"], "started_at": cur["started_at"], "ended_at": None,
            "counts": counts(ws, cur["started_at"])}]
    for r in ws.db.q("SELECT * FROM shifts ORDER BY id DESC LIMIT ?", (limit,)):
        out.append({"name": r["name"], "started_at": r["started_at"], "ended_at": r["ended_at"],
                    "counts": json.loads(r["counts"])})
    return out
