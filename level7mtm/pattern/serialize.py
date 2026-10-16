"""PatternResult <-> JSON (stored with every generation so a result can be shown and exported later without
re-computing, and re-computed results can be compared with the stored ones)."""
from __future__ import annotations

from .allowance import Issue
from .geometry import Path, Segment
from .model import PatternResult, PieceResult


def _path_to(p: Path) -> list:
    return [[list(pt) for pt in s.pts] for s in p.segments]


def _path_from(d, name="") -> Path:
    return Path([Segment(tuple(tuple(pt) for pt in s)) for s in d], name)


def issue_to(i: Issue) -> dict:
    return {"severity": i.severity, "code": i.code, "message": i.message,
            "position": list(i.position) if i.position is not None else None, "piece": i.piece, "edge": i.edge}


def issue_from(d: dict) -> Issue:
    return Issue(d["severity"], d["code"], d["message"], tuple(d["position"]) if d.get("position") else None,
                 d.get("piece"), d.get("edge"))


def result_to_json(r: PatternResult) -> dict:
    return {
        "model_id": r.model_id, "model_version": r.model_version, "model_status": r.model_status,
        "inputs": r.inputs, "options": r.options, "variables": r.variables,
        "pieces": [{
            "id": p.id, "name": p.name, "material": p.material, "copies": p.copies, "mirrored": p.mirrored,
            "seam_edges": [[n, _path_to(path)] for n, path in p.seam_edges],
            "seam": [list(x) for x in p.seam], "cut": [list(x) for x in p.cut], "allowances": p.allowances,
            "notches": [{**n, "position": list(n["position"])} for n in p.notches],
            "grain": [list(p.grain[0]), list(p.grain[1])] if p.grain else None,
            "labels": [{**lb, "position": list(lb["position"])} for lb in p.labels],
            "internal": [_path_to(x) for x in p.internal], "edge_lengths": p.edge_lengths,
            "allow_rotation": p.allow_rotation, "allow_mirror_substitute": p.allow_mirror_substitute,
            "preview": p.preview, "production": p.production,
        } for p in r.pieces],
        "issues": [issue_to(i) for i in r.issues], "checks": r.checks, "seam_relations": r.seam_relations,
    }


def result_from_json(d: dict) -> PatternResult:
    pieces = []
    for p in d["pieces"]:
        pieces.append(PieceResult(
            p["id"], p["name"], p["material"], p["copies"], p["mirrored"],
            [(n, _path_from(path, n)) for n, path in p["seam_edges"]],
            [tuple(x) for x in p["seam"]], [tuple(x) for x in p["cut"]], p["allowances"],
            [{**n, "position": tuple(n["position"])} for n in p["notches"]],
            (tuple(p["grain"][0]), tuple(p["grain"][1])) if p.get("grain") else None,
            [{**lb, "position": tuple(lb["position"])} for lb in p["labels"]],
            [_path_from(x) for x in p["internal"]], p["edge_lengths"], p.get("allow_rotation", [0.0, 180.0]),
            p.get("allow_mirror_substitute", False), p.get("preview", True), p.get("production", True)))
    return PatternResult(d["model_id"], d["model_version"], d["model_status"], d["inputs"], d["options"],
                         d["variables"], {}, {}, pieces, [issue_from(i) for i in d["issues"]], d["checks"],
                         d["seam_relations"])
