"""Main window: navigation + the eleven work screens. Long work (import, calculation, nesting export) runs in a
worker thread so the window never freezes."""
from __future__ import annotations

import json
import os
import traceback

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import ENGINE_VERSION, __version__
from ..services.watcher import FolderWatcher
from ..services.workflow import WorkflowError, Workspace
from .i18n import label, language, set_language, tr
from .pattern_view import PatternView

STATUS_COLORS = {"needs_review": "#fde68a", "validation_failed": "#fecaca", "ready_for_review": "#bfdbfe",
                 "approved": "#bbf7d0", "sent_to_nesting": "#a7f3d0", "completed": "#d1fae5", "cancelled": "#e5e7eb",
                 "superseded": "#f3f4f6", "ready": "#ffffff", "blocking": "#fecaca", "review": "#fde68a",
                 "info": "#e0f2fe"}


# ---------------------------------------------------------------- helpers

class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Worker(QRunnable):
    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.fn(*self.args))
        except WorkflowError as e:
            self.signals.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001 - shown to the user, never swallowed
            self.signals.failed.emit(f"{e}\n\n{traceback.format_exc()}")


def table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels([tr(h) for h in headers])
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    t.horizontalHeader().setStretchLastSection(True)
    t.verticalHeader().setVisible(False)
    return t


def fill(t: QTableWidget, rows: list[list], colors: list[str | None] | None = None, data: list | None = None):
    t.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            it = QTableWidgetItem("" if v is None else str(v))
            if colors and colors[i]:
                it.setBackground(QColor(colors[i]))
            if data is not None and j == 0:
                it.setData(Qt.UserRole, data[i])
            t.setItem(i, j, it)
    t.resizeColumnsToContents()


def selected_data(t: QTableWidget):
    rows = sorted({i.row() for i in t.selectedItems()})
    return [t.item(r, 0).data(Qt.UserRole) for r in rows]


def ask_reason(parent, title) -> str | None:
    text, ok = QInputDialog.getText(parent, title, tr("Reason"))
    return text.strip() if ok and text.strip() else None


# ---------------------------------------------------------------- pages

class Page(QWidget):
    title = ""
    developer_only = False

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.ws: Workspace = win.ws

    def refresh(self):
        pass


class OrdersPage(Page):
    title = "Orders"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        b_imp = QPushButton(tr("Import XML files…"))
        b_imp.clicked.connect(win.import_dialog)
        b_calc = QPushButton(tr("Calculate"))
        b_calc.clicked.connect(self.calculate)
        b_open = QPushButton(tr("Open"))
        b_open.clicked.connect(self.open_selected)
        self.cb_old = QCheckBox(tr("Show superseded revisions"))
        self.cb_old.toggled.connect(self.refresh)
        for w in (b_imp, b_calc, b_open, self.cb_old):
            bar.addWidget(w)
        bar.addStretch()
        self.b_watch = QPushButton(tr("Watch folder"))
        self.b_watch.clicked.connect(win.toggle_watch)
        b_retry = QPushButton(tr("Retry failed"))
        b_retry.clicked.connect(win.retry_failed)
        bar.addWidget(self.b_watch)
        bar.addWidget(b_retry)
        lay.addLayout(bar)
        lay.addWidget(QLabel(tr("Drop XML files here or use Import")))
        self.t = table(["Order line", "Revision", "Status", "Quantity", "Imported"])
        self.t.doubleClicked.connect(self.open_selected)
        lay.addWidget(self.t, 3)
        self.log = QListWidget()
        lay.addWidget(self.log, 1)

    def refresh(self):
        rows = self.ws.list_revisions(self.cb_old.isChecked())
        fill(self.t, [[r["line_number"], r["revision"], tr(r["status"]), r["quantity"], r["imported_at"]] for r in rows],
             [STATUS_COLORS.get(r["status"]) for r in rows], [r["id"] for r in rows])
        self.b_watch.setText(tr("Stop watching") if self.win.watcher else tr("Watch folder"))

    def add_log(self, text):
        self.log.insertItem(0, text)

    def open_selected(self):
        ids = selected_data(self.t)
        if ids:
            self.win.open_revision(ids[0])

    def calculate(self):
        ids = selected_data(self.t)
        if not ids:
            return
        self.win.run(lambda: [self.ws.generate(i) for i in ids], lambda res: (
            self.add_log(f"{tr('Done')}: {', '.join(g['status'] for g in res)}"), self.refresh(),
            self.win.open_revision(ids[0], page="Pattern preview")))


class ImportPage(Page):
    title = "Import & mapping"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)
        w1 = QWidget()
        l1 = QVBoxLayout(w1)
        self.files = table(["File", "Status", "Imported", "Message"])
        self.files.itemSelectionChanged.connect(self.show_structure)
        self.struct = table(["Path", "Count", "Attributes"])
        sp = QSplitter(Qt.Vertical)
        sp.addWidget(self.files)
        sp.addWidget(self.struct)
        l1.addWidget(sp)
        tabs.addTab(w1, tr("XML structure"))
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        self.prof_info = QLabel()
        self.prof_info.setWordWrap(True)
        l2.addWidget(self.prof_info)
        self.fields = table(["Code", "Label", "Unit", "Kind", "Required", "Range", "Meaning", "Mapping status"])
        l2.addWidget(self.fields)
        self.opts = table(["Code", "Group", "Value"])
        l2.addWidget(self.opts)
        bar = QHBoxLayout()
        self.b_edit = QPushButton(tr("Save as new version"))
        self.b_edit.clicked.connect(self.edit_profile)
        bar.addWidget(self.b_edit)
        bar.addStretch()
        l2.addLayout(bar)
        tabs.addTab(w2, tr("ERP mapping profile"))

    def refresh(self):
        rows = self.ws.db.q("SELECT id, file_name, status, received_at, message FROM import_files ORDER BY id DESC")
        fill(self.files, [[r["file_name"], tr(r["status"]), r["received_at"], r["message"]] for r in rows],
             [STATUS_COLORS.get("blocking") if r["status"] == "failed" else None for r in rows], [r["id"] for r in rows])
        pk, prof = self.ws.active("mapping")
        self.prof_info.setText(f"{prof['profile_id']} — {tr('Version')} {prof['version']} — {tr('Status')}: "
                               f"{prof.get('status')}\n{label(prof.get('description'))}")
        f = prof["measurements"]["fields"]
        fill(self.fields, [[k, label(v["label"]), v["unit"], v["kind"], v.get("required"), v.get("range"),
                            v.get("meaning"), v.get("status")] for k, v in f.items()],
             [None if v.get("status") in ("confirmed", "unit_confirmed") else STATUS_COLORS["review"] for v in f.values()])
        fill(self.opts, [[k, v["group"], v["value"]] for k, v in prof["options"]["map"].items()])
        self.b_edit.setEnabled(self.win.developer)

    def show_structure(self):
        ids = selected_data(self.files)
        if not ids:
            return
        r = self.ws.db.one("SELECT structure FROM import_files WHERE id=?", (ids[0],))
        s = json.loads(r["structure"] or "{}")
        paths = s.get("paths", {})
        fill(self.struct, [[p, v["count"], ", ".join(f"{k}={'|'.join(vals)}" for k, vals in v["attributes"].items())]
                           for p, v in paths.items()])

    def edit_profile(self):
        pk, prof = self.ws.active("mapping")
        self.win.edit_json_document("mapping", prof)


