"""The line watched: an alert when it stops, and the day's report."""
import json
from datetime import date, datetime, timedelta

from conftest import XML_53, read

from level7mtm.services import report


def test_no_new_xml_for_two_hours_is_said_in_working_hours(ws):
    ws.import_data(read(XML_53), "Order-9-53-1_2.xml")
    last = datetime.fromisoformat(ws.db.one("SELECT MAX(received_at) AS t FROM import_files")["t"])
    at = last.replace(hour=11, minute=0) if last.hour < 9 else last + timedelta(hours=3)
    if not report.working_now("08:00-20:00", at):
        ws.db.set_setting("alert.hours", "")                   # all day
    early = report.alerts(ws, [], at=last + timedelta(minutes=30), started=last - timedelta(hours=5))
    assert not early
    (a,) = report.alerts(ws, [], at=last + timedelta(hours=3), started=last - timedelta(hours=5))
    assert a["key"] == "xml" and "3 h" in a["text"]
    assert not report.alerts(ws, [], at=last + timedelta(hours=3), started=last - timedelta(hours=5), erp_on=False)
    ws.db.set_setting("alert.on", False)
    assert not report.alerts(ws, [], at=last + timedelta(hours=3), started=last - timedelta(hours=5))


def test_working_hours_also_over_midnight():
    assert report.working_now("08:00-20:00", datetime(2026, 10, 26, 9))
    assert not report.working_now("08:00-20:00", datetime(2026, 10, 26, 21))
    assert report.working_now("20:00-08:00", datetime(2026, 10, 26, 2))
    assert report.working_now("", datetime(2026, 10, 26, 2))


def test_robot_and_nesting_stops_are_said_only_after_the_program_ran_long_enough(ws):
    ws.db.set_setting("robot.enabled", True)
    rows = [{"stage": "grafis"}, {"stage": "grafis"}, {"stage": "grafis", "nest_queue": True}]
    t0 = datetime(2026, 10, 26, 10)
    assert not report.alerts(ws, rows, at=t0 + timedelta(minutes=10), started=t0, erp_on=False)
    got = {a["key"]: a for a in report.alerts(ws, rows, at=t0 + timedelta(minutes=40), started=t0, erp_on=False)}
    assert set(got) == {"robot", "nesting"} and got["robot"]["args"]["n"] == 2 and got["nesting"]["args"]["n"] == 1
    ws.db.set_setting("nesting.paused", True)                      # stopped by the owner: not a stop of the line
    assert {a["key"] for a in report.alerts(ws, rows, at=t0 + timedelta(minutes=40), started=t0,
                                            erp_on=False)} == {"robot"}
    assert not report.alerts(ws, rows, at=t0 + timedelta(minutes=40), started=t0, erp_on=False, chain_on=False)


def test_alert_text_in_arabic():
    from level7mtm.ui.i18n import set_language, tr
    set_language("ar")
    try:
        a = {"tmpl": report.ROBOT_TEXT, "args": {"ago": 20, "n": 3}}
        text = report.alert_text(a, tr)
        assert "AI TOOL" in text and "20" in text and "3" in text and "دقيقة" in text
    finally:
        set_language("en")


def test_the_days_report(ws, tmp_path):
    o = ws.import_data(read(XML_53), "Order-9-53-1_2.xml")
    day = datetime.fromisoformat(ws.db.one("SELECT MAX(received_at) AS t FROM import_files")["t"]).date()
    report.record(ws, [{"key": "xml", "tmpl": report.XML_TEXT, "args": {"ago": 150, "last": "08:10"}}])
    rows = [{"stage": "err", "reason": "pattern"}, {"stage": "err", "reason": "pattern"}, {"stage": "cut"}]
    rep = report.day_report(ws, day, rows)
    assert rep["counts"]["orders"] == len(o.lines) and sum(v["in"] for v in rep["customers"].values()) == len(o.lines)
    assert rep["reasons"] == {"pattern": 2} and rep["now"] == {"err": 2, "cut": 1}
    assert len(rep["alerts"]) == 1
    ws.db.set_setting("report.folder", str(tmp_path))
    p = report.write_report(ws, day, rows)
    page = open(p, encoding="utf-8").read()
    assert "dir='rtl'" in page and "08:10" in page
    at = datetime.combine(day + timedelta(days=1), datetime.min.time()).replace(hour=9)
    assert report.daily_due(ws, at) == day
    assert report.daily_due(ws, at.replace(hour=7)) is None             # before the report time
    report.mark_written(ws, day)
    assert report.daily_due(ws, at) is None                             # once a day
    json.dumps(rep)
