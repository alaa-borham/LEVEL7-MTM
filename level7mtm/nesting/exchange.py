"""Hand-over to LEVEL7 Nesting.

Integration choice (after reading alaa-borham/LEVEL7-Nesting): the nesting application already opens its own
project files ``*.l7n`` (ZIP with ``project.json``, ``format: level7-nesting-project``, ``format_version: 1``,
see ``level7/project_storage/store.py`` there) and keeps one geometry per *size* for every piece. LEVEL7 MTM
writes such a project directly:

* one nesting ``Piece`` per model piece (front, back, sleeve ...);
* one *size* per order line revision, named ``<model>:<line>_<revision>`` (e.g. ``thobe-engine-test:9-53-1_2``) holding that customer's geometry, so pieces of many
  orders can be nested in one marker and every placed copy stays traceable to its order;
* ``cut_order.size_quantities`` = garments per order line, ``piece_counts`` = copies / mirrored copies per
  garment from the model (left/right handled by mirrored copies, never by doubling);
* shrinkage already applied by MTM → the nesting shrink settings stay 0 (no double compensation);
* the versioned MTM contract (``level7mtm-nesting-contract`` v1) is stored inside the same ZIP as
  ``level7mtm_contract.json`` and next to it as ``<name>.contract.json``.

The nesting application ignores files it does not know inside the ZIP, so the project opens unchanged there.
"""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from dataclasses import dataclass, field

from ..pattern.finishing import cut_notch, grain_angle

NESTING_FORMAT = "level7-nesting-project"
NESTING_FORMAT_VERSION = 1
CONTRACT_FORMAT = "level7mtm-nesting-contract"
CONTRACT_VERSION = 1


class ExchangeError(ValueError):
    pass


@dataclass
class ExchangeLine:
    """One approved order line revision ready for nesting."""
    order_number: str          # e.g. 9-53
    line_ref: str              # e.g. 9-53-1
    revision: str              # e.g. 2
    quantity: int
    model_id: str
    model_version: str
    model_status: str
    generation_id: str
    result: object             # PatternResult after finishing (shrinkage applied)
    fabric_width: float | None = None
    fabric_number: str | None = None
    shrink_factors: dict = field(default_factory=dict)

    @property
    def size_key(self) -> str:
        return f"{self.line_ref}_{self.revision}"

    @property
    def nesting_size(self) -> str:
        """LEVEL7 Nesting convention for several models in one session: '<model>:<size>'."""
        return f"{self.model_id}:{self.size_key}"


def piece_hash(piece) -> str:
    h = hashlib.sha256()
    for pt in piece.cut + [(-1e9, -1e9)] + piece.seam:
        h.update(f"{pt[0]:.3f},{pt[1]:.3f};".encode())
    return h.hexdigest()


def _geom(piece, size_key: str, generation_id: str) -> dict:
    notches = []
    for n in piece.notches:
        base, d = cut_notch(piece, n["position"])
        if base is not None:
            notches.append({"position": list(base), "direction": list(d) if d else None, "length": 5.0,
                            "source_id": n.get("id") or None})
    labels = [{"text": f"{size_key} {piece.name.get('en', piece.id)}", "position": list(piece.labels[0]["position"]),
               "height": 10.0, "box": None, "angle": 0.0}] if piece.labels else []
    return {
        "size": size_key, "cut": [list(p) for p in piece.cut], "corners": [], "holes": [],
        "seam": [{"points": [list(p) for p in piece.seam], "closed": True, "source_id": None}],
        "internal": [{"points": [list(p) for p in ip.flatten(0.05)], "closed": False, "source_id": None}
                     for ip in piece.internal],
        "notches": notches, "drills": [], "measures": [],
        "grain": {"points": [list(piece.grain[0]), list(piece.grain[1])], "closed": False, "source_id": None}
        if piece.grain else None,
        "labels": labels, "source_unit": "mm", "unit_factor": 1.0,
        "completeness": {"cut": "confirmed", "seam": "confirmed", "notches": "confirmed", "grain": "confirmed"},
        "source": f"LEVEL7 MTM generation {generation_id}",
    }