class OrderPage(Page):
    title = "Order details"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        self.head = QLabel(tr("No order selected"))
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        self.meas = table(["Code", "Measurement", "Source value", "Unit", "Value (mm)", "Kind", "Meaning", "Mapping status"])
        lay.addWidget(self.meas, 3)
        sp = QSplitter()
        self.opts = table(["Group", "Value"])
        self.probs = table(["Severity", "Message"])
        sp.addWidget(self.opts)
        sp.addWidget(self.probs)
        lay.addWidget(sp, 2)
        bar = QHBoxLayout()
        for text, fn in (("Edit measurement…", self.edit_measure), ("Confirm problems handled…", self.resolve),
                         ("Cancel order…", self.cancel), ("Calculate", self.calculate)):
            b = QPushButton(tr(text))
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        lay.addLayout(bar)

    def refresh(self):
        rid = self.win.current_rid
        if rid is None:
            return
        r = self.ws.revision(rid)
        raw = r["raw_line"]
        self.head.setText(f"<b>{r['line_number']}</b> — {tr('Status')}: {tr(r['status'])} — {tr('Quantity')}: "
                          f"{r['quantity']} — Fabric {raw.get('fabric_number')} / {raw.get('fabric_width_mm')} mm — "
                          f"{raw.get('measurement_table')} / Basic-Size {raw.get('basic_size')} "
                          f"(Basic-Size not used: open question) — {raw.get('order_date')}")
        ms = r["normalized"]["measurements"]
        fill(self.meas, [[k, label(v["label"]), v["source_value"], v["source_unit"],
                          "—" if v["value_mm"] is None else f"{v['value_mm']:.1f}" + (" ✎" if v.get("manual") else ""),
                          v["kind"], v["meaning"], v["status"]] for k, v in ms.items()],
             [None if v["value_mm"] is not None else STATUS_COLORS["review"] for v in ms.values()], list(ms))
        fill(self.opts, [[g, v] for g, v in r["normalized"]["options"].items()] +
             [["(ERP)", c] for c in raw.get("option_codes", [])])
        pr = r["normalized"]["problems"]
        fill(self.probs, [[tr(p["severity"]), p["message"]] for p in pr], [STATUS_COLORS.get(p["severity"]) for p in pr])

    def edit_measure(self):
        rid = self.win.current_rid
        codes = selected_data(self.meas)
        if rid is None or not codes:
            return
        code = codes[0]
        cur = self.ws.revision(rid)["normalized"]["measurements"][code]["value_mm"] or 0.0
        val, ok = QInputDialog.getDouble(self, code, tr("Value (mm)"), cur, 0, 5000, 1)
        if not ok:
            return
        reason = ask_reason(self, code)
        if reason:
            self.win.guard(lambda: self.ws.edit_measurement(rid, code, val, reason))
            self.refresh()

    def resolve(self):
        rid = self.win.current_rid
        reason = ask_reason(self, tr("Confirm problems handled…")) if rid is not None else None
        if reason:
            self.win.guard(lambda: self.ws.resolve_problems(rid, reason))
            self.refresh()

    def cancel(self):
        rid = self.win.current_rid
        reason = ask_reason(self, tr("Cancel order…")) if rid is not None else None
        if reason:
            self.win.guard(lambda: self.ws.cancel(rid, reason))
            self.refresh()

    def calculate(self):
        rid = self.win.current_rid
        if rid is not None:
            self.win.run(lambda: self.ws.generate(rid), lambda g: self.win.open_revision(rid, page="Pattern preview"))


class ModelPage(Page):
    title = "Model & design"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.model = QComboBox()
        form.addRow(tr("Model"), self.model)
        lay.addLayout(form)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        lay.addWidget(self.notice)
        self.opts = QTableWidget(0, 3)
        self.opts.setHorizontalHeaderLabels([tr("Group"), tr("Value"), tr("Order line")])
        lay.addWidget(self.opts)
        self.inputs = table(["Code", "Label", "Range", "Value (mm)"])
        lay.addWidget(self.inputs)
        bar = QHBoxLayout()
        b = QPushButton(tr("Calculate"))
        b.clicked.connect(self.calculate)
        bar.addWidget(b)
        bar.addStretch()
        lay.addLayout(bar)

    def refresh(self):
        self.model.clear()
        active = self.ws.active("model")[0]
        for d in self.ws.documents("model"):
            self.model.addItem(f"{d['doc_id']} {d['version']} ({d['status']})", d["id"])
            if d["id"] == active:
                self.model.setCurrentIndex(self.model.count() - 1)
        m = self.ws.document(self.model.currentData()) if self.model.count() else None
        if not m:
            return
        self.notice.setText(f"<b>{label(m['name'])}</b><br>{label(m.get('notice'))}")
        self.notice.setStyleSheet("color:#b91c1c" if m["status"] != "approved" else "")
        rev = self.ws.revision(self.win.current_rid) if self.win.current_rid is not None else None
        sel = rev["normalized"]["options"] if rev else {}
        self.opts.setRowCount(len(m.get("options", {})))
        for i, (g, spec) in enumerate(m.get("options", {}).items()):
            self.opts.setItem(i, 0, QTableWidgetItem(f"{g} — {label(spec.get('label'))}"))
            cb = QComboBox()
            for v, vl in spec["values"].items():
                cb.addItem(f"{v} — {label(vl)}", v)
            if sel.get(g) in spec["values"]:
                cb.setCurrentIndex(list(spec["values"]).index(sel[g]))
            else:
                cb.insertItem(0, "—", None)
                cb.setCurrentIndex(0)
            cb.currentIndexChanged.connect(lambda _i, g=g, cb=cb: self.set_option(g, cb.currentData()))
            self.opts.setCellWidget(i, 1, cb)
            self.opts.setItem(i, 2, QTableWidgetItem(str(sel.get(g, "—"))))
        xv = {}
        if rev:
            try:
                xv = self.ws.resolve_order(rev["id"])[2].inputs
            except WorkflowError as e:
                self.notice.setText(self.notice.text() + f"<br><b>{e}</b>")
        fill(self.inputs, [[i["name"], label(i.get("label")), i.get("range"), xv.get(i["name"], "—")]
                           for i in m.get("inputs", [])])

    def set_option(self, group, value):
        rid = self.win.current_rid
        if rid is None or value is None:
            return
        reason = ask_reason(self, f"{group} = {value}")
        if reason:
            self.win.guard(lambda: self.ws.set_option(rid, group, value, reason))
        self.refresh()

    def calculate(self):
        rid = self.win.current_rid
        pk = self.model.currentData()
        if rid is not None:
            self.win.run(lambda: self.ws.generate(rid, pk), lambda g: self.win.open_revision(rid, page="Pattern preview"))


