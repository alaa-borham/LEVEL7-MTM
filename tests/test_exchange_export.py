import json
import os
import sys
import zipfile

import ezdxf
import pytest
from conftest import OPTS, X_9_53

from level7mtm.export.dxf import write_dxf
from level7mtm.export.pdf import write_pdf
from level7mtm.nesting.exchange import ExchangeError, ExchangeLine, write_exchange
from level7mtm.pattern.finishing import ShrinkSpec, apply_shrinkage
from level7mtm.pattern.geometry import bbox
from level7mtm.pattern.model import evaluate_model

NESTING = os.environ.get("LEVEL7_NESTING_SRC", os.path.join(os.path.dirname(__file__), "..", "..",
                                                             "alaa-borham", "level7-nesting"))


def _lines(model, status=None):
    out = []
    for ref, over in (("9-53-1", {}), ("9-52-1", dict(X3=460, X9=600, X12=540, X13=600, X14=780))):
        r = evaluate_model(model, {**X_9_53, **over}, OPTS)
        out.append(ExchangeLine(ref.rsplit("-", 1)[0], ref, "0", 2 if ref == "9-53-1" else 1, model["model_id"],
                                model["version"], status or model["status"], "g-" + ref, r, 1450, "100"))
    return out


def test_shrinkage_definition():
    fl, fw = ShrinkSpec("O", 2, 1).factors()
    assert fl == pytest.approx(1 / 0.98) and fw == pytest.approx(1 / 0.99)
    assert ShrinkSpec("O", 2, 1, "compensation").factors() == pytest.approx((1.02, 1.01))
    assert ShrinkSpec("O", 2, 1, already_applied_by_source=True).factors() == (1.0, 1.0)


def test_shrinkage_along_grain(model):
    r = evaluate_model(model, X_9_53, OPTS)
    s, used, _ = apply_shrinkage(r, {"O": ShrinkSpec("O", 2, 0)})
    f0 = next(p for p in r.pieces if p.id == "front")
    f1 = next(p for p in s.pieces if p.id == "front")
    h0 = bbox(f0.seam)[3] - bbox(f0.seam)[1]
    h1 = bbox(f1.seam)[3] - bbox(f1.seam)[1]
    w0 = bbox(f0.seam)[2] - bbox(f0.seam)[0]
    w1 = bbox(f1.seam)[2] - bbox(f1.seam)[0]
    assert h1 / h0 == pytest.approx(1 / 0.98) and w1 == pytest.approx(w0)      # grain is vertical
    c0 = next(p for p in r.pieces if p.id == "collar")
    c1 = next(p for p in s.pieces if p.id == "collar")
    assert (bbox(c1.seam)[2] - bbox(c1.seam)[0]) / (bbox(c0.seam)[2] - bbox(c0.seam)[0]) == pytest.approx(1 / 0.98)


def test_exchange_blocks_unapproved(model, tmp_path):
    with pytest.raises(ExchangeError):
        write_exchange(_lines(model), str(tmp_path / "x.l7n"))


def test_exchange_contract(model, tmp_path):
    path, contract = write_exchange(_lines(model, "approved"), str(tmp_path / "x.l7n"))
    with zipfile.ZipFile(path) as z:
        doc = json.loads(z.read("project.json"))
        assert json.loads(z.read("level7mtm_contract.json")) == contract
    proj = doc["project"]
    assert doc["format"] == "level7-nesting-project" and doc["format_version"] == 1
    assert proj["cut_order"]["size_quantities"] == {"thobe-engine-test:9-53-1_0": 2, "thobe-engine-test:9-52-1_0": 1}
    sleeve = next(p for p in proj["pieces"] if p["uid"] == "thobe-engine-test:sleeve")
    assert (sleeve["default_count"], sleeve["default_mirrored"], sleeve["allow_mirror"]) == (1, 1, True)
    front = next(p for p in proj["pieces"] if p["uid"] == "thobe-engine-test:front")
    assert front["allow_mirror"] is False                     # never mirrored to fake a left/right piece
    assert abs(front["grain_angle"] - 90.0) < 1e-9
    cs = [p for p in contract["pieces"] if p["piece_uid"].endswith(":sleeve")]
    assert {p["total_copies"] for p in cs} == {4, 2}          # (1 + 1 mirrored) x garments
    r0 = _lines(model)[0].result
    f = next(p for p in r0.pieces if p.id == "front")
    assert front["sizes"]["thobe-engine-test:9-53-1_0"]["cut"] == [list(p) for p in f.cut]   # no dimension change


@pytest.mark.skipif(not os.path.isdir(NESTING), reason="LEVEL7 Nesting source not available")
def test_nesting_opens_and_nests(model, tmp_path):
    pytest.importorskip("shapely")
    pytest.importorskip("pyclipper")
    sys.path.insert(0, NESTING)
    from level7.model import FabricSettings, NestSettings
    from level7.project_storage.store import load_project
    from level7.service import run_nesting
    path, contract = write_exchange(_lines(model), str(tmp_path / "t.l7n"), test_only=True)
    proj = load_project(path)
    m, info = run_nesting(proj, FabricSettings(width=1450), NestSettings(time_limit=5, engine="builtin"), workers=1)
    assert info["validation"].ok
    placed = [pl for pl in m.placements if pl.placed]
    assert len(placed) == sum(p["total_copies"] for p in contract["pieces"]) == 18
    assert {pl.size for pl in placed} == {"thobe-engine-test:9-53-1_0", "thobe-engine-test:9-52-1_0"}


def test_dxf_true_scale(model, tmp_path):
    r = evaluate_model(model, X_9_53, OPTS)
    p = write_dxf(r, str(tmp_path / "a.dxf"), "9-53-1_2", ["test"])
    doc = ezdxf.readfile(p)
    assert doc.header["$INSUNITS"] == 4
    assert {"CUT", "SEAM", "NOTCH", "GRAIN", "TEXT"} <= {ly.dxf.name for ly in doc.layers}
    blk = doc.blocks.get("9-53-1_2_front")
    cut = [e for e in blk if e.dxftype() == "LWPOLYLINE" and e.dxf.layer == "CUT"][0]
    pts = [tuple(v[:2]) for v in cut.get_points()]
    f = next(x for x in r.pieces if x.id == "front")
    assert max(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(pts, f.cut)) < 1e-9
    assert len([e for e in blk if e.dxf.layer == "NOTCH"]) == 2


def test_pdf(model, tmp_path):
    r = evaluate_model(model, X_9_53, OPTS)
    p = write_pdf(r, str(tmp_path / "a.pdf"), {"order": "9-53-1_2"}, [("CH — الصدر", "690.0", "69.00 cm")])
    assert open(p, "rb").read(5) == b"%PDF-"
