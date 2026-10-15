"""ERP XML import. The two real samples are used as delivered; every other case is derived from them in the
test itself (clearly test data, not ERP output)."""
import json

import pytest
from conftest import PROFILE, XML_52, XML_53, read

from level7mtm.erp.importer import import_bytes
from level7mtm.erp.mapping import MappingError, load_profile
from level7mtm.erp.xmlsafe import XmlError, load_bytes


def test_real_sample_52(profile):
    r = import_bytes(read(XML_52), profile)
    assert r.ok and r.encoding == "UTF-8" and r.root == "OrderMaster"
    assert r.order_number == "9-52-1_0" and r.order_date == "2026-07-30T13:51:09"
    assert [ln.number for ln in r.lines] == ["9-52-1_0", "9-52-2_0"]       # multi-line order
    ln = r.lines[0]
    assert (ln.reseller, ln.order_no, ln.line_no, ln.revision, ln.line_ref) == ("9", "52", "1", "0", "9-52-1")
    assert ln.quantity == 1 and ln.fabric_width_mm == 1450 and ln.fabric_number == "100"
    assert ln.measurements["CH"].value_mm == 600.0 and ln.measurements["CH"].kind == "half_girth"
    assert ln.measurements["CH"].source_value == "60.00" and ln.measurements["CH"].source_unit == "cm"
    assert ln.options == {"body_shape": "slim2", "sleeve": "saada", "pocket_side": "hidden", "jabzour": "splender",
                          "collar": "classic", "pocket_chest": "cut"}
    assert "COG" in ln.measurements and "COS" not in ln.measurements
    assert r.lines[1].options["collar"] == "square" and "COS" in r.lines[1].measurements
    assert ln.programs[0]["use_in_calculation"] is False
    assert not ln.problems


def test_real_sample_53_revision(profile):
    r = import_bytes(read(XML_53), profile)
    assert [(x.line_ref, x.revision) for x in r.lines] == [("9-53-1", "2"), ("9-53-2", "2")]
    assert r.lines[0].measurements["DSR"].value_mm == 70.0


def test_damaged_xml(profile):
    r = import_bytes(read(XML_52)[:-40], profile)          # also: a file whose copy did not finish
    assert not r.ok and r.problems[0].code == "xml.invalid"


def test_empty_file(profile):
    assert not import_bytes(b"   ", profile).ok


@pytest.mark.parametrize("payload", [
    b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><OrderMaster>&a;</OrderMaster>',
    b'<?xml version="1.0"?><!DOCTYPE x SYSTEM "http://example.com/x.dtd"><OrderMaster/>',
    b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><OrderMaster>&e;</OrderMaster>',
])
def test_dtd_and_entities_forbidden(payload):
    with pytest.raises(XmlError):
        load_bytes(payload)


def test_depth_limit():
    with pytest.raises(XmlError):
        load_bytes(b"<a>" * 100 + b"</a>" * 100)


def test_wrong_root(profile):
    r = import_bytes(b"<Invoice/>", profile)
    assert not r.ok and r.problems[0].code == "profile.root"


def test_missing_field(profile):
    data = read(XML_52).replace(b'<Type Name="Chest" Abbreviation="CH" Value="60.00"/>', b"", 1)
    ln = import_bytes(data, profile).lines[0]
    assert ln.needs_review and any(p.code == "measure.missing" and p.field == "CH" for p in ln.problems)


def test_unknown_option_code(profile):
    data = read(XML_52).replace(b'Variation Name="Jabzour Splender"', b'Variation Name="Jabzour Zigzag"', 1)
    ln = import_bytes(data, profile).lines[0]
    assert ln.needs_review and any(p.code == "option.unknown" for p in ln.problems)
    assert "jabzour" not in ln.options                    # never guessed


def test_unknown_measurement_code(profile):
    data = read(XML_52).replace(b'Abbreviation="EB"', b'Abbreviation="EBX"', 1)
    ln = import_bytes(data, profile).lines[0]
    assert any(p.code == "measure.unknown" for p in ln.problems)


def test_not_a_number_and_range(profile):
    data = read(XML_52).replace(b'Abbreviation="SH" Value="46.00"', b'Abbreviation="SH" Value="46,5"', 1)
    data = data.replace(b'Abbreviation="FL" Value="157.00"', b'Abbreviation="FL" Value="1570"', 1)
    ln = import_bytes(data, profile).lines[0]
    codes = {(p.code, p.field) for p in ln.problems}
    assert ("measure.invalid", "SH") in codes and ("measure.range", "FL") in codes
    assert ln.measurements["SH"].value_mm is None         # missing is not zero


def test_unit_difference(profile):
    """A profile version declaring mm reads the same file 10x smaller -> range check catches the mismatch."""
    p = json.loads(json.dumps(profile))
    p["version"] = "test-mm"
    for f in p["measurements"]["fields"].values():
        f["unit"] = "mm"
    ln = import_bytes(read(XML_52), load_profile(p)).lines[0]
    assert ln.measurements["CH"].value_mm == 60.0
    p2 = json.loads(json.dumps(profile))
    p2["measurements"]["fields"]["CH"]["unit"] = "furlong"
    with pytest.raises(MappingError):
        load_profile(p2)


def test_namespaces(profile):
    data = read(XML_52).replace(b"<OrderMaster ", b'<OrderMaster xmlns="urn:test:erp" ', 1)
    assert not import_bytes(data, profile).ok              # profile without namespaces does not match silently
    p = json.loads(json.dumps(profile))
    p["namespaces"] = {"e": "urn:test:erp"}
    p["detect"]["root"] = "OrderMaster"
    p["lines"]["path"] = "e:Orders/e:Order"
    for k in ("item", "fabric_number", "fabric_color", "measurement_table", "basic_size"):
        p["lines"][k] = "/".join(("e:" + s if not s.startswith("@") else s) for s in p["lines"][k].split("/"))
    p["lines"]["quantity"]["path"] = "e:Item/@Quantity"
    p["lines"]["fabric_width"]["path"] = "e:Fabric/@Width"
    p["measurements"]["path"] = "e:Measurement/e:Type"
    p["options"]["path"] = "e:Variations/e:Variation/@Name"
    p["programs"]["path"] = "e:Programs/e:Program"
    p["order"]["remarks"] = "e:Remarks/text()"
    r = import_bytes(data, load_profile(p))
    assert r.ok and len(r.lines) == 2 and r.lines[0].measurements["CH"].value_mm == 600


def test_conflicting_options(profile):
    data = read(XML_52).replace(b'<Variation Name="Slim2"/>', b'<Variation Name="Slim2"/><Variation Name="Athlete1"/>', 1)
    ln = import_bytes(data, profile).lines[0]
    assert any(p.code == "option.conflict" for p in ln.problems)


def test_structure_report(profile):
    r = import_bytes(read(XML_52), profile)
    paths = r.structure["paths"]
    assert paths["OrderMaster/Orders/Order"]["count"] == 2
    assert paths["OrderMaster/Orders/Order/Measurement/Type"]["count"] == 36
    assert "Abbreviation" in paths["OrderMaster/Orders/Order/Measurement/Type"]["attributes"]
    assert r.structure["namespaces"] == []


def test_profile_file_valid():
    load_profile(PROFILE)