class PreviewPage(Page):
    title = "Pattern preview"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.cbs = {}
        for key, text in (("cut", "Cut line"), ("seam", "Seam line"), ("notches", "Notches"), ("grain", "Grain"),
                          ("labels", "Labels")):
            cb = QCheckBox(tr(text))
            cb.setChecked(True)
            cb.toggled.connect(lambda on, k=key: self.view.set_layer(k, on))
            bar.addWidget(cb)
            self.cbs[key] = cb
        bar.addWidget(QLabel(tr("Compare with")))
        self.cmp = QComboBox()
        self.cmp.currentIndexChanged.connect(self.refresh_view)
        bar.addWidget(self.cmp)
        b = QPushButton(tr("Fit"))
        b.clicked.connect(lambda: self.view.fit())
        bar.addWidget(b)
        bar.addStretch()
        lay.addLayout(bar)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        lay.addWidget(self.banner)
        sp = QSplitter()
        self.view = PatternView()
        self.view.on_select = self.piece_selected
        sp.addWidget(self.view)
        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)
        sp.addWidget(self.info)
        sp.setSizes([900, 300])
        lay.addWidget(sp, 1)
        self.result = None

    def refresh(self):
        rid = self.win.current_rid
        g = self.ws.latest_generation(rid) if rid is not None else None
        self.cmp.blockSignals(True)
        self.cmp.clear()
        self.cmp.addItem(tr("nothing"), None)
        if g:
            self.cmp.addItem(tr("before shrinkage"), ("base", g["id"]))
            for o in self.ws.db.q("SELECT id, created_at, status FROM generations WHERE revision_id=? AND id<>? "
                                  "ORDER BY created_at DESC", (rid, g["id"])):
                self.cmp.addItem(f"{tr('previous calculation')} {o['created_at']} ({o['status']})", ("gen", o["id"]))
            for o in self.ws.db.q("SELECT g.id, r.line_number FROM generations g JOIN revisions r ON r.id=g.revision_id "
                                  "WHERE r.order_id=(SELECT order_id FROM revisions WHERE id=?) AND r.id<>? "
                                  "ORDER BY g.created_at DESC", (rid, rid)):
                self.cmp.addItem(f"{o['line_number']}", ("gen", o["id"]))
        self.cmp.blockSignals(False)
        self.gen = g
        self.refresh_view()

    def refresh_view(self):
        g = self.gen
        if not g:
            self.view.scene().clear()
            self.banner.setText(tr("No calculation yet"))
            return
        res = self.ws.pattern(g["id"])
        res.pieces = [p for p in res.pieces if p.preview]       # preview flag is separate from production
        data = self.cmp.currentData()
        compare = None
        if data:
            compare = self.ws.pattern(data[1], base=(data[0] == "base"))
        self.result = res
        self.view.show_result(res, compare)
        warn = "" if res.model_status == "approved" else f"⚠ {tr('Engine test model — NOT the customer pattern')} — "
        self.banner.setText(f"{warn}{res.model_id} {res.model_version} — {g['status']} — {g['created_at']} — "
                            f"fingerprint {g['fingerprint'][:12]}")
        self.banner.setStyleSheet("color:#b91c1c;font-weight:bold" if warn else "")
        self.info.setPlainText("\n".join(f"{p.id}: {label(p.name)}\n  " + "\n  ".join(
            f"{k}: {v:.1f} mm (allowance {p.allowances.get(k)})" for k, v in p.edge_lengths.items())
            for p in res.pieces))

    def piece_selected(self, pid):
        if not self.result:
            return
        p = next((x for x in self.result.pieces if x.id == pid), None)
        if p:
            self.info.setPlainText(f"{label(p.name)} ({p.id})\n{tr('Material')}: {p.material}\ncopies: {p.copies}, "
                                   f"mirrored: {p.mirrored}\n\n" + "\n".join(
                                       f"{k}: {v:.2f} mm  (+{p.allowances.get(k)} mm)" for k, v in p.edge_lengths.items()))


class ValidationPage(Page):
    title = "Validation"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        self.issues = table(["Severity", "Message"])
        self.checks = table(["Label", "Value", "Result"])
        self.rels = table(["Label", "Value", "Result"])
        self.block = QListWidget()
        tabs = QTabWidget()
        tabs.addTab(self.issues, tr("Problems"))
        tabs.addTab(self.checks, tr("Checks"))
        tabs.addTab(self.rels, tr("Seam relations"))
        lay.addWidget(tabs, 3)
        lay.addWidget(QLabel(tr("Approval blockers")))
        lay.addWidget(self.block, 2)
        bar = QHBoxLayout()
        self.b_ok = QPushButton(tr("Approve"))
        self.b_ok.clicked.connect(self.approve)
        bar.addWidget(self.b_ok)
        bar.addStretch()
        lay.addLayout(bar)

    def refresh(self):
        rid = self.win.current_rid
        g = self.ws.latest_generation(rid) if rid is not None else None
        if not g:
            for t in (self.issues, self.checks, self.rels):
                t.setRowCount(0)
            self.block.clear()
            self.b_ok.setEnabled(False)
            return
        iss = g["issues"]
        fill(self.issues, [[tr(i["severity"]), i["message"]] for i in iss], [STATUS_COLORS.get(i["severity"]) for i in iss])
        ch = g["result"]["checks"]
        fill(self.checks, [[c["label"], f"{c['actual']:.1f} / {c['target']:.1f} ± {c['tolerance']:g}", "OK" if c["ok"]
                            else "✗"] for c in ch], [None if c["ok"] else STATUS_COLORS["review"] for c in ch])
        rl = g["result"]["seam_relations"]
        fill(self.rels, [[f"{r['label']} ({r['a']} / {r['b']})", f"{r['len_a']:.1f} − {r['len_b']:.1f} = {r['diff']:.1f}"
                          f" (expected {r['expected_diff']:.1f} ± {r['tolerance']:g})", "OK" if r["ok"] else "✗"]
                         for r in rl], [None if r["ok"] else STATUS_COLORS["blocking"] for r in rl])
        bl = self.ws.approval_blockers(g["id"])
        self.block.clear()
        self.block.addItems(bl)
        self.b_ok.setEnabled(not bl)          # no general override: blockers must be fixed
        self.gid = g["id"]

    def approve(self):
        note, ok = QInputDialog.getText(self, tr("Approve"), tr("Note"))
        if ok:
            self.win.guard(lambda: self.ws.approve(self.gid, note))
            self.refresh()


class SendPage(Page):
    title = "Approve & send"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        self.t = table(["Order line", "Revision", "Status", "Quantity"])
        self.t.setSelectionMode(QAbstractItemView.MultiSelection)
        lay.addWidget(self.t, 2)
        bar = QHBoxLayout()
        for text, fn in (("Send selected to nesting", lambda: self.send(False)),
                         ("TEST export (not for production)", lambda: self.send(True)),
                         ("Export DXF", lambda: self.export("dxf")), ("Export PDF", lambda: self.export("pdf")),
                         ("X values for GRAFIS", self.grafis_x)):
            b = QPushButton(tr(text))
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        lay.addLayout(bar)
        lay.addWidget(QLabel(tr("Nesting jobs")))
        self.jobs = table(["ID", "Time", "Status", "Path"])
        lay.addWidget(self.jobs, 1)

    def refresh(self):
        rows = [r for r in self.ws.list_revisions() if r["status"] in ("ready", "needs_review", "ready_for_review",
                                                                       "validation_failed", "approved",
                                                                       "sent_to_nesting")]
        fill(self.t, [[r["line_number"], r["revision"], tr(r["status"]), r["quantity"]] for r in rows],
             [STATUS_COLORS.get(r["status"]) for r in rows], [r["id"] for r in rows])
        jobs = self.ws.db.q("SELECT id, created_at, status, path, test_only FROM nesting_jobs ORDER BY created_at DESC")
        fill(self.jobs, [[j["id"], j["created_at"], j["status"] + (" (TEST)" if j["test_only"] else ""), j["path"]]
                         for j in jobs])

    def send(self, test):
        ids = selected_data(self.t)
        if not ids:
            return
        path, _ = QFileDialog.getSaveFileName(self, "LEVEL7 Nesting", os.path.join(self.ws.folder, "nesting"),
                                              "LEVEL7 Nesting (*.l7n)")
        if not path:
            return
        self.win.run(lambda: self.ws.send_to_nesting(ids, path, test_only=test),
                     lambda j: (QMessageBox.information(self, "LEVEL7", f"{j['path']}" + (" (existing job)" if j[
                         "duplicate"] else "")), self.refresh()))

    def grafis_x(self):
        from ..services.grafis_handover import x_sheet
        ids = selected_data(self.t)
        if not ids:
            return
        mdl = self.ws.db.get_setting("grafis.mdl")
        if not mdl or not os.path.exists(mdl):
            mdl, _ = QFileDialog.getOpenFileName(self, "GRAFIS", "", "GRAFIS (*.mdl *.MDL)")
            if mdl:
                self.ws.db.set_setting("grafis.mdl", mdl)
        for rid in ids:
            d = self.win.guard(lambda rid=rid: x_sheet(self.ws, rid, mdl or None))
            if d:
                QMessageBox.information(self, tr("X values for GRAFIS"), "\n".join(
                    [f"{r['x']} {r['x_name']}: {r['order_value_mm']} mm" for r in d["rows"] if not r["superseded"]]
                    + [""] + d["warnings"][:12] + ["", *d["files"]]))

    def export(self, kind):
        ids = selected_data(self.t)
        for rid in ids:
            g = self.ws.latest_generation(rid)
            if g:
                p = self.win.guard(lambda: self.ws.export(g["id"], kind))
                if p:
                    self.win.status(p)


