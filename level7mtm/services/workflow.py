"""Order workflow. Each step is a separate function with its own result; a successful import is not a
successful pattern, a successful generation is not an approval, an approval is not a nesting job.

Statuses of an order revision:
    imported → needs_review | ready → validation_failed | ready_for_review → approved → sent_to_nesting
    → completed;  cancelled (manual);  superseded (a newer revision arrived)
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field

from .. import ENGINE_VERSION, __version__
from ..erp.importer import ImportResult, import_bytes
from ..erp.mapping import load_profile, profile_hash
from ..erp.xvars import load_xvars
from .catalog import CatalogError, apply_plan, catalog_hash, find_style, load_catalog, resolve
from ..pattern.finishing import ShrinkSpec, apply_shrinkage
from ..pattern.model import evaluate_model, load_model, model_hash
from ..pattern.serialize import issue_to, result_from_json, result_to_json
from ..storage.db import Database, new_id, now

RES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources")
BUILTIN = [
    ("mapping", os.path.join(RES, "mappings", "level7_erp_ordermaster_v1.json")),
    ("xvars", os.path.join(RES, "xvars", "alshiaka_2027_v0_1.json")),
    ("model", os.path.join(RES, "models", "thobe_engine_test_v0_1.json")),
    ("finishing", os.path.join(RES, "finishing_default_v1.json")),
    ("catalog", os.path.join(RES, "catalogs", "level7_catalog_v1.json")),
]
STATUSES = ("imported", "needs_review", "ready", "calculating", "validation_failed", "ready_for_review",
            "approved", "sent_to_nesting", "completed", "cancelled", "superseded")
OPEN_FOR_EDIT = ("imported", "needs_review", "ready", "validation_failed", "ready_for_review")


class WorkflowError(Exception):
    pass


@dataclass
class FileOutcome:
    file_name: str
    status: str                        # imported / duplicate / failed
    message: str = ""
    lines: list[dict] = field(default_factory=list)   # {line_number, revision_id, action, status}
    grafis: list[dict] = field(default_factory=list)  # order MDLs written for GRAFIS (services.grafis_orders)


def _doc_id_key(kind: str, body: dict) -> str:
    return {"model": "model_id", "mapping": "profile_id", "xvars": "table_id", "finishing": "doc_id",
            "catalog": "catalog_id"}[kind]


DEFAULT_CUSTOMER = "level7"           # the first customer: the settings made before customers existed
CUSTOMER_KINDS = ("mapping", "catalog")   # each customer has its own XML mapping and rules (size table, options, parts)


def _sha(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Workspace:
    def __init__(self, folder: str, actor: str = "operator"):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        for sub in ("exports", "nesting", "backups"):
            os.makedirs(os.path.join(folder, sub), exist_ok=True)
        self.db = Database(os.path.join(folder, "level7mtm.db"))
        self.actor = actor
        self._ctx = threading.local()
        self._register_builtin()

    # ------------------------------------------------------------ customers
    def customer(self) -> str:
        """The customer whose settings are used now: the one of the order being worked on (``for_customer``);
        on the screen otherwise the customer chosen for editing; in the background the first customer."""
        cid = getattr(self._ctx, "cid", None)
        if cid:
            return cid
        if threading.current_thread() is threading.main_thread():
            return self.db.get_setting("ui.customer") or DEFAULT_CUSTOMER
        return DEFAULT_CUSTOMER

    @contextmanager
    def for_customer(self, cid: str | None):
        old = getattr(self._ctx, "cid", None)
        self._ctx.cid = cid or DEFAULT_CUSTOMER
        try:
            yield
        finally:
            self._ctx.cid = old

    def customer_of(self, rid: int) -> str:
        r = self.db.one("SELECT normalized FROM revisions WHERE id=?", (rid,))
        return (json.loads(r["normalized"]).get("customer") if r else None) or DEFAULT_CUSTOMER

    def _key(self, base: str, kind: str | None = None) -> str:
        cid = self.customer()
        if cid == DEFAULT_CUSTOMER or (kind is not None and kind not in CUSTOMER_KINDS):
            return base
        return f"{base}@{cid}"

    def grafis_mdl(self) -> str | None:
        """The customer's GRAFIS base model (the first customer's when none is chosen)."""
        return self.db.get_setting(self._key("grafis.mdl")) or self.db.get_setting("grafis.mdl")

    def set_grafis_mdl(self, path: str) -> None:
        self.db.set_setting(self._key("grafis.mdl"), path)

    # ------------------------------------------------------------ documents (versioned)
    def _register_builtin(self) -> None:
        for kind, path in BUILTIN:
            with open(path, encoding="utf-8") as fh:
                body = json.load(fh)
            key = body[_doc_id_key(kind, body)]
            if not self.db.one("SELECT id FROM documents WHERE kind=? AND doc_id=? AND version=?",
                               (kind, key, str(body["version"]))):
                self.save_document(kind, body, note="built-in", actor="system")
            row = self.db.one("SELECT id FROM documents WHERE kind=? AND doc_id=? AND version=?",
                              (kind, key, str(body["version"])))
            act = self.db.get_setting(f"active.{kind}")
            cur = self.db.one("SELECT doc_id, created_by FROM documents WHERE id=?", (act,)) if act else None
            # a newer built-in replaces the active built-in of the same document; a user's own version stays active
            if cur is None or (cur["created_by"] == "system" and cur["doc_id"] == key and act != row["id"]):
                self.db.set_setting(f"active.{kind}", row["id"])

    def save_document(self, kind: str, body: dict, note: str = "", actor: str | None = None) -> int:
        """Store a new version. An existing version is never overwritten (a changed body needs a new version)."""
        if kind == "model":
            load_model(body)
        elif kind == "mapping":
            load_profile(body)
        elif kind == "xvars":
            load_xvars(body)
        elif kind == "catalog":
            load_catalog(body)
        key = body[_doc_id_key(kind, body)]
        ver = str(body["version"])
        sha = _sha(body)
        old = self.db.one("SELECT id, sha256 FROM documents WHERE kind=? AND doc_id=? AND version=?", (kind, key, ver))
        if old:
            if old["sha256"] == sha:
                return old["id"]
            raise WorkflowError(f"{kind} {key} version {ver} already exists with different content — "
                                f"increase the version number")
        with self.db.tx() as c:
            cur = c.execute("INSERT INTO documents(kind, doc_id, version, sha256, status, body, created_at, created_by, "
                            "note) VALUES (?,?,?,?,?,?,?,?,?)",
                            (kind, key, ver, sha, body.get("status", "draft"), json.dumps(body, ensure_ascii=False),
                             now(), actor or self.actor, note))
            self.db.audit(c, actor or self.actor, "document.saved", kind, cur.lastrowid, {"doc_id": key, "version": ver})
            return cur.lastrowid

    def document(self, doc_pk: int) -> dict:
        r = self.db.one("SELECT body FROM documents WHERE id=?", (doc_pk,))
        if not r:
            raise WorkflowError(f"document {doc_pk} not found")
        return json.loads(r["body"])

    def active(self, kind: str) -> tuple[int, dict]:
        """The document in use (for mapping and catalog: the current customer's)."""
        pk = self.db.get_setting(self._key(f"active.{kind}", kind)) or self.db.get_setting(f"active.{kind}")
        return pk, self.document(pk)

    def set_active(self, kind: str, doc_pk: int) -> None:
        self.document(doc_pk)
        self.db.set_setting(self._key(f"active.{kind}", kind), doc_pk)
        with self.db.tx() as c:
            self.db.audit(c, self.actor, "document.activated", kind, doc_pk)

    def documents(self, kind: str):
        return self.db.q("SELECT id, doc_id, version, status, sha256, created_at, created_by, note FROM documents "
                         "WHERE kind=? ORDER BY doc_id, id", (kind,))

    # ------------------------------------------------------------ import
    def import_file(self, path: str) -> FileOutcome:
        name = os.path.basename(path)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as e:
            return FileOutcome(name, "failed", f"cannot read file: {e}")
        return self.import_data(data, name, path)

    def import_data(self, data: bytes, name: str, source_path: str = "") -> FileOutcome:
        sha = hashlib.sha256(data).hexdigest()
        dup = self.db.one("SELECT id, status FROM import_files WHERE sha256=?", (sha,))
        if dup and dup["status"] == "imported":
            return FileOutcome(name, "duplicate", "the same file was already imported; nothing created")
        from .customers import customer_for_xml
        cid, why = customer_for_xml(self, data)
        if cid is None:                    # the XML names a customer that has no settings
            status, msg = "failed", why
            with self.db.tx() as c:
                if dup:
                    c.execute("UPDATE import_files SET status=?, message=?, received_at=? WHERE id=?",
                              (status, msg, now(), dup["id"]))
                    fid = dup["id"]
                else:
                    fid = c.execute("INSERT INTO import_files(sha256, file_name, source_path, received_at, size, "
                                    "encoding, mapping_doc, status, message, raw, structure) "
                                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                    (sha, name, source_path, now(), len(data), "", None, status, msg, data,
                                     "{}")).lastrowid
                self.db.audit(c, self.actor, "import.failed", "import_file", fid, {"file": name, "message": msg})
            return FileOutcome(name, status, msg)
        with self.for_customer(cid):
            return self._import_for(data, name, source_path, sha, dup, cid)

    def _import_for(self, data, name, source_path, sha, dup, cid) -> FileOutcome:
        map_pk, profile = self.active("mapping")
        res: ImportResult = import_bytes(data, profile)
        res.customer = cid
        status = "imported" if res.ok else "failed"
        msg = "; ".join(p.message for p in res.problems if p.severity == "error")
        out = FileOutcome(name, status, msg)
        with self.db.tx() as c:
            if dup:      # a failed file imported again (e.g. after a mapping fix) — update the record
                c.execute("UPDATE import_files SET status=?, message=?, mapping_doc=?, received_at=? WHERE id=?",
                          (status, msg, map_pk, now(), dup["id"]))
                fid = dup["id"]
            else:
                fid = c.execute("INSERT INTO import_files(sha256, file_name, source_path, received_at, size, encoding, "
                                "mapping_doc, status, message, raw, structure) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                (sha, name, source_path, now(), len(data), res.encoding, map_pk, status, msg, data,
                                 json.dumps(res.structure, ensure_ascii=False))).lastrowid
            self.db.audit(c, self.actor, f"import.{status}", "import_file", fid, {"file": name, "message": msg})
            if not res.ok:
                return out
            for ln in res.lines:
                out.lines.append(self._store_line(c, fid, map_pk, res, ln))
        return out

    def _store_line(self, c, fid, map_pk, res, ln) -> dict:
        raw = {"number": ln.number, "item": ln.item, "quantity": ln.quantity, "fabric_number": ln.fabric_number,
               "fabric_width_mm": ln.fabric_width_mm, "fabric_color": ln.fabric_color,
               "measurement_table": ln.measurement_table, "basic_size": ln.basic_size,
               "measurements": {k: {"name": m.source_name, "value": m.source_value, "unit": m.source_unit}
                                for k, m in ln.measurements.items()},
               "option_codes": ln.option_codes, "programs": ln.programs, "order_date": ln.date or res.order_date}
        norm = {"measurements": {k: {"value_mm": m.value_mm, "kind": m.kind, "label": m.label, "meaning": m.meaning,
                                     "status": m.status, "variable": m.variable, "source_value": m.source_value,
                                     "source_unit": m.source_unit} for k, m in ln.measurements.items()},
                "options": ln.options, "quantity": ln.quantity, "fabric_width_mm": ln.fabric_width_mm,
                "problems": [p.__dict__ for p in ln.problems],
                "customer": getattr(res, "customer", None) or DEFAULT_CUSTOMER}
        dsha = _sha({"m": {k: v["value_mm"] for k, v in norm["measurements"].items()}, "o": ln.options,
                     "q": ln.quantity, "w": ln.fabric_width_mm})
        cid = getattr(res, "customer", None) or DEFAULT_CUSTOMER
        line_ref = ln.line_ref or ln.number
        if cid != DEFAULT_CUSTOMER:                    # the same order number of two customers: two orders
            line_ref = f"{cid}:{line_ref}"
        rev = ln.revision or "0"
        order = c.execute("SELECT * FROM orders WHERE line_ref=?", (line_ref,)).fetchone()
        if order is None:
            oid = c.execute("INSERT INTO orders(source, line_ref, order_no, reseller, customer_id, created_at) "
                            "VALUES (?,?,?,?,?,?)",
                            ("ERP", line_ref, ln.order_no, ln.reseller, cid, now())).lastrowid
        else:
            oid = order["id"]
            if order["cancelled_at"]:
                self.db.audit(c, self.actor, "import.after_cancel", "order", oid, {"line": ln.number})
        existing = c.execute("SELECT * FROM revisions WHERE order_id=? AND revision=?", (oid, rev)).fetchone()
        if existing:
            if existing["data_sha256"] == dsha:
                return {"line_number": ln.number, "revision_id": existing["id"], "action": "unchanged",
                        "status": existing["status"]}
            # same revision number, different content: never overwrite — keep the stored one, flag for review
            self.db.audit(c, self.actor, "import.conflict", "revision", existing["id"],
                          {"line": ln.number, "message": "same revision number arrived with different data"})
            return {"line_number": ln.number, "revision_id": existing["id"], "action": "conflict",
                    "status": existing["status"]}
        status = "needs_review" if ln.needs_review else "ready"
        if order is not None and order["cancelled_at"]:
            status = "cancelled"
        rid = c.execute("INSERT INTO revisions(order_id, revision, line_number, import_file, imported_at, quantity, "
                        "raw_line, normalized, mapping_doc, status, data_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (oid, rev, ln.number, fid, now(), ln.quantity, json.dumps(raw, ensure_ascii=False),
                         json.dumps(norm, ensure_ascii=False), map_pk, status, dsha)).lastrowid
        action = "new"
        latest = c.execute("SELECT * FROM revisions WHERE order_id=? AND id<>? ORDER BY CAST(revision AS INTEGER) DESC",
                           (oid, rid)).fetchall()
        newer = [r for r in latest if _revnum(r["revision"]) > _revnum(rev)]
        if newer:
            c.execute("UPDATE revisions SET status='superseded', superseded_by=? WHERE id=?", (newer[0]["id"], rid))
            status, action = "superseded", "older_revision"
        else:
            for r in latest:
                if r["status"] != "superseded":
                    if r["status"] in ("approved", "sent_to_nesting", "completed"):
                        self.db.audit(c, self.actor, "revision.replaces_approved", "revision", r["id"],
                                      {"new_revision": rev, "old_status": r["status"]})
                    c.execute("UPDATE revisions SET status='superseded', superseded_by=? WHERE id=?", (rid, r["id"]))
                    c.execute("UPDATE generations SET status='invalidated' WHERE revision_id=? AND status='ok'",
                              (r["id"],))
                    action = "new_revision"
        self.db.audit(c, self.actor, "revision.imported", "revision", rid, {"line": ln.number, "action": action})
        return {"line_number": ln.number, "revision_id": rid, "action": action, "status": status}

    # ------------------------------------------------------------ order data
    def revision(self, rid: int) -> dict:
        r = self.db.one("SELECT r.*, o.line_ref, o.cancelled_at, o.cancel_reason FROM revisions r "
                        "JOIN orders o ON o.id=r.order_id WHERE r.id=?", (rid,))
        if not r:
            raise WorkflowError(f"revision {rid} not found")
        d = dict(r)
        d["raw_line"] = json.loads(d["raw_line"])
        d["normalized"] = json.loads(d["normalized"])
        return d

    def list_revisions(self, include_superseded: bool = False):
        sql = ("SELECT r.id, r.line_number, r.revision, r.status, r.quantity, r.imported_at, o.line_ref, o.cancelled_at "
               "FROM revisions r JOIN orders o ON o.id=r.order_id ")
        if not include_superseded:
            sql += "WHERE r.status <> 'superseded' "
        return self.db.q(sql + "ORDER BY r.imported_at DESC, r.id DESC")

    def edit_measurement(self, rid: int, code: str, value_mm: float, reason: str) -> None:
        """Manual correction of a measurement (recorded). Any earlier generation becomes invalid."""
        rev = self.revision(rid)
        if rev["status"] not in OPEN_FOR_EDIT:
            raise WorkflowError(f"revision is {rev['status']}; it cannot be edited")
        norm = rev["normalized"]
        old = norm["measurements"].get(code, {}).get("value_mm")
        norm["measurements"].setdefault(code, {"kind": "other", "label": {"en": code}, "meaning": "", "status": "manual",
                                               "variable": code, "source_value": "", "source_unit": ""})
        norm["measurements"][code]["value_mm"] = float(value_mm)
        norm["measurements"][code]["manual"] = {"old_mm": old, "reason": reason, "at": now(), "by": self.actor}
        norm["problems"] = [p for p in norm["problems"] if p.get("field") != code]
        dsha = _sha({"m": {k: v["value_mm"] for k, v in norm["measurements"].items()}, "o": norm["options"],
                     "q": norm["quantity"], "w": norm["fabric_width_mm"]})
        status = "needs_review" if any(p["severity"] in ("review", "error") for p in norm["problems"]) else "ready"
        with self.db.tx() as c:
            c.execute("UPDATE revisions SET normalized=?, data_sha256=?, status=? WHERE id=?",
                      (json.dumps(norm, ensure_ascii=False), dsha, status, rid))
            c.execute("UPDATE generations SET status='invalidated' WHERE revision_id=? AND status='ok'", (rid,))
            self.db.audit(c, self.actor, "measurement.edited", "revision", rid,
                          {"code": code, "old_mm": old, "new_mm": value_mm, "reason": reason})

    def set_option(self, rid: int, group: str, value: str, reason: str) -> None:
        rev = self.revision(rid)
        if rev["status"] not in OPEN_FOR_EDIT:
            raise WorkflowError(f"revision is {rev['status']}; it cannot be edited")
        norm = rev["normalized"]
        old = norm["options"].get(group)
        norm["options"][group] = value
        norm["problems"] = [p for p in norm["problems"] if not (p["code"].startswith("option") and p.get("field") in
                                                               (group, *rev["raw_line"]["option_codes"]))]
        dsha = _sha({"m": {k: v["value_mm"] for k, v in norm["measurements"].items()}, "o": norm["options"],
                     "q": norm["quantity"], "w": norm["fabric_width_mm"]})
        status = "needs_review" if any(p["severity"] in ("review", "error") for p in norm["problems"]) else "ready"
        with self.db.tx() as c:
            c.execute("UPDATE revisions SET normalized=?, data_sha256=?, status=? WHERE id=?",
                      (json.dumps(norm, ensure_ascii=False), dsha, status, rid))
            c.execute("UPDATE generations SET status='invalidated' WHERE revision_id=? AND status='ok'", (rid,))
            self.db.audit(c, self.actor, "option.set", "revision", rid, {"group": group, "old": old, "new": value,
                                                                          "reason": reason})

    def resolve_problems(self, rid: int, reason: str) -> None:
        """Operator confirms that the remaining *review* problems of the import are handled (recorded)."""
        rev = self.revision(rid)
        norm = rev["normalized"]
        if any(p["severity"] == "error" for p in norm["problems"]):
            raise WorkflowError("errors cannot be confirmed away")
        with self.db.tx() as c:
            norm["resolved"] = {"problems": norm["problems"], "reason": reason, "by": self.actor, "at": now()}
            norm["problems"] = []
            c.execute("UPDATE revisions SET normalized=?, status='ready' WHERE id=? AND status IN ('needs_review')",
                      (json.dumps(norm, ensure_ascii=False), rid))
            self.db.audit(c, self.actor, "problems.resolved", "revision", rid, {"reason": reason})

    def cancel(self, rid: int, reason: str) -> None:
        rev = self.revision(rid)
        with self.db.tx() as c:
            c.execute("UPDATE orders SET cancelled_at=?, cancel_reason=? WHERE id=?", (now(), reason, rev["order_id"]))
            c.execute("UPDATE revisions SET status='cancelled' WHERE order_id=? AND status <> 'superseded'",
                      (rev["order_id"],))
            self.db.audit(c, self.actor, "order.cancelled", "order", rev["order_id"], {"reason": reason})

    # ------------------------------------------------------------ generation
    def model_for(self, model_id: str, version: str) -> int:
        r = self.db.one("SELECT id FROM documents WHERE kind='model' AND doc_id=? AND version=?", (model_id, version))
        if not r:
            raise WorkflowError(f"base pattern {model_id} {version} is not in the model library")
        return r["id"]

    def resolve_order(self, rid: int) -> tuple[int, dict, object]:
        """Catalog step only: measurements / options -> pattern variables + piece plan (no geometry)."""
        rev = self.revision(rid)
        with self.for_customer(rev["normalized"].get("customer")):     # the rules of the order's customer
            cat_pk, cat = self.active("catalog")
        raw, norm = rev["raw_line"], rev["normalized"]
        try:
            product, style = find_style(cat, raw.get("item"), raw.get("measurement_table"))
        except CatalogError as e:
            raise WorkflowError(str(e)) from None
        mm = {k: v["value_mm"] for k, v in norm["measurements"].items()}
        res = resolve(cat, product, style, mm, norm["options"], factory=self.db.get_setting("factory", "main"),
                      size=raw.get("basic_size"))
        return cat_pk, cat, res

    def generate(self, rid: int, model_pk: int | None = None) -> dict:
        rev = self.revision(rid)
        if rev["status"] in ("cancelled", "superseded", "approved", "sent_to_nesting", "completed"):
            raise WorkflowError(f"revision is {rev['status']}; no new calculation")
        cat_pk, cat, res = self.resolve_order(rid)
        bp = res.base_pattern
        model_pk = model_pk or self.model_for(bp["model_id"], str(bp["version"]))
        fin_pk, fin = self.active("finishing")
        model = load_model(self.document(model_pk))
        norm = rev["normalized"]
        inputs = dict(res.inputs)                       # pattern variables only (from the rules)
        result = evaluate_model(model, inputs, norm["options"])
        plan_issues = apply_plan(result, res.piece_plan)
        specs = {m: ShrinkSpec(m, s["length_pct"], s["width_pct"], s["definition"], s["already_applied_by_source"])
                 for m, s in fin["materials"].items()}
        finished, factors, _ = apply_shrinkage(result, specs)
        issues = [issue_to(i) for i in finished.issues + res.issues + plan_issues]
        # geometry status: plan problems block approval but do not make the geometry invalid
        status = "failed" if any(i["severity"] == "blocking" and not i["code"].startswith("plan.")
                                 for i in issues) else "ok"
        gid = new_id()
        input_sha = _sha({"rev": rev["data_sha256"], "model": model_hash(model), "catalog": catalog_hash(cat),
                          "fin": _sha(fin), "engine": ENGINE_VERSION})
        body = result_to_json(finished)
        body["shrink_factors"] = {k: list(v) for k, v in factors.items()}
        body["base"] = result_to_json(result)          # geometry before shrinkage (for review / comparison)
        body["catalog"] = {"doc": cat_pk, "catalog_id": cat["catalog_id"], "version": cat["version"],
                           "product": res.product, "style": res.style}
        body["rules_trace"] = [t.__dict__ for t in res.trace]
        body["piece_plan"] = res.piece_plan
        with self.db.tx() as c:
            c.execute("UPDATE generations SET status='invalidated' WHERE revision_id=? AND status='ok'", (rid,))
            c.execute("INSERT INTO generations(id, revision_id, model_doc, rules_doc, finishing_doc, engine_version, "
                      "inputs, options, result, issues, fingerprint, input_sha256, status, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (gid, rid, model_pk, cat_pk, fin_pk, f"{__version__}/{ENGINE_VERSION}", json.dumps(inputs),
                       json.dumps(norm["options"]), json.dumps(body, ensure_ascii=False),
                       json.dumps(issues, ensure_ascii=False), finished.geometry_fingerprint(), input_sha, status, now()))
            new_status = "ready_for_review" if status == "ok" else "validation_failed"
            c.execute("UPDATE revisions SET status=? WHERE id=?", (new_status, rid))
            self.db.audit(c, self.actor, "generation.created", "revision", rid, {"generation": gid, "status": status})
        return self.generation(gid)

    def generation(self, gid: str) -> dict:
        g = self.db.one("SELECT * FROM generations WHERE id=?", (gid,))
        if not g:
            raise WorkflowError(f"generation {gid} not found")
        d = dict(g)
        for k in ("inputs", "options", "result", "issues"):
            d[k] = json.loads(d[k])
        return d

    def latest_generation(self, rid: int) -> dict | None:
        g = self.db.one("SELECT id FROM generations WHERE revision_id=? ORDER BY created_at DESC, rowid DESC", (rid,))
        return self.generation(g["id"]) if g else None

    def pattern(self, gid: str, base: bool = False):
        g = self.generation(gid)
        return result_from_json(g["result"]["base"] if base else g["result"])

    def regenerate_and_compare(self, gid: str) -> tuple[bool, str, str]:
        """Re-run a stored generation from its stored inputs and document versions; compare fingerprints."""
        g = self.generation(gid)
        model = load_model(self.document(g["model_doc"]))
        fin = self.document(g["finishing_doc"])
        result = evaluate_model(model, g["inputs"], g["options"])
        apply_plan(result, g["result"].get("piece_plan", {}))
        specs = {m: ShrinkSpec(m, s["length_pct"], s["width_pct"], s["definition"], s["already_applied_by_source"])
                 for m, s in fin["materials"].items()}
        finished, _, _ = apply_shrinkage(result, specs)
        fp = finished.geometry_fingerprint()
        return fp == g["fingerprint"], fp, g["fingerprint"]

    # ------------------------------------------------------------ approval
    def approval_blockers(self, gid: str) -> list[str]:
        g = self.generation(gid)
        rev = self.revision(g["revision_id"])
        out = []
        if g["status"] != "ok":
            out.append(f"generation status is '{g['status']}'")
        if rev["status"] != "ready_for_review":
            out.append(f"order revision status is '{rev['status']}'")
        if rev["cancelled_at"]:
            out.append("order is cancelled")
        latest = self.latest_generation(rev["id"])
        if latest and latest["id"] != gid:
            out.append("a newer calculation exists for this revision")
        model = self.document(g["model_doc"])
        if model["status"] != "approved":
            out.append(f"model {model['model_id']} {model['version']} has status '{model['status']}' — "
                       f"only approved models can go to production")
        mapping = self.document(rev["mapping_doc"])
        if mapping.get("status") != "approved":
            out.append(f"ERP mapping {mapping['profile_id']} {mapping['version']} is '{mapping.get('status')}' (not approved)")
        cat = self.document(g["rules_doc"]) if g.get("rules_doc") else None
        if cat and cat.get("format") == "level7mtm-catalog" and cat.get("status") != "approved":
            out.append(f"catalog {cat['catalog_id']} {cat['version']} is '{cat.get('status')}' (rules not approved)")
        fin = self.document(g["finishing_doc"])
        for m, s in fin["materials"].items():
            if not s.get("confirmed"):
                out.append(f"shrinkage for material {m} is not confirmed")
        for i in g["issues"]:
            if i["severity"] in ("blocking", "review"):
                out.append(f"[{i['severity']}] {i['message']}")
        if rev["normalized"]["problems"]:
            out.append("import problems not resolved")
        return out

    def approve(self, gid: str, note: str = "") -> None:
        blockers = self.approval_blockers(gid)
        if blockers:
            raise WorkflowError("cannot approve:\n- " + "\n- ".join(blockers))
        g = self.generation(gid)
        with self.db.tx() as c:
            c.execute("INSERT INTO approvals(generation_id, approved_by, approved_at, note) VALUES (?,?,?,?)",
                      (gid, self.actor, now(), note))
            c.execute("UPDATE revisions SET status='approved' WHERE id=?", (g["revision_id"],))
            self.db.audit(c, self.actor, "generation.approved", "generation", gid, {"note": note})

    # ------------------------------------------------------------ nesting
    def send_to_nesting(self, rids: list[int], path: str | None = None, test_only: bool = False) -> dict:
        from ..nesting.exchange import ExchangeLine, write_exchange
        lines, items = [], []
        for rid in rids:
            rev = self.revision(rid)
            if rev["cancelled_at"] or rev["status"] in ("cancelled", "superseded"):
                raise WorkflowError(f"{rev['line_number']}: {rev['status']} — not sent")
            g = self.latest_generation(rid)
            if g is None:
                raise WorkflowError(f"{rev['line_number']}: no calculation")
            if not test_only:
                if rev["status"] not in ("approved", "sent_to_nesting"):
                    raise WorkflowError(f"{rev['line_number']}: status {rev['status']} — only approved orders are sent")
                if not self.db.one("SELECT 1 FROM approvals WHERE generation_id=?", (g["id"],)):
                    raise WorkflowError(f"{rev['line_number']}: calculation is not approved")
            elif g["status"] != "ok":
                raise WorkflowError(f"{rev['line_number']}: calculation failed validation — not even a test export")
            model = self.document(g["model_doc"])
            lines.append(ExchangeLine(f"{rev['raw_line']['number'].rsplit('-', 1)[0]}", rev["line_ref"], rev["revision"],
                                      int(rev["quantity"] or 0), model["model_id"], str(model["version"]),
                                      model["status"], g["id"], result_from_json(g["result"]),
                                      rev["normalized"].get("fabric_width_mm"), rev["raw_line"].get("fabric_number"),
                                      g["result"].get("shrink_factors", {})))
            items.append((rid, g["id"]))
        # no duplicate production jobs: the same set of generations already written -> return that job
        key = sorted(gid for _, gid in items)
        for job in self.db.q("SELECT id, path, status, test_only FROM nesting_jobs WHERE status IN ('written','sent','done')"):
            gids = sorted(r["generation_id"] for r in self.db.q("SELECT generation_id FROM nesting_items WHERE job_id=?",
                                                                 (job["id"],)))
            if gids == key and bool(job["test_only"]) == test_only:
                return {"job_id": job["id"], "path": job["path"], "duplicate": True}
        jid = new_id()[:12]
        prefix = "TEST-" if test_only else ""
        path = path or os.path.join(self.folder, "nesting", f"{prefix}MTM-{jid}.l7n")
        _, contract = write_exchange(lines, path, f"{prefix}MTM-{jid}", test_only=test_only)
        with self.db.tx() as c:
            c.execute("INSERT INTO nesting_jobs(id, created_at, path, contract, status, test_only) VALUES (?,?,?,?,?,?)",
                      (jid, now(), path, json.dumps(contract, ensure_ascii=False), "written", int(test_only)))
            for rid, gid in items:
                c.execute("INSERT INTO nesting_items(job_id, revision_id, generation_id) VALUES (?,?,?)", (jid, rid, gid))
                if not test_only:
                    c.execute("UPDATE revisions SET status='sent_to_nesting' WHERE id=?", (rid,))
            self.db.audit(c, self.actor, "nesting.written", "nesting_job", jid, {"path": path, "test_only": test_only,
                                                                               "revisions": rids})
        return {"job_id": jid, "path": path, "duplicate": False, "contract": contract}

    def set_job_status(self, jid: str, status: str, external_id: str | None = None) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE nesting_jobs SET status=?, external_job_id=COALESCE(?, external_job_id) WHERE id=?",
                      (status, external_id, jid))
            if status == "done":
                c.execute("UPDATE revisions SET status='completed' WHERE id IN (SELECT revision_id FROM nesting_items "
                          "WHERE job_id=?) AND status='sent_to_nesting'", (jid,))
            self.db.audit(c, self.actor, "nesting.status", "nesting_job", jid, {"status": status})

    # ------------------------------------------------------------ export
    def export(self, gid: str, kind: str, path: str | None = None) -> str:
        from ..export.dxf import write_dxf
        from ..export.pdf import write_pdf
        g = self.generation(gid)
        rev = self.revision(g["revision_id"])
        res = result_from_json(g["result"])
        res.pieces = [p for p in res.pieces if (p.production if kind == "dxf" else p.preview)]
        tag = f"{rev['line_number']}"
        path = path or os.path.join(self.folder, "exports", f"{tag}_{gid[:8]}.{kind}")
        info = [f"order {tag}", f"generation {gid}", f"model {res.model_id} {res.model_version} ({res.model_status})",
                f"engine {g['engine_version']}", "units: mm, scale 1:1"]
        if kind == "dxf":
            write_dxf(res, path, tag, info)
        elif kind == "pdf":
            meas = [(f"{k} — {v['label'].get('ar', '')} / {v['label'].get('en', '')}", f"{v['value_mm']:.1f}" if v[
                "value_mm"] is not None else "—", f"{v['source_value']} {v['source_unit']} · {v['kind']}")
                    for k, v in rev["normalized"]["measurements"].items()]
            write_pdf(res, path, {"order": tag, "generation": gid, "model": f"{res.model_id} {res.model_version}",
                                  "engine": g["engine_version"], "created": g["created_at"]}, meas)
        else:
            raise WorkflowError(f"unknown export {kind}")
        with self.db.tx() as c:
            self.db.audit(c, self.actor, f"export.{kind}", "generation", gid, {"path": path})
        return path

    def audit_log(self, limit: int = 500):
        return self.db.q("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))


def _revnum(r: str) -> int:
    try:
        return int(r)
    except (TypeError, ValueError):
        return -1