def build_exchange(lines: list[ExchangeLine], name: str, test_only: bool = False) -> tuple[dict, dict]:
    """``test_only`` allows non-approved models for integration tests; the marker name and the contract are
    then marked TEST / NOT FOR PRODUCTION."""
    if not lines:
        raise ExchangeError("nothing to send")
    if test_only and not name.startswith("TEST-"):
        name = "TEST-NOT-FOR-PRODUCTION-" + name
    for ln in lines:
        if ln.model_status != "approved" and not test_only:
            raise ExchangeError(f"{ln.size_key}: model status '{ln.model_status}' is not approved for production")
        if ln.quantity < 1:
            raise ExchangeError(f"{ln.size_key}: quantity {ln.quantity}")
    materials = {p.material for ln in lines for p in ln.result.pieces if p.production}
    if len(materials) > 1:
        raise ExchangeError(f"one marker per material; got {sorted(materials)}")
    pieces: dict[str, dict] = {}
    contract_pieces = []
    for ln in lines:
        for p in (q for q in ln.result.pieces if q.production):
            uid = f"{ln.model_id}:{p.id}"
            ent = pieces.setdefault(uid, {
                "uid": uid, "source_id": uid, "number": None, "name": p.name.get("en", p.id), "material": p.material,
                "production": True, "default_count": p.copies, "default_mirrored": p.mirrored, "sizes": {},
                "allowed_rotations": p.allow_rotation, "allow_mirror": p.mirrored > 0,
                "grain_angle": grain_angle(p), "notes": [f"LEVEL7 MTM model {ln.model_id} {ln.model_version}"],
                "model": ln.model_id, "match": None})
            if (ent["default_count"], ent["default_mirrored"]) != (p.copies, p.mirrored):
                raise ExchangeError(f"piece {uid}: copies differ between model versions in one marker")
            ent["sizes"][ln.nesting_size] = _geom(p, ln.nesting_size, ln.generation_id)
            contract_pieces.append({
                "piece_uid": f"{ln.size_key}:{p.id}", "nesting_piece_uid": uid, "nesting_size": ln.nesting_size,
                "order_number": ln.order_number, "line_ref": ln.line_ref, "revision": ln.revision,
                "model_id": ln.model_id, "model_version": ln.model_version, "generation_id": ln.generation_id,
                "material": p.material, "copies_per_garment": p.copies, "mirrored_per_garment": p.mirrored,
                "garments": ln.quantity, "total_copies": (p.copies + p.mirrored) * ln.quantity,
                "mirror_allowed_only_for_mirrored_copies": True,
                "mirror_substitute_allowed": p.allow_mirror_substitute,
                "rotations": p.allow_rotation, "grain_angle": grain_angle(p), "unit": "mm",
                "shrink_factors_applied": ln.shrink_factors.get(p.id, [1.0, 1.0]),
                "geometry_sha256": piece_hash(p),
            })
    project = {
        "name": name, "pieces": list(pieces.values()), "size_names": [ln.nesting_size for ln in lines], "sources": [],
        "cut_order": {"size_quantities": {ln.nesting_size: ln.quantity for ln in lines},
                      "piece_counts": {uid: [e["default_count"], e["default_mirrored"]] for uid, e in pieces.items()},
                      "material": next(iter(materials)), "excluded": []},
        "nest_settings": {}, "markers": [], "export_configs": {}, "reports": {}, "sbinfo": {},
    }
    widths = {ln.fabric_width for ln in lines if ln.fabric_width}
    contract = {"format": CONTRACT_FORMAT, "format_version": CONTRACT_VERSION, "name": name, "unit": "mm",
                "test_only": test_only,
                "fabric_widths_from_orders": sorted(widths), "shrinkage_applied_by_mtm": True,
                "nesting_shrink_must_be_zero": True,
                "orders": [{"order_number": ln.order_number, "line_ref": ln.line_ref, "revision": ln.revision,
                            "quantity": ln.quantity, "nesting_size": ln.nesting_size, "fabric_number": ln.fabric_number,
                            "generation_id": ln.generation_id} for ln in lines],
                "pieces": contract_pieces}
    return project, contract


def write_exchange(lines: list[ExchangeLine], path: str, name: str | None = None,
                   test_only: bool = False) -> tuple[str, dict]:
    name = name or os.path.splitext(os.path.basename(path))[0]
    project, contract = build_exchange(lines, name, test_only)
    doc = {"format": NESTING_FORMAT, "format_version": NESTING_FORMAT_VERSION, "project": project}
    tmp = path + ".part"
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(doc, ensure_ascii=False, indent=1))
        z.writestr("level7mtm_contract.json", json.dumps(contract, ensure_ascii=False, indent=1))
    os.replace(tmp, path)
    side = os.path.splitext(path)[0] + ".contract.json"
    with open(side + ".part", "w", encoding="utf-8") as fh:
        json.dump(contract, fh, ensure_ascii=False, indent=1)
    os.replace(side + ".part", side)
    return path, contract