class GrafisPage(Page):
    """Phase 1: GRAFIS computes the pattern and writes an SCB per order (named after the XML). Here the SCB is
    checked against the order and handed to LEVEL7 Nesting unchanged."""
    title = "GRAFIS markers"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        bar = QHBoxLayout()
        bar0 = QHBoxLayout()
        for text, fn in (("GRAFIS model (MDL)…", self.choose_mdl), ("GRAFIS input folder…", self.choose_input),
                         ("Write GRAFIS model for order…", self.write_mdl)):
            b = QPushButton(tr(text))
            b.clicked.connect(fn)
            bar0.addWidget(b)
        bar0.addStretch()
        lay.addLayout(bar0)
        self.auto_mdl = QCheckBox(tr("Automatic: write the order's X values into a GRAFIS model (MDL) on import"))
        self.auto_mdl.setChecked(bool(self.ws.db.get_setting("grafis.auto_mdl", True)))
        self.auto_mdl.toggled.connect(lambda on: (self.ws.db.set_setting("grafis.auto_mdl", on), self.refresh()))
        lay.addWidget(self.auto_mdl)
        self.models = table(["Time", "Order line", "Status", "File", "X values", "Findings"])
        lay.addWidget(self.models, 1)
        for text, fn in (("Register GRAFIS marker (SCB)…", self.register), ("Watch GRAFIS output folder", self.toggle),
                         ("Accept review findings…", self.accept), ("Nesting folder…", self.choose_nesting),
                         ("LEVEL7 Nesting program…", self.choose_cli), ("Cutting files folder…", self.choose_out)):
            b = QPushButton(tr(text))
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        lay.addLayout(bar)
        self.auto = QCheckBox(tr("Automatic: nest and write cutting files (PLT / ISO)"))
        self.auto.setChecked(bool(self.ws.db.get_setting("cutting.auto", True)))
        self.auto.toggled.connect(lambda on: (self.ws.db.set_setting("cutting.auto", on), self.refresh()))
        lay.addWidget(self.auto)
        self.t = table(["File", "Order line", "Status", "Width", "Bundles", "Pieces", "Cutting files", "Findings"])
        lay.addWidget(self.t, 1)
        self.busy = False
        self.watcher = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        folder = self.ws.db.get_setting("grafis.watch.folder")
        if folder and self.ws.db.get_setting("grafis.watch.enabled", False) and os.path.isdir(folder):
            self.start(folder)

    def _nesting_dir(self):
        return self.ws.db.get_setting("nesting.inbox") or os.path.join(self.ws.folder, "nesting")

    def refresh(self):
        from ..services.auto_cut import NestingNotConfigured, nesting_command, settings
        from ..services.grafis_orders import settings as gsettings
        gs = gsettings(self.ws)
        mrows = self.ws.db.q("SELECT g.*, r.line_number FROM grafis_orders g JOIN revisions r ON r.id=g.revision_id "
                             "ORDER BY g.id DESC LIMIT 500")
        mo, mc = [], []
        for r in mrows:
            info = json.loads(r["info"])
            mo.append([r["created_at"], r["line_number"], tr(r["status"]), os.path.basename(r["path"] or ""),
                       ", ".join(f"{x['x']}={x['value_mm']:g}" for x in info.get("rows", [])),
                       " | ".join(info.get("problems", []))])
            mc.append({"written": STATUS_COLORS["approved"], "blocked": STATUS_COLORS["blocking"],
                       "not_configured": STATUS_COLORS["blocking"]}.get(r["status"]))
        fill(self.models, mo, mc, [r["revision_id"] for r in mrows])
        st = settings(self.ws)
        try:
            cli = " ".join(nesting_command(self.ws)[0][-1:])
        except NestingNotConfigured:
            cli = "⚠ " + tr("not found")
        mdl = gs["template"]
        self.info.setText(f"{tr('GRAFIS model (MDL)')}: {mdl or '⚠ ' + tr('not set')}"
                          f"{'' if not mdl or os.path.isfile(mdl) else ' ⚠ ' + tr('not found')} — "
                          f"{tr('GRAFIS input folder')}: {gs['out']}\n"
                          f"{tr('GRAFIS output folder')}: {self.ws.db.get_setting('grafis.watch.folder') or '—'}"
                          f" ({tr('watching') if self.watcher else tr('not watching')}) — "
                          f"{tr('Nesting folder')}: {self._nesting_dir()}\n"
                          f"LEVEL7 Nesting: {cli} — {tr('Cutting files folder')}: {st['out']} — "
                          f"{', '.join(st['formats']).upper()} ({st['plt_preset']}, {st['iso_preset']})")
        rows = self.ws.db.q("SELECT * FROM grafis_results ORDER BY checked_at DESC")
        out, colors, data = [], [], []
        for r in rows:
            info, issues, lines = json.loads(r["info"]), json.loads(r["issues"]), json.loads(r["lines"])
            cj = self.ws.db.one("SELECT status, outputs, info FROM cutting_jobs WHERE sha256=? ORDER BY id DESC",
                                (r["sha256"],))
            cut = ""
            if cj:
                ci = json.loads(cj["info"])
                cut = (", ".join(os.path.basename(x) for x in json.loads(cj["outputs"])) if cj["status"] == "done"
                       else f"{tr(cj['status'])}: {ci.get('message', '')}")
            out.append([os.path.basename(r["path"]), ", ".join(ln["line_number"] for ln in lines), tr(r["status"]),
                        info.get("width_mm"), info.get("bundles"), info.get("pieces_placed"), cut,
                        " | ".join(f"[{tr(sv)}] {m}" for sv, m in issues)])
            colors.append({"handed_over": STATUS_COLORS["approved"], "needs_review": STATUS_COLORS["review"],
                           "unmatched": STATUS_COLORS["blocking"]}.get(r["status"]))
            data.append(r["path"])
        fill(self.t, out, colors, data)

    def _register(self, path, reason=None):
        from ..services.grafis_intake import register_scb
        return register_scb(self.ws, path, nesting_dir=self._nesting_dir(), confirm_reason=reason)

    def register(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "GRAFIS", self.ws.db.get_setting("grafis.watch.folder", ""),
                                                "GRAFIS marker (*.scb *.SCB)")
        if paths:
            self.win.run(lambda: [self._register(p) for p in paths], lambda _r: self.refresh())

    def choose_mdl(self):
        p, _ = QFileDialog.getOpenFileName(self, tr("GRAFIS model (MDL)…"), self.ws.db.get_setting("grafis.mdl") or "",
                                           "GRAFIS (*.mdl *.MDL)")
        if p:
            self.ws.db.set_setting("grafis.mdl", p)
            self.refresh()

    def choose_input(self):
        from ..services.grafis_orders import settings as gsettings
        d = QFileDialog.getExistingDirectory(self, tr("GRAFIS input folder…"), gsettings(self.ws)["out"])
        if d:
            self.ws.db.set_setting("grafis.input", d)
            self.refresh()

    def write_mdl(self):
        """Write (again) the GRAFIS model of order lines — e.g. after their review problems were resolved."""
        from ..services.grafis_orders import order_mdl
        revs = [r for r in self.ws.list_revisions() if r["status"] not in ("superseded", "cancelled")]
        if not revs:
            return
        line, ok = QInputDialog.getItem(self, tr("Write GRAFIS model for order…"), tr("Order line"),
                                        [r["line_number"] for r in revs], 0, False)
        if ok:
            rid = next(r["id"] for r in revs if r["line_number"] == line)
            self.win.run(lambda: order_mdl(self.ws, rid), lambda g: (self.refresh(), self.win.status(
                f"{line}: {tr(g['status'])} {g['path'] or ''} {'; '.join(g['problems'])}"[:300])))

    def choose_cli(self):
        p, _ = QFileDialog.getOpenFileName(self, tr("LEVEL7 Nesting program…"), r"C:\Program Files\LEVEL7 Nesting",
                                           "level7-cli (level7-cli.exe *.exe)")
        if p:
            self.ws.db.set_setting("nesting.cli", p)
            self.refresh()

    def choose_out(self):
        d = QFileDialog.getExistingDirectory(self, tr("Cutting files folder…"),
                                             self.ws.db.get_setting("cutting.output", ""))
        if d:
            self.ws.db.set_setting("cutting.output", d)
            self.refresh()

    def accept(self):
        paths = selected_data(self.t)
        if not paths:
            return
        reason = ask_reason(self, tr("Accept review findings…"))
        if reason:
            for p in paths:
                r = self.win.guard(lambda p=p: self._register(p, reason))
                if r and r["status"] != "handed_over":
                    QMessageBox.warning(self, "GRAFIS", "\n".join(m for _, m in r["check"].issues))
        self.refresh()

    def choose_nesting(self):
        d = QFileDialog.getExistingDirectory(self, tr("Nesting folder…"), self._nesting_dir())
        if d:
            self.ws.db.set_setting("nesting.inbox", d)
            self.refresh()

    def toggle(self):
        if self.watcher:
            self.timer.stop()
            self.watcher = None
            self.ws.db.set_setting("grafis.watch.enabled", False)
        else:
            d = QFileDialog.getExistingDirectory(self, tr("Watch GRAFIS output folder"),
                                                 self.ws.db.get_setting("grafis.watch.folder", ""))
            if d:
                self.start(d)
        self.refresh()

    def start(self, folder):
        self.watcher = FolderWatcher(self.ws, folder, settle_seconds=3, extensions=(".scb",), handler=self._register)
        self.ws.db.set_setting("grafis.watch.folder", folder)
        self.ws.db.set_setting("grafis.watch.enabled", True)
        self.timer.start(2000)

    def poll(self):
        """Runs in a worker thread: nesting can take a minute and must not freeze the window."""
        if not self.watcher or self.busy:
            return
        self.busy = True

        def done(res):
            self.busy = False
            if res:
                self.refresh()
        w = Worker(self.watcher.poll)
        w.signals.done.connect(done)
        w.signals.failed.connect(lambda msg: (setattr(self, "busy", False), self.win.status(msg[:200])))
        self.win.pool.start(w)


