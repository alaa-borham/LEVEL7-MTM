import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ERP = os.path.join(ROOT, "reference", "erp")
XML_52 = os.path.join(ERP, "Order-9-52-1_0.xml")
XML_53 = os.path.join(ERP, "Order-9-53-1_2.xml")
MDL = os.path.join(ROOT, "reference", "grafis", "1-S- alshiaka-2027.mdl")
SCB = os.path.join(ROOT, "reference", "grafis", "alshiaka-2027-S-52.scb")
RES = os.path.join(ROOT, "level7mtm", "resources")
MODEL = os.path.join(RES, "models", "thobe_engine_test_v0_1.json")
PROFILE = os.path.join(RES, "mappings", "level7_erp_ordermaster_v1.json")
XVARS = os.path.join(RES, "xvars", "alshiaka_2027_v0_1.json")

# order 9-53-1_2 converted to X values (mm) — from the real ERP sample
X_9_53 = dict(X1=1570, X3=495, X4=70, X5=70, X6=235, X7=420, X9=690, X12=640, X13=700, X14=830, X31=675, X37=160)
OPTS = dict(body_shape="athlete1", collar="square", sleeve="saada", jabzour="splender", pocket_chest="cut",
            pocket_side="hidden")

needs_mdl = pytest.mark.skipif(not os.path.exists(MDL), reason="customer MDL not in the checkout (not committed)")


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


@pytest.fixture
def profile():
    from level7mtm.erp.mapping import load_profile
    return load_profile(PROFILE)


@pytest.fixture
def model():
    from level7mtm.pattern.model import load_model
    return load_model(MODEL)


@pytest.fixture
def ws(tmp_path):
    from level7mtm.services.workflow import Workspace
    w = Workspace(str(tmp_path / "ws"), actor="test")
    yield w
    w.db.close()


def approved_setup(ws):
    """Test-only: register an *approved* copy of the engine-test model, an approved mapping, confirmed X table
    and confirmed shrinkage so the approval path can be exercised. Never shipped as production data."""
    m = json.load(open(MODEL, encoding="utf-8"))
    m["version"] = "0.1.0+test-approved"
    m["status"] = "approved"
    ws.set_active("model", ws.save_document("model", m))
    p = json.load(open(PROFILE, encoding="utf-8"))
    p["version"] = "1.0.0+test-approved"
    p["status"] = "approved"
    ws.set_active("mapping", ws.save_document("mapping", p))
    x = json.load(open(XVARS, encoding="utf-8"))
    x["version"] = "0.1.0+test-confirmed"
    for e in x["entries"]:
        e["status"] = "confirmed"
    ws.set_active("xvars", ws.save_document("xvars", x))
    _, fin = ws.active("finishing")
    fin = json.loads(json.dumps(fin))
    fin["version"] = "1+test"
    fin["materials"]["O"].update({"length_pct": 2.0, "width_pct": 1.0, "confirmed": True})
    ws.set_active("finishing", ws.save_document("finishing", fin))
    m2 = json.load(open(MODEL, encoding="utf-8"))
    return m2
