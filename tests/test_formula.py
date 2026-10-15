import pytest

from level7mtm.formula.expr import FormulaError, UndefinedVariable, compute, parse, references
from level7mtm.formula.graph import solve


@pytest.mark.parametrize("src,env,expected", [
    ("X7/6.7+Z1", {"X7": 670, "Z1": 2}, 102.0),
    ("CMDIF[X4>=64]{51.5}[X4<64]{48}{50}", {"X4": 70}, 51.5),
    ("CMDIF[X4>=64]{51.5}[X4<64]{48}{50}", {"X4": 60}, 48.0),
    ("CMDIF[X4>=83]{0}[X4>=76]{-7}{0}", {"X4": 80}, -7.0),
    ("CMDIF[X12/2<500]{40}[X12/2<=550]{35}{20}", {"X12": 1200}, 20.0),
    ("CMDIF[X4>50]{X4/20}{0}-10", {"X4": 100}, -5.0),
    ("Z46-(Z48+30+Z39)", {"Z46": 400, "Z48": 300, "Z39": 15}, 55.0),
    (".5", {}, 0.5),
    ("if(a > 1 and not b, max(1, 2, 3), 0)", {"a": 2, "b": 0}, 3.0),
    ("hypot(B.x - A.x, B.y - A.y)", {"A.x": 0, "A.y": 0, "B.x": 3, "B.y": 4}, 5.0),
    ("2^3", {}, 8.0),
])
def test_values(src, env, expected):
    assert compute(src, env) == pytest.approx(expected)


@pytest.mark.parametrize("src", [
    "X35/",                      # real malformed formula found in the MDL (part 2, Z76)
    "__import__('os')",
    "open('x')",
    "a.__class__",
    "1;2",
    "X1**2",
    "eval(1)",
    "CMDIF{1}",
    "",
    "[1]",
    "lambda: 1",
])
def test_rejected(src):
    with pytest.raises(FormulaError):
        compute(src, {"X35": 1, "X1": 1, "a": 1})


def test_no_python_eval_reachable():
    # names are only looked up in the given environment; builtins are never visible
    with pytest.raises(UndefinedVariable):
        compute("__builtins__", {})


def test_length_and_depth_limits():
    with pytest.raises(FormulaError):
        parse("1+" * 1500 + "1")
    with pytest.raises(FormulaError):
        parse("(" * 200 + "1" + ")" * 200)


def test_division_by_zero():
    with pytest.raises(FormulaError):
        compute("1/(a-a)", {"a": 3})


def test_references():
    assert references(parse("CMDIF[X4>50]{X4/20}{Z1+len.neck}")) == {"X4", "Z1", "len.neck"}


def test_graph_order_cycles_undefined():
    g = solve({"a": "b+1", "b": "c*2", "c": "X1", "d": "e+1", "e": "d+1", "f": "missing+1", "h": "f+1"}, {"X1": 3})
    assert g.values["a"] == 7 and g.values["b"] == 6
    assert "circular" in g.results["d"].error and "circular" in g.results["e"].error
    assert g.undefined == {"missing": ["f"]}
    assert "depends on failed" in g.results["h"].error
    assert "f" not in g.values and "h" not in g.values          # no stale / default values


def test_self_reference_is_cycle():
    g = solve({"a": "a+1"}, {})
    assert g.cycles == [["a"]] and not g.results["a"].ok