class LibraryPage(Page):
    title = "Model library"
    developer_only = True

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        self.kind = QComboBox()
        for k in ("catalog", "model", "mapping", "finishing", "xvars"):
            self.kind.addItem(k, k)
        self.kind.currentIndexChanged.connect(self.refresh)
        lay.addWidget(self.kind)
        self.t = table(["ID", "Version", "Status", "Time", "User", "Note"])
        lay.addWidget(self.t)
        bar = QHBoxLayout()
        b = QPushButton(tr("Activate"))
        b.clicked.connect(self.activate)
        bar.addWidget(b)
        b2 = QPushButton(tr("Save as new version"))
        b2.clicked.connect(self.edit)
        bar.addWidget(b2)
        bar.addStretch()
        lay.addLayout(bar)

    def refresh(self):
        k = self.kind.currentData()
        act = self.ws.active(k)[0]
        rows = self.ws.documents(k)
        fill(self.t, [[r["doc_id"] + (" ★" if r["id"] == act else ""), r["version"], r["status"], r["created_at"],
                       r["created_by"], r["note"]] for r in rows], None, [r["id"] for r in rows])

    def activate(self):
        ids = selected_data(self.t)
        if ids:
            self.ws.set_active(self.kind.currentData(), ids[0])
            self.refresh()

    def edit(self):
        ids = selected_data(self.t)
        if ids:
            self.win.edit_json_document(self.kind.currentData(), self.ws.document(ids[0]))
            self.refresh()


RULE_COLS = ["ID", "Source type", "Source", "Piece", "Construction", "Variable", "Value / formula", "Order",
             "Factories", "Sizes", "Enabled", "Status", "Note"]
MEAS_COLS = ["Code", "Name (ar)", "Name (en)", "Unit", "Kind", "Method", "Order", "Visible", "Editable", "Status"]
PIECE_COLS = ["Piece", "Name", "Material", "Copies", "Mirrored", "Rotations", "Mirror allowed", "Preview",
              "Production"]
OPT_COLS = ["Group", "Value", "Pieces", "Variables (JSON)", "Properties (JSON)"]


def _put(t: QTableWidget, rows):
    t.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            t.setItem(i, j, QTableWidgetItem("" if v is None else str(v)))
    t.resizeColumnsToContents()


def _rows(t: QTableWidget):
    for i in range(t.rowCount()):
        yield [(t.item(i, j).text().strip() if t.item(i, j) else "") for j in range(t.columnCount())]


