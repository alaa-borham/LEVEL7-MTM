"""The line watched for the owner: an alert when it stops (no new XML, AI TOOL or the nesting not moving), and the
day's report (what came in, what was cut, for whom, what waits and why) written once a day.

Owner: "XML files stopped coming after 21/10 and nobody noticed for days" — a stop is said at once, on the screen."""
from __future__ import annotations

import html
import json
import os
from datetime import date, datetime, timedelta

ALERT_DEFAULTS = {"alert.on": True, "alert.hours": "08:00-20:00", "alert.xml_hours": 2.0,
                  "alert.robot_minutes": 15, "alert.nest_minutes": 30}


def alert_settings(ws) -> dict:
    return {k: ws.db.get_setting(k, v) for k, v in ALERT_DEFAULTS.items()}


def _at(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def working_now(hours: str, at: datetime) -> bool:
    """"08:00-20:00" (a night shift "20:00-08:00" goes over midnight); empty: all day."""
    try:
        a, b = [datetime.strptime(x.strip(), "%H:%M").time() for x in (hours or "").split("-")]
    except ValueError:
        return True
    t = at.time()
    return a <= t < b if a <= b else (t >= a or t < b)


def _last(ws, sql: str) -> datetime | None:
    r = ws.db.one(sql)
    return _at(r["t"]) if r else None


def alerts(ws, rows: list[dict], at: datetime | None = None, started: datetime | None = None,
           erp_on: bool = True, chain_on: bool = True) -> list[dict]:
    """What has stopped, now: [{"key", "text", "since"}]. ``rows`` are the order list's (ui.cad.order_rows);
    ``started`` is when the program (or the system) was started — nothing is said about the time before it."""
    s = alert_settings(ws)
    if not s["alert.on"]:
        return []
    at = at or datetime.now()
    started = started or at
    out = []

    def quiet(last: datetime | None, limit: timedelta) -> datetime | None:
        since = max(last or started, started)
        return since if at - since >= limit else None

    if erp_on and working_now(s["alert.hours"], at):
        last = _last(ws, "SELECT MAX(received_at) AS t FROM import_files")
        since = quiet(last, timedelta(hours=float(s["alert.xml_hours"])))
        if since and last is not None:                    # a program that never read a file: nothing to compare
            out.append(_alert("xml", last, XML_TEXT, ago=_mins(at - last), last=_hhmm(last, at)))
    if not chain_on:
        return out
    cad_waiting = sum(1 for r in rows if r.get("stage") == "grafis" and not r.get("nest_queue")
                      and not r.get("robot_failed"))
    if cad_waiting and ws.db.get_setting("robot.enabled", False):
        last = _last(ws, "SELECT MAX(at) AS t FROM robot_jobs")
        since = quiet(last, timedelta(minutes=int(s["alert.robot_minutes"])))
        if since:
            out.append(_alert("robot", since, ROBOT_TEXT, ago=_mins(at - since), n=cad_waiting))
    nest_waiting = sum(1 for r in rows if r.get("nest_queue"))
    if nest_waiting and not ws.db.get_setting("nesting.paused", False) and ws.db.get_setting("cutting.auto", True):
        last = _last(ws, "SELECT MAX(created_at) AS t FROM cutting_jobs")
        since = quiet(last, timedelta(minutes=int(s["alert.nest_minutes"])))
        if since:
            out.append(_alert("nesting", since, NEST_TEXT, ago=_mins(at - since), n=nest_waiting))
    return out


XML_TEXT = "No new XML file for {ago} (the last at {last}) — check that ERP still exports to the XML-process folder"
ROBOT_TEXT = "AI TOOL has not finished a model for {ago} — {n} orders wait for CAD"
NEST_TEXT = "The nesting has not finished a marker for {ago} — {n} orders wait for nesting"


def _alert(key: str, since: datetime, tmpl: str, **args) -> dict:
    a = {"key": key, "since": since.isoformat(timespec="minutes"), "tmpl": tmpl, "args": args}
    a["text"] = alert_text(a)
    return a


def _mins(d: timedelta) -> int:
    return int(d.total_seconds() // 60)


def ago_text(m: int, tr=lambda s: s) -> str:
    return tr("{n} min").format(n=m) if m < 120 else tr("{n} h").format(n=m // 60) if m < 48 * 60 else \
        tr("{n} days").format(n=m // 1440)


def alert_text(a: dict, tr=lambda s: s) -> str:
    """The alert in the interface's language."""
    if not a.get("tmpl"):
        return a.get("text", "")
    args = dict(a.get("args") or {})
    if "ago" in args:
        args["ago"] = ago_text(int(args["ago"]), tr)
    return tr(a["tmpl"]).format(**args)


def _hhmm(t: datetime, at: datetime) -> str:
    return t.strftime("%H:%M") if t.date() == at.date() else t.strftime("%d/%m %H:%M")


# ---------------------------------------------------------------- the day's report

def day_report(ws, day: date, rows: list[dict] | None = None) -> dict:
    """What was done on ``day`` (from the database), with what waits now and why (from the order list rows)."""
    from .customers import names
    from .shifts import counts
    start, end = day.isoformat() + "T00:00:00", (day + timedelta(days=1)).isoformat() + "T00:00:00"
    c = counts(ws, start, end)
    cust = names(ws)
    per: dict[str, dict] = {}

    def of(rid) -> str:
        r = ws.db.one("SELECT normalized FROM revisions WHERE id=?", (rid,))
        k = (json.loads(r["normalized"]).get("customer") if r else None) or "level7"
        return cust.get(k, k)

    for r in ws.db.q("SELECT id, normalized FROM revisions WHERE imported_at>=? AND imported_at<?", (start, end)):
        k = json.loads(r["normalized"]).get("customer") or "level7"
        per.setdefault(cust.get(k, k), {"in": 0, "cut": 0, "metres": 0.0})["in"] += 1
    for j in ws.db.q("SELECT sha256, info FROM cutting_jobs WHERE status='done' AND created_at>=? AND created_at<?",
                     (start, end)):
        res = ws.db.one("SELECT lines FROM grafis_results WHERE sha256=?", (j["sha256"],))
        rids = [ln["revision_id"] for ln in json.loads(res["lines"]) if ln.get("revision_id") is not None] \
            if res else []
        name = of(rids[0]) if rids else "—"
        p = per.setdefault(name, {"in": 0, "cut": 0, "metres": 0.0})
        p["cut"] += max(1, len(rids))
        p["metres"] = round(p["metres"] + (json.loads(j["info"] or "{}").get("length_mm") or 0) / 1000, 2)
    eff = [json.loads(j["info"] or "{}").get("efficiency_pct") for j in
           ws.db.q("SELECT info FROM cutting_jobs WHERE status='done' AND created_at>=? AND created_at<?", (start, end))]
    eff = [e for e in eff if isinstance(e, (int, float))]
    fails = [dict(r) for r in ws.db.q("SELECT at, message FROM robot_jobs WHERE status='failed' AND at>=? AND at<? "
                                      "ORDER BY id DESC LIMIT 10", (start, end))]
    raised = [json.loads(r["details"] or "{}") | {"at": r["at"]} for r in
              ws.db.q("SELECT at, details FROM audit WHERE action='alert.raised' AND at>=? AND at<? ORDER BY id",
                      (start, end))]
    rows = rows or []
    stages: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for r in rows:
        k = "queue" if r.get("nest_queue") else r.get("stage", "")
        stages[k] = stages.get(k, 0) + 1
        if r.get("stage") == "err":
            reasons[r.get("reason") or "other"] = reasons.get(r.get("reason") or "other", 0) + 1
    return {"day": day.isoformat(), "counts": c, "customers": per, "efficiency": round(sum(eff) / len(eff), 1)
            if eff else None, "robot_failures": fails, "alerts": raised, "now": stages, "reasons": reasons,
            "made_at": datetime.now().isoformat(timespec="minutes")}


def report_html(rep: dict, tr=lambda s: s) -> str:
    """The report as a page (Arabic, right to left), to read on the screen, print or keep."""
    e = html.escape
    c = rep["counts"]

    def tiles(items):
        return "".join(f"<td class='t'><div class='n'>{e(str(v))}</div><div>{e(tr(k))}</div></td>" for k, v in items)

    def table(head, body):
        if not body:
            return f"<p class='m'>{e(tr('Nothing'))}</p>"
        h = "".join(f"<th>{e(tr(x))}</th>" for x in head)
        b = "".join("<tr>" + "".join(f"<td>{e(str(x))}</td>" for x in r) + "</tr>" for r in body)
        return f"<table class='g'><tr>{h}</tr>{b}</table>"

    reasons = {"pattern": "Distorted pattern", "measure": "Measurement", "variation": "Variation", "cad": "CAD",
               "nesting": "Nesting", "other": "Other"}
    now = rep["now"]
    parts = [
        f"<h1>{e(tr('Daily report'))} — {e(rep['day'])}</h1>",
        f"<p class='m'>{e(tr('Made at'))} {e(rep['made_at'].replace('T', ' '))}</p>",
        f"<h2>{e(tr('The day'))}</h2><table><tr>" + tiles([
            ("Order lines received", c["orders"]), ("Models written for CAD", c["models"]),
            ("Saved by AI TOOL", c["cad_saved"]), ("Markers nested", c["nested"]),
            ("Cutting files", c["cut_files"]), ("Metres", c["metres"]), ("Problems", c["problems"])]
        ) + "</tr></table>",
        (f"<p>{e(tr('Average efficiency'))}: <b>{rep['efficiency']}%</b></p>" if rep["efficiency"] is not None
         else ""),
        f"<h2>{e(tr('By customer'))}</h2>" + table(
            ["Customer", "Received", "Cut", "Metres"],
            [[k, v["in"], v["cut"], v["metres"]] for k, v in sorted(rep["customers"].items())]),
        f"<h2>{e(tr('Now'))}</h2><table><tr>" + tiles([
            ("New", now.get("new", 0)), ("In GRAFIS", now.get("grafis", 0)),
            ("Waiting for nesting", now.get("queue", 0)), ("Ready to cut", now.get("cut", 0)),
            ("Needs attention", now.get("err", 0))]) + "</tr></table>",
        f"<h2>{e(tr('Needs attention — reasons'))}</h2>" + table(
            ["Reason", "Order count"], [[tr(reasons.get(k, k)), n] for k, n in
                                   sorted(rep["reasons"].items(), key=lambda x: -x[1])]),
        f"<h2>{e(tr('Line stops'))}</h2>" + table(
            ["Time", "Alert"], [[a["at"][11:16], alert_text(a, tr)] for a in rep["alerts"]]),
        f"<h2>{e(tr('AI TOOL failures'))}</h2>" + table(
            ["Time", "Message"], [[f["at"][11:16], f["message"][:160]] for f in rep["robot_failures"]]),
    ]
    style = ("body{font-family:Segoe UI,Tahoma,Arial;direction:rtl;margin:24px;color:#111}"
             "h1{font-size:20pt;margin:0}h2{font-size:13pt;margin-top:22px;border-bottom:1px solid #ccc}"
             ".m{color:#666}.t{border:1px solid #ccc;padding:8px 14px;text-align:center}"
             ".n{font-size:18pt;font-weight:bold}.g{border-collapse:collapse}"
             ".g td,.g th{border:1px solid #ccc;padding:4px 10px}.g th{background:#eee}")
    return (f"<!doctype html><html dir='rtl'><head><meta charset='utf-8'><title>{e(tr('Daily report'))} "
            f"{e(rep['day'])}</title><style>{style}</style></head><body>{''.join(parts)}</body></html>")


def reports_folder(ws) -> str:
    return ws.db.get_setting("report.folder") or os.path.join(ws.folder, "reports")


def write_report(ws, day: date, rows=None, tr=lambda s: s) -> str:
    folder = reports_folder(ws)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"LEVEL7 report {day.isoformat()}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report_html(day_report(ws, day, rows), tr))
    return path


def daily_due(ws, at: datetime | None = None) -> date | None:
    """The day whose report is due: yesterday's, once, after the report time (default 08:00)."""
    at = at or datetime.now()
    try:
        h = datetime.strptime(ws.db.get_setting("report.at", "08:00"), "%H:%M").time()
    except ValueError:
        h = datetime.strptime("08:00", "%H:%M").time()
    if at.time() < h:
        return None
    day = at.date() - timedelta(days=1)
    return None if ws.db.get_setting("report.last") == day.isoformat() else day


def mark_written(ws, day: date) -> None:
    ws.db.set_setting("report.last", day.isoformat())


def record(ws, raised: list[dict]) -> None:
    """Each new stop kept in the audit: the day's report lists them."""
    with ws.db.tx() as c:
        for a in raised:
            ws.db.audit(c, ws.actor, "alert.raised", "line", a["key"], a)
