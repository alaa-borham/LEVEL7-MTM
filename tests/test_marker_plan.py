"""The printed plan of a marker of several orders: which piece is whose on the cutting table."""
import json
import zipfile

from level7mtm.export.marker_plan import order_of, read_plan, write_plan


def test_order_of_a_piece():
    assert order_of("9-53-1_2:56-S") == "9-53-1_2"
    assert order_of("base:9-53-2_2:__HN49_0") == "9-53-2_2"
    assert order_of("base:__HN49_0") == "" and order_of("44") == ""      # a marker of one order


def _l7n(path):
    sq = [[0, 0], [400, 0], [400, 300], [0, 300]]
    proj = {"pieces": [{"uid": "a", "name": "front", "sizes": {"9-1_1:S": {"cut": sq}, "9-2_1:S": {"cut": sq}}}],
            "markers": [{"fabric": {"width": 1450}, "length": 3000, "placements": [
                {"piece_uid": "a", "size": "9-1_1:S", "x": 0, "y": 0},
                {"piece_uid": "a", "size": "9-2_1:S", "x": 2500, "y": 500, "rotation": 90}]}]}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("project.json", json.dumps({"project": proj}))


def test_the_plan_is_drawn_with_each_order(tmp_path):
    _l7n(tmp_path / "m.l7n")
    plan = read_plan(str(tmp_path / "m.l7n"))
    assert [p["order"] for p in plan["pieces"]] == ["9-1_1", "9-2_1"] and plan["width"] == 1450
    assert plan["pieces"][1]["cut"][1] == (2500, 900)                      # turned 90° and moved, as nested
    out = write_plan(str(tmp_path / "m.l7n"), str(tmp_path / "m - PLAN.pdf"), "m +1",
                     [{"order": "9-1_1", "text": "LEVEL7 · 1 pcs"}])
    data = open(out, "rb").read()
    assert data[:5] == b"%PDF-" and data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") >= 1
