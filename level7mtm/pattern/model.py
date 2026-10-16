"""Parametric model definitions and their evaluation.

A model file (JSON, ``format: level7mtm-model``) describes inputs, derived variables, points, paths, pieces,
options, compatibility rules, seam relations and dimension checks. Every geometric value is a formula in the
restricted language of ``level7mtm.formula.expr``. Evaluation is deterministic: the same model version, inputs,
options and settings always give the same geometry (no randomness, no AI).

Names available inside formulas:
    X1, Z9, chest_half ...      inputs and variables
    A.x, A.y                    coordinates of an already defined point
    len.<path>                  length of a path
    option.<group>.<value>      1 if that option value is selected, else 0
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field

from ..formula.expr import FormulaError, evaluate, parse, references
from ..formula.graph import solve
from .allowance import Issue, make_cut
from .geometry import (GeometryError, Path, Pt, Segment, angle_of, bezier_by_tangents, dist, intersect_line_circle,
                       intersect_lines, mirror, polar, project, self_intersections, unit, sub, add, mul)

FORMAT = "level7mtm-model"
FORMAT_VERSION = 1
MODEL_STATUSES = ("engine_test", "draft", "in_review", "approved", "retired")


class ModelError(ValueError):
    pass


@dataclass
class PieceResult:
    id: str
    name: dict
    material: str
    copies: int
    mirrored: int
    seam_edges: list[tuple[str, Path]]
    seam: list[Pt]
    cut: list[Pt]
    allowances: dict[str, float]
    notches: list[dict]
    grain: tuple[Pt, Pt] | None
    labels: list[dict]
    internal: list[Path]
    edge_lengths: dict[str, float]
    allow_rotation: list[float] = field(default_factory=lambda: [0.0, 180.0])
    allow_mirror_substitute: bool = False
    preview: bool = True          # shown in the preview
    production: bool = True       # sent to nesting / production exports (set by the piece plan)


@dataclass
class PatternResult:
    model_id: str
    model_version: str
    model_status: str
    inputs: dict[str, float]
    options: dict[str, str]
    variables: dict[str, float]
    points: dict[str, Pt]
    paths: dict[str, Path]
    pieces: list[PieceResult]
    issues: list[Issue]
    checks: list[dict]
    seam_relations: list[dict]

    @property
    def ok(self) -> bool:
        return not any(i.severity == "blocking" for i in self.issues)

    def geometry_fingerprint(self, decimals: int = 3) -> str:
        """SHA-256 over the rounded cut and seam lines of all pieces (documented numeric precision: 0.001 mm)."""
        h = hashlib.sha256()
        for p in sorted(self.pieces, key=lambda p: p.id):
            h.update(p.id.encode())
            for pt in p.cut + [(-1e9, -1e9)] + p.seam:
                h.update(f"{pt[0]:.{decimals}f},{pt[1]:.{decimals}f};".encode())
        return h.hexdigest()


def load_model(path_or_dict) -> dict:
    if isinstance(path_or_dict, dict):
        doc = copy.deepcopy(path_or_dict)
    else:
        with open(path_or_dict, encoding="utf-8") as fh:
            doc = json.load(fh)
    check_model(doc)
    return doc


def model_hash(doc: dict) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def check_model(doc: dict) -> None:
    if doc.get("format") != FORMAT:
        raise ModelError("not a LEVEL7 MTM model file")
    if int(doc.get("format_version", 0)) > FORMAT_VERSION:
        raise ModelError(f"model format {doc.get('format_version')} is newer than supported {FORMAT_VERSION}")
    for key in ("model_id", "version", "status", "pieces", "points", "paths"):
        if key not in doc:
            raise ModelError(f"model file misses '{key}'")
    if doc["status"] not in MODEL_STATUSES:
        raise ModelError(f"unknown model status {doc['status']!r}")
    for kind in ("points", "paths"):
        for name in doc[kind]:
            if not name.replace("_", "").isalnum():
                raise ModelError(f"{kind[:-1]} name {name!r} must be letters, digits and _ only")
    ids = [p["id"] for p in doc["pieces"]]
    if len(ids) != len(set(ids)):
        raise ModelError("duplicate piece ids")


class _Ctx:
    def __init__(self, doc, values, options):
        self.doc = doc
        self.values = values
        self.options = options
        self.points: dict[str, Pt] = {}
        self.paths: dict[str, Path] = {}
        self.busy: set[str] = set()
        self.issues: list[Issue] = []

    # --- formula lookup with lazy point / path resolution
    def lookup(self, name: str) -> float:
        if name in self.values:
            return self.values[name]
        if name.startswith("option."):
            parts = name.split(".")
            if len(parts) == 3:
                return 1.0 if self.options.get(parts[1]) == parts[2] else 0.0
        if name.startswith("len."):
            return self.path(name[4:]).length()
        if name.endswith(".x") or name.endswith(".y"):
            p = self.point(name[:-2])
            return p[0] if name.endswith(".x") else p[1]
        raise FormulaError(f"undefined variable {name}")

    def f(self, text, where: str) -> float:
        if isinstance(text, (int, float)):
            return float(text)
        try:
            return evaluate(parse(str(text)), self.lookup, str(text))
        except FormulaError as e:
            raise ModelError(f"{where}: {e}") from None

    def point(self, name: str) -> Pt:
        if name in self.points:
            return self.points[name]
        spec = self.doc["points"].get(name)
        if spec is None:
            raise ModelError(f"undefined point {name}")
        if name in self.busy:
            raise ModelError(f"circular point definition involving {name}")
        self.busy.add(name)
        try:
            p = self._point(name, spec)
        except GeometryError as e:
            raise ModelError(f"point {name}: {e}") from None
        finally:
            self.busy.discard(name)
        self.points[name] = p
        return p

    def _point(self, name, s) -> Pt:
        w = f"point {name}"
        if "xy" in s:
            return (self.f(s["xy"][0], w + ".x"), self.f(s["xy"][1], w + ".y"))
        if "from" in s:
            o = self.point(s["from"])
            return (o[0] + self.f(s.get("dx", 0), w + ".dx"), o[1] + self.f(s.get("dy", 0), w + ".dy"))
        if "polar" in s:
            return polar(self.point(s["polar"]), self.f(s["angle"], w + ".angle"), self.f(s["dist"], w + ".dist"))
        if "intersect" in s:
            (a, b), (c, d) = s["intersect"]
            return intersect_lines(self.point(a), self.point(b), self.point(c), self.point(d))
        if "project" in s:
            a, b = s["on"]
            return project(self.point(s["project"]), self.point(a), self.point(b))
        if "along" in s:
            a, b = self.point(s["along"]), self.point(s["to"])
            return add(a, mul(unit(sub(b, a)), self.f(s["dist"], w + ".dist")))
        if "on_path" in s:
            return self.path(s["on_path"]).point_at_length(self.f(s["dist"], w + ".dist"))
        if "mirror" in s:
            a, b = s["axis"]
            return mirror(self.point(s["mirror"]), self.point(a), self.point(b))
        if "circle" in s:
            a, b = s["circle"]
            return intersect_line_circle(self.point(a), self.point(b), self.point(s["center"]),
                                         self.f(s["radius"], w + ".radius"), s.get("pick", "far"))
        raise ModelError(f"{w}: unknown point construction {sorted(s)}")

    def path(self, name: str) -> Path:
        rev = name.startswith("-")
        key = name[1:] if rev else name
        if key not in self.paths:
            spec = self.doc["paths"].get(key)
            if spec is None:
                raise ModelError(f"undefined path {key}")
            if key in self.busy:
                raise ModelError(f"circular path definition involving {key}")
            self.busy.add(key)
            try:
                self.paths[key] = self._path(key, spec)
            except GeometryError as e:
                raise ModelError(f"path {key}: {e}") from None
            finally:
                self.busy.discard(key)
        return self.paths[key].reversed() if rev else self.paths[key]

    def _path(self, name, s) -> Path:
        w = f"path {name}"
        if "line" in s:
            a, b = s["line"]
            return Path([Segment((self.point(a), self.point(b)))], name)
        if "curve" in s:
            a, b = s["curve"]
            pa, pb = self.point(a), self.point(b)
            chord = angle_of(pa, pb)
            sa = self.f(s.get("start_angle", chord), w + ".start_angle")
            ea = self.f(s.get("end_angle", chord), w + ".end_angle")
            return Path([bezier_by_tangents(pa, pb, sa, ea, self.f(s.get("fa", 1 / 3), w + ".fa"),
                                            self.f(s.get("fb", 1 / 3), w + ".fb"))], name)
        if "bezier" in s:
            return Path([Segment(tuple(self.point(p) for p in s["bezier"]))], name)
        if "chain" in s:
            segs = []
            for part in s["chain"]:
                segs.extend(self.path(part).segments)
            return Path(segs, name)
        raise ModelError(f"{w}: unknown path construction {sorted(s)}")


def _when(ctx: _Ctx, expr, where) -> bool:
    if expr in (None, "", True):
        return True
    return bool(ctx.f(expr, where))


def evaluate_model(doc: dict, inputs: dict[str, float], options: dict[str, str],
                   allowance_overrides: dict[str, float] | None = None) -> PatternResult:
    issues: list[Issue] = []
    # 1. inputs
    for spec in doc.get("inputs", []):
        n = spec["name"]
        if n not in inputs:
            if spec.get("required", True):
                issues.append(Issue("blocking", "input.missing", f"required measurement {n} is missing"))
            continue
        lo, hi = (spec.get("range") or [None, None])
        v = inputs[n]
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            issues.append(Issue("blocking", "input.range",
                                f"{n} = {v:g} mm is outside the model range {lo}–{hi} mm"))
    # 2. options
    groups = doc.get("options", {})
    for g, spec in groups.items():
        val = options.get(g)
        if val is None:
            if spec.get("required", True):
                issues.append(Issue("blocking", "option.missing", f"option '{g}' is not set"))
        elif val not in spec.get("values", {}):
            issues.append(Issue("blocking", "option.unknown", f"option '{g}' = '{val}' is not defined in the model"))
    for g in options:
        if g not in groups:
            issues.append(Issue("blocking", "option.unknown_group", f"option group '{g}' is not defined in the model"))
    # 3. variables
    opt_vals = {f"option.{g}.{v}": (1.0 if options.get(g) == v else 0.0)
                for g, spec in groups.items() for v in spec.get("values", {})}
    var_formulas = {k: (v["formula"] if isinstance(v, dict) else str(v)) for k, v in doc.get("variables", {}).items()}
    graph = solve(var_formulas, {**inputs, **opt_vals})
    for name, err in graph.errors.items():
        issues.append(Issue("blocking", "variable.error", f"variable {name} = {var_formulas[name]!r}: {err}"))
    values = {**inputs, **opt_vals, **graph.values}
    ctx = _Ctx(doc, values, options)
    ctx.issues = issues

    for rule in doc.get("constraints", []):
        try:
            if not ctx.f(rule["expr"], "constraint"):
                issues.append(Issue(rule.get("severity", "blocking"), "constraint", rule.get("message", rule["expr"])))
        except ModelError as e:
            issues.append(Issue("blocking", "constraint.error", str(e)))

    pieces: list[PieceResult] = []
    if not any(i.severity == "blocking" and i.code.startswith(("input", "option", "variable")) for i in issues):
        for ps in doc["pieces"]:
            try:
                if not _when(ctx, ps.get("when"), f"piece {ps['id']}.when"):
                    continue
                pieces.append(_build_piece(ctx, ps, allowance_overrides or {}))
            except ModelError as e:
                issues.append(Issue("blocking", "piece.error", str(e), piece=ps["id"]))

    relations = _seam_relations(ctx, doc, pieces, issues)
    checks = _checks(ctx, doc, pieces, issues)
    return PatternResult(doc["model_id"], str(doc["version"]), doc["status"], dict(inputs), dict(options),
                         graph.values, dict(ctx.points), dict(ctx.paths), pieces, issues, checks, relations)


def _build_piece(ctx: _Ctx, ps: dict, overrides: dict) -> PieceResult:
    pid = ps["id"]
    edges = []
    allowances = {}
    seam_edges = []
    for e in ps["outline"]:
        path = ctx.path(e["path"])
        ename = e.get("edge", e["path"].lstrip("-"))
        a = overrides.get(f"{pid}.{ename}", ctx.f(e.get("allowance", ps.get("allowance", 0)), f"{pid}.{ename}.allowance"))
        allowances[ename] = a
        seam_edges.append((ename, path))
        edges.append((ename, path.flatten(0.05), a))
    seam = [p for _, pts, _ in edges for p in pts[:-1]]
    for p in self_intersections(seam):
        ctx.issues.append(Issue("blocking", "seam.self_intersection", "seam line crosses itself", p, pid))
    res = make_cut(edges, ps.get("miter_limit", 4.0), pid)
    ctx.issues.extend(res.issues)
    edge_lengths = {n: p.length() for n, p in seam_edges}
    for n, L in edge_lengths.items():
        ctx.values[f"edge.{pid}.{n}"] = L
    notches = []
    for nd in ps.get("notches", []):
        if "point" in nd:
            pos = ctx.point(nd["point"])
            edge = nd.get("edge")
        else:
            edge = nd["edge"]
            path = dict(seam_edges).get(edge)
            if path is None:
                raise ModelError(f"piece {pid}: notch on unknown edge {edge}")
            pos = path.point_at_length(ctx.f(nd["dist"], f"{pid}.notch"))
        notches.append({"position": pos, "edge": edge, "id": nd.get("id", ""), "pair": nd.get("pair")})
    grain = None
    if ps.get("grain"):
        a, b = ps["grain"]
        grain = (ctx.point(a), ctx.point(b))
    labels = [{"text": lb["text"], "position": ctx.point(lb["at"]), "height": lb.get("height", 10)}
              for lb in ps.get("labels", [])]
    internal = [ctx.path(n) for n in ps.get("internal", [])]
    copies = ps.get("copies", {"normal": 1, "mirrored": 0})
    return PieceResult(pid, ps.get("name", {"en": pid, "ar": pid}), ps.get("material", "O"),
                       int(copies.get("normal", 1)), int(copies.get("mirrored", 0)), seam_edges, seam, res.cut,
                       allowances, notches, grain, labels, internal, edge_lengths,
                       [float(r) for r in ps.get("rotations", [0, 180])], bool(ps.get("mirror_substitute", False)))


def _seam_relations(ctx, doc, pieces, issues) -> list[dict]:
    have = {p.id: p for p in pieces}
    out = []
    for rel in doc.get("seam_relations", []):
        pa, ea = rel["a"].split(":")
        pb, eb = rel["b"].split(":")
        if pa not in have or pb not in have:
            continue                          # pieces not part of this option set
        la, lb = have[pa].edge_lengths.get(ea), have[pb].edge_lengths.get(eb)
        if la is None or lb is None:
            issues.append(Issue("blocking", "relation.edge", f"seam relation {rel['a']} / {rel['b']}: unknown edge"))
            continue
        try:
            expected = ctx.f(rel.get("diff", 0), "relation.diff")
        except ModelError as e:
            issues.append(Issue("blocking", "relation.error", str(e)))
            continue
        tol = float(rel.get("tolerance", 1.0))
        diff = la - lb
        ok = abs(diff - expected) <= tol
        r = {"a": rel["a"], "b": rel["b"], "len_a": la, "len_b": lb, "diff": diff, "expected_diff": expected,
             "tolerance": tol, "ok": ok, "label": rel.get("label", "")}
        out.append(r)
        if not ok:
            issues.append(Issue(rel.get("severity", "blocking"), "relation.mismatch",
                                f"{rel['a']} ({la:.1f}) − {rel['b']} ({lb:.1f}) = {diff:.1f} mm, expected "
                                f"{expected:.1f} ± {tol:g} mm", piece=pa, edge=ea))
    return out


def _checks(ctx, doc, pieces, issues) -> list[dict]:
    out = []
    have = {p.id for p in pieces}
    for chk in doc.get("checks", []):
        if chk.get("pieces") and not set(chk["pieces"]) <= have:
            continue
        try:
            actual = ctx.f(chk["measure"], f"check {chk['id']}")
            target = ctx.f(chk["target"], f"check {chk['id']}")
        except ModelError as e:
            issues.append(Issue("blocking", "check.error", str(e)))
            continue
        tol = float(chk.get("tolerance", 1.0))
        ok = abs(actual - target) <= tol
        out.append({"id": chk["id"], "label": chk.get("label", chk["id"]), "actual": actual, "target": target,
                    "tolerance": tol, "ok": ok})
        if not ok:
            issues.append(Issue(chk.get("severity", "blocking"), "check.dimension",
                                f"{chk.get('label', chk['id'])}: {actual:.1f} mm, target {target:.1f} ± {tol:g} mm"))
    return out