class RulesPage(Page):
    title = "Rules editor"
    developer_only = True

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)
        # unified rule editor (catalog): measurement / option -> piece / construction / variable
        wx = QWidget()
        lx = QVBoxLayout(wx)
        top = QHBoxLayout()
        self.style_cb = QComboBox()
        self.style_cb.currentIndexChanged.connect(self.show_style)
        top.addWidget(QLabel(tr("Style")))
        top.addWidget(self.style_cb, 1)
        lx.addLayout(top)
        self.xinfo = QLabel()
        self.xinfo.setWordWrap(True)
        lx.addWidget(self.xinfo)
        sub = QTabWidget()
        self.rt = QTableWidget(0, len(RULE_COLS))
        self.rt.setHorizontalHeaderLabels([tr(c) for c in RULE_COLS])
        self.mt = QTableWidget(0, len(MEAS_COLS))
        self.mt.setHorizontalHeaderLabels([tr(c) for c in MEAS_COLS])
        self.pt = QTableWidget(0, len(PIECE_COLS))
        self.pt.setHorizontalHeaderLabels([tr(c) for c in PIECE_COLS])
        self.ot = QTableWidget(0, len(OPT_COLS))
        self.ot.setHorizontalHeaderLabels([tr(c) for c in OPT_COLS])
        for t, name in ((self.rt, "Rules"), (self.mt, "Measurement definitions"), (self.pt, "Production pieces"),
                        (self.ot, "Design option rules")):
            t.horizontalHeader().setStretchLastSection(True)
            sub.addTab(t, tr(name))
        self.sub = sub
        lx.addWidget(sub, 3)
        bx = QHBoxLayout()
        for text, fn in (("Add row", self.add_row), ("Remove row", self.remove_row), ("Test with order", self.test_rules),
                         ("Save as new version", self.save_catalog)):
            b = QPushButton(tr(text))
            b.clicked.connect(fn)
            bx.addWidget(b)
        bx.addStretch()
        lx.addLayout(bx)
        self.trace = QPlainTextEdit()
        self.trace.setReadOnly(True)
        self.trace.setMaximumHeight(170)
        lx.addWidget(self.trace, 1)
        tabs.addTab(wx, tr("Rules"))
        # model JSON
        wm = QWidget()
        lm = QVBoxLayout(wm)
        self.edit = QPlainTextEdit()
        lm.addWidget(self.edit)
        bm = QHBoxLayout()
        for text, fn in (("Validate", self.validate), ("Save as new version", self.save_model)):
            b = QPushButton(tr(text))
            b.clicked.connect(fn)
            bm.addWidget(b)
        bm.addStretch()
        lm.addLayout(bm)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMaximumHeight(160)
        lm.addWidget(self.out)
        tabs.addTab(wm, tr("Model rules (JSON)"))
        # GRAFIS formulas
        wg = QWidget()
        lg = QVBoxLayout(wg)
        bg = QHBoxLayout()
        b = QPushButton(tr("Open MDL…"))
        b.clicked.connect(self.open_mdl)
        bg.addWidget(b)
        b2 = QPushButton(tr("Test with order"))
        b2.clicked.connect(self.eval_mdl)
        bg.addWidget(b2)
        bg.addStretch()
        lg.addLayout(bg)
        self.gt = table(["Part", "Z", "Formula", "Description", "Result", "History"])
        lg.addWidget(self.gt)
        tabs.addTab(wg, tr("GRAFIS formulas"))
        self.mdl = None

    def refresh(self):
        self.cat_pk, cat = self.ws.active("catalog")
        self.cat = json.loads(json.dumps(cat))
        self.style_cb.blockSignals(True)
        self.style_cb.clear()
        for pid, prod in self.cat["products"].items():
            for sid, st in prod["styles"].items():
                self.style_cb.addItem(f"{label(prod['name'])} / {label(st['name'])}", (pid, sid))
        self.style_cb.blockSignals(False)
        self.show_style()
        _, m = self.ws.active("model")
        self.edit.setPlainText(json.dumps(m, ensure_ascii=False, indent=1))

    def _style(self):
        pid, sid = self.style_cb.currentData()
        return self.cat["products"][pid]["styles"][sid]

    def show_style(self):
        if self.style_cb.currentData() is None:
            return
        st = self._style()
        bp = st["base_pattern"]
        self.xinfo.setText(f"{self.cat['catalog_id']} {self.cat['version']} ({self.cat.get('status')}) — "
                           f"{tr('Base pattern')}: {bp['model_id']} {bp['version']} (piece {bp.get('base_piece')}) — "
                           f"{bp.get('note', '')}")
        _put(self.rt, [[r["id"], r["source"]["type"], r["source"].get("code") or
                        f"{r['source'].get('group')}={r['source'].get('value')}", r["target"]["piece"],
                        r["target"].get("construction", ""), r["target"]["variable"], r["value"], r.get("order", 0),
                        ",".join(r.get("factories", ["*"])), ",".join(r.get("sizes", ["*"])),
                        "1" if r.get("enabled", True) else "0", r.get("status", "proposed"), r.get("note", "")]
                       for r in sorted(st["rules"], key=lambda r: (r.get("order", 0), r["id"]))])
        _put(self.mt, [[c, m["name"].get("ar", ""), m["name"].get("en", ""), m["unit"], m["kind"],
                        label(m.get("method")), m["order"], "1" if m.get("visible", True) else "0",
                        "1" if m.get("editable", True) else "0", m.get("status", "")]
                       for c, m in sorted(self.cat["measurements"].items(), key=lambda kv: kv[1]["order"])])
        _put(self.pt, [[pid, label(p["name"]), p["material"], p["copies"], p["mirrored"],
                        ",".join(str(int(r)) for r in p.get("rotations", [0, 180])),
                        "1" if p.get("mirror_allowed") else "0", "1" if p.get("preview", True) else "0",
                        "1" if p.get("production", True) else "0"] for pid, p in st["pieces"].items()])
        _put(self.ot, [[o["group"], o["value"], ",".join(o.get("pieces", [])),
                        json.dumps(o.get("variables", []), ensure_ascii=False),
                        json.dumps(o.get("properties", {}), ensure_ascii=False)] for o in st["option_rules"]])

    def _cur(self):
        return self.sub.currentWidget()

    def add_row(self):
        t = self._cur()
        t.insertRow(t.rowCount())

    def remove_row(self):
        t = self._cur()
        if t.currentRow() >= 0:
            t.removeRow(t.currentRow())

    def _collect(self) -> dict:
        cat = json.loads(json.dumps(self.cat))
        pid, sid = self.style_cb.currentData()
        st = cat["products"][pid]["styles"][sid]
        rules = []
        for row in _rows(self.rt):
            if not row[0]:
                continue
            src = ({"type": "measurement", "code": row[2]} if row[1] != "option" else
                   {"type": "option", "group": row[2].split("=")[0], "value": row[2].split("=")[-1]})
            rules.append({"id": row[0], "source": src, "target": {"piece": row[3], "construction": row[4],
                          "variable": row[5]}, "value": row[6] or "{M}", "order": float(row[7] or 0),
                          "factories": [x for x in row[8].split(",") if x] or ["*"],
                          "sizes": [x for x in row[9].split(",") if x] or ["*"], "enabled": row[10] != "0",
                          "status": row[11] or "proposed", "note": row[12]})
        st["rules"] = rules
        meas = {}
        for row in _rows(self.mt):
            if not row[0]:
                continue
            old = cat["measurements"].get(row[0], {})
            meas[row[0]] = {**old, "name": {"ar": row[1], "en": row[2]}, "unit": row[3] or "mm", "kind": row[4] or "other",
                            "method": {**old.get("method", {}), "en": row[5]}, "order": float(row[6] or 0),
                            "visible": row[7] != "0", "editable": row[8] != "0", "status": row[9] or "proposed"}
        cat["measurements"] = meas
        pieces = {}
        for row in _rows(self.pt):
            if not row[0]:
                continue
            old = st["pieces"].get(row[0], {})
            pieces[row[0]] = {**old, "name": {**old.get("name", {}), "en": row[1]}, "material": row[2] or "O",
                              "copies": int(float(row[3] or 1)), "mirrored": int(float(row[4] or 0)),
                              "rotations": [float(x) for x in row[5].split(",") if x] or [0.0, 180.0],
                              "mirror_allowed": row[6] == "1", "preview": row[7] != "0", "production": row[8] != "0"}
        st["pieces"] = pieces
        orules = []
        for row in _rows(self.ot):
            if not row[0]:
                continue
            orules.append({"group": row[0], "value": row[1], "pieces": [x for x in row[2].split(",") if x],
                           "variables": json.loads(row[3] or "[]"), "properties": json.loads(row[4] or "{}")})
        st["option_rules"] = orules
        return cat

    def test_rules(self):
        from ..services.catalog import load_catalog, resolve
        rid = self.win.current_rid
        try:
            cat = load_catalog(self._collect())
        except Exception as e:  # noqa: BLE001
            self.trace.setPlainText(f"{tr('Error')}: {e}")
            return
        if rid is None:
            self.trace.setPlainText("OK (structure). " + tr("No order selected"))
            return
        rev = self.ws.revision(rid)
        pid, sid = self.style_cb.currentData()
        mm = {k: v["value_mm"] for k, v in rev["normalized"]["measurements"].items()}
        res = resolve(cat, pid, sid, mm, rev["normalized"]["options"], self.ws.db.get_setting("factory", "main"),
                      rev["raw_line"].get("basic_size"))
        lines = [f"{t.rule_id:8} {t.source:14} → {t.target:34} {t.expression:14} = "
                 f"{'' if t.value is None else f'{t.value:.1f}'} [{t.status}] {t.message}" for t in res.trace]
        lines += ["", tr("Production pieces") + ": " + ", ".join(res.piece_plan)]
        lines += [f"[{i.severity}] {i.message}" for i in res.issues]
        self.trace.setPlainText("\n".join(lines))

    def save_catalog(self):
        try:
            cat = self._collect()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Error"), str(e))
            return
        ver, ok = QInputDialog.getText(self, tr("Version"), tr("Version"), text=str(cat["version"]) + ".1")
        if not ok:
            return
        cat["version"] = ver
        pk = self.win.guard(lambda: self.ws.save_document("catalog", cat, "edited in rule editor"))
        if pk:
            self.ws.set_active("catalog", pk)
            self.refresh()

    def _model(self):
        return json.loads(self.edit.toPlainText())

    def validate(self):
        from ..pattern.model import evaluate_model, load_model
        try:
            m = load_model(self._model())
        except Exception as e:  # noqa: BLE001
            self.out.setPlainText(f"{tr('Error')}: {e}")
            return
        rid = self.win.current_rid
        if rid is None:
            self.out.setPlainText("OK (structure). " + tr("No order selected"))
            return
        rev = self.ws.revision(rid)
        xv = self.ws.resolve_order(rid)[2].inputs
        r = evaluate_model(m, xv, rev["normalized"]["options"])
        self.out.setPlainText("\n".join([f"pieces: {[p.id for p in r.pieces]}"] +
                                        [f"[{i.severity}] {i.message}" for i in r.issues]))

    def save_model(self):
        try:
            m = self._model()
        except json.JSONDecodeError as e:
            self.out.setPlainText(f"{tr('Error')}: {e}")
            return
        pk = self.win.guard(lambda: self.ws.save_document("model", m, "edited in rules editor"))
        if pk:
            self.ws.set_active("model", pk)
            self.out.setPlainText(f"{tr('Done')}: {m['model_id']} {m['version']}")

    def open_mdl(self):
        path, _ = QFileDialog.getOpenFileName(self, "GRAFIS", "", "GRAFIS (*.mdl *.MDL)")
        if not path:
            return
        from ..grafis.formulas import read_formulas
        self.mdl = self.win.guard(lambda: read_formulas(path))
        self.show_mdl({})

    def show_mdl(self, values_by_part):
        if not self.mdl:
            return
        rows = []
        for part, pf in sorted(self.mdl.items()):
            res = values_by_part.get(part, {})
            for r in pf.records:
                if r.placeholder:
                    continue
                v = res.get(r.name)
                rows.append([part, r.name, r.formula, r.description,
                             r.parse_error or (f"{v:.2f}" if isinstance(v, float) else (v or "")),
                             " | ".join(f"{h.date}: {h.formula}" for h in r.history[:4])])
        fill(self.gt, rows)

    def eval_mdl(self):
        if not self.mdl or self.win.current_rid is None:
            return
        from ..formula.graph import solve
        from ..grafis.formulas import canonical_formula
        xv = {k: v for k, v in self.ws.resolve_order(self.win.current_rid)[2].inputs.items() if not k.startswith("P")}
        out = {}
        for part, pf in self.mdl.items():
            formulas = {r.name: canonical_formula(r.formula) for r in pf.records if not r.measured and not r.parse_error}
            blocked = {r.name: f"measured in the GRAFIS construction ({r.measured})" for r in pf.records if r.measured}
            g = solve(formulas, xv, blocked)
            out[part] = {k: (r.value if r.ok else r.error) for k, r in g.results.items()}
            out[part].update({k: v for k, v in blocked.items()})
        self.show_mdl(out)


