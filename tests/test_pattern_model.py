import copy

import pytest
from conftest import OPTS, X_9_53

from level7mtm.pattern.model import ModelError, evaluate_model, load_model

# Regression lock for the engine-test model 0.1.0 with order 9-53-1_2 (no shrinkage). Precision: 0.001 mm.
# If an intentional engine change alters geometry, ENGINE_VERSION must change and this value with it.
FINGERPRINT_9_53 = "ec423fc60873dc162e4a7181801fd4c511623b5addb7dd911d5f8d7f6368f25a"


def run(model, **over):
    inp = {**X_9_53, **over}
    return evaluate_model(model, inp, OPTS)


def test_stable_result(model):
    a, b = run(model), run(model)
    assert a.geometry_fingerprint() == b.geometry_fingerprint() == FINGERPRINT_9_53
    assert a.ok and {p.id for p in a.pieces} == {"front", "back", "sleeve", "collar"}


def test_one_measurement_changes_expected_dimension(model):
    base, wider = run(model), run(model, X9=710)            # chest half girth +20 mm
    fb = next(p for p in base.pieces if p.id == "front")
    fw = next(p for p in wider.pieces if p.id == "front")
    assert wider.points["FCR"][0] - base.points["FCR"][0] == pytest.approx(10.0)    # quarter = X9/2 -> +10
    assert base.points["FBR"] == wider.points["FBR"]                               # hem unchanged
    assert fw.edge_lengths["hem"] == fb.edge_lengths["hem"]
    longer = run(model, X1=1600)
    assert longer.points["FBR"][1] - base.points["FBR"][1] == pytest.approx(-30.0)


def test_asymmetric_shoulders(model):
    r = run(model, X4=90, X5=60)
    assert r.points["FSR"][1] != r.points["FSL"][1]
    assert r.points["FSR"][1] == pytest.approx(-(90 - 90 / 8))      # GRAFIS Z11 = X4-(X4/8)
    assert r.points["FSL"][1] == pytest.approx(-(60 - 1))           # GRAFIS Z12 = X5-1


@pytest.mark.parametrize("over", [dict(X1=1100, X3=380, X9=480, X12=420, X13=480, X14=600, X7=340),
                                  dict(X1=1780, X3=640, X9=880, X12=880, X13=920, X14=1080, X7=500)])
def test_small_and_large(model, over):
    r = run(model, **over)
    assert not [i for i in r.issues if i.severity == "blocking"], [i.message for i in r.issues]


def test_large_belly_within_rules(model):
    r = run(model, X12=880, X13=900, X14=1000)
    assert r.ok


def test_out_of_range_blocks(model):
    r = run(model, X9=2000)
    assert not r.ok and any(i.code == "input.range" for i in r.issues)
    assert not r.pieces                                          # no geometry from invalid input


def test_missing_input_blocks(model):
    inp = dict(X_9_53)
    del inp["X9"]
    r = evaluate_model(model, inp, OPTS)
    assert any(i.code == "input.missing" for i in r.issues) and not r.pieces


def test_unknown_or_unimplemented_option(model):
    r = evaluate_model(model, X_9_53, {**OPTS, "collar": "mandarin"})
    assert any(i.code == "option.unknown" for i in r.issues)
    r = evaluate_model(model, X_9_53, {**OPTS, "sleeve": "kabak"})
    assert any(i.code == "constraint" for i in r.issues) and not r.ok


def test_option_changes_piece_list(model):
    m = copy.deepcopy(model)
    m["options"]["sleeve"]["values"]["none"] = {"en": "test"}
    m["constraints"] = []
    r = evaluate_model(m, X_9_53, {**OPTS, "sleeve": "none"})
    assert "sleeve" not in {p.id for p in r.pieces}


def test_circular_formula(model):
    m = copy.deepcopy(model)
    m["variables"]["chest_y"] = {"formula": "hip_y - 400"}
    r = evaluate_model(m, X_9_53, OPTS)
    assert any("circular" in i.message for i in r.issues) and not r.pieces


def test_circular_points(model):
    m = copy.deepcopy(model)
    m["points"]["FNR"] = {"from": "FCN", "dx": "1"}
    m["points"]["FCN"] = {"from": "FNR", "dx": "1"}
    r = evaluate_model(m, X_9_53, OPTS)
    assert any("circular point" in i.message for i in r.issues)


def test_allowance_failure_is_blocking(model):
    m = copy.deepcopy(model)
    for e in m["pieces"][3]["outline"]:       # collar band 35 mm high
        e["allowance"] = "40"
    m["pieces"][3]["outline"][2]["allowance"] = "-5"
    r = evaluate_model(m, X_9_53, OPTS)
    assert any(i.code == "allowance.negative" for i in r.issues)


def test_curve_failure(model):
    m = copy.deepcopy(model)
    m["paths"]["front_arm_r"] = {"curve": ["FCR", "FCR"]}
    r = evaluate_model(m, X_9_53, OPTS)
    assert not r.ok


def test_seam_relations_and_checks(model):
    r = run(model)
    rel = {x["label"]: x for x in r.seam_relations}
    assert rel["side seam R"]["ok"] and rel["shoulder L"]["ok"]
    chk = {c["id"]: c for c in r.checks}
    assert chk["chest_quarter"]["ok"] and chk["length"]["ok"] and chk["collar_vs_neck"]["ok"]
    assert not chk["cap_vs_armhole"]["ok"]           # test model: cap rule not defined -> reported for review


def test_relation_tolerance_is_per_pair(model):
    m = copy.deepcopy(model)
    m["seam_relations"][0]["diff"] = "5"              # intended ease of 5 mm between front and back side seam
    r = evaluate_model(m, X_9_53, OPTS)
    assert any(i.code == "relation.mismatch" for i in r.issues)


def test_bad_model_file():
    with pytest.raises(ModelError):
        load_model({"format": "x"})
