"""Product catalog, measurement definitions and the unified rule engine."""
import copy
import os

import pytest
from conftest import CATALOG, MODEL, OPTS, XML_53, read

from level7mtm.pattern.model import evaluate_model, load_model
from level7mtm.services.catalog import CatalogError, apply_plan, find_style, load_catalog, resolve

MM_9_53 = dict(FL=1570, SH=495, SLS=675, CH=690, WA=640, HP=700, BO=830, WB=50, AH=235, EB=190, WRS=160, SWS=50,
               COS=420, JL=450, TKL=170, TKWD=95, DSR=70, DSL=70)


@pytest.fixture
def cat():
    return load_catalog(CATALOG)


def style(cat):
    return cat["products"]["saudi-thobe"]["styles"]["alshiaka-2027"]


def test_catalog_structure(cat):
    assert find_style(cat, "91001", None) == ("saudi-thobe", "alshiaka-2027")
    assert find_style(cat, None, "Saudi Thobe_TableMeasurement") == ("saudi-thobe", "alshiaka-2027")
    with pytest.raises(CatalogError):
        find_style(cat, "99999", "other")
    st = style(cat)
    assert st["base_pattern"]["model_id"] == "thobe-engine-test" and st["base_pattern"]["version"] == "0.1.0"
    m = cat["measurements"]["FL"]
    assert {"name", "unit", "kind", "method", "order", "visible", "editable"} <= set(m)
    assert cat["measurements"]["NH"]["kind"] == "length"           # neck height: an ordinary measurement


def test_owner_example_rule(cat):
    r = next(r for r in style(cat)["rules"] if r["source"].get("code") == "FL")
    assert r["target"] == {"piece": "002", "construction": "Basic-Construction 0", "variable": "X1"}
    assert r["value"] == "{M}" and r["status"] == "confirmed"
    res = resolve(cat, "saudi-thobe", "alshiaka-2027", MM_9_53, OPTS)
    assert res.inputs["X1"] == 1570


def test_missing_measurement_is_not_zero(cat):
    mm = dict(MM_9_53)
    del mm["CH"]
    res = resolve(cat, "saudi-thobe", "alshiaka-2027", mm, OPTS)
    assert "X9" not in res.inputs                                  # no zero, no reference value
    assert next(t for t in res.trace if t.source == "CH").status == "skipped"
    r = evaluate_model(load_model(MODEL), res.inputs, OPTS)
    assert any(i.code == "input.missing" for i in r.issues) and not r.pieces


def test_several_rules_order_scope_and_formula(cat):
    c = copy.deepcopy(cat)
    st = style(c)
    st["rules"] += [
        {"id": "R900", "source": {"type": "measurement", "code": "FL"}, "target": {"piece": "005", "variable": "X1"},
         "value": "{M}/2 + {CH}/10", "order": 900, "factories": ["*"], "sizes": ["*"], "enabled": True,
         "status": "confirmed"},
        {"id": "R901", "source": {"type": "measurement", "code": "FL"}, "target": {"piece": "002", "variable": "X99"},
         "value": "X1 + 5", "order": 901, "factories": ["other"], "sizes": ["*"], "enabled": True,
         "status": "confirmed"},
        {"id": "R902", "source": {"type": "measurement", "code": "FL"}, "target": {"piece": "002", "variable": "X98"},
         "value": "X1 + 7", "order": 902, "factories": ["*"], "sizes": ["60 XL"], "enabled": True,
         "status": "confirmed"},
    ]
    res = resolve(c, "saudi-thobe", "alshiaka-2027", MM_9_53, OPTS, factory="main", size="60 XL")
    assert res.inputs["P005.X1"] == pytest.approx(1570 / 2 + 69)    # second rule for FL, other piece
    assert "X99" not in res.inputs and next(t for t in res.trace if t.rule_id == "R901").status == "out_of_scope"
    assert res.inputs["X98"] == 1577                                # uses X1 set by the earlier rule
    res2 = resolve(c, "saudi-thobe", "alshiaka-2027", MM_9_53, OPTS, factory="main", size="52-S")
    assert "X98" not in res2.inputs


def test_override_reported(cat):
    mm = dict(MM_9_53, COG=410)                                     # both neck sizes present: COG rule wins, reported
    res = resolve(cat, "saudi-thobe", "alshiaka-2027", mm, OPTS)
    assert res.inputs["X7"] == 410
    assert any(i.code == "rule.override" for i in res.issues)


def test_bad_expression_blocks(cat):
    c = copy.deepcopy(cat)
    style(c)["rules"][0]["value"] = "{M} / (1-1)"
    res = resolve(c, "saudi-thobe", "alshiaka-2027", MM_9_53, OPTS)
    assert any(i.code == "rule.error" and i.severity == "blocking" for i in res.issues) and "X1" not in res.inputs