class SettingsPage(Page):
    title = "Materials & export"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.t = QTableWidget(0, 6)
        self.t.setHorizontalHeaderLabels([tr("Material"), tr("Length %"), tr("Width %"), tr("Definition"),
                                          tr("Applied by source"), tr("Confirmed")])
        lay.addWidget(self.t)
        bar = QHBoxLayout()
        self.b_save = QPushButton(tr("Save as new version"))
        self.b_save.clicked.connect(self.save)
        bar.addWidget(self.b_save)
        bar.addStretch()
        lay.addLayout(bar)
        lay.addStretch()

    def refresh(self):
        _, fin = self.ws.active("finishing")
        self.fin = fin
        self.info.setText(f"{fin['doc_id']} {fin['version']} — {label(fin.get('notes'))}\n"
                          f"expected_shrinkage s%: pattern × 1/(1−s) · compensation c%: pattern × (1+c). "
                          f"Applied once by MTM; LEVEL7 Nesting shrink must stay 0.")
        mats = fin["materials"]
        self.t.setRowCount(len(mats))
        for i, (m, s) in enumerate(mats.items()):
            self.t.setItem(i, 0, QTableWidgetItem(m))
            for j, key in ((1, "length_pct"), (2, "width_pct")):
                sb = QDoubleSpinBox()
                sb.setRange(0, 30)
                sb.setDecimals(2)
                sb.setValue(s[key])
                self.t.setCellWidget(i, j, sb)
            cb = QComboBox()
            cb.addItems(["expected_shrinkage", "compensation"])
            cb.setCurrentText(s["definition"])
            self.t.setCellWidget(i, 3, cb)
            for j, key in ((4, "already_applied_by_source"), (5, "confirmed")):
                c = QCheckBox()
                c.setChecked(bool(s.get(key)))
                self.t.setCellWidget(i, j, c)
        self.b_save.setEnabled(self.win.developer)

    def save(self):
        fin = json.loads(json.dumps(self.fin))
        ver, ok = QInputDialog.getText(self, tr("Version"), tr("Version"), text=str(int(str(fin["version"]).split("+")[0]) + 1)
                                       if str(fin["version"]).isdigit() else fin["version"] + ".1")
        if not ok:
            return
        for i, m in enumerate(fin["materials"]):
            s = fin["materials"][m]
            s["length_pct"] = self.t.cellWidget(i, 1).value()
            s["width_pct"] = self.t.cellWidget(i, 2).value()
            s["definition"] = self.t.cellWidget(i, 3).currentText()
            s["already_applied_by_source"] = self.t.cellWidget(i, 4).isChecked()
            s["confirmed"] = self.t.cellWidget(i, 5).isChecked()
        fin["version"] = ver
        pk = self.win.guard(lambda: self.ws.save_document("finishing", fin, "materials settings"))
        if pk:
            self.ws.set_active("finishing", pk)
            self.refresh()


class LogPage(Page):
    title = "Log & backups"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        b = QPushButton(tr("Backup now"))
        b.clicked.connect(self.backup)
        bar.addWidget(b)
        b2 = QPushButton(tr("Restore backup…"))
        b2.clicked.connect(self.restore)
        bar.addWidget(b2)
        bar.addStretch()
        lay.addLayout(bar)
        self.t = table(["Time", "User", "Action", "Details"])
        lay.addWidget(self.t)

    def refresh(self):
        rows = self.ws.audit_log()
        fill(self.t, [[r["at"], r["actor"], f"{r['action']} {r['entity']} {r['entity_id']}", r["details"]] for r in rows])

    def backup(self):
        p = self.win.guard(lambda: self.ws.db.backup(os.path.join(self.ws.folder, "backups")))
        if p:
            self.win.status(p)

    def restore(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Restore backup…"), os.path.join(self.ws.folder, "backups"),
                                              "SQLite (*.db)")
        if not path:
            return
        from ..storage.db import restore_backup
        db_path = self.ws.db.path
        self.ws.db.close()
        self.win.guard(lambda: restore_backup(path, db_path))
        QMessageBox.information(self, "LEVEL7 MTM", tr("Restart LEVEL7 MTM to use the restored database."))
        self.win.close()


PAGES = [OrdersPage, ImportPage, OrderPage, ModelPage, PreviewPage, ValidationPage, SendPage, GrafisPage, LibraryPage,
         RulesPage,
         SettingsPage, LogPage]


# ---------------------------------------------------------------- window

class MainWindow(QMainWindow):
    def __init__(self, ws: Workspace):
        super().__init__()
        self.ws = ws
        self.pool = QThreadPool.globalInstance()
        self.current_rid = None
        self.watcher: FolderWatcher | None = None
        self.developer = bool(ws.db.get_setting("ui.developer", False))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_watch)
        self.setAcceptDrops(True)
        self.resize(1400, 900)
        self.build()
        folder = ws.db.get_setting("watch.folder")
        if folder and ws.db.get_setting("watch.enabled", False) and os.path.isdir(folder):
            self.start_watch(folder)

    def build(self):
        self.setWindowTitle(f"LEVEL7 MTM {__version__} (engine {ENGINE_VERSION}) — {self.ws.folder}")
        self.menuBar().clear()
        m = self.menuBar().addMenu(tr("File"))
        a = QAction(tr("Import XML files…"), self)
        a.triggered.connect(self.import_dialog)
        m.addAction(a)
        a = QAction(tr("Exit"), self)
        a.triggered.connect(self.close)
        m.addAction(a)
        v = self.menuBar().addMenu(tr("View"))
        for code, name in (("ar", "العربية"), ("en", "English")):
            a = QAction(name, self)
            a.triggered.connect(lambda _=False, c=code: self.set_lang(c))
            v.addAction(a)
        v.addSeparator()
        a = QAction(tr("Pattern developer"), self, checkable=True)
        a.setChecked(self.developer)
        a.toggled.connect(self.set_role)
        v.addAction(a)
        h = self.menuBar().addMenu(tr("Help"))
        a = QAction(tr("About"), self)
        a.triggered.connect(lambda: QMessageBox.about(self, "LEVEL7 MTM", f"LEVEL7 MTM {__version__}\nengine "
                                                      f"{ENGINE_VERSION}\nData: {self.ws.folder}"))
        h.addAction(a)
        root = QWidget()
        lay = QHBoxLayout(root)
        self.nav = QListWidget()
        self.nav.setFixedWidth(250)
        self.stack = QStackedWidget()
        self.pages = {}
        for cls in PAGES:
            p = cls(self)
            self.pages[cls.title] = p
            self.stack.addWidget(p)
            self.nav.addItem(tr(cls.title))
        self.nav.currentRowChanged.connect(self.goto_row)
        lay.addWidget(self.nav)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.apply_role()
        self.nav.setCurrentRow(0)

    def apply_role(self):
        for i, cls in enumerate(PAGES):
            self.nav.item(i).setHidden(cls.developer_only and not self.developer)
        self.statusBar().showMessage(f"{tr('Role')}: {tr('Pattern developer') if self.developer else tr('Operator')}")

    def set_role(self, dev):
        self.developer = dev
        self.ws.db.set_setting("ui.developer", dev)
        self.apply_role()
        self.stack.currentWidget().refresh()

    def set_lang(self, code):
        from PySide6.QtWidgets import QApplication
        set_language(code)
        self.ws.db.set_setting("ui.language", code)
        QApplication.instance().setLayoutDirection(Qt.RightToLeft if code == "ar" else Qt.LeftToRight)
        self.build()

    def goto_row(self, row):
        self.stack.setCurrentIndex(row)
        self.guard(self.stack.currentWidget().refresh)

    def goto(self, title):
        self.nav.setCurrentRow(list(self.pages).index(title))

    def open_revision(self, rid, page="Order details"):
        self.current_rid = rid
        self.goto(page)

    def status(self, text):
        self.statusBar().showMessage(text, 10000)

    def guard(self, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - errors are shown, not hidden
            QMessageBox.warning(self, tr("Error"), str(e))
            return None

    def run(self, fn, on_done):
        self.status(tr("Working…"))
        w = Worker(fn)
        w.signals.done.connect(lambda res: (self.status(tr("Done")), on_done(res)))
        w.signals.failed.connect(lambda msg: (self.status(tr("Error")), QMessageBox.warning(self, tr("Error"), msg)))
        self.pool.start(w)

    # ---- import
    def import_paths(self, paths):
        page = self.pages["Orders"]

        def work():
            from ..services.grafis_orders import import_and_prepare
            return [import_and_prepare(self.ws, p) for p in paths]

        def done(outs):
            for o in outs:
                page.add_log(f"{o.file_name}: {tr(o.status)} {o.message} " +
                             ", ".join(f"{x['line_number']}→{x['action']}" for x in o.lines))
                for g in o.grafis:
                    page.add_log(f"  GRAFIS {g['line_number']}: {tr(g['status'])} "
                                 f"{os.path.basename(g['path'] or '')} {'; '.join(g['problems'])}")
            page.refresh()
        self.run(work, done)

    def import_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Import XML files…"), "", "XML (*.xml *.XML)")
        if paths:
            self.goto("Orders")
            self.import_paths(paths)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile().lower().endswith(".xml")]
        if paths:
            self.goto("Orders")
            self.import_paths(paths)

    # ---- watched folder
    def toggle_watch(self):
        if self.watcher:
            self.timer.stop()
            self.watcher = None
            self.ws.db.set_setting("watch.enabled", False)
        else:
            folder = QFileDialog.getExistingDirectory(self, tr("Choose folder…"), self.ws.db.get_setting("watch.folder", ""))
            if folder:
                self.start_watch(folder)
        self.pages["Orders"].refresh()

    def start_watch(self, folder):
        from ..services.grafis_orders import import_and_prepare
        self.watcher = FolderWatcher(self.ws, folder, settle_seconds=3, handler=lambda p: import_and_prepare(self.ws, p))
        self.ws.db.set_setting("watch.folder", folder)
        self.ws.db.set_setting("watch.enabled", True)
        self.timer.start(2000)
        self.status(f"{tr('Watch folder')}: {folder}")

    def poll_watch(self):
        if not self.watcher:
            return
        outs = self.guard(self.watcher.poll) or []
        if outs:
            page = self.pages["Orders"]
            for o in outs:
                page.add_log(f"[watch] {o.file_name}: {tr(o.status)} {o.message}")
                for g in o.grafis:
                    page.add_log(f"  GRAFIS {g['line_number']}: {tr(g['status'])} "
                                 f"{os.path.basename(g['path'] or '')} {'; '.join(g['problems'])}")
            page.refresh()

    def retry_failed(self):
        if not self.watcher:
            return
        for p in list(self.watcher.state.failed):
            o = self.guard(lambda p=p: self.watcher.retry(p))
            if o:
                self.pages["Orders"].add_log(f"[retry] {o.file_name}: {tr(o.status)} {o.message}")
        self.pages["Orders"].refresh()

    # ---- documents
    def edit_json_document(self, kind, body):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{kind} — {tr('Save as new version')}")
        lay = QVBoxLayout(dlg)
        ed = QPlainTextEdit(json.dumps(body, ensure_ascii=False, indent=1))
        lay.addWidget(ed)
        ver = QLineEdit(str(body.get("version", "")) + ".1")
        f = QFormLayout()
        f.addRow(tr("Version"), ver)
        lay.addLayout(f)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.resize(900, 700)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            new = json.loads(ed.toPlainText())
        except json.JSONDecodeError as e:
            QMessageBox.warning(self, tr("Error"), str(e))
            return
        new["version"] = ver.text().strip()
        pk = self.guard(lambda: self.ws.save_document(kind, new, "edited"))
        if pk:
            self.ws.set_active(kind, pk)
            self.stack.currentWidget().refresh()