def test_option_change_rebuilds_piece_list(cat):
    a = resolve(cat, "saudi-thobe", "alshiaka-2027", MM_9_53, OPTS)
    assert {"front", "back", "sleeve", "collar", "jabzour_splender", "pocket_side_hidden", "pocket_chest_cut"} == set(
        a.piece_plan)
    b = resolve(cat, "saudi-thobe", "alshiaka-2027", MM_9_53, {**OPTS, "pocket_side": "none", "pocket_chest": "none"})
    assert "pocket_side_hidden" not in b.piece_plan and "pocket_chest_cut" not in b.piece_plan
    c = resolve(cat, "saudi-thobe", "alshiaka-2027", MM_9_53, {**OPTS, "sleeve": "kabak"})
    assert "sleeve" not in c.piece_plan and {"sleeve_kabak", "cuff"} <= set(c.piece_plan)


def test_option_rule_sets_variables_and_properties(cat):
    c = copy.deepcopy(cat)
    orule = next(o for o in style(c)["option_rules"] if o["group"] == "collar" and o["value"] == "square")
    orule["variables"] = [{"target": {"piece": "002", "variable": "X40"}, "value": "35", "status": "confirmed"}]
    orule["properties"] = {"collar": {"copies": 3}}
    res = resolve(c, "saudi-thobe", "alshiaka-2027", MM_9_53, OPTS)
    assert res.inputs["X40"] == 35 and res.piece_plan["collar"]["copies"] == 3


def test_preview_and_production_are_separate(cat):
    res = resolve(cat, "saudi-thobe", "alshiaka-2027", MM_9_53, OPTS)
    r = evaluate_model(load_model(MODEL), res.inputs, OPTS)
    plan = copy.deepcopy(res.piece_plan)
    plan["collar"]["production"] = False                          # shown, not cut
    issues = apply_plan(r, plan)
    pieces = {p.id: p for p in r.pieces}
    assert pieces["collar"].preview and not pieces["collar"].production
    assert pieces["sleeve"].mirrored == 1 and pieces["front"].production
    missing = {i.piece for i in issues if i.code == "plan.piece_missing"}
    assert missing == {"jabzour_splender", "pocket_side_hidden", "pocket_chest_cut"}   # honest: not in base pattern


def test_invalid_catalog_rejected(cat):
    c = copy.deepcopy(cat)
    style(c)["rules"][0]["source"]["code"] = "ZZ"
    with pytest.raises(CatalogError):
        load_catalog(c)
    c = copy.deepcopy(cat)
    style(c)["base_pattern"] = {}
    with pytest.raises(CatalogError):
        load_catalog(c)


NESTING = os.environ.get("LEVEL7_NESTING_SRC", os.path.join(os.path.dirname(__file__), "..", "..", "alaa-borham",
                                                             "level7-nesting"))


def test_fl_only_experiment(ws, tmp_path):
    """Same real order twice, only FL 157 -> 160 cm: the changes must match the base-pattern rules."""
    from level7mtm.services.experiment import fl_experiment
    have_nesting = os.path.isdir(NESTING)
    if have_nesting:
        pytest.importorskip("shapely")
        pytest.importorskip("pyclipper")
    r = fl_experiment(ws, XML_53, "9-53-1_2", "160.00", str(tmp_path), NESTING if have_nesting else None)
    assert r["measurement_differences"] == {"FL": (1570.0, 1600.0)}
    assert r["variable_differences"] == {"X1": (1570.0, 1600.0)}         # only X1 (rule R001 {M})
    assert set(r["moved_points"]) == {"FBL", "FBR", "BBL", "BBR"}         # hem corners only
    assert all(v == pytest.approx((0.0, -30.0)) for v in r["moved_points"].values())
    for pid in ("front", "back"):
        d = r["pieces"][pid]
        assert d["height_delta"] == pytest.approx(30.0) and d["width_delta"] == pytest.approx(0.0)
        changed = {e for e, v in d["edges"].items() if abs(v["delta"]) > 1e-9}
        assert changed == {"side_r", "side_l"}
        assert d["notches_moved"] == [0.0, 0.0]
        assert d["copies"][0] == d["copies"][1]
    assert r["pieces"]["front"]["edges"]["side_r"]["delta"] == pytest.approx(
        r["pieces"]["back"]["edges"]["side_r"]["delta"])
    assert r["pieces"]["sleeve"]["identical"] and r["pieces"]["collar"]["identical"]
    assert all(x["ok"] for x in r["relations"]["b"])
    assert r["quantity"] == [1, 1] and r["generation_status"] == ["ok", "ok"]
    if have_nesting:
        for k, line in (("a", "9-53-1_2"), ("b", "9-53-1_3")):
            n = r["nesting"][k]
            assert n["max_geometry_difference_mm"] == 0.0 and n["valid"]
            assert n["sizes"] == [f"thobe-engine-test:{line}"] and n["placed_sizes"] == n["sizes"]
            assert n["placed"] == 6                                       # front, back, sleeve L+R, collar x2
