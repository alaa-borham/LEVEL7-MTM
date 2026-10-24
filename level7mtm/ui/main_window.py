"""Main window: navigation + the eleven work screens. Long work (import, calculation, nesting export) runs in a
worker thread so the window never freezes."""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import traceback
import threading

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFrame,
                               QGridLayout, QSizePolicy, QToolButton,
                               QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter,
                               QTabBar, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import ENGINE_VERSION, __version__
from ..services.watcher import FolderWatcher
from ..services.workflow import WorkflowError, Workspace
from .i18n import label, set_language, tr, tr_msg
from .pattern_view import PatternView

STATUS_COLORS = {"needs_review": "#fde68a", "validation_failed": "#fecaca", "ready_for_review": "#bfdbfe",
                 "approved": "#bbf7d0", "sent_to_nesting": "#a7f3d0", "completed": "#d1fae5", "cancelled": "#e5e7eb",
                 "superseded": "#f3f4f6", "ready": "#ffffff", "blocking": "#fecaca", "review": "#fde68a",
                 "info": "#e0f2fe"}


# ---------------------------------------------------------------- helpers

class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


_LIVE: set = set()          # workers whose result may still be queued for the GUI thread


class Worker(QRunnable):
    """Background job. Kept alive (with its signal object) until its result was delivered in the GUI thread:
    a worker collected while its queued signal is pending crashes Qt (access violation seen on Windows CI)."""

    def __init__(self, fn, *args):
        super().__init__()
        self.setAutoDelete(False)
        self.fn, self.args = fn, args
        self.signals = _Signals()
        _LIVE.add(self)
        self.signals.done.connect(self._release)
        self.signals.failed.connect(self._release)

    def _release(self, *_):
        QTimer.singleShot(0, lambda: _LIVE.discard(self))

    def run(self):
        try:
            self.signals.done.emit(self.fn(*self.args))
        except WorkflowError as e:
            self.signals.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001 - shown to the user, never swallowed
            self.signals.failed.emit(f"{e}\n\n{traceback.format_exc()}")


def renest_why(chk) -> str:
    """Why a model cannot be nested again: never an empty reason."""
    if chk.info.get("waiting"):
        return tr("The model was written again and GRAFIS has not saved it yet: the robot runs it, then it is nested")
    why = "; ".join(tr_msg(m) for s_, m in chk.issues if s_ != "info") or "; ".join(tr_msg(m) for _, m in chk.issues)
    return why or tr("No order line matches this model")


def remnant_note(r: dict) -> str:
    """What "small pieces together" did, for the message after nesting."""
    rm = r.get("remnant")
    if not rm:
        return ""
    if rm.get("done"):
        return (f"\n\n{tr('Small pieces together')}: {tr('largest free piece')} {rm.get('before_m2', 0):.2f} → "
                f"{rm.get('after_m2', 0):.2f} m²")
    if rm.get("why") == "old LEVEL7 Nesting":
        return f"\n\n{tr('Small pieces together')}: {tr('update LEVEL7 Nesting (Help → Check for updates) to use it')}"
    return f"\n\n{tr('Small pieces together')}: {tr('no better arrangement for this marker')}"


def seconds_box() -> QSpinBox:
    """Seconds between checks: left-to-right so the number is always visible, wide enough for 86400."""
    b = QSpinBox()
    b.setRange(5, 86400)
    b.setSingleStep(5)
    b.setSuffix(" " + tr("s"))
    b.setLayoutDirection(Qt.LeftToRight)
    b.setAlignment(Qt.AlignCenter)
    b.setMinimumWidth(96)
    return b


def table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels([tr(h) for h in headers])
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    t.horizontalHeader().setStretchLastSection(True)
    t.verticalHeader().setVisible(False)
    copyable(t)
    return t


def selected_text(t: QTableWidget) -> str:
    """The selected rows, cells separated by tabs (pastes into Excel as columns)."""
    rows = sorted({i.row() for i in t.selectedIndexes()})
    out = []
    for r in rows:
        out.append("\t".join((t.item(r, c).text() if t.item(r, c) else "") for c in range(t.columnCount())
                             if not t.isColumnHidden(c)))
    return "\n".join(out)


def quoted_names(text: str) -> list[str]:
    """The names a message quotes ("design code 'Small Size Fit' is not mapped …" → ["Small Size Fit"])."""
    out = []
    for n in re.findall(r"'([^']+)'", text or ""):
        if n.strip() and n.strip() not in out:
            out.append(n.strip())
    return out


def copyable(t: QTableWidget) -> None:
    """Ctrl+C copies the selected rows; right click: copy the rows, copy the cell, or show the cell's text to
    select words in it (owner: "select words and copy them")."""
    from PySide6.QtGui import QKeySequence, QShortcut
    from PySide6.QtWidgets import QApplication, QMenu, QPlainTextEdit
    QShortcut(QKeySequence.Copy, t, activated=lambda: QApplication.clipboard().setText(selected_text(t)),
              context=Qt.WidgetShortcut)

    def show_text(item):
        d = QDialog(t)
        d.setWindowTitle(t.horizontalHeaderItem(item.column()).text() if t.horizontalHeaderItem(item.column())
                         else "")
        lay = QVBoxLayout(d)
        text = item.text()
        names = quoted_names(text)
        ed = QPlainTextEdit(text + ("\n\n" + "\n".join(names) if names else ""))
        ed.setReadOnly(True)                              # select words and copy them (Ctrl+C / right click)
        lay.addWidget(ed)
        if names:                                         # each name in the message on a line of its own
            ed.setFocus()
            cur = ed.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.movePosition(cur.MoveOperation.StartOfBlock, cur.MoveMode.KeepAnchor)
            ed.setTextCursor(cur)                         # the last name selected: Ctrl+C copies it
            for n in names:
                b = QPushButton(f"{tr('Copy')}: {n}")
                b.clicked.connect(lambda _=False, n=n: QApplication.clipboard().setText(n))
                lay.addWidget(b)
        else:
            ed.selectAll()
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        d.resize(560, 200)
        d.exec()

    def menu(pos):
        item = t.itemAt(pos)
        m = QMenu(t)
        m.addAction(tr("Copy"), lambda: QApplication.clipboard().setText(selected_text(t)))
        if item is not None:
            m.addAction(tr("Copy the cell"), lambda: QApplication.clipboard().setText(item.text()))
            m.addAction(tr("Show the text to select words…"), lambda: show_text(item))
        m.exec(t.viewport().mapToGlobal(pos))
    t.setContextMenuPolicy(Qt.CustomContextMenu)
    t.customContextMenuRequested.connect(menu)
    t.show_text = show_text                               # message tables open it with a click


def messages_open_on_click(t: QTableWidget) -> None:
    """A message row opens its text window at once (the names in it on lines of their own, to copy)."""
    t.itemClicked.connect(lambda it: t.show_text(t.item(it.row(), t.columnCount() - 1) or it))


_KIND = {"#bbf7d0": "ok", "#a7f3d0": "ok", "#d1fae5": "ok", "#fde68a": "review", "#fecaca": "blocking",
         "#bfdbfe": "info", "#e0f2fe": "info", "#e5e7eb": "neutral", "#f3f4f6": "neutral"}


def paint(it: QTableWidgetItem, color: str | None) -> None:
    """Status colour of a cell in the current theme (tinted on dark, pastel on light; text always readable)."""
    kind = _KIND.get((color or "").lower())
    if kind is None:
        return
    from .themes import status_colours
    bg, fg = status_colours(kind)
    it.setBackground(bg)
    it.setForeground(fg)


def fill(t: QTableWidget, rows: list[list], colors: list[str | None] | None = None, data: list | None = None):
    t.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            it = QTableWidgetItem("" if v is None else str(v))
            if colors and colors[i]:
                paint(it, colors[i])
            if data is not None and j == 0:
                it.setData(Qt.UserRole, data[i])
            t.setItem(i, j, it)
    t.resizeColumnsToContents()


def selected_data(t: QTableWidget):
    rows = sorted({i.row() for i in t.selectedItems()})
    return [t.item(r, 0).data(Qt.UserRole) for r in rows]


def measurement_dialog(parent, code: str, name: str, value_mm: float) -> tuple[float, str] | None:
    """New value of one measurement, in cm (as the ERP sends it), and the reason, in one window.
    Returns (value in mm, reason) or None."""
    from PySide6.QtCore import QLocale
    d = QDialog(parent)
    d.setWindowTitle(tr("Edit measurement…"))
    d.setMinimumWidth(360)
    f = QFormLayout(d)
    f.addRow(QLabel(f"<b>{name}</b> ({code})"))
    box = QDoubleSpinBox()
    box.setLocale(QLocale(QLocale.English, QLocale.UnitedStates))    # 60.5, never ٦٠٫٥
    box.setLayoutDirection(Qt.LeftToRight)
    box.setRange(0, 500)
    box.setDecimals(1)
    box.setSingleStep(0.5)
    box.setSuffix(" cm")
    box.setValue(value_mm / 10)
    box.setMinimumWidth(160)
    f.addRow(tr("Now") + ":", QLabel(f"{value_mm / 10:g} cm"))
    f.addRow(tr("New value") + ":", box)
    why = QLineEdit()
    f.addRow(tr("Reason") + ":", why)
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    ok = bb.button(QDialogButtonBox.Ok)
    ok.setEnabled(False)
    why.textChanged.connect(lambda t: ok.setEnabled(bool(t.strip())))   # a change is always recorded with why
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    f.addRow(bb)
    box.setFocus()
    box.selectAll()
    if d.exec() != QDialog.Accepted or not why.text().strip():
        return None
    return round(box.value() * 10, 1), why.text().strip()


def ordered_ribbon(groups, cfg: dict) -> list:
    """The ribbon groups in the owner's order (setting ui.ribbon: groups, items per group, hidden icons).
    ``groups``: [(id, title, items)]. Icons and groups the setting does not know yet (a newer version) come
    after the known ones; a group whose icons are all hidden is left out."""
    rank = lambda order, key: order.index(key) if key in order else len(order)   # noqa: E731
    gorder = cfg.get("groups", [])
    hidden = set(cfg.get("hidden", []))
    out = []
    for gid, title, items in sorted(groups, key=lambda g: rank(gorder, g[0])):
        iorder = cfg.get("items", {}).get(gid, [])
        its = [it for it in sorted(items, key=lambda it: rank(iorder, it[0])) if it[0] not in hidden]
        if its:
            out.append((gid, title, its))
    return out


_GRAFIS_POLL = threading.Lock()          # one GRAFIS folder poll (check + nesting) at a time
_ROBOT_POLL = threading.Lock()           # one robot step at a time — beside the nesting, not after it


def alive(obj) -> bool:
    """False once Qt has deleted the widget (e.g. pages made anew on a theme / language switch)."""
    try:
        import shiboken6
        return shiboken6.isValid(obj)
    except ImportError:
        return True


def customer_bar(page) -> QWidget:
    """"Customer: [..]" at the top of a settings page: whose size table / options / parts are shown and saved."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 4)
    lab = QLabel(tr("Customer") + ":")
    lab.setStyleSheet("font-weight: 700;")
    box = QComboBox()
    box.setMinimumWidth(180)
    box.setToolTip(tr("The customer whose settings are shown and changed"))
    h.addWidget(lab)
    h.addWidget(box)
    h.addStretch()
    page.customer_box = box

    def chosen(_i):
        page.ws.db.set_setting("ui.customer", box.currentData())
        page.win.customer_changed()
    box.activated.connect(chosen)
    return w


def format_box(ws, cid: str | None) -> QComboBox:
    """Drop-down of the cutting file formats LEVEL7 Nesting can write; the first entry: as on the markers page."""
    from ..services.auto_cut import export_formats
    box = QComboBox()
    box.addItem(tr("As on the markers page"), None)
    for f in export_formats(ws):
        box.addItem(f.upper(), f)
    own = ws.db.get_setting(f"cutting.formats@{cid}") if cid else None
    if own:
        i = box.findData(own[0])
        if i < 0:
            box.addItem(own[0].upper(), own[0])
            i = box.count() - 1
        box.setCurrentIndex(i)
    box.setToolTip(tr("The cutting file written for this customer's orders by LEVEL7 Nesting"))
    return box


def save_format(ws, cid: str, box: QComboBox) -> None:
    ws.db.set_setting(f"cutting.formats@{cid}", [box.currentData()] if box.currentData() else None)


def _few(values: list[str], n: int = 4) -> str:
    return ", ".join(values[:n]) + (f" … (+{len(values) - n})" if len(values) > n else "") if values else "—"


def pattern_bar(page) -> QWidget:
    """"Pattern to review: [..]" — the model the size table / fixed pieces are shown on: one of the customer's
    patterns. A customer without patterns chooses a GRAFIS model file instead."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 4)
    h.addWidget(QLabel(tr("Pattern to review") + ":"))
    box = QComboBox()
    box.setMinimumWidth(260)
    h.addWidget(box)
    pick = QPushButton(tr("GRAFIS model (MDL)…"))
    h.addWidget(pick)
    h.addStretch()
    page.pattern_box, page.pattern_pick = box, pick

    def chosen(_i):
        if box.currentData():
            page.ws.set_grafis_mdl(box.currentData())
            page.refresh()

    def choose_file():
        p, _ = QFileDialog.getOpenFileName(page, tr("GRAFIS model (MDL)…"), page.ws.grafis_mdl() or "",
                                           "GRAFIS (*.mdl *.MDL)")
        if p:
            page.ws.set_grafis_mdl(p)
            page.refresh()
    box.activated.connect(chosen)
    pick.clicked.connect(choose_file)
    return w


def fill_pattern_box(page) -> None:
    from ..services.customers import patterns
    pats = patterns(page.ws)
    box, cur = page.pattern_box, page.ws.grafis_mdl()
    box.blockSignals(True)
    box.clear()
    for p in pats:
        box.addItem(f"{p['variation']}  —  {os.path.basename(p['mdl'])}", p["mdl"])
    if pats and box.findData(cur) < 0 and cur:
        box.addItem(f"{os.path.basename(cur)}", cur)
    box.setCurrentIndex(max(box.findData(cur), 0))
    box.blockSignals(False)
    box.setVisible(bool(pats))
    page.pattern_box.parentWidget().layout().itemAt(0).widget().setVisible(bool(pats))
    page.pattern_pick.setVisible(not pats)              # no patterns: the customer's model file


def fill_customer_box(ws, box) -> None:
    from ..services.customers import names
    box.blockSignals(True)
    box.clear()
    for cid, n in names(ws).items():
        box.addItem(n, cid)
    box.setCurrentIndex(max(box.findData(ws.customer()), 0))
    box.blockSignals(False)


_REREADING: set = set()                        # customers whose held orders are being read again now
_AGAIN: dict = {}                               # saved again meanwhile: read once more after (customer → message)


def reread_held_question(page, msg: str, cid: str | None = None) -> None:
    """After the customer's settings were saved: his held orders are read again at once, in the background (owner:
    asked first, an order missed the question and kept its old messages although its Variation had a meaning).
    The status bar says how far it is; the result comes at the end."""
    from ..services.customers import held_files, held_lines, reread_held
    cid = cid or page.ws.customer()                      # the customer edited (in the background: no screen choice)
    fids = held_files(page.ws, cid)
    if not fids:
        QMessageBox.information(page, tr("Saved"), msg)
        return
    win = page.win
    if cid in _REREADING:                                # being read again (an earlier save): once more after it, with
        _AGAIN[cid] = msg                                # this save's settings for the orders it has passed already
        win.status(tr("Reading the held orders again") + "…")
        return
    _REREADING.add(cid)
    lines = held_lines(page.ws, fids)
    win.status(f"{tr('Saved')} — {tr('Reading the held orders again')}: {len(fids)} XML ({lines})")

    def done(outs):
        _REREADING.discard(cid)
        win.refresh_cad(force=True)
        if cid in _AGAIN:                                # saved again meanwhile: once more, then the result
            reread_held_question(page, _AGAIN.pop(cid), cid)
            return
        QMessageBox.information(win, tr("Saved"), msg + "\n\n" + reread_report(win.ws, outs))

    def work():
        try:
            return reread_held(page.ws, cid, fids)
        finally:
            _REREADING.discard(cid)
    win.run(work, done)


def reread_report(ws, outs) -> str:
    """Each file read again, its lines' status and — for a line still held — why (each reason once). Many files:
    the totals and the reasons only (a list of a thousand files would not fit on the screen)."""
    if len(outs) > 20:
        from collections import Counter
        st = Counter(x["status"] for o in outs for x in o.lines)
        why = Counter()
        for o in outs:
            if o.status == "failed" and o.message:       # the file itself could not be read again
                why[tr_msg(o.message)] += 1
            for x in o.lines:
                if x["status"] != "ready" and x.get("revision_id"):
                    for p in ws.revision(x["revision_id"])["normalized"].get("problems") or []:
                        why[tr_msg(p["message"])] += 1
        text = f"{tr('Read again')}: {len(outs)} — " + ", ".join(f"{tr(k)}: {v}" for k, v in st.most_common())
        if why:
            text += "\n\n" + tr("Still held because") + ":\n" + "\n".join(f"  • {m} ({c})"
                                                                     for m, c in why.most_common(15))
        return text
    from ..services.customers import names
    who = names(ws)
    out = []
    for o in outs:
        cust = next((ws.customer_of(x["revision_id"]) for x in o.lines if x.get("revision_id")), None)
        out.append(f"{o.file_name}: {tr(o.status)} " + (f"({tr('read with the settings of')} {who.get(cust, cust)}) "
                                                        if cust else "") +
                   ", ".join(f"{x['line_number']} → {tr(x['status'])}" for x in o.lines))
        if o.message:
            out.append("  " + tr_msg(o.message))
        why: list[str] = []
        for x in o.lines:
            if x["status"] == "ready" or not x.get("revision_id"):
                continue
            for p in ws.revision(x["revision_id"])["normalized"].get("problems") or []:
                m = tr_msg(p["message"])
                if m not in why:
                    why.append(m)
        out += [f"  • {m}" for m in why[:15]]
        if len(why) > 15:
            out.append(f"  … (+{len(why) - 15})")
    return "\n".join(out)


def _same_path(a, b) -> bool:
    return bool(a and b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _mdl_or_none(ws):
    m = ws.grafis_mdl()
    return m if m and os.path.isfile(m) else None


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


class HomePage(Page):
    """Start screen: what is happening now (cards), what is wrong, quick actions, the latest events."""
    title = "Home"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        grid = QHBoxLayout()
        grid.setSpacing(14)
        self.cards = {}
        for key, label_ in (("orders", "Orders this shift"), ("waiting", "Waiting for GRAFIS"),
                            ("cut", "Cutting files this shift"), ("errors", "Need attention")):
            card, val = self._card(tr(label_))
            self.cards[key] = val
            grid.addWidget(card)
        lay.addLayout(grid)
        sh = QFrame()                                  # the shift: running since …, end it; every shift's work
        sh.setObjectName("card")
        shl = QVBoxLayout(sh)
        top = QHBoxLayout()
        self.shift_label = QLabel()
        self.shift_label.setObjectName("cardTitle")
        top.addWidget(self.shift_label, 1)
        b = QPushButton(tr("End the shift"))
        b.setObjectName("primary")
        b.clicked.connect(self.end_shift)
        top.addWidget(b)
        b = QPushButton(tr("Shifts…"))
        b.clicked.connect(self.edit_shifts)
        top.addWidget(b)
        shl.addLayout(top)
        self.shifts = table(["Shift", "From", "To", "Order lines", "Models for CAD", "Saved by CAD", "Nested",
                             "Cutting files", "Metres", "Problems in the shift"])
        self.shifts.setMinimumHeight(170)
        shl.addWidget(self.shifts)
        lay.addWidget(sh, 2)
        row = QHBoxLayout()
        row.setSpacing(14)
        st = QFrame()
        st.setObjectName("card")
        sl = QVBoxLayout(st)
        t = QLabel(tr("System status"))
        t.setObjectName("cardTitle")
        sl.addWidget(t)
        self.status_rows = QVBoxLayout()
        sl.addLayout(self.status_rows)
        sl.addStretch()
        row.addWidget(st, 1)
        qa = QFrame()
        qa.setObjectName("card")
        ql = QVBoxLayout(qa)
        t = QLabel(tr("Quick actions"))
        t.setObjectName("cardTitle")
        ql.addWidget(t)
        for text, fn in (("⇪  " + tr("Import XML files…"), lambda: self.win.import_dialog()),
                         ("▶  " + tr("Robot: run now"), lambda: self._grafis().robot_now()),
                         ("✂  " + tr("Open cutting files folder"), lambda: self._grafis().open_out()),
                         ("⚠  " + tr("Errors folder (ER)"), self._open_er)):
            b = QPushButton(text)
            b.setObjectName("quick")
            b.clicked.connect(fn)
            ql.addWidget(b)
        ql.addStretch()
        row.addWidget(qa, 1)
        lay.addLayout(row, 1)
        rc = QFrame()
        rc.setObjectName("card")
        rl = QVBoxLayout(rc)
        t = QLabel(tr("Latest events"))
        t.setObjectName("cardTitle")
        rl.addWidget(t)
        self.recent = table(["Time", "Action", "Details"])
        rl.addWidget(self.recent)
        lay.addWidget(rc, 2)

    @staticmethod
    def _card(label_):
        f = QFrame()
        f.setObjectName("card")
        f.setMinimumHeight(110)
        v = QVBoxLayout(f)
        val = QLabel("—")
        val.setObjectName("cardValue")
        lab = QLabel(label_)
        lab.setObjectName("cardLabel")
        v.addWidget(val)
        v.addWidget(lab)
        v.addStretch()
        return f, val

    def _grafis(self):
        return self.win.pages["GRAFIS markers"]

    def end_shift(self):
        """The shift ends: counted into the history; the "ready to cut" list starts from zero (nothing deleted)."""
        from ..services.shifts import end_shift
        if QMessageBox.question(self, tr("End the shift"), tr("End the shift now? The «Ready to cut» list starts "
                                                             "from zero; the cutting files stay in the customers' "
                                                             "folders and every order stays in the program.")) \
                != QMessageBox.Yes:
            return
        rec = self.win.guard(lambda: end_shift(self.ws))
        if rec:
            self.win.status(f"{tr('Shift ended')}: {rec['counts']['nested']} {tr('Nested')}, "
                            f"{rec['counts']['cut_files']} {tr('Cutting files')}")
            self.win.refresh_cad(force=True)
        self.refresh()

    def edit_shifts(self):
        """The day's shifts: name and start time (e.g. morning 08:00, evening 20:00); a shift ends by itself when
        the next one starts. None: a shift lasts until «End the shift»."""
        from ..services.shifts import schedule, set_schedule
        d = QDialog(self)
        d.setWindowTitle(tr("Shifts…"))
        d.setMinimumWidth(380)
        v = QVBoxLayout(d)
        note = QLabel(tr("Each shift and the time it starts (24 h, e.g. 08:00, 20:00). A shift ends by itself when "
                         "the next starts; without shifts here, it lasts until «End the shift»."))
        note.setWordWrap(True)
        v.addWidget(note)
        t = QTableWidget(0, 2)
        t.setHorizontalHeaderLabels([tr("Shift"), tr("Starts at")])
        t.horizontalHeader().setStretchLastSection(True)
        for r in schedule(self.ws):
            t.insertRow(t.rowCount())
            t.setItem(t.rowCount() - 1, 0, QTableWidgetItem(r["name"]))
            t.setItem(t.rowCount() - 1, 1, QTableWidgetItem(r["start"]))
        v.addWidget(t)
        bar = QHBoxLayout()
        for text, fn in ((tr("Add"), lambda: t.insertRow(t.rowCount())),
                         (tr("Remove"), lambda: t.removeRow(t.currentRow()) if t.currentRow() >= 0 else None)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        v.addLayout(bar)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        v.addWidget(bb)
        if d.exec() != QDialog.Accepted:
            return
        rows = [{"name": (t.item(i, 0).text() if t.item(i, 0) else ""),
                 "start": (t.item(i, 1).text() if t.item(i, 1) else "")} for i in range(t.rowCount())]
        try:
            set_schedule(self.ws, rows)
        except ValueError:
            QMessageBox.warning(self, tr("Error"), tr("A start time is written as 08:00 or 20:30"))
            return
        self.refresh()

    def _open_er(self):
        d = self.ws.db.get_setting("errors.folder")
        if d and os.path.isdir(d) and hasattr(os, "startfile"):
            os.startfile(d)
        else:
            QMessageBox.information(self, "ER", d or tr("not set"))

    def refresh(self):
        from datetime import date
        from ..services.auto_cut import NestingNotConfigured, nesting_command
        from ..services.grafis_robot import pending
        today = date.today().isoformat()
        db = self.ws.db
        from ..services.shifts import FIRST, history
        hist = history(self.ws)
        cur = hist[0]
        n_orders, n_cut = cur["counts"]["orders"], cur["counts"]["nested"]
        when = lambda t: "—" if not t or t == FIRST else t.replace("T", " ")[:16]   # noqa: E731
        self.shift_label.setText(f"{tr('Shift')}: {cur['name'] or '—'} — {tr('since')} {when(cur['started_at'])}")
        keys = ("orders", "models", "cad_saved", "nested", "cut_files", "metres", "problems")
        fill(self.shifts, [[h["name"] or "—", when(h["started_at"]), when(h["ended_at"]) if h["ended_at"] else
                            tr("now")] + [h["counts"].get(k, 0) for k in keys] for h in hist],
             [STATUS_COLORS.get("info")] + [None] * (len(hist) - 1))
        del today
        n_bad = db.one("SELECT COUNT(*) AS n FROM grafis_orders g WHERE archived=0 AND status IN "
                       "('blocked','not_configured') AND id IN (SELECT MAX(id) FROM grafis_orders GROUP BY "
                       "revision_id)")["n"] + db.one("SELECT COUNT(*) AS n FROM grafis_results WHERE archived=0 "
                                                     "AND status IN ('needs_review','unmatched')")["n"]
        try:
            n_wait = len(pending(self.ws))
        except Exception:  # noqa: BLE001
            n_wait = 0
        for k, v in (("orders", n_orders), ("waiting", n_wait), ("cut", n_cut), ("errors", n_bad)):
            self.cards[k].setText(str(v))
        while self.status_rows.count():
            w = self.status_rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        try:
            cli = nesting_command(self.ws)[0][-1]
        except NestingNotConfigured:
            cli = None
        mdl = db.get_setting("grafis.mdl")
        rb = db.get_setting("robot.buttons", {}) or {}
        items = [(bool(mdl and os.path.isfile(mdl)), tr("GRAFIS model (MDL)"), os.path.basename(mdl or "") or
                  tr("not set")),
                 (bool(db.get_setting("grafis.watch.enabled", False)), tr("Watch GRAFIS output folder"),
                  db.get_setting("grafis.input") or ""),
                 (bool(db.get_setting("robot.enabled", False)) and "test_run" in rb, tr("GRAFIS robot"),
                  tr("on") if db.get_setting("robot.enabled", False) else tr("off")),
                 (cli is not None, "LEVEL7 Nesting", cli or tr("not found")),
                 (bool(db.get_setting("watch.enabled", False)), tr("Watch folder") + " (ERP)",
                  db.get_setting("watch.folder") or "")]
        for ok, what, detail in items:
            line = QWidget()
            hl = QHBoxLayout(line)
            hl.setContentsMargins(0, 2, 0, 2)
            dot = QLabel("●")
            dot.setObjectName("dotOk" if ok else "dotBad")
            hl.addWidget(dot)
            hl.addWidget(QLabel(f"<b>{what}</b>"))
            d = QLabel(detail)
            d.setObjectName("cardLabel")
            d.setToolTip(detail)
            d.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # long paths never widen the window
            hl.addWidget(d, 1)
            self.status_rows.addWidget(line)
        rows = db.q("SELECT at, action, details FROM audit ORDER BY id DESC LIMIT 12")
        fill(self.recent, [[r["at"].replace("T", " "), r["action"], r["details"][:160]] for r in rows])


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
        messages_open_on_click(self.probs)
        sp.addWidget(self.opts)
        sp.addWidget(self.probs)
        lay.addWidget(sp, 2)
        bar = QHBoxLayout()
        for text, fn in (("Edit measurement…", self.edit_measure), ("Measurement limits…", self.edit_range),
                         ("Confirm problems handled…", self.resolve),
                         ("Cancel order…", self.cancel), ("Calculate", self.calculate)):
            b = QPushButton(tr(text))
            b.clicked.connect(fn)
            bar.addWidget(b)
            if text == "Calculate":
                self.b_calc = b
            elif text == "Edit measurement…":
                self.b_edit = b
        bar.addStretch()
        lay.addLayout(bar)

    def refresh(self):
        rid = self.win.current_rid
        if rid is None:
            return
        r = self.ws.revision(rid)
        done = r["status"] in ("cancelled", "superseded", "approved", "sent_to_nesting", "completed")
        from ..services.workflow import OPEN_FOR_EDIT
        editable = r["status"] in OPEN_FOR_EDIT
        self.b_edit.setEnabled(editable)
        self.b_edit.setToolTip("" if editable else
                               tr("This order is {s}: its measurements are not changed").format(s=tr(r["status"])))
        self.b_calc.setEnabled(not done)                 # a finished order is not calculated again
        self.b_calc.setToolTip(tr("This order is {s}: its pattern is not calculated again").format(s=tr(r["status"]))
                               if done else "")
        raw = r["raw_line"]
        self.head.setText(f"<b>{r['line_number']}</b> — {tr('Status')}: {tr(r['status'])} — {tr('Quantity')}: "
                          f"{r['quantity']} — Fabric {raw.get('fabric_number')} / {raw.get('fabric_width_mm')} mm — "
                          f"{raw.get('measurement_table')} / Basic-Size {raw.get('basic_size')} — "
                          f"{raw.get('order_date')}")
        ms = r["normalized"]["measurements"]
        from .cad import ltr                                  # -17.5 stays -17.5 (not 17.5- in Arabic)
        fill(self.meas, [[k, label(v["label"]), ltr(v["source_value"]), v["source_unit"],
                          "—" if v["value_mm"] is None else ltr(f"{v['value_mm']:.1f}") + (" ✎" if v.get("manual") else ""),
                          v["kind"], v["meaning"], v["status"]] for k, v in ms.items()],
             [None if v["value_mm"] is not None else STATUS_COLORS["review"] for v in ms.values()], list(ms))
        fill(self.opts, [[g, v] for g, v in r["normalized"]["options"].items()] +
             [["(ERP)", c] for c in raw.get("option_codes", [])])
        pr = r["normalized"]["problems"]
        fill(self.probs, [[tr(p["severity"]), tr_msg(p["message"])] for p in pr],
             [STATUS_COLORS.get(p["severity"]) for p in pr])

    def edit_measure(self):
        rid = self.win.current_rid
        codes = selected_data(self.meas)
        if rid is None or not codes:
            return
        code = codes[0]
        m = self.ws.revision(rid)["normalized"]["measurements"][code]
        got = measurement_dialog(self, code, label(m.get("label")) or code, m["value_mm"] or 0.0)
        if got:
            val, reason = got
            if self.win.guard(lambda: self.ws.edit_measurement(rid, code, val, reason) or True):
                self.win.model_again(rid)                # through CAD again, then nested again
            self.refresh()

    def _range_code(self, rid):
        """The measurement chosen in the table, else the one an "outside the range" message names."""
        codes = selected_data(self.meas)
        if codes:
            return codes[0]
        for p in self.ws.revision(rid)["normalized"]["problems"]:
            if p.get("code") == "measure.range":
                return p.get("field") or (re.match(r"(\S+) = ", p["message"]) or [None, None])[1]
        return None

    def edit_range(self):
        """The check of a measurement for this order's customer (or product) — for all his patterns or the order's
        pattern: from … to, no check, or a formula; another customer keeps his own. Then his held orders may be
        read again."""
        from ..services.checks import ALL, get_checks, set_checks
        from ..services.customers import _plain, measure_range, names, patterns
        rid = self.win.current_rid
        code = self._range_code(rid) if rid is not None else None
        if not code:
            QMessageBox.information(self, tr("Measurement limits…"), tr("Choose a measurement in the table"))
            return
        cid = self.ws.customer_of(rid)
        rng, unit = measure_range(self.ws, cid, code)
        have = {_plain(c) for c in self.ws.revision(rid)["raw_line"].get("option_codes") or []}
        mine = [p["variation"] for p in patterns(self.ws, cid) if _plain(p["variation"]) in have]
        every = get_checks(self.ws, cid)
        d = QDialog(self)
        d.setWindowTitle(f"{tr('Measurement limits…')} — {names(self.ws).get(cid, cid)}")
        d.setMinimumWidth(420)
        f = QFormLayout(d)
        f.addRow(QLabel(f"<b>{code}</b> ({unit}) — {tr('for this customer / product only')}"))
        pat = QComboBox()
        pat.addItem(tr("All patterns"), ALL)
        for v in mine:
            pat.addItem(f"{tr('Pattern')} {v}", v)
        f.addRow(tr("Pattern") + ":", pat)
        mode = QComboBox()
        for k, text in CHECK_MODES:
            mode.addItem(tr(text), k)
        f.addRow(tr("Check") + ":", mode)
        lo, hi, formula = QLineEdit(), QLineEdit(), QLineEdit()
        formula.setPlaceholderText("FL - 10")
        for w in (lo, hi, formula):
            w.setLayoutDirection(Qt.LeftToRight)
        f.addRow(tr("From") + ":", lo)
        f.addRow(tr("To") + ":", hi)
        f.addRow(tr("Formula") + ":", formula)

        def load(_i=None):
            r = every.get(pat.currentData(), {}).get(code) or every.get(ALL, {}).get(code) or \
                ({"mode": "range", "min": rng[0], "max": rng[1]} if rng else {"mode": "none"})
            mode.setCurrentIndex([k for k, _ in CHECK_MODES].index(r.get("mode", "range")))
            lo.setText("" if r.get("min") is None else f"{r['min']:g}")
            hi.setText("" if r.get("max") is None else f"{r['max']:g}")
            formula.setText(r.get("formula", ""))

        def shown(_i=None):
            m = mode.currentData()
            for w in (lo, hi):
                w.setEnabled(m != "none")
            formula.setEnabled(m == "formula")
        pat.currentIndexChanged.connect(load)
        mode.currentIndexChanged.connect(shown)
        load()
        shown()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec() != QDialog.Accepted:
            return
        rows = dict(every.get(pat.currentData(), {}))
        rows[code] = {"mode": mode.currentData(), "min": lo.text().strip().replace(",", "."),
                      "max": hi.text().strip().replace(",", "."), "formula": formula.text().strip()}
        try:
            changed = set_checks(self.ws, cid, pat.currentData(), rows)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Error"), tr_msg(str(e)))
            return
        if changed:
            reread_held_question(self, tr("Saved"), cid)

    def resolve(self):
        rid = self.win.current_rid
        reason = ask_reason(self, tr("Confirm problems handled…")) if rid is not None else None
        if reason:
            if self.win.guard(lambda: self.ws.resolve_problems(rid, reason) or True):
                self.win.model_again(rid)
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
        if reason and self.win.guard(lambda: self.ws.set_option(rid, group, value, reason) or True):
            self.win.model_again(rid)
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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        from . import cad
        self.sheet = cad.MarkerPanel()               # the order's pattern from GRAFIS (the pieces of its marker)
        self.sheet.setMinimumHeight(420)
        outer.addWidget(self.sheet, 1)
        self.engine = QWidget()                      # the program's own engine (test model): developer only
        outer.addWidget(self.engine, 1)
        lay = QVBoxLayout(self.engine)
        lay.setContentsMargins(0, 0, 0, 0)
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
        from . import cad
        rid = self.win.current_rid
        row = next((r for r in cad.order_rows(self.ws) if r["rid"] == rid), None) if rid is not None else None
        sheet = cad.pattern_sheet(cad.load_marker(row["l7n"])) if row and row.get("l7n") else None
        use_engine = sheet is None and self.win.developer
        self.sheet.setVisible(not use_engine)
        self.engine.setVisible(use_engine)
        if not use_engine:
            msg = "" if sheet else (tr("No order selected") if rid is None else
                                    tr("The pattern of this order is made in GRAFIS: its pieces are shown here once "
                                       "GRAFIS has saved the model and it was nested"))
            self.sheet.set_marker(sheet, None, msg)
            return
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
        messages_open_on_click(self.issues)
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
            mdl, _ = QFileDialog.getOpenFileName(self, tr("GRAFIS"), "", "GRAFIS (*.mdl *.MDL)")
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

    @staticmethod
    def _card(title_: str):
        f = QFrame()
        f.setObjectName("card")
        v = QVBoxLayout(f)
        v.setContentsMargins(14, 12, 14, 12)
        t = QLabel(title_)
        t.setObjectName("cardTitle")
        v.addWidget(t)
        return f, v

    @staticmethod
    def _buttons(layout, items, primary=()):
        row = QHBoxLayout()
        for text, fn in items:
            b = QPushButton(tr(text))
            if text in primary:
                b.setObjectName("primary")
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        layout.addLayout(row)

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        # status strip + settings switch
        top = QHBoxLayout()
        self.chips = QGridLayout()                  # 3 per row: the page fits the window, no side scrolling
        self.chips.setHorizontalSpacing(8)
        self.chips.setVerticalSpacing(6)
        top.addLayout(self.chips)
        top.addStretch()
        self.settings_btn = QPushButton("⚙  " + tr("Settings"))
        self.settings_btn.setCheckable(True)
        self.settings_btn.toggled.connect(self._show_settings)
        top.addWidget(self.settings_btn)
        lay.addLayout(top)
        # settings: numbered steps in the order they are done (each shows ✓ when done)
        self.settings_card, sv = self._card(tr("Setup steps"))
        self.steps = {}

        def step(key, n, title_, desc, buttons, extra=None):
            f = QFrame()
            f.setObjectName("step")
            h = QHBoxLayout(f)
            h.setContentsMargins(12, 10, 12, 10)
            badge = QLabel(str(n))
            badge.setObjectName("stepBadge")
            badge.setAlignment(Qt.AlignCenter)
            badge.setFixedSize(34, 34)
            h.addWidget(badge, 0, Qt.AlignTop)
            col = QVBoxLayout()
            t = QLabel(f"<b>{tr(title_)}</b>")
            d = QLabel(tr(desc))
            d.setObjectName("cardLabel")
            d.setWordWrap(True)
            st_ = QLabel()
            st_.setObjectName("cardLabel")
            st_.setWordWrap(True)
            st_.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            col.addWidget(t)
            col.addWidget(d)
            if extra is not None:
                col.addWidget(extra)
            col.addWidget(st_)
            h.addLayout(col, 1)
            bc = QVBoxLayout()
            for text, fn in buttons:
                b = QPushButton(tr(text))
                b.clicked.connect(fn)
                bc.addWidget(b)
            bc.addStretch()
            h.addLayout(bc)
            self.steps[key] = (badge, st_, n)
            sv.addWidget(f)

        step("model", 1, "GRAFIS base model", "The model whose X values and parts are written for each order.",
             (("GRAFIS model (MDL)…", self.choose_mdl),))
        step("folders", 2, "Folders", "One main folder with MDL (models for GRAFIS), SCB, ER (errors) and CUT "
                                      "(cutting files); each can also be chosen alone.",
             (("Folders MDL / SCB / ER / CUT…", self.choose_folders), ("GRAFIS input folder…", self.choose_input),
              ("Errors folder (ER)…", self.choose_errors), ("Cutting files folder…", self.choose_out),
              ("Markers folder…", self.choose_markers), ("Order models archive…", self.choose_archive)))
        step("nesting", 3, "LEVEL7 Nesting", "The nesting program that writes the marker and the cutting files.",
             (("LEVEL7 Nesting program…", self.choose_cli),))

        robot_box = QWidget()
        rl_ = QHBoxLayout(robot_box)
        rl_.setContentsMargins(0, 4, 0, 0)
        self.no_mouse = QCheckBox(tr("Without the mouse (in the background)"))
        self.no_mouse.setToolTip(tr("The robot sends GRAFIS its menu commands or clicks the button's own window: "
                                    "the mouse pointer never moves, you can keep working"))
        self.no_mouse.setChecked(self.ws.db.get_setting("robot.mode", "auto") == "background")
        self.no_mouse.toggled.connect(lambda on: (self.ws.db.set_setting("robot.mode", "background" if on else "auto"),
                                                  self.refresh()))
        self.behind = QCheckBox(tr("GRAFIS behind my windows"))
        self.behind.setToolTip(tr("With 'Without the mouse': GRAFIS opens behind your windows (not over the whole "
                                  "screen) and the window you work in stays in front"))
        self.behind.setChecked(bool(self.ws.db.get_setting("robot.behind", True)))
        self.behind.toggled.connect(lambda on: self.ws.db.set_setting("robot.behind", on))
        b_menu = QPushButton(tr("GRAFIS menu commands…"))
        b_menu.clicked.connect(self.choose_menu)
        rl_.addWidget(self.no_mouse)
        rl_.addWidget(self.behind)
        rl_.addWidget(b_menu)
        rl_.addStretch()
        erp_box = QWidget()
        el = QHBoxLayout(erp_box)
        el.setContentsMargins(0, 4, 0, 0)
        el.addWidget(QLabel(tr("Check every") + ":"))
        self.erp_every = seconds_box()
        self.erp_every.setValue(self.win.watch_interval_s() if hasattr(self.win, "watch_interval_s") else 60)
        self.erp_every.valueChanged.connect(lambda v: (self.win.set_watch_interval(v), self.refresh()))
        el.addWidget(self.erp_every)
        el.addStretch()
        step("robot", 4, "Teach the GRAFIS robot", "Open a model in GRAFIS, press a button below and point the mouse "
                                                   "at that GRAFIS button within 5 seconds.",
             (("Teach: Test Run All Parts button", lambda: self.teach("test_run")),
              ("Teach: Save button", lambda: self.teach("save")), ("Teach: Close button", lambda: self.teach("close")),
              ("Teach: button of a GRAFIS start window", self.teach_dialog)),
             extra=robot_box)
        auto_box = QWidget()
        al = QVBoxLayout(auto_box)
        al.setContentsMargins(0, 4, 0, 0)
        self.robot = QCheckBox(tr("Robot on this PC"))
        self.robot.setToolTip(tr("GRAFIS robot on this PC: open the model, Test Run All Parts, save"))
        self.robot.setChecked(bool(self.ws.db.get_setting("robot.enabled", False)))
        self.robot.toggled.connect(lambda on: (self.ws.db.set_setting("robot.enabled", on), self.refresh()))
        self.auto_mdl = QCheckBox(tr("Write the order models"))
        self.auto_mdl.setToolTip(tr("Automatic: write the order's X values into a GRAFIS model (MDL) on import"))
        self.auto_mdl.setChecked(bool(self.ws.db.get_setting("grafis.auto_mdl", True)))
        self.auto_mdl.toggled.connect(lambda on: (self.ws.db.set_setting("grafis.auto_mdl", on), self.refresh()))
        self.auto = QCheckBox(tr("Nest and write cutting files"))
        self.auto.setToolTip(tr("Automatic: nest and write cutting files (PLT / ISO)"))
        self.auto.setChecked(bool(self.ws.db.get_setting("cutting.auto", True)))
        self.auto.toggled.connect(lambda on: (self.ws.db.set_setting("cutting.auto", on), self.refresh()))
        self.pcheck = QCheckBox(tr("Check the pattern for distortion"))
        self.pcheck.setToolTip(tr("A piece with a spike, a slit or an outline crossing itself: no cutting file, the "
                                  "model goes to MDL-ERR with the reason"))
        self.pcheck.setChecked(bool(self.ws.db.get_setting("cutting.pattern_check", True)))
        self.pcheck.toggled.connect(lambda on: self.ws.db.set_setting("cutting.pattern_check", on))
        r1 = QHBoxLayout()
        for w in (self.auto_mdl, self.robot, self.auto, self.pcheck):
            r1.addWidget(w)
        r1.addStretch()
        al.addLayout(r1)
        frow = QHBoxLayout()
        frow.addWidget(QLabel(tr("Formats") + ":"))
        fmts = self.ws.db.get_setting("cutting.formats", ["plt", "iso"])
        self.fmt_boxes = {}
        for code, label_ in (("plt", "PLT (Algotex)"), ("iso", "ISO (Bullmer)"), ("dxf", "DXF")):
            cb = QCheckBox(label_)
            cb.setChecked(code in fmts)
            cb.toggled.connect(self._save_formats)
            self.fmt_boxes[code] = cb
            frow.addWidget(cb)
        frow.addSpacing(16)
        frow.addWidget(QLabel(tr("Nesting time (s)") + ":"))
        self.nest_time = QSpinBox()
        self.nest_time.setRange(10, 1800)
        self.nest_time.setValue(int(float(self.ws.db.get_setting("cutting.time_s", 60))))
        self.nest_time.valueChanged.connect(lambda v: self.ws.db.set_setting("cutting.time_s", int(v)))
        frow.addWidget(self.nest_time)
        frow.addStretch()
        al.addLayout(frow)
        frow = QHBoxLayout()                        # a row of its own: the page stays as wide as the window
        self.remnant = QCheckBox(tr("Small pieces together (one large piece of fabric left)"))
        self.remnant.setToolTip(tr("After nesting, LEVEL7 Nesting packs the small pieces next to each other so "
                                   "the fabric left over is one large piece; the marker length does not change"))
        self.remnant.setChecked(bool(self.ws.db.get_setting("cutting.remnant", True)))
        self.remnant.toggled.connect(lambda on: self.ws.db.set_setting("cutting.remnant", on))
        frow.addWidget(self.remnant)
        frow.addStretch()
        al.addLayout(frow)
        step("auto", 5, "Switch on", "What runs by itself for each new order, and the cutting files it writes.",
             (("Watch GRAFIS output folder", self.toggle),), extra=auto_box)
        step("erp", 6, "New orders from ERP", "The folder where the ERP puts the XML orders: checked on this timer, "
                                               "every new order goes through the whole chain by itself.",
             (("ERP XML folder…", self.choose_erp), ("Check now", lambda: self.win.poll_watch())), extra=erp_box)
        adv = QHBoxLayout()
        for text, fn in (("Nesting folder…", self.choose_nesting), ("Register GRAFIS marker (SCB)…", self.register)):
            b = QPushButton(tr(text))
            b.clicked.connect(fn)
            adv.addWidget(b)
        adv.addStretch()
        sv.addLayout(adv)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setObjectName("cardLabel")
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        sv.addWidget(self.info)
        lay.addWidget(self.settings_card)
        # order models for GRAFIS
        mcard, mv = self._card(tr("Order models for GRAFIS"))
        self._buttons(mv, (("Robot: run now", self.robot_now), ("Write GRAFIS model for order…", self.write_mdl),
                           ("Robot: try again", self.robot_retry)), primary=("Robot: run now",))
        self.models = table(["Time", "Order line", "Status", "File", "X values", "GRAFIS parts", "Findings"])
        mv.addWidget(self.models, 1)
        lay.addWidget(mcard, 1)
        # markers and cutting files
        ccard, cv = self._card(tr("Markers and cutting files"))
        self._buttons(cv, (("Show marker in LEVEL7 Nesting", self.show_marker), ("Nest again", self.renest),
                           ("Open cutting files folder", self.open_out), ("Accept review findings…", self.accept),
                           ("Clean the list…", self.clean_list)), primary=("Show marker in LEVEL7 Nesting",))
        self.t = table(["File", "Order line", "Status", "Width", "Bundles", "Pieces", "Cutting files", "Findings"])
        self.t.cellDoubleClicked.connect(lambda *_: self.show_marker())
        cv.addWidget(self.t, 1)
        lay.addWidget(ccard, 1)
        self._settings_auto = True
        self.settings_card.setVisible(False)
        self.busy = False
        self.watcher = None
        self.mdl_watcher = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        if getattr(win, "chain_on", False):           # started (Start pressed) before a theme / language switch
            self.auto_start()
        else:
            self._start_mdl()                         # the nesting has its own button: it runs without Start

    def auto_start(self):
        """The chain runs by itself as soon as the program is open — nobody presses the robot: the robot takes
        each order model written on import, the models GRAFIS saves are checked and nested, cutting files are
        written. Watches the GRAFIS output folder chosen before (or the SCB folder next to the MDL folder); the
        MDL folder is always watched."""
        from ..services.grafis_orders import settings as gsettings
        folder = self.ws.db.get_setting("grafis.watch.folder")
        if not (folder and os.path.isdir(folder)):
            scb = os.path.join(os.path.dirname(os.path.abspath(gsettings(self.ws)["out"])), "SCB")
            folder = scb if os.path.isdir(scb) else None
        if folder:
            self.start(folder)
        else:
            self._start_mdl()

    def _nesting_dir(self):
        return self.ws.db.get_setting("nesting.inbox") or os.path.join(self.ws.folder, "nesting")

    def _show_settings(self, on):
        self._settings_auto = False                     # the owner decided: keep it as chosen
        self.settings_card.setVisible(on)

    def _chips(self, items):
        """Status strip: one chip per part of the chain (green = set up, red = needs attention)."""
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, (ok, text) in enumerate(items):
            c = QLabel(f"<span style='color:{'#2ecc71' if ok else '#e74c3c'}'>●</span> {text}")
            c.setObjectName("statChip")
            c.setStyleSheet("QLabel { border: 1px solid rgba(128,128,128,0.35); border-radius: 12px; "
                            "padding: 4px 12px; }")
            self.chips.addWidget(c, i // 3, i % 3)

    def refresh(self):
        from ..services.auto_cut import NestingNotConfigured, nesting_command, settings
        from ..services.grafis_orders import settings as gsettings
        gs = gsettings(self.ws)
        mrows = self.ws.db.q("SELECT g.*, r.line_number FROM grafis_orders g JOIN revisions r ON r.id=g.revision_id "
                             "WHERE g.archived=0 AND g.id IN (SELECT MAX(id) FROM grafis_orders GROUP BY revision_id) "
                             "ORDER BY g.id DESC LIMIT 500")
        mo, mc = [], []
        for r in mrows:
            info = json.loads(r["info"])
            mo.append([r["created_at"], r["line_number"], tr(r["status"]), os.path.basename(r["path"] or ""),
                       ", ".join(f"{x['x']}={x['value_mm']:g}" for x in info.get("rows", [])),
                       ", ".join(f"{p['part']} {p['name']} {p['material']} {p['copies']}+{p['mirrored']}"
                                 for p in info.get("part_rows", [])) or info.get("parts_note", ""),
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
        from ..services.grafis_robot import settings as rsettings
        rb = rsettings(self.ws)["buttons"]
        last = self.ws.db.one("SELECT status, message, path FROM robot_jobs ORDER BY id DESC LIMIT 1")
        robot_line = (f"{tr('GRAFIS robot')}: {tr('on') if self.robot.isChecked() else tr('off')} — "
                      f"Test Run: {'✓' if 'test_run' in rb else '⚠ ' + tr('not taught')}, "
                      f"Save: {'✓' if 'save' in rb else 'Ctrl+S'}, Close: {'✓' if 'close' in rb else '—'}"
                      + (f" — {os.path.basename(last['path'])}: {last['status']} {last['message']}" if last else ""))
        err = self.ws.db.get_setting("grafis.last_poll_error")
        if err:
            robot_line += f"\n⚠ {err.splitlines()[0][:200]}"
        self.info.setText(robot_line + "\n" + f"{tr('GRAFIS model (MDL)')}: {mdl or '⚠ ' + tr('not set')}"
                          f"{'' if not mdl or os.path.isfile(mdl) else ' ⚠ ' + tr('not found')} — "
                          f"{tr('GRAFIS input folder')}: {gs['out']} — "
                          f"{tr('Errors folder (ER)')}: {self.ws.db.get_setting('errors.folder') or '⚠ ' + tr('not set')}\n"
                          f"{tr('GRAFIS output folder')}: {self.ws.db.get_setting('grafis.watch.folder') or '—'}"
                          f" ({tr('watching') if self.watcher else tr('not watching')}) — "
                          f"{tr('Nesting folder')}: {self._nesting_dir()}\n"
                          f"LEVEL7 Nesting: {cli} — {tr('Cutting files folder')}: {st['out']} — "
                          f"{', '.join(st['formats']).upper()} ({st['plt_preset']}, {st['iso_preset']})")
        robot_on = self.robot.isChecked() and "test_run" in rb
        cli_ok = not cli.startswith("⚠")
        mdl_ok = bool(mdl and os.path.isfile(mdl))
        self._chips([(mdl_ok, tr("GRAFIS model (MDL)")),
                     (self.running(), tr("watching") if self.running() else tr("not watching")),
                     (robot_on, f"{tr('GRAFIS robot')}: {tr('on') if robot_on else tr('off')}"),
                     (cli_ok, "LEVEL7 Nesting"),
                     (bool(st["formats"]), f"{tr('Cutting files')}: {', '.join(st['formats']).upper()}")])
        from ..services.auto_cut import markers_folder
        er = self.ws.db.get_setting("errors.folder")
        menu = self.ws.db.get_setting("robot.menu", {}) or {}
        rmode = self.ws.db.get_setting("robot.mode", "auto")
        done = {"model": (mdl_ok, os.path.basename(mdl or "") or tr("not set")),
                "folders": (bool(er) and os.path.isdir(gs["out"]), f"MDL: {gs['out']}   ER: {er or '—'}   "
                                                                    f"CUT: {st['out']}   L7N: {markers_folder(self.ws)}"),
                "nesting": (cli_ok, cli),
                "robot": ("test_run" in rb or bool(menu.get("test_run")),
                          " · ".join(f"{k}: {self._how(k, menu, rb)}" for k in ("test_run", "save", "close"))
                          + (f" — {tr('Without the mouse (in the background)')}" if rmode == "background" else "")),
                "erp": (self.win.watcher is not None if hasattr(self.win, "watcher") else False,
                        f"{self.ws.db.get_setting('watch.folder') or tr('not set')} — {tr('Check every')} "
                        f"{self.win.watch_interval_s()} {tr('s')} — {tr('last check')}: "
                        f"{(self.ws.db.get_setting('watch.last_check') or '—').replace('T', ' ')[:19]}"),
                "auto": (robot_on and self.running(),
                         f"{tr('watching') if self.running() else tr('not watching')} — "
                         f"{', '.join(st['formats']).upper()}, {st['time']:g} s")}
        for key, (badge, label_, n) in self.steps.items():
            ok, text = done[key]
            badge.setText("✓" if ok else str(n))
            badge.setProperty("done", ok)
            badge.style().unpolish(badge)
            badge.style().polish(badge)
            label_.setText(("✓ " if ok else "⚠ ") + text)
        if self._settings_auto:                         # open the settings while the chain is not set up
            need = not (mdl_ok and cli_ok and self.running() and "test_run" in rb)
            self.settings_btn.blockSignals(True)
            self.settings_btn.setChecked(need)
            self.settings_btn.blockSignals(False)
            self.settings_card.setVisible(need)
        rows = self.ws.db.q("SELECT * FROM grafis_results WHERE archived=0 ORDER BY checked_at DESC LIMIT 500")
        out, colors, data, seen = [], [], [], set()
        for r in rows:
            if r["path"] in seen:                     # an older attempt of the same file: the newest row counts
                continue
            seen.add(r["path"])
            info, issues, lines = json.loads(r["info"]), json.loads(r["issues"]), json.loads(r["lines"])
            cj = self.ws.db.one("SELECT status, outputs, info FROM cutting_jobs WHERE sha256=? ORDER BY id DESC",
                                (r["sha256"],))
            cut = ""
            if cj:
                ci = json.loads(cj["info"])
                if info.get("pieces_placed") is None and ci.get("placed") is not None:
                    info["pieces_placed"] = ci["placed"]        # model read by LEVEL7 Nesting: pieces it placed
                if cj["status"] == "done":
                    r = dict(r, status="cutting_done")
                cut = (", ".join(os.path.basename(x) for x in json.loads(cj["outputs"])) if cj["status"] == "done"
                       else f"{tr(cj['status'])}: {ci.get('message', '')}")
            out.append([os.path.basename(r["path"]), ", ".join(ln["line_number"] for ln in lines), tr(r["status"]),
                        info.get("width_mm"), info.get("bundles"), info.get("pieces_placed"), cut,
                        " | ".join(f"[{tr(sv)}] {m}" for sv, m in issues)])
            colors.append({"handed_over": STATUS_COLORS["approved"], "cutting_done": STATUS_COLORS["approved"],
                           "needs_review": STATUS_COLORS["review"],
                           "unmatched": STATUS_COLORS["blocking"]}.get(r["status"]))
            data.append(r["path"])
        fill(self.t, out, colors, data)

    def _register(self, path, reason=None):
        from ..services.grafis_intake import register_scb
        return register_scb(self.ws, path, nesting_dir=self._nesting_dir(), confirm_reason=reason)

    def register(self):
        paths, _ = QFileDialog.getOpenFileNames(self, tr("GRAFIS"), self.ws.db.get_setting("grafis.watch.folder", ""),
                                                "GRAFIS marker (*.scb *.SCB)")
        if paths:
            self.win.run(lambda: [self._register(p) for p in paths], lambda _r: self.refresh())

    def choose_mdl(self):
        p, _ = QFileDialog.getOpenFileName(self, tr("GRAFIS model (MDL)…"), self.ws.grafis_mdl() or "",
                                           "GRAFIS (*.mdl *.MDL)")
        if p:
            self.ws.set_grafis_mdl(p)                    # for the customer being edited
            self.refresh()

    def teach(self, name):
        from ..services.grafis_robot import teach
        QMessageBox.information(self, tr("GRAFIS robot"), tr("Open a model in GRAFIS. After OK you have 5 seconds to "
                                                             "point the mouse at the button (do not click)."))

        def record():
            try:
                spec = teach(self.ws, name)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, tr("GRAFIS robot"), str(e))
                return
            QMessageBox.information(self, tr("GRAFIS robot"), f"{tr('Saved')}: {name} — {spec['title']} "
                                                              f"({spec['dx']}, {spec['dy']})\n\n" +
                                    (tr("✓ This button can be pressed in the background, without the mouse.")
                                     if spec.get("child") else
                                     tr("This button needs the mouse; choose its GRAFIS menu command to work "
                                        "without it.")))
            self.refresh()
        QTimer.singleShot(5000, record)

    @staticmethod
    def _how(key, menu, rb) -> str:
        if menu.get(key):
            return "✓ " + tr("menu")
        if (rb.get(key) or {}).get("child"):
            return "✓ " + tr("background")
        return "✓ " + tr("mouse") if key in rb else "—"

    def choose_erp(self):
        d = QFileDialog.getExistingDirectory(self, tr("ERP XML folder…"), self.ws.db.get_setting("watch.folder", ""))
        if d:
            if self.win.watcher:
                self.win.timer.stop()
                self.win.watcher = None
            self.win.start_watch(d)
            self.refresh()

    def choose_menu(self):
        """Pick the GRAFIS menu commands the robot sends in the background (Test Run All Parts, Save, Close)."""
        from ..services.grafis_robot import MENU_TEXT, RobotError, Win32
        try:
            drv = Win32()
            wins = [w for w in drv.windows() if "grafis" in w["title"].lower()]
            items = next((it for it in (drv.menu_items(w["hwnd"]) for w in wins) if it), [])
        except RobotError as e:
            QMessageBox.information(self, tr("GRAFIS robot"), str(e))
            return
        if not items:
            QMessageBox.information(self, tr("GRAFIS robot"), tr("Open GRAFIS with a model first. If GRAFIS is open "
                                                                 "and nothing is listed, its menu cannot be read: "
                                                                 "teach the buttons instead."))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("GRAFIS menu commands…"))
        f = QFormLayout(dlg)
        chosen = dict(self.ws.db.get_setting("robot.menu", {}) or {})
        combos = {}
        paths = [i["path"] for i in items]
        for key, label_ in (("test_run", "Test Run All Parts"), ("save", tr("Save")), ("close", tr("Close"))):
            cb = QComboBox()
            cb.addItem("—", "")
            for pth in paths:
                cb.addItem(pth, pth)
            guess = chosen.get(key) or next((i["path"] for i in items
                                             if i["text"].lower().rstrip(".… ") in MENU_TEXT[key]), "")
            cb.setCurrentIndex(max(0, cb.findData(guess)))
            combos[key] = cb
            f.addRow(label_, cb)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        f.addRow(bb)
        if dlg.exec() == QDialog.Accepted:
            self.ws.db.set_setting("robot.menu", {k: c.currentData() for k, c in combos.items() if c.currentData()})
            self.refresh()

    def teach_dialog(self):
        from ..services.grafis_robot import teach_dialog
        QMessageBox.information(self, tr("GRAFIS robot"), tr("Start GRAFIS so its start window shows. After OK you "
                                                             "have 5 seconds to point the mouse at the button you "
                                                             "press in that window (do not click)."))

        def record():
            try:
                d = teach_dialog(self.ws)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, tr("GRAFIS robot"), str(e))
                return
            QMessageBox.information(self, tr("GRAFIS robot"), f"{tr('Saved')}: «{d['title']}» → {d['button']}")
            self.refresh()
        QTimer.singleShot(5000, record)

    def robot_now(self):
        """Run the robot on the next waiting model now and show what happened (also when nothing is waiting)."""
        from ..services.grafis_robot import pending, run_one
        jobs = pending(self.ws)
        if not jobs:
            from ..services.grafis_orders import settings as gsettings
            QMessageBox.information(self, tr("GRAFIS robot"), tr("No order model is waiting for GRAFIS in") +
                                    f"\n{gsettings(self.ws)['out']}\n\n" +
                                    tr("Write the models again with 'Write GRAFIS model for order…'."))
            return
        self.win.run(lambda: run_one(self.ws, jobs[0]), lambda r: (self.refresh(), QMessageBox.information(
            self, tr("GRAFIS robot"), f"{os.path.basename(r['path'])}: {tr(r['status'])}\n{tr_msg(r['message'])}")))

    def robot_retry(self):
        with self.ws.db.tx() as c:
            c.execute("DELETE FROM robot_jobs WHERE status<>'done'")
            self.ws.db.audit(c, self.ws.actor, "robot.retry", "robot", "-")
        self.refresh()

    def choose_folders(self):
        """One main folder with the subfolders MDL (order models for GRAFIS), SCB (GRAFIS output, watched) and ER
        (files with an error); missing subfolders are created."""
        d = QFileDialog.getExistingDirectory(self, tr("Folders MDL / SCB / ER…"),
                                             os.path.dirname(self.ws.db.get_setting("errors.folder") or "") or "")
        if not d:
            return
        sub = {}
        chosen = self.ws.db.get_setting("cutting.output_chosen", False)
        for name in ("MDL", "SCB", "ER", "MDL-ARCHIVE") + (() if chosen else ("CUT",)):
            have = next((e for e in os.listdir(d) if e.lower() == name.lower() and os.path.isdir(os.path.join(d, e))),
                        None)
            sub[name] = os.path.join(d, have or name)
            os.makedirs(sub[name], exist_ok=True)
        self.ws.db.set_setting("grafis.input", sub["MDL"])
        self.ws.db.set_setting("errors.folder", sub["ER"])
        self.ws.db.set_setting("grafis.mdl_archive", sub["MDL-ARCHIVE"])
        if not chosen:                                  # a cutting folder the owner chose stays
            from ..services.auto_cut import settings
            old = settings(self.ws)["out"]
            self.ws.db.set_setting("cutting.output", sub["CUT"])
            self._offer_move(old, sub["CUT"])
        else:
            sub["CUT"] = self.ws.db.get_setting("cutting.output")
        if self.watcher:
            self.timer.stop()
            self.watcher = None
        self.start(sub["SCB"])
        self.refresh()
        QMessageBox.information(self, tr("GRAFIS"), f"MDL: {sub['MDL']}\nSCB: {sub['SCB']} ({tr('watching')})\n"
                                                f"ER: {sub['ER']}\nCUT ({tr('Cutting files')}): {sub['CUT']}\n"
                                                f"MDL-ARCHIVE ({tr('Order models archive…').rstrip('…')}): "
                                                f"{sub['MDL-ARCHIVE']}")

    def choose_errors(self):
        d = QFileDialog.getExistingDirectory(self, tr("Errors folder (ER)…"), self.ws.db.get_setting("errors.folder") or "")
        if d:
            self.ws.db.set_setting("errors.folder", d)
            self.refresh()

    def choose_input(self):
        from ..services.grafis_orders import settings as gsettings
        d = QFileDialog.getExistingDirectory(self, tr("GRAFIS input folder…"), gsettings(self.ws)["out"])
        if d:
            self.ws.db.set_setting("grafis.input", d)
            if self.watcher:                          # watch the new MDL folder too
                self.start(self.watcher.folder)
            else:
                self._start_mdl()
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
            self.win.run(lambda: order_mdl(self.ws, rid), lambda g: (self.refresh(), self.win.refresh_cad(force=True),
                                                                     self.win.model_result(line, rid, g)))

    def choose_cli(self):
        p, _ = QFileDialog.getOpenFileName(self, tr("LEVEL7 Nesting program…"), r"C:\Program Files\LEVEL7 Nesting",
                                           "level7-cli (level7-cli.exe *.exe)")
        if p:
            self.ws.db.set_setting("nesting.cli", p)
            self.refresh()

    def _save_formats(self):
        chosen = [c for c, cb in self.fmt_boxes.items() if cb.isChecked()]
        if not chosen:                                 # at least one cutting file
            self.fmt_boxes["plt"].setChecked(True)
            return
        self.ws.db.set_setting("cutting.formats", chosen)
        self.refresh()

    def open_out(self):
        from ..services.auto_cut import settings
        d = settings(self.ws)["out"]
        os.makedirs(d, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(d)                            # Windows Explorer
        else:
            QMessageBox.information(self, tr("Cutting files folder"), d)

    def clean_list(self):
        from ..services.cleanup import clean, files_of_rows
        n = len(files_of_rows(self.ws))
        box = QMessageBox(self)
        box.setWindowTitle(tr("Clean the list…"))
        box.setText(tr("Hide all rows of this screen? The history stays in the log."))
        only = box.addButton(tr("Clean the list only"), QMessageBox.AcceptRole)
        files = box.addButton(f"{tr('Clean and delete the files')} ({n})", QMessageBox.DestructiveRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() not in (only, files):
            return
        out = clean(self.ws, delete_files=box.clickedButton() is files)
        self.refresh()
        msg = f"{tr('Done')}: {out['rows']} {tr('rows')}, {len(out['deleted'])} {tr('files deleted')}"
        if out["failed"]:
            msg += "\n\n" + tr("Not deleted (open in another program?)") + ":\n" + "\n".join(out["failed"][:10])
        QMessageBox.information(self, tr("Clean the list…"), msg)

    def renest(self, path=None):
        """Nest the selected row (or ``path``) again (e.g. after a longer nesting time was set) and rewrite its
        cutting files."""
        from ..services.auto_cut import auto_cut
        from ..services.grafis_intake import check_scb
        from ..services.mdl_intake import check_mdl
        paths = [path] if isinstance(path, str) else selected_data(self.t)
        if not paths:
            QMessageBox.information(self, "LEVEL7 Nesting", tr("Select a row first"))
            return
        p = paths[0]

        def work():
            chk = check_mdl(self.ws, p)[0] if p.lower().endswith(".mdl") else check_scb(self.ws, p, renest=True)
            if not chk.ok or not chk.lines:
                return {"status": "failed", "message": renest_why(chk)}
            return auto_cut(self.ws, chk, p, force=True)
        self.win.run(work, lambda r: (self.refresh(), self.win.refresh_cad(force=True), QMessageBox.information(
            self, "LEVEL7 Nesting", f"{tr(r['status'])}: "
            + (f"L {r['length_mm'] / 1000:.2f} m, E {r['efficiency_pct']:.1f} %" if r.get("length_mm") and
               r.get("efficiency_pct") else r.get("message", "")) + remnant_note(r))))

    def show_marker(self, path=None):
        """Open the marker of the selected row (or ``path``) in LEVEL7 Nesting (the .l7n saved next to the PLT /
        ISO)."""
        import subprocess
        from ..services.auto_cut import NestingNotConfigured, nesting_command
        paths = [path] if isinstance(path, str) else selected_data(self.t)
        if not paths:
            QMessageBox.information(self, "LEVEL7 Nesting", tr("Select a row first"))
            return
        r = self.ws.db.one("SELECT sha256 FROM grafis_results WHERE path=? ORDER BY id DESC", (paths[0],))
        cj = self.ws.db.one("SELECT info FROM cutting_jobs WHERE sha256=? ORDER BY id DESC", (r["sha256"],)) if r else None
        l7n = json.loads(cj["info"]).get("l7n") if cj else None
        if not l7n or not os.path.isfile(l7n):
            QMessageBox.information(self, "LEVEL7 Nesting", tr("This row has no marker yet"))
            return
        try:
            cli = nesting_command(self.ws)[0][0]
        except NestingNotConfigured:
            cli = ""
        gui = os.path.join(os.path.dirname(cli), "LEVEL7Nesting.exe") if cli else ""
        if gui and os.path.isfile(gui):
            subprocess.Popen([gui, l7n])
        elif hasattr(os, "startfile"):
            os.startfile(l7n)
        else:
            QMessageBox.information(self, "LEVEL7 Nesting", l7n)

    def choose_archive(self):
        """The folder where a copy of every order model (MDL) is kept as a reference."""
        from ..services.grafis_orders import archive_folder
        d = QFileDialog.getExistingDirectory(self, tr("Order models archive…"), archive_folder(self.ws))
        if d:
            self.ws.db.set_setting("grafis.mdl_archive", d)
            self.refresh()

    def choose_markers(self):
        """The folder where the markers (.l7n) of the nestings are kept; the ones already written move there."""
        from ..services.auto_cut import markers_folder, tidy_markers
        d = QFileDialog.getExistingDirectory(self, tr("Markers folder…"), markers_folder(self.ws))
        if not d:
            return
        self.ws.db.set_setting("cutting.markers", d)
        self.win.run(lambda: tidy_markers(self.ws), lambda n: (
            self.win.status(f"{tr('Markers folder')}: {d} — {tr('markers moved')}: {n}"), self.refresh()))

    def choose_out(self):
        from ..services.auto_cut import settings
        old = settings(self.ws)["out"]
        d = QFileDialog.getExistingDirectory(self, tr("Cutting files folder…"), old)
        if not d:
            return
        self.ws.db.set_setting("cutting.output", d)
        self.ws.db.set_setting("cutting.output_chosen", True)       # the folders button keeps this choice
        self._offer_move(old, d)
        self.refresh()

    def _offer_move(self, old, new):
        """Move the cutting files already written (PLT / ISO / L7N) from the old folder to the new one."""
        import shutil
        if not old or not os.path.isdir(old) or os.path.normcase(os.path.abspath(old)) == \
                os.path.normcase(os.path.abspath(new)):
            return
        files = [f for f in os.listdir(old) if f.lower().endswith((".plt", ".iso", ".l7n"))]
        if not files or QMessageBox.question(self, tr("Cutting files folder"),
                                             f"{len(files)} {tr('cutting files are in the old folder')}:\n{old}\n\n"
                                             f"{tr('Move them to the new folder?')}") != QMessageBox.Yes:
            return
        os.makedirs(new, exist_ok=True)
        moved = {}
        for f in files:
            target = os.path.join(new, f)
            if not os.path.exists(target):
                shutil.move(os.path.join(old, f), target)
                moved[os.path.join(old, f)] = target
        with self.ws.db.tx() as c:                      # the jobs keep pointing at their files
            for r in c.execute("SELECT id, outputs, info FROM cutting_jobs").fetchall():
                outs = [moved.get(p, p) for p in json.loads(r["outputs"])]
                info = json.loads(r["info"])
                if info.get("l7n") in moved:
                    info["l7n"] = moved[info["l7n"]]
                c.execute("UPDATE cutting_jobs SET outputs=?, info=? WHERE id=?",
                          (json.dumps(outs), json.dumps(info, ensure_ascii=False), r["id"]))
            self.ws.db.audit(c, self.ws.actor, "cutting.files_moved", "folder", new, {"files": len(moved)})

    def accept(self):
        paths = selected_data(self.t)
        if not paths:
            return
        reason = ask_reason(self, tr("Accept review findings…"))
        if reason:
            for p in paths:
                r = self.win.guard(lambda p=p: self._register(p, reason))
                if r and r["status"] != "handed_over":
                    QMessageBox.warning(self, tr("GRAFIS"), "\n".join(m for _, m in r["check"].issues))
        self.refresh()

    def choose_nesting(self):
        d = QFileDialog.getExistingDirectory(self, tr("Nesting folder…"), self._nesting_dir())
        if d:
            self.ws.db.set_setting("nesting.inbox", d)
            self.refresh()

    def toggle(self):
        if self.watcher:
            self.timer.stop()
            self.watcher = self.mdl_watcher = None    # stopped for now; it starts again with the program
            self.ws.db.set_setting("grafis.watch.enabled", False)
        else:
            d = QFileDialog.getExistingDirectory(self, tr("Watch GRAFIS output folder"),
                                                 self.ws.db.get_setting("grafis.watch.folder", ""))
            if d:
                self.start(d)
        self.refresh()

    def start_saved(self):
        """Switch the watch on again with the folder chosen before (no dialog); ask only when there is none."""
        if self.watcher:
            return
        d = self.ws.db.get_setting("grafis.watch.folder")
        if d and os.path.isdir(d):
            self.start(d)
            self.refresh()
        else:
            self.toggle()

    def start(self, folder):
        self.watcher = FolderWatcher(self.ws, folder, settle_seconds=3, extensions=(".scb",), handler=self._register)
        self.ws.db.set_setting("grafis.watch.folder", folder)
        self.ws.db.set_setting("grafis.watch.enabled", True)
        self._start_mdl()

    def _start_mdl(self):
        """The MDL folder (models saved back by GRAFIS) and the robot run on the same timer."""
        from ..services.grafis_orders import settings as gsettings
        from ..services.mdl_intake import register_mdl
        mdl_dir = gsettings(self.ws)["out"]
        try:
            os.makedirs(mdl_dir, exist_ok=True)
        except OSError:
            return                                    # folder not reachable (network drive): set it up first
        from ..services.grafis_robot import current_model
        from ..services.mdl_intake import handled_before
        if self.mdl_watcher is None or os.path.normcase(self.mdl_watcher.folder) != os.path.normcase(mdl_dir):
            self.mdl_watcher = FolderWatcher(self.ws, mdl_dir, settle_seconds=3, extensions=(".mdl",),
                                             handler=lambda p: register_mdl(self.ws, p),
                                             skip=lambda p: _same_path(p, current_model()),   # open in CAD now
                                             known=lambda p: handled_before(self.ws, p))   # thousands: not again
        self.timer.start(2000)

    def running(self) -> bool:
        """The whole chain runs (Start): folders, robot and nesting."""
        return bool(getattr(self.win, "chain_on", False)) and self.timer.isActive() and self.mdl_watcher is not None

    def poll(self):
        """Two workers side by side: the robot drives GRAFIS (next model) while the models GRAFIS saved are checked
        and nested one after the other (a nesting takes a minute; GRAFIS does not wait for it)."""
        if not (self.watcher or self.mdl_watcher):
            return
        if getattr(self.win, "chain_on", False):      # the robot drives CAD only when the system was started
            self._poll_robot()
        self._poll_intake()                           # the nesting: its own button (stopped / running)

    def _poll_robot(self):
        if not _ROBOT_POLL.acquire(blocking=False):
            return

        def work():
            from ..services.grafis_robot import step
            return step(self.ws)                      # one pending model (if the robot is on)

        def done(r):
            _ROBOT_POLL.release()
            if r and alive(self):
                self.refresh()

        def failed(msg):
            _ROBOT_POLL.release()
            if alive(self):
                self.win.status(msg[:200])
        w = Worker(work)
        w.signals.done.connect(done)
        w.signals.failed.connect(failed)
        self.win.pool.start(w)

    def _poll_intake(self):
        """Runs in a worker thread: nesting can take a minute and must not freeze the window. One poll at a time
        for the whole program (also across a theme / language switch, which makes this page anew)."""
        if not _GRAFIS_POLL.acquire(blocking=False):
            return
        self.busy = True

        def done(res):
            _GRAFIS_POLL.release()
            self.ws.db.set_setting("grafis.last_poll_error", "")
            if not alive(self):                           # the page was made anew meanwhile
                return
            self.busy = False
            if res and self.isVisible():              # the page itself only when it is shown (thousands of rows)
                self.refresh()
            if res:
                self.win._refresh_cad_bg()            # the order list in the background, not here
        mw = getattr(self, "mdl_watcher", None)

        def work():                                   # saved models wait here for their nesting, in turn
            out = self.watcher.poll() if self.watcher else []
            if self.ws.db.get_setting("nesting.paused", False):
                return out                            # the nesting is stopped: the saved models wait in the queue
            got = mw.poll(limit=1) if mw else []      # one model at a time: a stop takes effect after it
            if not got:
                from ..services.mdl_intake import next_queued, register_mdl
                p = next_queued(self.ws)              # left over in the queue (saved while stopped …)
                if p:
                    got = [register_mdl(self.ws, p)]
            return out + got
        w = Worker(work)
        w.signals.done.connect(done)
        def failed(msg):
            _GRAFIS_POLL.release()
            self.ws.db.set_setting("grafis.last_poll_error", msg[:500])
            if alive(self):
                self.busy = False
                self.win.status(msg[:200])
        w.signals.failed.connect(failed)
        self.win.pool.start(w)


class OptionsPage(Page):
    """Design option groups (collar, sleeve …), their values and the Variation name(s) in the XML that select them."""
    title = "Design options"
    GCOLS = ["Group code", "Name (ar)", "Name (en)", "Required", "Default value"]
    VCOLS = ["Value code", "Name (ar)", "Name (en)", "Name in the XML (Variation)", "Part numbers", "Copies",
             "Mirrored"]

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.addWidget(customer_bar(self))
        self.info = QLabel(tr("Groups such as collar or sleeve; each value has the name(s) it has in the XML "
                              "(Variation Name). An XML name that is not here makes the order 'needs review'."))
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.unlinked_label = QLabel()                   # Variations of this customer's XML files with no meaning
        self.unlinked_label.setWordWrap(True)
        lay.addWidget(self.unlinked_label)
        lay.addWidget(QLabel(tr("Option groups") + " — " + tr("click a group to set its values")))
        self.g = QTableWidget(0, len(self.GCOLS))
        self.g.setHorizontalHeaderLabels([tr(c) for c in self.GCOLS])
        self.g.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)   # no wide empty column
        self.g.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.g.setEditTriggers(QAbstractItemView.NoEditTriggers)     # edited in the group's own window
        self.g.currentCellChanged.connect(lambda r, c, pr, pc: self._switch(pr, r))
        self.g.cellClicked.connect(lambda r, c: self.open_group(r))
        lay.addWidget(self.g, 1)                         # the groups take the page
        bg = QHBoxLayout()
        for text, fn in (("Add group", self.add_group), ("Remove group", self.remove_group), ("Save", self.save)):
            b = QPushButton(tr(text))
            b.clicked.connect(fn)
            bg.addWidget(b)
        bg.addStretch()
        lay.addLayout(bg)
        # the window of one group: its names, required / default, its values and their Variation names
        self.gd = QDialog(self)
        self.gd.resize(720, 640)
        dl = QVBoxLayout(self.gd)
        form = QFormLayout()
        self.gf = {k: QLineEdit() for k in ("group", "ar", "en", "default")}
        for k, label_ in (("group", "Group code"), ("ar", "Name (ar)"), ("en", "Name (en)"),
                          ("default", "Default value")):
            form.addRow(tr(label_), self.gf[k])
        self.gf_required = QCheckBox(tr("Required"))
        form.addRow("", self.gf_required)
        dl.addLayout(form)
        self.unlinked_hint = QLabel()
        self.unlinked_hint.setWordWrap(True)
        dl.addWidget(self.unlinked_hint)
        self.unlinked = QListWidget()
        self.unlinked.setFlow(QListWidget.LeftToRight)
        self.unlinked.setWrapping(True)
        self.unlinked.setMaximumHeight(64)
        self.unlinked.setToolTip(tr("Click a name: it is added to the selected value"))
        self.unlinked.itemClicked.connect(self._link)
        dl.addWidget(self.unlinked)
        self.vlabel = QLabel()
        dl.addWidget(self.vlabel)
        self.v = QTableWidget(0, len(self.VCOLS))
        self.v.setHorizontalHeaderLabels([tr(c) for c in self.VCOLS])
        self.v.horizontalHeader().setStretchLastSection(True)
        dl.addWidget(self.v, 1)
        bv = QHBoxLayout()
        for text, fn in (("Add value", self.add_value), ("Remove value", self.remove_value)):
            b = QPushButton(tr(text))
            b.clicked.connect(fn)
            bv.addWidget(b)
        bv.addStretch()
        dl.addLayout(bv)
        bb = QDialogButtonBox()
        ok = bb.addButton(tr("Save and close"), QDialogButtonBox.AcceptRole)
        ok.setObjectName("primary")
        ok.clicked.connect(self._group_save_close)
        bb.addButton(tr("Cancel"), QDialogButtonBox.RejectRole)
        bb.rejected.connect(self.gd.reject)
        dl.addWidget(bb)
        self.gd.rejected.connect(self._group_cancel)
        self._before = None
        self.groups: list[dict] = []
        self.cur = -1

    def refresh(self):
        from ..services.options import option_table
        fill_customer_box(self.ws, self.customer_box)
        self.groups = option_table(self.ws)
        self._fill_unlinked()
        self.cur = -1
        self._fill_groups()
        if self.groups:
            self.g.setCurrentCell(0, 0)
            self._switch(-1, 0)

    def _fill_unlinked(self):
        """The Variation names that came in this customer's XML files and no value has yet."""
        from ..services.customers import _plain, patterns, variations_seen
        linked = {_plain(n) for gr in self.groups for v in gr.get("values", []) for n in v.get("erp_names", [])}
        linked |= {_plain(p["variation"]) for p in patterns(self.ws)}
        free = [v for v in variations_seen(self.ws) if _plain(v) not in linked]
        self.unlinked.clear()
        self.unlinked.addItems(free)
        self.unlinked.setVisible(bool(free))
        self.unlinked_hint.setText(
            (tr("Variations in this customer's XML files without a meaning — choose a value below, then click a name")
             + ":") if free else tr("Every Variation in this customer's XML files has a meaning"))
        self.unlinked_label.setText(
            (tr("Variations in this customer's XML files without a meaning") + ": " + ", ".join(free))
            if free else tr("Every Variation in this customer's XML files has a meaning"))

    def _link(self, item):
        r = self.v.currentRow()
        if r < 0:
            QMessageBox.information(self, tr("Design options"), tr("Choose a value first (lower table)"))
            return
        col = 3                                           # "Name in the XML (Variation)"
        cell = self.v.item(r, col)
        names = [n.strip() for n in (cell.text() if cell else "").split(";") if n.strip()]
        if item.text() not in names:
            names.append(item.text())
        self.v.setItem(r, col, QTableWidgetItem("; ".join(names)))
        self.unlinked.takeItem(self.unlinked.row(item))

    def _fill_groups(self):
        self.g.blockSignals(True)
        self.g.setRowCount(len(self.groups))
        for i, gr in enumerate(self.groups):
            for j, val in enumerate([gr["group"], gr["label"].get("ar", ""), gr["label"].get("en", ""), None,
                                     gr["default"]]):
                it = QTableWidgetItem("" if val is None else val)
                if j == 3:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable & ~Qt.ItemIsUserCheckable)
                    it.setCheckState(Qt.Checked if gr["required"] else Qt.Unchecked)
                self.g.setItem(i, j, it)
        self.g.blockSignals(False)
        self.g.resizeColumnsToContents()

    def _read_group_row(self, i):
        from ..services.options import code
        gr = self.groups[i]
        t = lambda j: self.g.item(i, j).text().strip() if self.g.item(i, j) else ""   # noqa: E731
        gr["group"] = code(t(0))
        gr["label"] = {"ar": t(1) or t(0), "en": t(2) or t(0)}
        gr["required"] = self.g.item(i, 3).checkState() == Qt.Checked if self.g.item(i, 3) else True
        gr["default"] = code(t(4))

    def _read_values(self):
        from ..services.options import code
        if not (0 <= self.cur < len(self.groups)):
            return
        vals = []
        for i in range(self.v.rowCount()):
            t = lambda j: self.v.item(i, j).text().strip() if self.v.item(i, j) else ""   # noqa: E731,B023
            if not t(0) and not t(3):
                continue
            vals.append({"value": code(t(0) or t(3)), "label": {"ar": t(1) or t(0), "en": t(2) or t(0)},
                         "erp_names": [n.strip() for n in re.split(r"[;\n]", t(3)) if n.strip()],
                         "parts_text": t(4), "copies_text": t(5), "mirrored_text": t(6)})
        self.groups[self.cur]["values"] = vals

    def _part_names(self) -> dict[int, str]:
        try:
            return {n: str(v[0]).strip() for n, v in _grafis_model_parts(self.ws).items()}
        except Exception:  # noqa: BLE001 - names are a help only
            return {}

    def _parts_of_values(self) -> dict[str, str]:
        """{"group=value": "7, 12"} — the GRAFIS part numbers each value produces, as the owner types them (the
        names of the parts are in the tooltip)."""
        from ..services.gparts import parts_table
        out: dict[str, list[str]] = {}
        self._counts: dict[str, tuple[int, int]] = {}      # the copies / mirrored of the value's (first) part
        for r in parts_table(self.ws):
            k = r.get("option") or "*"
            out.setdefault(k, []).append(str(int(r.get("part") or 0)))
            self._counts.setdefault(k, (int(r.get("copies", 1)), int(r.get("mirrored", 0))))
        return {k: ", ".join(v) for k, v in out.items()}

    def _switch(self, prev, row):
        if 0 <= self.cur < len(self.groups):
            self._read_values()
        self.cur = row
        if not (0 <= row < len(self.groups)):
            self.v.setRowCount(0)
            return
        gr = self.groups[row]
        self.vlabel.setText(f"{tr('Values of')} {gr['label'].get('ar') or gr['group']} ({gr['group']})")
        self.v.setRowCount(len(gr["values"]))
        parts, pnames = self._parts_of_values(), self._part_names()
        for i, val in enumerate(gr["values"]):
            key = f"{gr['group']}={val['value']}"
            made = val.get("parts_text", parts.get(key, ""))
            cp, mi = getattr(self, "_counts", {}).get(key, (1, 0)) if made else ("", "")
            cp, mi = val.get("copies_text", str(cp)), val.get("mirrored_text", str(mi))
            for j, x in enumerate([val["value"], val["label"].get("ar", ""), val["label"].get("en", ""),
                                   "; ".join(val["erp_names"]), made, cp, mi]):
                it = QTableWidgetItem(x)
                if j == 4:                                # the parts of the MDL this Variation chooses
                    named = [f"{int(m)}: {pnames[int(m)]}" for m in re.findall(r"\d+", x) if int(m) in pnames]
                    it.setToolTip("\n".join([tr("The GRAFIS part numbers this value produces, e.g. 36 or 36, 37 "
                                                 "(kept in Fixed pieces)")] + named))
                elif j in (5, 6):                         # how many of each part, how many of them mirrored
                    it.setToolTip(tr("Copies of each part of this value") if j == 5 else
                                  tr("Mirrored copies of each part of this value"))
                self.v.setItem(i, j, it)
        self.v.resizeColumnsToContents()

    def open_group(self, row: int, new: bool = False):
        """The group in its own window: names, required, default and its values; Save and close or Cancel."""
        if not (0 <= row < len(self.groups)):
            return
        if self.cur != row:
            self._switch(self.cur, row)
        gr = self.groups[row]
        self._before = (row, copy.deepcopy(gr), new)
        self._asked = False                               # a new change: saving it offers the re-read again
        for k, val in (("group", gr["group"]), ("ar", gr["label"].get("ar", "")), ("en", gr["label"].get("en", "")),
                       ("default", gr.get("default") or "")):
            self.gf[k].setText(val)
        self.gf_required.setChecked(bool(gr.get("required", True)))
        self.gd.setWindowTitle(f"{tr('Option groups')}: {gr['label'].get('ar') or gr['group']}")
        self.gd.open()

    def _group_save_close(self):
        from ..services.options import code
        if self._before is None:
            return
        row = self._before[0]
        gr = self.groups[row]
        self._read_values()
        gc = code(self.gf["group"].text().strip())
        if not gc:
            QMessageBox.warning(self.gd, tr("Error"), tr("Group code") + " (a-z, 0-9, _)")
            return
        gr.update(group=gc, label={"ar": self.gf["ar"].text().strip() or gc, "en": self.gf["en"].text().strip() or gc},
                  required=self.gf_required.isChecked(), default=code(self.gf["default"].text().strip()))
        self._fill_groups()
        if self.save() is False:                          # an error keeps the window open to correct it
            return
        self._before = None
        self.gd.accept()

    def _group_cancel(self):
        if self._before is None:
            return
        row, old, new = self._before
        self._before = None
        if new:
            del self.groups[row]
        else:
            self.groups[row] = old
        self.cur = -1
        self._fill_groups()
        self._switch(-1, min(row, len(self.groups) - 1))

    def add_group(self):
        from ..services.options import code
        self._read_values()
        for i in range(len(self.groups)):
            self._read_group_row(i)
        n = len(self.groups) + 1
        while any(g["group"] == f"group{n}" for g in self.groups):
            n += 1
        self.groups.append({"group": code(f"group{n}"), "label": {"ar": "", "en": ""}, "required": False,
                            "default": "", "values": []})
        self.cur = -1
        self._fill_groups()
        self.g.setCurrentCell(len(self.groups) - 1, 0)
        self.open_group(len(self.groups) - 1, new=True)

    def remove_group(self):
        r = self.g.currentRow()
        if 0 <= r < len(self.groups):
            for i in range(len(self.groups)):
                self._read_group_row(i)
            del self.groups[r]
            self.cur = -1
            self._fill_groups()
            self._switch(-1, min(r, len(self.groups) - 1))

    def add_value(self):
        self.v.insertRow(self.v.rowCount())

    def remove_value(self):
        if self.v.currentRow() >= 0:
            self.v.removeRow(self.v.currentRow())

    def collect(self) -> list[dict]:
        self._read_values()
        for i in range(len(self.groups)):
            self._read_group_row(i)
        return [{**g, "values": [{k: x for k, x in v.items() if k not in ("parts_text", "copies_text",
                                                                           "mirrored_text")}
                                 for v in g.get("values", [])]} for g in self.groups]

    def save(self):
        from ..services.gparts import set_option_parts
        from ..services.options import save_options
        groups = self.collect()
        wanted = {}                                       # value -> the part numbers typed next to it
        for gr in self.groups:
            for v in gr.get("values", []):
                text = v.get("parts_text")
                if text is not None:
                    nums = [int(m.group(1)) for m in (re.match(r"\s*(\d+)", x) for x in re.split(r"[,،;]", text))
                            if m]
                    num = lambda t: int(t) if (t or "").strip().isdigit() else None   # noqa: E731
                    wanted[f"{gr['group']}={v['value']}"] = {"parts": nums, "copies": num(v.get("copies_text")),
                                                             "mirrored": num(v.get("mirrored_text"))}
        try:
            out = save_options(self.ws, groups)
            parts_ver = set_option_parts(self.ws, wanted, _mdl_or_none(self.ws))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Error"), tr_msg(str(e)))
            return False
        if not parts_ver and not out["catalog_version"]:
            if not getattr(self, "_asked", False):     # saved before (e.g. the window froze then): the held orders
                self._asked = True                        # may still wait for it — offered once, not again on close
                reread_held_question(self, tr("Saved"))
            self.refresh()
            return True                                   # nothing changed (e.g. saved already in a group's window)
        msg = f"{tr('Saved')}: {tr('Rules')} {parts_ver or out['catalog_version']}"
        if out["mapping_version"]:
            msg += f"\n{tr('ERP mapping profile')} {out['mapping_version']}"
        self._asked = True
        reread_held_question(self, msg)
        self.refresh()
        return True


class GrafisPartsPage(Page):
    """Design option → GRAFIS part (number, material, normal / mirrored copies). The base model may have no
    production part: exactly the parts chosen here (for the order's options) are produced."""
    title = "GRAFIS parts"
    COLS = ["Design option", "Part number", "GRAFIS part name", "Material", "Copies", "Mirrored", ""]

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        lay.addWidget(customer_bar(self))
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        lay.addWidget(pattern_bar(self))
        self.t = QTableWidget(0, len(self.COLS))
        self.t.setHorizontalHeaderLabels([tr(c) for c in self.COLS])
        self.t.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)    # the name read from the model
        self.t.verticalHeader().setVisible(False)
        self.t.setColumnHidden(0, True)                  # fixed pieces are for every order; the parts an option
        lay.addWidget(self.t, 1)                         # chooses are typed in Piece options setup
        bar = QHBoxLayout()
        for text, fn in (("Add row", lambda: self.add_row({})), ("Remove row", self.remove_row), ("Save", self.save)):
            b = QPushButton(tr(text))
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        lay.addLayout(bar)
        self.parts: dict = {}
        self.options: list = []

    def _mdl(self):
        m = self.ws.grafis_mdl()                         # the base model of the customer being edited
        return m if m and os.path.isfile(m) else None

    def refresh(self):
        fill_customer_box(self.ws, self.customer_box)
        fill_pattern_box(self)
        from ..services.gparts import model_parts, option_choices, parts_table
        mdl = self._mdl()
        self.info.setText(tr("The GRAFIS parts every order has (always in the marker). The parts a design option "
                             "of the XML chooses are set next to its value in Piece options setup.") +
                          f"\n{tr('GRAFIS model (MDL)')}: {mdl or '⚠ ' + tr('not set')}")
        try:
            self.parts = model_parts(mdl) if mdl else {}
        except Exception as e:  # noqa: BLE001
            self.parts = {}
            self.info.setText(f"{tr('Error')}: {e}")
        self.options = option_choices(self.ws)
        self.t.setRowCount(0)
        for r in parts_table(self.ws):
            if r["option"] == "*":
                self.add_row(r)
        self.t.resizeColumnsToContents()

    def add_row(self, r: dict):
        i = self.t.rowCount()
        self.t.insertRow(i)
        oc = QComboBox()
        oc.addItem(tr("always") + " (*)", "*")
        pc = QSpinBox()                               # the part number, typed; its name is read from the model
        pc.setRange(0, 999)
        pc.setSpecialValueText("—")                   # 0: no part chosen yet
        pc.setValue(int(r["part"]) if r.get("part") is not None else 0)
        nm = QLabel()
        nm.setContentsMargins(6, 0, 6, 0)
        mc = QComboBox()
        mats = sorted({m for _, m in self.parts.values() if m} | {"O"})
        mc.addItems(mats)
        mc.setCurrentText(r.get("material") or "O")

        def show_name(_=0, pc=pc, nm=nm):
            n = pc.value()
            name = (self.parts.get(n) or ("", None))[0].strip()
            nm.setText(name if name else ("" if not n else "⚠ " + tr("not in the GRAFIS model")))
            nm.setStyleSheet("" if name or not n else "color:#d97706;")
        pc.valueChanged.connect(show_name)
        show_name()
        if not r:                                     # new row: the part's own material
            pc.valueChanged.connect(lambda _=0, pc=pc, mc=mc: mc.setCurrentText(
                (self.parts.get(pc.value(), ("", None))[1]) or "O"))
        cs, ms = QSpinBox(), QSpinBox()
        cs.setRange(0, 99)
        ms.setRange(0, 99)
        cs.setValue(int(r.get("copies", 1)))
        ms.setValue(int(r.get("mirrored", 0)))
        rm = QPushButton("✕")
        rm.setToolTip(tr("Remove row"))
        rm.setFixedWidth(36)
        rm.clicked.connect(lambda _=False, b=rm: self._remove_widget_row(b))
        for j, w in enumerate((oc, pc, nm, mc, cs, ms, rm)):
            self.t.setCellWidget(i, j, w)

    def _remove_widget_row(self, button):
        for i in range(self.t.rowCount()):            # rows move up when one above is removed: find it now
            if self.t.cellWidget(i, 6) is button:
                self.t.removeRow(i)
                return

    def remove_row(self):
        if self.t.currentRow() >= 0:
            self.t.removeRow(self.t.currentRow())

    def rows(self) -> list[dict]:
        out = []
        for i in range(self.t.rowCount()):
            w = [self.t.cellWidget(i, j) for j in range(6)]
            if not w[1].value():
                continue
            out.append({"option": w[0].currentData(), "part": w[1].value(), "material": w[3].currentText(),
                        "copies": w[4].value(), "mirrored": w[5].value()})
        return out

    def save(self):
        from ..services.gparts import parts_table, save_parts
        chosen = [r for r in parts_table(self.ws) if r["option"] != "*"]     # kept: set in Piece options setup
        try:
            ver = save_parts(self.ws, self.rows() + chosen, self._mdl())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Error"), str(e))
            return False
        reread_held_question(self, f"{tr('Saved')}: {tr('Rules')} {ver}")
        self.refresh()
        return True


class XValuesPage(Page):
    """Every X value of a GRAFIS part next to the XML code(s) that fill it. Saved as a new rules version."""
    title = "X values for GRAFIS"
    COLS = ["X", "Name in GRAFIS", "GRAFIS default", "XML codes", "Value / formula", "Confirmed"]
    ALL = "all"                                       # every main part with X values, in one table

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        self.part_names: dict[int, str] = {}
        lay.addWidget(customer_bar(self))
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.carry = QWidget()                          # XML codes held on a part number this model has not
        cl = QHBoxLayout(self.carry)
        cl.setContentsMargins(0, 0, 0, 0)
        self.carry_note = QLabel()
        self.carry_note.setWordWrap(True)
        self.carry_note.setStyleSheet("color:#b26a00;font-weight:bold")
        cl.addWidget(self.carry_note, 1)
        self.carry_from, self.carry_to = QComboBox(), QComboBox()
        cl.addWidget(self.carry_from)
        cl.addWidget(QLabel("→"))
        cl.addWidget(self.carry_to)
        b = QPushButton(tr("Move the XML codes"))
        b.clicked.connect(self.carry_codes)
        cl.addWidget(b)
        b = QPushButton(tr("Delete them"))
        b.setToolTip(tr("This model's X values have their codes already: delete the codes left on that part"))
        b.clicked.connect(self.drop_codes)
        cl.addWidget(b)
        self.carry.hide()
        lay.addWidget(self.carry)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("Part")))
        self.part = QComboBox()
        self.part.currentIndexChanged.connect(self.load)
        top.addWidget(self.part, 1)
        b = QPushButton(tr("Save"))
        b.clicked.connect(self.save)
        top.addWidget(b)
        lay.addWidget(pattern_bar(self))
        lay.addLayout(top)
        self.t = QTableWidget(0, len(self.COLS))
        self.t.setHorizontalHeaderLabels([tr(c) for c in self.COLS])
        self.t.horizontalHeader().setStretchLastSection(True)
        self.t.verticalHeader().setVisible(False)
        self.t.itemChanged.connect(self._changed)
        self._loading = False
        lay.addWidget(self.t, 1)
        self.codes = QLabel()
        self.codes.setWordWrap(True)
        lay.addWidget(self.codes)

    def _mdl(self):
        m = self.ws.grafis_mdl()                         # the base model of the customer being edited
        return m if m and os.path.isfile(m) else None

    def refresh(self):
        fill_customer_box(self.ws, self.customer_box)
        fill_pattern_box(self)
        from ..services.xtable import x_parts
        mdl = self._mdl()
        _, cat = self.ws.active("catalog")
        self.info.setText(tr("Type next to each X the XML code(s) that fill it, e.g. FL or COS,COG. An X without "
                             "an XML code is inactive. A new XML code is added automatically.") + "\n" +
                          f"{tr('GRAFIS model (MDL)')}: {mdl or '⚠ ' + tr('not set')} — "
                          f"{tr('Rules')}: {cat['catalog_id']} {cat['version']}")
        self.codes.setText(tr("XML codes") + ": " + ", ".join(sorted(cat["measurements"])))
        cur = self.part.currentData()
        self.part.blockSignals(True)
        self.part.clear()
        if mdl:
            try:
                from ..grafis.textsections import read_text
                names = {p.number: p.name for p in read_text(mdl).parts}
                parts = sorted(x_parts(mdl).items())
                self.part_names = {num: names.get(num, "") for num, _ in parts}
                if len(parts) > 1:
                    self.part.addItem(f"{tr('All main parts')} ({sum(n for _, n in parts)} X)", self.ALL)
                for num, n in parts:
                    self.part.addItem(f"{num:03d} {names.get(num, '')} ({n} X)", num)
            except Exception as e:  # noqa: BLE001
                self.info.setText(f"{tr('Error')}: {e}")
        i = self.part.findData(cur if cur is not None else self.ALL)
        self.part.setCurrentIndex(max(i, 0))
        self.part.blockSignals(False)
        self._fill_carry(mdl)
        self.load()

    def _fill_carry(self, mdl):
        """Codes saved on a part number this model has not (copied from another customer's model): offer to move
        them onto this model's part."""
        from ..services.xtable import codes_elsewhere
        try:
            other = codes_elsewhere(self.ws, mdl) if mdl and self.part_names else {}
        except Exception:  # noqa: BLE001
            other = {}
        self.carry.setVisible(bool(other))
        if not other:
            return
        self.carry_note.setText(tr("XML codes are saved on part {parts}, which is not in this model — move them "
                                   "to the part of this model:").format(
            parts=", ".join(f"{p} ({n} X)" for p, n in other.items())))
        self.carry_from.clear()
        for p, n in other.items():
            self.carry_from.addItem(f"{p} ({n} X)", p)
        self.carry_to.clear()
        for num, name in sorted(self.part_names.items()):
            self.carry_to.addItem(f"{num:03d} {name.strip()}", num)

    def drop_codes(self):
        from ..services.xtable import drop_codes
        src = self.carry_from.currentData()
        if src is None:
            return
        if QMessageBox.question(self, tr("Delete them"), tr("Delete the XML codes saved on part {p}? (the rules "
                                                            "version before stays in the library)").format(p=src)) \
                != QMessageBox.Yes:
            return
        try:
            out = drop_codes(self.ws, src)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Error"), str(e))
            return
        reread_held_question(self, f"{tr('Saved')}: {tr('Rules')} {out['catalog_version']}")
        self.refresh()

    def carry_codes(self):
        from ..services.xtable import carry_codes
        src, dst, mdl = self.carry_from.currentData(), self.carry_to.currentData(), self._mdl()
        if src is None or dst is None or not mdl:
            return
        try:
            out = carry_codes(self.ws, mdl, src, dst)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Error"), str(e))
            return
        msg = tr("{n} X moved").format(n=len(out["moved"]))
        if out["catalog_version"]:
            msg += f" — {tr('Rules')} {out['catalog_version']}"
        if out["left"]:
            msg += "\n" + tr("Not moved (no X of the same name or number, or it has codes already)") + ": " + \
                ", ".join(out["left"])
        QMessageBox.information(self, tr("Move the XML codes"), msg)
        self.refresh()

    def load(self):
        from ..services.xtable import x_table
        num, mdl = self.part.currentData(), self._mdl()
        self.t.setRowCount(0)
        if num is None or not mdl:
            return
        nums = sorted(self.part_names) if num == self.ALL else [num]
        rows = [(p, r) for p in nums for r in x_table(self.ws, mdl, p)]
        self._loading = True
        self.t.setRowCount(len(rows))
        for i, (p, r) in enumerate(rows):
            x = f"X{r['x']}" if num != self.ALL else f"{p:03d}/X{r['x']}"   # which part, in one table
            name = r["name"] if num != self.ALL else f"{r['name']}  —  {self.part_names.get(p, '').strip()}"
            vals = [x, name, f"{r['default']:g}" if r["default"] is not None else "",
                    ",".join(r["codes"]), r["formula"]]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j < 3:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if j == 0:
                    it.setData(Qt.UserRole, (p, r["x"]))
                self.t.setItem(i, j, it)
            ck = QTableWidgetItem()
            ck.setFlags((ck.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            self.t.setItem(i, 5, ck)
            self._style_row(i, bool(r["codes"]), r["confirmed"])
        self._loading = False
        self.t.resizeColumnsToContents()

    def _style_row(self, i, active: bool, confirmed: bool = True):
        """Green with a confirm box when the X has an XML code; grey "inactive" without (owner decision #21)."""
        ck = self.t.item(i, 5)
        if active:
            if ck.data(Qt.CheckStateRole) is None:
                ck.setCheckState(Qt.Checked if confirmed else Qt.Unchecked)
            ck.setText("")
            for j in range(6):
                paint(self.t.item(i, j), STATUS_COLORS["approved"])
        else:
            ck.setData(Qt.CheckStateRole, None)
            ck.setText(tr("inactive"))
            for j in range(6):
                self.t.item(i, j).setData(Qt.BackgroundRole, None)
                self.t.item(i, j).setForeground(QColor("#888888"))

    def _changed(self, item):
        """Typing an XML code makes the X active at once (and clearing it makes it inactive)."""
        if self._loading or item.column() != 3:
            return
        self._loading = True
        try:
            self._style_row(item.row(), bool(item.text().strip()))
        finally:
            self._loading = False

    def rows(self) -> list[dict]:
        out = []
        for i in range(self.t.rowCount()):
            codes = [c.strip() for c in re.split(r"[,،;\s]+", self.t.item(i, 3).text()) if c.strip()]
            part, x = self.t.item(i, 0).data(Qt.UserRole)
            out.append({"part": part, "x": x, "codes": codes,
                        "formula": self.t.item(i, 4).text().strip() or "{M}",
                        "confirmed": self.t.item(i, 5).data(Qt.CheckStateRole) is None   # code typed in now
                        or self.t.item(i, 5).checkState() == Qt.Checked})
        return out

    def save(self):
        from ..services.xtable import save_x_tables
        num, mdl = self.part.currentData(), self._mdl()
        if num is None or not mdl:
            return False
        by_part: dict[int, list] = {}
        for r in self.rows():
            by_part.setdefault(r.pop("part"), []).append(r)
        try:
            out = save_x_tables(self.ws, mdl, by_part)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Error"), str(e))
            return False
        msg = f"{tr('Saved')}: {tr('Rules')} {out['catalog_version']}"
        if out["new_codes"]:
            msg += f"\n{tr('New XML codes')}: {', '.join(out['new_codes'])} ({tr('ERP mapping profile')} " \
                   f"{out['mapping_version']})"
        reread_held_question(self, msg)
        self.refresh()
        return True


CHECK_MODES = (("range", "From … to"), ("none", "No check"), ("formula", "Formula"))


class MeasureChecksPanel(QWidget):
    """The customer's / product's checks of each XML measurement, for all his patterns or one pattern: from … to,
    no check, or a value computed by a formula from the other measurements (services/checks.py)."""
    COLS = ["Code", "Measurement", "Part / X", "Check", "From", "To", "Formula", "ERP mapping range"]

    def __init__(self, page):
        super().__init__()
        self.page, self.ws = page, page.ws
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("Pattern") + ":"))
        self.pattern = QComboBox()
        self.pattern.currentIndexChanged.connect(self._pattern_changed)
        top.addWidget(self.pattern, 1)
        lay.addLayout(top)
        note = QLabel(tr("For each measurement of the XML (in its unit, e.g. cm): From … to (outside: the order waits "
                         "for review; an empty side is open), No check, or Formula: the value computed from the other "
                         "measurements, e.g. FL - 10 or CH / 2 + 3 (M = its own XML value) — when the XML has none, "
                         "or it is outside From … to, or always when both are empty. A pattern's checks come over "
                         "the ones for all patterns."))
        note.setWordWrap(True)
        lay.addWidget(note)
        self.progs = QLabel()                          # the order's Programs a formula can use
        self.progs.setWordWrap(True)
        self.progs.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.progs)
        self.t = QTableWidget(0, len(self.COLS))
        self.t.setHorizontalHeaderLabels([tr(c) for c in self.COLS])
        self.t.verticalHeader().setVisible(False)
        self.t.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.t, 1)
        self.edits: dict[str, dict] = {}
        self._cur = None

    def _codes(self):
        _, prof = self.ws.active("mapping")
        return prof["measurements"]["fields"]

    def refresh(self):
        from ..services.checks import ALL, get_checks
        from ..services.customers import patterns, programs_seen
        self.cid = self.ws.customer()
        seen = programs_seen(self.ws, self.cid)
        self.progs.setText(tr("Programs in this customer's XML (for a formula)") + ": " + ", ".join(seen)
                           if seen else "")
        self.progs.setVisible(bool(seen))
        self.saved = get_checks(self.ws, self.cid)
        self.edits, self._cur = {}, None
        self.pattern.blockSignals(True)
        self.pattern.clear()
        self.pattern.addItem(tr("All patterns"), ALL)
        for p in patterns(self.ws, self.cid):
            self.pattern.addItem(p["variation"], p["variation"])
        self.pattern.blockSignals(False)
        self._show(ALL)

    def _pattern_changed(self, _i):
        if self._cur is not None:
            self.edits[self._cur] = self.rows()
        self._show(self.pattern.currentData())

    def _show(self, pat):
        self._cur = pat
        rules = self.edits.get(pat, self.saved.get(pat, {}))
        from ..services.xtable import targets_of_codes
        fields = self._codes()
        where = targets_of_codes(self.ws)                 # the part / X each measurement goes to
        codes = list(fields) + [c for c in rules if c not in fields]
        self.t.setRowCount(len(codes))
        for i, code in enumerate(codes):
            f, r = fields.get(code, {}), rules.get(code, {})
            it = QTableWidgetItem(code)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.t.setItem(i, 0, it)
            it = QTableWidgetItem(label(f.get("label")) or code)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.t.setItem(i, 1, it)
            it = QTableWidgetItem(", ".join(where.get(code, [])) or "—")
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            it.setToolTip(it.text())
            self.t.setItem(i, 2, it)
            cb = QComboBox()
            for k, text in CHECK_MODES:
                cb.addItem(tr(text), k)
            cb.setCurrentIndex([k for k, _ in CHECK_MODES].index(r.get("mode", "range")))
            self.t.setCellWidget(i, 3, cb)
            for j, k in ((4, "min"), (5, "max")):
                self.t.setItem(i, j, QTableWidgetItem("" if r.get(k) is None else f"{r[k]:g}"))
            self.t.setItem(i, 6, QTableWidgetItem(r.get("formula", "")))
            rng = f.get("range")
            it = QTableWidgetItem(f"{rng[0]:g}–{rng[1]:g} {f.get('unit', '')}" if rng else "—")
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.t.setItem(i, 7, it)
        self.t.resizeColumnsToContents()

    def rows(self) -> dict:
        out = {}
        for i in range(self.t.rowCount()):
            code = self.t.item(i, 0).text()
            mode = self.t.cellWidget(i, 3).currentData()
            vals = [self.t.item(i, j).text().strip().replace(",", ".") if self.t.item(i, j) else "" for j in (4, 5, 6)]
            if mode == "range" and not vals[0] and not vals[1]:
                continue                                  # nothing set: as the ERP mapping
            out[code] = {"mode": mode, "min": vals[0], "max": vals[1], "formula": vals[2]}
        return out

    def save(self):
        from ..services.checks import set_checks
        self.edits[self._cur] = self.rows()
        changed = False
        try:
            for pat, rows in self.edits.items():
                changed |= set_checks(self.ws, self.cid, pat, rows)
        except Exception as e:  # noqa: BLE001 - shown, the window stays open to correct it
            QMessageBox.warning(self, tr("Error"), tr_msg(str(e)))
            return False
        reread_held_question(self.page, tr("Saved"), self.cid)
        return True


class PatternsPanel(QWidget):
    """The customer's patterns (body shapes): the Variation name in the XML that chooses each and its GRAFIS
    model. The size table, piece options and fixed pieces stay the customer's."""

    def __init__(self, page):
        super().__init__()
        self.page, self.ws = page, page.ws
        lay = QVBoxLayout(self)
        self.note = QLabel()
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.t = QTableWidget(0, 2)
        self.t.setHorizontalHeaderLabels([tr("Variation in the XML"), tr("GRAFIS model (MDL)")])
        self.t.horizontalHeader().setStretchLastSection(True)
        self.t.setColumnWidth(0, 220)
        lay.addWidget(self.t, 1)
        bar = QHBoxLayout()
        for text, fn in (("Add models…", self.browse), ("Change model…", self.change_mdl),
                         ("Remove", lambda: self.t.removeRow(self.t.currentRow()) if self.t.currentRow() >= 0
                          else None), ("Save", self.save)):
            b = QPushButton(tr(text))
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        lay.addLayout(bar)
        self.seen: list[str] = []

    def refresh(self):
        from ..services.customers import patterns, variations_seen
        self.seen = variations_seen(self.ws)
        self.note.setText(tr("Each pattern: the Variation in the XML that chooses it (e.g. Athlete1, Slim2) and its "
                             "GRAFIS model. An order without one of these Variations goes to ER. No patterns: the "
                             "customer's GRAFIS model is used for every order.") + "\n" +
                          (tr("Variations in this customer's XML files") + ": " + ", ".join(self.seen)
                           if self.seen else tr("No XML of this customer imported yet")))
        self.t.setRowCount(0)
        for p in patterns(self.ws):
            self._add(p["variation"], p["mdl"])

    def _add(self, v="", m=""):
        r = self.t.rowCount()
        self.t.insertRow(r)
        cb = QComboBox()                              # the names that came in this customer's XML files
        cb.setEditable(True)
        cb.addItems(self.seen)
        cb.setCurrentText(v)
        self.t.setCellWidget(r, 0, cb)
        self.t.setItem(r, 1, QTableWidgetItem(m))

    def browse(self):
        files, _ = QFileDialog.getOpenFileNames(self, tr("GRAFIS model (MDL)…"), self.ws.grafis_mdl() or "",
                                                "GRAFIS (*.mdl *.MDL)")
        for f in files:
            self._add(os.path.splitext(os.path.basename(f))[0], f)

    def change_mdl(self):
        r = self.t.currentRow()
        if r < 0:
            return
        f, _ = QFileDialog.getOpenFileName(self, tr("GRAFIS model (MDL)…"), self.t.item(r, 1).text() or "",
                                           "GRAFIS (*.mdl *.MDL)")
        if f:
            self.t.setItem(r, 1, QTableWidgetItem(f))

    def rows(self) -> list[dict]:
        return [{"variation": (self.t.cellWidget(r, 0).currentText() if self.t.cellWidget(r, 0) else ""),
                 "mdl": (self.t.item(r, 1).text() if self.t.item(r, 1) else "")} for r in range(self.t.rowCount())]

    def save(self):
        from ..services.customers import set_patterns
        try:
            set_patterns(self.ws, self.rows())
        except Exception as e:  # noqa: BLE001 - shown, nothing saved
            QMessageBox.warning(self, tr("Error"), tr_msg(str(e)))
            return False
        reread_held_question(self.page, tr("Saved"))
        return True


class CustomersPage(Page):
    """One place to set a customer up: the customers (his name in the XML, his GRAFIS model) and, for the one
    chosen, his patterns, size table, piece options and fixed pieces."""
    title = "Customers"
    INNER = {"patterns": 0, "X values for GRAFIS": 1, "Design options": 2, "GRAFIS parts": 3}

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        self.setToolTip(tr("Each customer has his own name in the XML (Reference), his own size table, piece "
                           "options, fixed pieces and GRAFIS model. An XML is read with the settings of the customer "
                           "it names; an XML naming no customer goes to ER."))
        body = QHBoxLayout()                          # the list on one side (full height), the settings beside it
        left = QVBoxLayout()
        self.t = table(["Customer / product", "Cutting files folder", "XML name (Reference)", "Reseller in the XML",
                        "Item Number", "Measurement table (XML)", "Basic-Size in the XML", "Cutting file",
                        "Patterns"])
        self.t.itemSelectionChanged.connect(self._picked)
        left.addWidget(self.t, 1)
        bar = QHBoxLayout()
        for text, fn in (("Add customer…", self.add), ("Add product…", self.add_product), ("Edit…", self.edit),
                         ("Learn from an XML file…", self.learn_xml)):
            b = QPushButton(tr(text))
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        left.addLayout(bar)
        body.addLayout(left, 1)
        side = QVBoxLayout()
        self.head = QLabel()
        self.head.setObjectName("pageTitle")
        self.head.setWordWrap(True)
        self.head.setStyleSheet("font-size: 13pt; font-weight: 800; padding-top: 6px;")
        side.addWidget(self.head)
        tiles = QGridLayout()                         # each setting opens in its own window: save and close
        self.tile_buttons = {}
        from . import cad
        from . import themes as _th
        for n, (key, ic, label_) in enumerate((("patterns", "nest", "Patterns"),
                                               ("X values for GRAFIS", "xvalues", "Size table setup"),
                                               ("Design options", "options", "Piece options setup"),
                                               ("GRAFIS parts", "parts", "Fixed pieces"),
                                               ("Measurement checks", "ruler", "Measurement checks"))):
            b = QToolButton()
            b.setObjectName("ribbonButton")
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setIcon(cad.icon(ic, _th.colour("accent")))
            b.setIconSize(QSize(28, 28))
            b.setText(tr(label_))
            b.setMinimumSize(140, 80)
            b.clicked.connect(lambda _=False, k=key: self.open_editor(k))
            self.tile_buttons[key] = b
            tiles.addWidget(b, n // 2, n % 2)
        side.addLayout(tiles)
        side.addStretch()
        body.addLayout(side)
        lay.addLayout(body, 1)
        self.editor_dialog = None
        self.editor_page = None
        self.editor_key = None

    LABELS = {"patterns": "Patterns", "X values for GRAFIS": "Size table setup",
              "Design options": "Piece options setup", "GRAFIS parts": "Fixed pieces",
              "Measurement checks": "Measurement checks"}

    def show_tab(self, key: str):
        self.open_editor(key)

    def open_editor(self, key: str):
        """One of the customer's settings in its own window: change it, then Save and close (or Cancel)."""
        from ..services.customers import names
        if self.editor_dialog is not None and self.editor_dialog.isVisible():
            self.editor_dialog.close()
        if key in ("patterns", "Measurement checks"):
            page = (PatternsPanel if key == "patterns" else MeasureChecksPanel)(self)
        else:
            page = {"X values for GRAFIS": XValuesPage, "Design options": OptionsPage,
                    "GRAFIS parts": GrafisPartsPage}[key](self.win)
        box = getattr(page, "customer_box", None)
        if box is not None:
            box.parentWidget().hide()                 # the customer is the one chosen on the Customers page
        for b in page.findChildren(QPushButton):      # one Save: the window's own
            if b.text() == tr("Save"):
                b.hide()
        d = QDialog(self)
        d.setWindowTitle(f"{tr(self.LABELS[key])} — {names(self.ws).get(self.ws.customer(), '')}")
        d.resize({"X values for GRAFIS": 1150, "Measurement checks": 1000}.get(key, 720), 720)   # narrow but tables
        v = QVBoxLayout(d)
        v.addWidget(page, 1)
        bb = QDialogButtonBox()
        ok = bb.addButton(tr("Save and close"), QDialogButtonBox.AcceptRole)
        bb.addButton(tr("Cancel"), QDialogButtonBox.RejectRole)
        ok.setObjectName("primary")

        def save_close():
            if page.save() is not False:              # an error keeps the window open to correct it
                d.accept()
                self.refresh()
        ok.clicked.connect(save_close)
        bb.rejected.connect(d.reject)
        v.addWidget(bb)
        self.editor_dialog, self.editor_page, self.editor_key = d, page, key
        self.win.guard(page.refresh)
        d.open()                                      # modal to the window, without blocking the program

    def _picked(self):
        cid = self._cid()
        if cid and cid != self.ws.customer():
            self.ws.db.set_setting("ui.customer", cid)
            self.win.customer_changed()               # refreshes this page with his settings

    def refresh(self):
        from ..services.auto_cut import customer_formats
        from ..services.customers import basic_sizes_seen, list_customers, measurement_table, patterns
        every = list_customers(self.ws)
        cs = []                                          # each customer, his products under him
        for c in every:
            if not c.get("parent"):
                cs.append(c)
                cs += [p for p in every if p.get("parent") == c["id"]]
        cur = self.ws.customer()
        rows = []
        with_products = {c["parent"] for c in every if c.get("parent")}
        for c in cs:
            # a customer with products: his XML lines are read with the product of their Item Number, so the
            # patterns are the products' — none in front of the customer
            pats = [] if c["id"] in with_products else patterns(self.ws, c["id"])
            mark = "● " if c["id"] == cur else ""
            rows.append([(f"      ↳ {mark}" if c.get("parent") else mark) + c["name"], "", c["reference"],
                         c.get("reseller") or "—", c.get("item") or ("—" if not c.get("parent") else ""),
                         measurement_table(self.ws, c["id"]) or "—",
                         _few(basic_sizes_seen(self.ws, c["id"])),
                         ", ".join(f.upper() for f in customer_formats(self.ws, c["id"]) or []) or
                         tr("As on the markers page"),
                         tr("for each product") if c["id"] in with_products else    # the names: in Patterns
                         (tr("{n} patterns").format(n=len(pats)) if pats else "—")])
        pb = self.tile_buttons["patterns"]
        pb.setEnabled(cur not in with_products)
        pb.setToolTip(tr("Patterns are set for each product of this customer") if cur in with_products else "")
        self.t.blockSignals(True)
        fill(self.t, rows, None, [c["id"] for c in cs])
        for i, c in enumerate(cs):                       # beside each customer: where his cutting files go
            self.t.setCellWidget(i, 1, self._folder_cell(c))
        self.t.resizeColumnToContents(1)
        k = next((i for i, c in enumerate(cs) if c["id"] == cur), 0)
        self.t.selectRow(k)
        self.t.blockSignals(False)
        from ..services.customers import names
        self.head.setText(f"{tr('Settings of')} {names(self.ws).get(cur, '')}")

    def _folder_cell(self, c) -> QWidget:
        from PySide6.QtWidgets import QToolButton

        from ..services.auto_cut import cut_folder
        own = self.ws.db.get_setting(f"cutting.folder@{c['id']}")
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(2)
        b = QToolButton()
        where = cut_folder(self.ws, [c["id"]])
        b.setText(("📁 " + where) if own else (tr("as his customer") + " — " + where if c.get("parent") and
                                                 self.ws.db.get_setting(f"cutting.folder@{c['parent']}")
                                                 else "📁 " + tr("Cutting folder") + " / " + os.path.basename(where)))
        b.setToolTip(tr("Choose the folder this customer's cutting files are sent to") + f"\n{where}")
        b.clicked.connect(lambda _=False, cid=c["id"], cur=where: self.choose_cut_folder(cid, cur))
        h.addWidget(b, 1)
        if own:
            r = QToolButton()
            r.setText("↺")
            r.setToolTip(tr("Back to the customer's folder inside the cutting folder"))
            r.clicked.connect(lambda _=False, cid=c["id"]: (
                self.ws.db.set_setting(f"cutting.folder@{cid}", None), self.refresh()))
            h.addWidget(r)
        return w

    def choose_cut_folder(self, cid: str, current: str):
        d = QFileDialog.getExistingDirectory(self, tr("Cutting files folder…"), current)
        if d:
            self.ws.db.set_setting(f"cutting.folder@{cid}", d)
            self.refresh()

    def _cid(self):
        ids = selected_data(self.t)
        return ids[0] if ids else None

    def add_product(self):
        """A product of the customer chosen (his XML lines with its Item Number use its own patterns and tables)."""
        from ..services.customers import add_product, get, products
        cid = self._cid() or self.ws.customer()
        c = get(self.ws, cid)
        if c is None:
            return
        parent = c.get("parent") or cid
        pc = get(self.ws, parent)
        d = QDialog(self)
        d.setWindowTitle(f"{tr('Add product…')} — {pc['name']}")
        d.setMinimumWidth(420)
        f = QFormLayout(d)
        e_name, e_item, e_table = QLineEdit(), QLineEdit(), QLineEdit()
        e_item.setPlaceholderText("1001001001")
        e_table.setPlaceholderText("Thobe_Measurments_Full")
        e_table.setToolTip(tr("Measurement-Table-Name in the XML; another name makes the order wait for review "
                              "(empty: any)"))
        f.addRow(tr("Product") + ":", e_name)
        f.addRow(tr("Item Number") + ":", e_item)
        f.addRow(tr("Measurement table (XML)") + ":", e_table)
        src = QComboBox()
        for x in [pc] + products(self.ws, parent):
            src.addItem(x["name"], x["id"])
        f.addRow(tr("Copy the settings of") + ":", src)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec() != QDialog.Accepted:
            return
        pid = self.win.guard(lambda: add_product(self.ws, parent, e_name.text(), e_item.text(),
                                                 copy_from=src.currentData()))
        if pid and e_table.text().strip():
            from ..services.customers import set_measurement_table
            self.win.guard(lambda: set_measurement_table(self.ws, pid, e_table.text()))
        if pid:
            self.ws.db.set_setting("ui.customer", pid)
            self.win.customer_changed()

    def _dialog(self, title, name="", ref="", copy_choices=None, reseller="", table=None, cid=None):
        d = QDialog(self)
        d.setWindowTitle(title)
        d.setMinimumWidth(420)
        f = QFormLayout(d)
        e_name, e_ref, e_res = QLineEdit(name), QLineEdit(ref), QLineEdit(reseller)
        e_ref.setPlaceholderText("From …")
        e_res.setPlaceholderText("10040")
        e_res.setToolTip(tr("The Reseller number in his XML files (empty: any)"))
        f.addRow(tr("Customer") + ":", e_name)
        f.addRow(tr("XML name (Reference)") + ":", e_ref)
        f.addRow(tr("Reseller in the XML") + ":", e_res)
        e_table = QLineEdit(table or "")
        if table is not None:                             # editing: his Measurement-Table-Name too
            e_table.setPlaceholderText("Thobe_Measurments_Full")
            f.addRow(tr("Measurement table (XML)") + ":", e_table)
        fmt = format_box(self.ws, cid)
        f.addRow(tr("Cutting file") + ":", fmt)
        self._fmt = fmt
        src = None
        if copy_choices:
            src = QComboBox()
            for cid, n in copy_choices:
                src.addItem(n, cid)
            f.addRow(tr("Copy the settings of") + ":", src)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if d.exec() != QDialog.Accepted:
            return None
        return e_name.text(), e_ref.text(), (src.currentData() if src else None), e_res.text(), e_table.text()

    def add(self):
        from ..services.customers import add_customer, list_customers
        got = self._dialog(tr("Add customer…"), copy_choices=[(c["id"], c["name"]) for c in list_customers(self.ws)])
        if got:
            name, ref, src, res, _ = got
            cid = self.win.guard(lambda: add_customer(self.ws, name, ref, copy_from=src, reseller=res))
            if cid:
                save_format(self.ws, cid, self._fmt)
                self.ws.db.set_setting("ui.customer", cid)
                self.win.customer_changed()
                QMessageBox.information(self, tr("Customers"), tr("Customer added with a copy of the settings of the "
                                                                  "customer chosen. Choose his GRAFIS model, then "
                                                                  "change his size table, piece options and fixed "
                                                                  "pieces."))

    def edit(self):
        from ..services.customers import get, update_customer
        cid = self._cid()
        c = get(self.ws, cid) if cid else None
        if c is None:
            return
        from ..services.customers import measurement_table, set_measurement_table
        table_now = measurement_table(self.ws, cid)
        if c.get("parent"):                               # a product: its name, Item Number and measurement table
            d = QDialog(self)
            d.setWindowTitle(f"{tr('Edit…')} — {c['name']}")
            d.setMinimumWidth(420)
            f = QFormLayout(d)
            e_name, e_item, e_table = QLineEdit(c["name"]), QLineEdit(c.get("item", "")), QLineEdit(table_now)
            e_table.setPlaceholderText("Thobe_Measurments_Full")
            f.addRow(tr("Product") + ":", e_name)
            f.addRow(tr("Item Number") + ":", e_item)
            f.addRow(tr("Measurement table (XML)") + ":", e_table)
            fmt = format_box(self.ws, cid)
            f.addRow(tr("Cutting file") + ":", fmt)
            bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            bb.accepted.connect(d.accept)
            bb.rejected.connect(d.reject)
            f.addRow(bb)
            if d.exec() == QDialog.Accepted:
                self.win.guard(lambda: update_customer(self.ws, cid, name=e_name.text(), item=e_item.text()))
                self.win.guard(lambda: set_measurement_table(self.ws, cid, e_table.text()))
                save_format(self.ws, cid, fmt)
                self.win.customer_changed()
            return
        got = self._dialog(tr("Edit…"), c["name"], c["reference"], reseller=c.get("reseller", ""), table=table_now,
                           cid=cid)
        if got:
            save_format(self.ws, cid, self._fmt)
            self.win.guard(lambda: update_customer(self.ws, cid, name=got[0], reference=got[1], reseller=got[3]))
            self.win.guard(lambda: set_measurement_table(self.ws, cid, got[4]))
            self.win.customer_changed()

    def learn_xml(self):
        """Read one of the customer's XML files and make his XML mapping read them as they are; then read the file
        again (its held order lines are taken back first)."""
        from ..services.customers import get, learn_from_xml, reread_file
        cid = self._cid() or self.ws.customer()
        c = get(self.ws, cid)
        path, _ = QFileDialog.getOpenFileName(self, f"{tr('Learn from an XML file…')} — {c['name']}", "",
                                              "XML (*.xml *.XML)")
        if not path:
            return
        res = self.win.guard(lambda: learn_from_xml(self.ws, cid, open(path, "rb").read()))
        if res is None:
            return
        lines = []
        if res.get("reseller"):
            lines.append(f"{tr('Reseller in the XML')}: {res['reseller']}")
        if res["table"]:
            lines.append(f"{tr('Size table name')}: {res['table']}")
        if res["line_pattern"]:
            lines.append(tr("Order numbers like this file's are read"))
        if res["added"]:
            lines.append(f"{tr('New measurement codes (in cm)')}: {', '.join(res['added'])}")
        if res["ranges_dropped"]:
            lines.append(f"{tr('Limits not used for this customer')}: {', '.join(res['ranges_dropped'])}")
        if res["not_required"]:
            lines.append(f"{tr('No longer required (not in his files)')}: {', '.join(res['not_required'])}")
        if res["unknown_variations"]:
            lines.append(f"⚠ {tr('Variations still without a meaning — give them one in Model settings → Piece options setup')}: "
                         f"{', '.join(res['unknown_variations'])}")
        msg = "\n".join("• " + x for x in lines) or tr("Nothing to change: his files are read as they are")
        if QMessageBox.question(self, tr("Learn from an XML file…"),
                                msg + "\n\n" + tr("Read this XML again now?")) == QMessageBox.Yes:
            out = self.win.guard(lambda: reread_file(self.ws, path))
            if out is not None:
                self.win.refresh_cad(force=True)
                QMessageBox.information(self, tr("Learn from an XML file…"),
                                        f"{out.file_name}: {tr(out.status)} {tr_msg(out.message)}")
        self.refresh()


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
GPART_COLS = ["Design option", "GRAFIS part no.", "GRAFIS part name", "Material", "Copies", "Mirrored"]


def _grafis_model_parts(ws) -> dict[int, tuple]:
    """{part number: (name, PartSpec | None)} of the GRAFIS model chosen in the GRAFIS screen ({} if none)."""
    mdl = ws.grafis_mdl()
    if not mdl or not os.path.isfile(mdl):
        return {}
    from ..grafis.container import read_container
    from ..grafis.parts import production
    return production(read_container(mdl))


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
        self.gpt = QTableWidget(0, len(GPART_COLS))
        self.gpt.setHorizontalHeaderLabels([tr(c) for c in GPART_COLS])
        for t, name in ((self.rt, "Rules"), (self.mt, "Measurement definitions"), (self.pt, "Production pieces"),
                        (self.ot, "Design option rules"), (self.gpt, "GRAFIS parts")):
            t.horizontalHeader().setStretchLastSection(True)
            sub.addTab(t, tr(name))
        self.sub = sub
        lx.addWidget(sub, 3)
        bx = QHBoxLayout()
        for text, fn in (("Add row", self.add_row), ("Remove row", self.remove_row), ("Test with order", self.test_rules),
                         ("Parts of the GRAFIS model", self.show_model_parts), ("Save as new version", self.save_catalog)):
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
        _put(self.gpt, [[g.get("option") or "*", g["part"], g.get("name", ""), g["material"], g["copies"],
                         g["mirrored"]] for g in sorted(st.get("grafis_parts") or [],
                                                         key=lambda g: (g.get("option") or "", g["part"]))])

    def show_model_parts(self):
        """List the parts of the GRAFIS model (number, name, current production) to fill the GRAFIS parts table."""
        try:
            parts = _grafis_model_parts(self.ws)
        except Exception as e:  # noqa: BLE001
            self.trace.setPlainText(f"{tr('Error')}: {e}")
            return
        if not parts:
            self.trace.setPlainText(tr("Choose the GRAFIS model (MDL) in the GRAFIS screen first"))
            return
        groups = self._style().get("option_groups", {})
        self.trace.setPlainText(
            tr("Design option") + ": * = " + tr("always") + " · " +
            " · ".join(f"{g}={v}" for g, vs in groups.items() for v in vs) + "\n\n" +
            "\n".join(f"{n:03d}  {nm:<28} " + (f"{tr('Production')}: {sp.material} {sp.copies:02d} {sp.mirrored:02d}"
                                                if sp else "") for n, (nm, sp) in sorted(parts.items())))

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
        before = {r["id"]: r for r in st["rules"]}
        for row in _rows(self.rt):
            if not row[0]:
                continue
            prev_t = before.get(row[0], {}).get("target", {})
            x_name = prev_t.get("x_name", "") if (prev_t.get("piece"), prev_t.get("variable")) == (row[3], row[5]) \
                else ""
            src = ({"type": "measurement", "code": row[2]} if row[1] != "option" else
                   {"type": "option", "group": row[2].split("=")[0], "value": row[2].split("=")[-1]})
            rules.append({"id": row[0], "source": src, "target": {"piece": row[3], "construction": row[4],
                          "variable": row[5], **({"x_name": x_name} if x_name else {})}, "value": row[6] or "{M}", "order": float(row[7] or 0),
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
        model_parts = _grafis_model_parts(self.ws)
        gparts = []
        for row in _rows(self.gpt):
            if not row[1]:
                continue
            num = int(float(row[1]))
            if model_parts and num not in model_parts:
                raise ValueError(f"{tr('GRAFIS parts')}: {num:03d} — {tr('not found')} (MDL)")
            name = row[2] or (model_parts.get(num, ("",))[0])
            gparts.append({"option": "" if row[0] in ("", "*") else row[0], "part": num, "name": name.strip(),
                           "material": (row[3] or "O").strip().upper(), "copies": int(float(row[4] or 1)),
                           "mirrored": int(float(row[5] or 0))})
        if gparts:
            st["grafis_parts"] = gparts
        else:
            st.pop("grafis_parts", None)
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
        from ..services.catalog import grafis_parts_for
        gp = grafis_parts_for(cat["products"][pid]["styles"][sid], res.options)
        lines += [tr("GRAFIS parts") + ": " + (", ".join(f"{n:03d} {g.get('name', '')} {g['material']} "
                                                         f"{g['copies']}+{g['mirrored']}" for n, g in sorted(gp.items()))
                                               if gp else "—")]
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
        path, _ = QFileDialog.getOpenFileName(self, tr("GRAFIS"), "", "GRAFIS (*.mdl *.MDL)")
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


# side menu: sections, their pages (by title) and an icon each; PAGES follows the same order
SECTIONS = [
    ("Daily work", [("Home", "⌂"), ("Orders", "☰"), ("Import & mapping", "⇪"), ("Order details", "✎")]),
    ("GRAFIS & cutting", [("GRAFIS markers", "✂")]),
    ("Customer settings", [("Customers", "☺"), ("X values for GRAFIS", "Ⅹ"), ("Design options", "◈"),
                      ("GRAFIS parts", "▦")]),
    ("Pattern engine", [("Model & design", "◇"), ("Pattern preview", "◫"), ("Validation", "✓"),
                        ("Approve & send", "➤")]),
    ("Settings & system", [("Model library", "▤"), ("Rules editor", "⚙"), ("Materials & export", "◰"),
                           ("Log & backups", "⟲")]),
]
SUBTITLES = {
    "Home": "What is happening now, what needs attention, quick actions",
    "Customers": "Each customer: his name in the XML, size table, piece options, fixed pieces and GRAFIS model",
    "Orders": "Order lines from the ERP, their revisions and status",
    "Import & mapping": "XML files received and how their fields are read",
    "Order details": "Measurements and design options of the selected order line",
    "GRAFIS markers": "Order models for GRAFIS, the robot, nesting and cutting files",
    "X values for GRAFIS": "Which XML code fills which X value of the GRAFIS model",
    "Design options": "Option groups, their values and their names in the XML",
    "GRAFIS parts": "Which GRAFIS parts each design option produces",
    "Model & design": "Pattern engine: model and design of the order",
    "Pattern preview": "The pieces of the order as GRAFIS made them (one of each)",
    "Validation": "Pattern engine: checks before approval",
    "Approve & send": "Pattern engine: approval and nesting hand-over",
    "Model library": "Versions of models, rules and mappings",
    "Rules editor": "Measurement and option rules (pattern developer)",
    "Materials & export": "Shrinkage, materials and export settings",
    "Log & backups": "Everything that happened, backups and restore",
}
_BY_TITLE = {c.title: c for c in [HomePage, OrdersPage, ImportPage, OrderPage, ModelPage, PreviewPage, ValidationPage,
                                  SendPage, GrafisPage, CustomersPage, XValuesPage, OptionsPage, GrafisPartsPage,
                                  LibraryPage,
                                  RulesPage, SettingsPage, LogPage]}
PAGES = [_BY_TITLE[t] for _, items in SECTIONS for t, _ in items]


# ---------------------------------------------------------------- window

class _ClickLabel(QLabel):
    def __init__(self, on_click):
        super().__init__()
        self._on_click = on_click

    def mousePressEvent(self, e):
        self._on_click()


class MainWindow(QMainWindow):
    """CAD workstation: menu, ribbon (tools in the order of the work), order browser, the marker canvas and the
    work screens as tabs, properties inspector, status bar with the state of the chain."""

    def __init__(self, ws: Workspace):
        super().__init__()
        self.ws = ws
        self.pool = QThreadPool.globalInstance()
        QTimer.singleShot(4000, self, self._tidy_markers)  # old markers out of the cutting folder, in the background
        QTimer.singleShot(6000, self, self._reread_held_at_start)
        self.current_rid = None
        self.sel_row = None
        self._cad_sig = None
        self.watcher: FolderWatcher | None = None
        self.developer = bool(ws.db.get_setting("ui.developer", False))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_watch)
        self.cad_timer = QTimer(self)
        self.cad_timer.timeout.connect(self._refresh_cad_bg)
        self.setAcceptDrops(True)
        self.resize(1440, 900)
        self.setMinimumSize(900, 560)
        self.chain_on = False             # nothing runs by itself after opening: the owner presses Start
        self.build()
        self.cad_timer.start(5000)

    # ---- building
    def build(self):
        from PySide6.QtWidgets import QScrollArea
        from . import cad, themes
        self.setWindowTitle(f"LEVEL7 MTM {__version__} — {self.ws.folder}")
        old = self.centralWidget()
        self.pages, self.wrappers, self.page_actions = {}, {}, {}
        for title, cls in _BY_TITLE.items():
            p = cls(self)
            self.pages[title] = p
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(22, 16, 22, 12)
            h = QLabel(tr(title))
            h.setObjectName("pageTitle")
            st = QLabel(tr(SUBTITLES.get(title, "")))
            st.setObjectName("pageSub")
            wl.addWidget(h)
            wl.addWidget(st)
            wl.addSpacing(6)
            wl.addWidget(p, 1)
            scroll = QScrollArea()                     # a wide page never makes the window wider than the screen
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidget(wrap)
            self.wrappers[title] = scroll
        self._menus()
        self._statusbar()
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        grafis = lambda: self.pages["GRAFIS markers"]
        self.ribbon_groups = [
            ("order", tr("The order"), [("import", "import", tr("Import XML"), self.import_dialog),
                           ("orders", "orders", tr("Orders"), lambda: self.goto("Orders")),
                           ("details", "ruler", tr("Order info"), self.open_details)]),
            ("grafis", tr("GRAFIS"), [("customers", "customers", tr("Customers"), lambda: self.goto("Customers")),
                        ("write", "model", tr("Write model"), self.write_model),
                        ("robot", "robot", tr("Robot"), lambda: grafis().robot_now()),
                        ("options", "options", tr("Model settings"),
                         [(tr("Size table setup"), lambda: self.goto("X values for GRAFIS")),
                          (tr("Piece options setup"), lambda: self.goto("Design options")),
                          (tr("Fixed pieces"), lambda: self.goto("GRAFIS parts"))])]),
            ("nesting", tr("Nesting"), [("renest", "renest", tr("Nest again"),
                              {"main": self.renest_selected,
                               "menu": [(tr("Nest again"), self.renest_selected),
                                        (tr("Show marker in LEVEL7 Nesting"), self.nesting_selected)]}),
                             ("markers", "nest", tr("Marker name"), lambda: self.goto("GRAFIS markers"))]),
            ("output", tr("Output"), [("cut", "folder", tr("Files"),
                             [(tr("Cutting files"), self.open_cutting),
                              (tr("Markers (L7N)"), self.open_markers),
                              (tr("Order models (MDL, saved)"), self.open_mdl_archive),
                              (tr("Pattern errors (MDL-ERR)"), self.open_mdl_err),
                              (tr("Finished XML (done)"), self.open_done_xml),
                              (tr("Errors (ER)"), lambda: self.pages["Home"]._open_er())])]),
            ("system", tr("System"), [("setup", "setup", tr("Setup"), self.open_setup),
                            ("log", "log", tr("Log"), lambda: self.goto("Log & backups"))]),
        ]
        self.ribbon = cad.Ribbon([(t, its) for _g, t, its in
                                  ordered_ribbon(self.ribbon_groups, self.ws.db.get_setting("ui.ribbon") or {})],
                                 extra=[(tr("Customer"), self._customer_widget()),
                                        (tr("ERP orders"), self._erp_timer_widget())])
        self.ribbon.home.connect(lambda: self.goto("Home"))         # the L7 mark: the day's overview
        self.ribbon.setContextMenuPolicy(Qt.CustomContextMenu)       # right click: arrange the icons
        self.ribbon.customContextMenuRequested.connect(self._ribbon_menu)
        lay.addWidget(self.ribbon)
        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(1)
        split.setChildrenCollapsible(False)
        self.browser = cad.OrderBrowser()
        self.browser.setMinimumWidth(150)                 # narrow sides (owner): a larger work area
        self.browser.selected.connect(self.select_row)
        self.browser.remove.connect(self.remove_orders)
        self.browser.reread.connect(self.reread_orders)
        self.browser.nesting_toggle.connect(self.toggle_nesting)
        self.browser.nesting_paused = bool(self.ws.db.get_setting("nesting.paused", False))
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.marker = cad.MarkerPanel()
        self.marker.view.zoom_changed.connect(lambda k: self.sb_zoom.setText(cad.ltr(f"1:{3.78 / k:.0f}" if k else "—")))
        self.tabs.addTab(self.marker, cad.icon("marker", themes.colour("accent"), 16), tr("Marker"))
        self.tabs.tabBar().setTabButton(0, QTabBar.ButtonPosition.RightSide, None)
        self.tabs.tabBar().setTabButton(0, QTabBar.ButtonPosition.LeftSide, None)
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        head, _ = cad.panel_head(tr("Properties"))
        sl.addWidget(head)
        self.inspector = cad.Inspector([("robot", "↻  " + tr("Robot: try again"), True),
                                        ("open_cut", tr("Open cutting file") + "  PLT / ISO", True),
                                        ("nesting", tr("Show marker in LEVEL7 Nesting"), False),
                                        ("renest", tr("Nest again"), False),
                                        ("details", tr("Order info"), False)])
        b = self.inspector.buttons
        b["open_cut"].clicked.connect(self.open_cutting)
        b["robot"].clicked.connect(self.robot_retry_selected)
        b["nesting"].clicked.connect(self.nesting_selected)
        b["renest"].clicked.connect(self.renest_selected)
        b["details"].clicked.connect(self.open_details)
        sl.addWidget(self.inspector, 1)
        side.setMinimumWidth(160)
        self.tabs.setMinimumWidth(240)                    # a small window: the sides and the work area all fit
        for w in (self.browser, self.tabs, side):
            split.addWidget(w)
        for i, k in enumerate((0, 1, 0)):                 # the window made smaller: the work area gives, the
            split.setStretchFactor(i, k)                  # sides keep their width
        saved = self.ws.db.get_setting("ui.sides")        # the widths the owner dragged them to
        split.setSizes([saved[0], 1000, saved[1]] if saved else [190, 1000, 200])
        split.splitterMoved.connect(lambda *_: self.ws.db.set_setting(
            "ui.sides", [split.sizes()[0], split.sizes()[2]]))
        self.split = split
        lay.addWidget(split, 1)
        self.setCentralWidget(root)
        if old is not None:
            old.deleteLater()
        self.apply_role()
        self.refresh_cad(force=True)
        self._tick()
        self.tabs.currentChanged.connect(self._tab_changed)

    def _ribbon_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction(tr("Arrange the icons…"), self.customize_ribbon)
        m.exec(self.ribbon.mapToGlobal(pos))

    def customize_ribbon(self):
        """The owner arranges the ribbon: the order of the groups and of the icons in each group, and which
        icons are shown. Kept in the setting ui.ribbon; "Default" puts everything back."""
        from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem
        d = QDialog(self)
        d.setWindowTitle(tr("Arrange the icons…"))
        d.resize(440, 560)
        lay = QVBoxLayout(d)
        note = QLabel(tr("Select a group or an icon and move it up or down; untick an icon to hide it."))
        note.setWordWrap(True)
        lay.addWidget(note)
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        lay.addWidget(tree, 1)

        def load(cfg):
            tree.clear()
            hidden = set(cfg.get("hidden", []))
            for gid, title, items in ordered_ribbon(self.ribbon_groups, {**cfg, "hidden": []}):
                g = QTreeWidgetItem([title])
                g.setData(0, Qt.UserRole, gid)
                for key, _ic, text, _fn in items:
                    it = QTreeWidgetItem([text])
                    it.setData(0, Qt.UserRole, key)
                    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                    it.setCheckState(0, Qt.Unchecked if key in hidden else Qt.Checked)
                    g.addChild(it)
                tree.addTopLevelItem(g)
                g.setExpanded(True)
        load(self.ws.db.get_setting("ui.ribbon") or {})

        def move(step):
            it = tree.currentItem()
            if it is None:
                return
            par = it.parent()
            if par is None:
                i = tree.indexOfTopLevelItem(it)
                j = i + step
                if 0 <= j < tree.topLevelItemCount():
                    tree.takeTopLevelItem(i)
                    tree.insertTopLevelItem(j, it)
                    it.setExpanded(True)
                    tree.setCurrentItem(it)
            else:
                i = par.indexOfChild(it)
                j = i + step
                if 0 <= j < par.childCount():
                    par.takeChild(i)
                    par.insertChild(j, it)
                    tree.setCurrentItem(it)
        bar = QHBoxLayout()
        for text, fn in (("▲  " + tr("Up"), lambda: move(-1)), ("▼  " + tr("Down"), lambda: move(1)),
                         (tr("Default"), lambda: load({}))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        lay.addLayout(bar)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        if d.exec() != QDialog.Accepted:
            return
        cfg = {"groups": [], "items": {}, "hidden": []}
        for i in range(tree.topLevelItemCount()):
            g = tree.topLevelItem(i)
            gid = g.data(0, Qt.UserRole)
            cfg["groups"].append(gid)
            cfg["items"][gid] = []
            for k in range(g.childCount()):
                it = g.child(k)
                key = it.data(0, Qt.UserRole)
                cfg["items"][gid].append(key)
                if it.checkState(0) != Qt.Checked:
                    cfg["hidden"].append(key)
        self.ws.db.set_setting("ui.ribbon", cfg)
        self._rebuild()

    def _customer_widget(self) -> QWidget:
        """Whose settings the settings pages show (size table, piece options, fixed pieces, GRAFIS model)."""
        self.customer_box = QComboBox()
        self.customer_box.setToolTip(tr("The customer whose settings are shown and changed"))
        self.customer_box.setMinimumWidth(130)
        self._fill_customers()
        self.customer_box.activated.connect(lambda _i: (self.ws.db.set_setting("ui.customer",
                                                                                self.customer_box.currentData()),
                                                        self.customer_changed()))
        return self.customer_box

    def _fill_customers(self):
        fill_customer_box(self.ws, self.customer_box)
        for p in self.pages.values():                     # the boxes at the top of the settings pages too
            if getattr(p, "customer_box", None) is not None:
                fill_customer_box(self.ws, p.customer_box)

    def customer_changed(self):
        """Another customer chosen (or one added / changed): the open settings pages show his settings."""
        self._fill_customers()
        page = self.current_page()
        if page is not None:
            self.guard(page.refresh)
        self.statusBar().showMessage(f"{tr('Customer')}: {self.customer_box.currentText()}", 5000)

    def _erp_timer_widget(self) -> QWidget:
        """Countdown to the next check of the ERP folder; the seconds are set right here; ▶ checks now."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(4, 0, 4, 0)
        h.setSpacing(6)
        self.erp_clock = _ClickLabel(self._erp_clock_clicked)
        self.erp_clock.setObjectName("erpClock")
        self.erp_clock.setCursor(Qt.PointingHandCursor)
        self.erp_clock.setAlignment(Qt.AlignCenter)
        self.erp_clock.setMinimumWidth(64)
        self.erp_clock.setLayoutDirection(Qt.LeftToRight)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.erp_spin = seconds_box()
        self.erp_spin.setValue(self.watch_interval_s())
        self.erp_spin.setToolTip(tr("Check every") + " (" + tr("s") + ")")
        self.erp_spin.valueChanged.connect(self.set_watch_interval)
        now_b = QPushButton("▶  " + tr("Check now"))
        now_b.clicked.connect(self.poll_watch)
        for x in (self.erp_spin, now_b):
            x.setFixedHeight(25)
        now_b.setStyleSheet("padding: 1px 8px;")
        col.addWidget(self.erp_spin)
        col.addWidget(now_b)
        self.start_b = QPushButton()                  # Start / Stop: the whole chain (ERP, GRAFIS, robot, nesting)
        self.start_b.setMinimumSize(64, 34)
        self.start_b.clicked.connect(self.toggle_chain)
        self._show_chain()
        h.addWidget(self.start_b)
        h.addWidget(self.erp_clock)
        h.addLayout(col)
        if not hasattr(self, "tick_timer"):
            self.tick_timer = QTimer(self)
            self.tick_timer.timeout.connect(self._tick)
            self.tick_timer.start(1000)
        return w

    def _open_folder(self, d: str | None, title: str):
        if d and os.path.isdir(d) and hasattr(os, "startfile"):
            os.startfile(d)
        else:
            QMessageBox.information(self, title, d or tr("not set"))

    def open_markers(self):
        """The markers (.l7n) of the nestings (the folder chosen in Setup, else the program's data folder)."""
        from ..services.auto_cut import markers_folder
        d = markers_folder(self.ws)
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        self._open_folder(d, tr("Markers (L7N)"))

    def open_mdl_err(self):
        """The models whose pattern came out distorted (MDL-ERR): never cut; the .txt beside each says why."""
        from ..services.pattern_check import err_folder
        d = err_folder(self.ws)
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        self._open_folder(d, tr("Pattern errors (MDL-ERR)"))

    def open_mdl_archive(self):
        """The copy of every order model as GRAFIS saved it (MDL-ARCHIVE): the reference when something is wrong."""
        from ..services.grafis_orders import archive_folder
        d = archive_folder(self.ws)
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        self._open_folder(d, tr("Order models (MDL, saved)"))

    def open_done_xml(self):
        """The ERP XML files whose orders have their cutting files (done, inside the ERP folder)."""
        erp = self.ws.db.get_setting("watch.folder")
        d = None
        if erp and os.path.isdir(erp):
            from ..services.done_folder import xml_dirs
            d = xml_dirs(erp)[0]
        self._open_folder(d, tr("Finished XML (done)"))

    def _show_chain(self):
        from . import themes
        b = getattr(self, "start_b", None)
        if b is None:
            return
        on = getattr(self, "chain_on", False)
        col = themes.colour("bad" if on else "ok").name()
        b.setText(("■  " + tr("Stop")) if on else ("▶  " + tr("Start")))
        b.setToolTip(tr("Stop reading ERP orders, the robot and the nesting") if on else
                     tr("Start: read the ERP folder, the robot drives GRAFIS, nesting writes the cutting files"))
        b.setStyleSheet(f"font-weight: 800; font-size: 9.5pt; color: {col}; border: 2px solid {col}; "
                        f"border-radius: 5px; padding: 2px 8px;")

    def toggle_chain(self):
        """Start: the ERP folder is read on the timer, the GRAFIS folders are watched, the robot and the nesting
        work. Stop: all of it waits. After opening the program nothing runs until Start."""
        gp = self.pages.get("GRAFIS markers")
        if getattr(self, "chain_on", False):
            self.chain_on = False
            self.timer.stop()
            self.watcher = None
            if gp is not None:
                gp.watcher = None                          # the CAD output folder and the robot wait
            self.ws.db.set_setting("nesting.paused", True)  # the whole system: the nesting too
            self.browser.nesting_paused = True
            self.status(tr("Stopped"))
        else:
            self.chain_on = True
            self.ws.db.set_setting("nesting.paused", False)
            self.ws.db.set_setting("cutting.auto", True)     # the whole system: the nesting too
            self.browser.nesting_paused = False
            folder = self.ws.db.get_setting("watch.folder")
            if folder and os.path.isdir(folder):
                self.start_watch(folder)
            else:                                          # no ERP folder yet: the red clock sets it up
                self.status(tr("ERP XML folder…") + " — " + tr("not set"))
            if gp is not None and not gp.running():
                gp.auto_start()
            self.status(tr("Started"))
        self._show_chain()
        self._tick()
        self.refresh_cad(force=True)

    def _erp_clock_clicked(self):
        if self.watcher is None:
            self._light_clicked("erp")
        else:
            self.poll_watch()

    def _tick(self):
        from ..services.activity import progress, snapshot
        busy = snapshot()
        pr = progress()
        if pr:                                              # a long job of many files: how far it is
            self.statusBar().showMessage(f"{tr('Reading the held orders again')}… {pr}", 2000)
        if hasattr(self, "browser"):
            self.browser.update_busy(busy)
            if set(getattr(self, "_was_busy", {})) - set(busy):
                self.refresh_cad()                         # a step just finished: show where the line is now
            self._was_busy = busy
        lab = getattr(self, "erp_clock", None)
        if lab is None:
            return
        from . import themes
        if self.watcher is None or not self.timer.isActive():
            lab.setText(tr("off"))
            lab.setToolTip(tr("ERP orders") + ": " + tr("not watching") + " — " + tr("Click: switch on / set up"))
            lab.setStyleSheet(f"color: {themes.colour('bad').name()}; font-size: 12pt; font-weight: 800;")
            return
        left = max(0, self.timer.remainingTime() // 1000)
        lab.setText(f"{left // 60:02d}:{left % 60:02d}")
        lab.setToolTip(f"{tr('ERP orders')}: {self.ws.db.get_setting('watch.folder')} — {tr('last check')}: "
                       f"{(self.ws.db.get_setting('watch.last_check') or '—').replace('T', ' ')[:19]}")
        lab.setStyleSheet(f"color: {themes.colour('accent').name()}; font-size: 12pt; font-weight: 800; "
                          f"font-family: Consolas, 'DejaVu Sans Mono', monospace;")

    def _menus(self):
        from . import themes
        from PySide6.QtWidgets import QMenu
        mb = self.menuBar()
        for a in getattr(self, "_menu_actions", []):      # the menus of before (theme / language switch)
            a.deleteLater()
        for m in mb.findChildren(QMenu):
            m.deleteLater()
        mb.clear()
        m = mb.addMenu(tr("File"))
        a = QAction(tr("Import XML files…"), self)
        a.setShortcut("Ctrl+O")
        a.triggered.connect(self.import_dialog)
        m.addAction(a)
        m.addSeparator()
        a = QAction(tr("Exit"), self)
        a.triggered.connect(self.close)
        m.addAction(a)
        for section, items in SECTIONS:
            sm = mb.addMenu(tr(section))
            for title, ic in items:
                a = QAction(f"{ic}  {tr(title)}", self)
                a.triggered.connect(lambda _=False, t=title: self.goto(t))
                sm.addAction(a)
                self.page_actions[title] = a
        v = mb.addMenu(tr("View"))
        a = QAction(tr("Marker"), self)
        a.triggered.connect(lambda: self.tabs.setCurrentIndex(0))
        v.addAction(a)
        a = QAction(tr("Arrange the icons…"), self)
        a.triggered.connect(self.customize_ribbon)
        v.addAction(a)
        a = QAction(tr("Light") if themes.name() == "dark" else tr("Dark"), self)
        a.triggered.connect(self.toggle_theme)
        v.addAction(a)
        v.addSeparator()
        for code, nm in (("ar", "العربية"), ("en", "English")):
            a = QAction(nm, self)
            a.triggered.connect(lambda _=False, c=code: self.set_lang(c))
            v.addAction(a)
        v.addSeparator()
        a = QAction(tr("Pattern developer"), self, checkable=True)
        a.setChecked(self.developer)
        a.toggled.connect(self.set_role)
        v.addAction(a)
        h = mb.addMenu(tr("Help"))
        a = QAction(tr("About"), self)
        a.triggered.connect(lambda: QMessageBox.about(self, "LEVEL7 MTM", f"LEVEL7 MTM {__version__}\nengine "
                                                      f"{ENGINE_VERSION}\nData: {self.ws.folder}"))
        h.addAction(a)
        from .updates import UpdateManager
        if getattr(self, "updates", None) is None:
            auto = getattr(sys, "frozen", False) and not os.environ.get("LEVEL7MTM_NO_UPDATE_CHECK")
            self.updates = UpdateManager(self, self.ws.db, self._before_update, auto_start=auto)
        a = QAction(tr("Check for updates…"), self)
        a.triggered.connect(lambda: self.updates.check(True))
        h.addAction(a)
        a = QAction(tr("Check for updates automatically"), self, checkable=True)
        a.setChecked(self.updates.auto_enabled())
        a.toggled.connect(self.updates.set_auto)
        h.addAction(a)
        from PySide6.QtWidgets import QMenu
        self._menu_actions = [x for m in mb.findChildren(QMenu) for x in m.actions() if x.parent() is self]

    def _statusbar(self):
        sb = self.statusBar()
        for w in getattr(self, "_sb_widgets", []):
            sb.removeWidget(w)
            w.deleteLater()
        self.sb_items = {}
        self._sb_widgets = []
        for key, text in (("grafis", tr("GRAFIS")), ("nesting", "LEVEL7 Nesting"), ("robot", tr("GRAFIS robot")),
                          ("watch", tr("Folder watch")), ("erp", tr("ERP orders"))):
            w = _ClickLabel(lambda k=key: self._light_clicked(k))
            w.setCursor(Qt.PointingHandCursor)
            w.setToolTip(tr("Click: switch on / set up"))
            w.setProperty("label", text)
            self.sb_items[key] = w
            sb.addPermanentWidget(w)
            self._sb_widgets.append(w)
        self.sb_zoom = QLabel("—")
        for w in (self.sb_zoom,):
            w.setStyleSheet("font-family: Consolas, 'DejaVu Sans Mono', monospace;")
            sb.addPermanentWidget(w)
            self._sb_widgets.append(w)

    def _light_clicked(self, key):
        gp = self.pages["GRAFIS markers"]
        if key == "erp" and self.watcher is None:
            folder = self.ws.db.get_setting("watch.folder")
            if folder and os.path.isdir(folder):
                self.start_watch(folder)
                self.refresh_cad(force=True)
                return
        if key == "watch" and not gp.running():
            gp.start_saved()
            self.refresh_cad(force=True)
            if gp.running():
                self.status(f"{tr('Folder watch')}: {tr('watching')} {self.ws.db.get_setting('grafis.watch.folder')}")
            return
        self.open_setup()

    def _status_lights(self):
        from ..services.auto_cut import NestingNotConfigured, nesting_command
        from . import themes
        db = self.ws.db
        try:
            cli = nesting_command(self.ws)[0][-1]
        except NestingNotConfigured:
            cli = None
        mdl = db.get_setting("grafis.mdl")
        rb = db.get_setting("robot.buttons", {}) or {}
        gp = self.pages.get("GRAFIS markers")
        states = {"grafis": bool(mdl and os.path.isfile(mdl)), "nesting": cli is not None,
                  "robot": bool(db.get_setting("robot.enabled", False)) and "test_run" in rb,
                  "watch": bool(gp is not None and gp.running()),
                  "erp": self.watcher is not None}
        if self.watcher is not None:
            self.sb_items["erp"].setProperty("label", f"{tr('ERP orders')} · {self.watch_interval_s()} "
                                                      f"{tr('s')}")
        for k, w in self.sb_items.items():
            col = themes.colour("ok" if states[k] else "bad").name()
            w.setText(f"<span style='color:{col}'>●</span> {w.property('label')}")
        return states

    # ---- the CAD workspace
    def _refresh_cad_bg(self):
        """The order list every few seconds, computed in the background (thousands of order lines): the window
        stays free; only the drawing happens here."""
        from . import cad
        if getattr(self, "_cad_busy", False):
            return
        self._cad_busy = True

        def done(res):
            self._cad_busy = False
            rows, sig = res
            if sig != self._cad_sig:                  # nothing changed: nothing to draw
                self._apply_rows(rows, False, sig)

        def failed(msg):
            self._cad_busy = False

        def work():
            rows = cad.order_rows(self.ws)
            return rows, json.dumps(rows, sort_keys=True, default=str)
        w = Worker(work)
        w.signals.done.connect(done)
        w.signals.failed.connect(failed)
        self.pool.start(w)

    def refresh_cad(self, force: bool = False):
        from . import cad
        try:
            rows = cad.order_rows(self.ws)
        except Exception as e:  # noqa: BLE001 - the workspace never stops the window
            self.status(f"{tr('Error')}: {e}")
            return
        self._apply_rows(rows, force)

    def _apply_rows(self, rows, force: bool, sig=None):
        self._status_lights()
        sig = sig or json.dumps(rows, sort_keys=True, default=str)
        if sig == self._cad_sig and not force:
            return
        self._cad_sig = sig
        self._rows = rows
        keep = self.sel_row["rid"] if self.sel_row else None
        self.browser.set_rows(rows, keep=keep)
        if keep is not None and not any(r["rid"] == keep for r in rows):
            self.select_row(None)

    def select_row(self, row):
        from . import cad
        self.sel_row = row
        if row is not None:
            changed = row["rid"] != self.current_rid
            self.current_rid = row["rid"]
            page = self.current_page()                    # the order page open now follows the list
            if changed and page is not None and page.title in ("Order details", "Pattern preview", "Validation"):
                self.guard(page.refresh)
        self.inspector.show_row(row)
        l7n = row.get("l7n") if row else None
        if l7n and not os.path.isfile(l7n):                 # moved (e.g. out of the cutting folder): found again
            from ..services.auto_cut import find_marker
            found = find_marker(self.ws, l7n, row.get("outputs") or ())
            if found:
                row["l7n"] = l7n = found
        try:
            key = (row["rid"], l7n, os.path.getmtime(l7n), row.get("job")) if l7n else None
        except OSError:
            key = None
        if key is not None and key == getattr(self, "_shown_key", None):
            return                     # the same marker: keep zoom, selection and measurements (list refreshed)
        self._shown_key = key
        marker = cad.load_marker(l7n) if l7n and os.path.isfile(l7n) else None
        msg = ""
        if row is not None and marker is None and l7n:      # nested, but its marker file is gone / unreadable
            msg = (tr("The marker file of this nesting could not be read" if os.path.isfile(l7n) else
                      "The marker file of this nesting is no longer there") + ":\n" + l7n + "\n\n" +
                   tr("The cutting files are kept; «Nest again» makes the marker anew"))
        elif row is not None and marker is None:
            msg = (tr("No marker yet") + " — " + tr(cad.STAGE_TEXT[row["stage"]])
                   + ("\n" + tr_msg(row["problems"][0]) if row["problems"] else ""))
            if row["stage"] in ("grafis", "nest") and not self.pages["GRAFIS markers"].running():
                msg += "\n\n⚠ " + tr("Folder watch is off: the robot and the saved GRAFIS files wait. "
                                        "Click «Folder watch» in the status bar to switch it on.")
        self.marker.set_marker(marker, row, msg)

    def _reread_held_at_start(self):
        """Once when the program opens: the held orders of every customer read again with today's settings (a
        Variation given a meaning before, the order never read since — owner: "it has a meaning and still says
        not"). In the background; the status bar says how far it is."""
        from ..services.customers import held_files, list_customers, reread_held
        if not self.isVisible() or _REREADING or not self.ws.db.get_setting("reread.at_start", True):
            return
        tops = [c["id"] for c in list_customers(self.ws) if not c.get("parent")]

        def work():
            outs = []
            for cid in tops:
                fids = held_files(self.ws, cid)
                if fids:
                    outs += reread_held(self.ws, cid, fids)
            return outs

        def done(outs):
            _REREADING.discard("*start*")
            if outs:
                self.refresh_cad(force=True)
                ok = sum(1 for o in outs for x in o.lines if x["status"] == "ready")
                self.status(f"{tr('Reading the held orders again')}: {len(outs)} XML — {tr('ready')}: {ok}")
        _REREADING.add("*start*")
        w = Worker(work)
        w.signals.done.connect(done)
        w.signals.failed.connect(lambda m: (_REREADING.discard("*start*"), self.status(m[:200])))
        self.pool.start(w)

    def _tidy_markers(self):
        from ..services.auto_cut import tidy_markers
        if self.isVisible():
            self.pool.start(Worker(lambda: tidy_markers(self.ws)))

    def toggle_nesting(self):
        """The button on the nesting queue: stop the nesting (the model being nested is finished first; the models
        CAD saves wait in the queue) or start it again (the queue is nested in turn)."""
        paused = not bool(self.ws.db.get_setting("nesting.paused", False))
        self.ws.db.set_setting("nesting.paused", paused)
        if not paused:                                    # Start means nest: also when "Nest and write cutting
            self.ws.db.set_setting("cutting.auto", True)  # files" was switched off in Setup (a hidden stop)
            self.ws.db.set_setting("grafis.last_poll_error", "")
        self.browser.nesting_paused = paused
        self.status(tr("Nesting stopped") if paused else tr("Nesting started"))
        self.refresh_cad(force=True)

    def reread_orders(self, rids: list[int]):
        """Right click → Read again: the XML of these lines is read with today's settings of its customer /
        product (after the size table, piece options … were corrected)."""
        from ..services.customers import reread_lines

        def done(res):
            outs, skipped = res
            self.refresh_cad(force=True)
            text = reread_report(self.ws, outs) if outs else ""
            if skipped:
                text += ("\n\n" if text else "") + "\n".join(tr_msg(x) for x in skipped)
            QMessageBox.information(self, tr("Read again with the current settings"), text or tr("Nothing to read"))
        self.run(lambda: reread_lines(self.ws, rids), done)

    def remove_orders(self, rids: list[int]):
        """Right click → Delete order…: the order lines leave the list (cancelled, kept in the log); their files
        are deleted too if asked."""
        from ..services.cleanup import remove_lines
        rows = {r["rid"]: r for r in getattr(self, "_rows", [])}
        names = ", ".join(str(rows[r]["line"]) if r in rows else str(r) for r in rids[:6]) + \
            (" …" if len(rids) > 6 else "")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("Delete order…"))
        box.setText(f"{tr('Delete from the order list')}: {names}?")
        box.setInformativeText(tr("The order leaves the list and stays in the log; its XML is moved to the folder "
                                  "'deleted' in the ERP folder — put it back in the ERP folder to read it again. "
                                  "Its files can be deleted too (order model, PLT / ISO / DXF / L7N)."))
        only = box.addButton(tr("Delete from the list only"), QMessageBox.AcceptRole)
        files = box.addButton(tr("Delete with its files"), QMessageBox.DestructiveRole)
        box.addButton(tr("Cancel"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() not in (only, files):
            return
        res = self.guard(lambda: remove_lines(self.ws, rids, delete_files=box.clickedButton() is files))
        if res is None:
            return
        self.sel_row = None
        self.refresh_cad(force=True)
        msg = f"{tr('Deleted')}: {res['lines']}"
        if res["deleted"]:
            msg += f" — {tr('files deleted')}: {len(res['deleted'])}"
        if res["failed"]:
            msg += "\n" + tr("Not deleted (open in another program?)") + ":\n" + "\n".join(res["failed"][:8])
        self.status(msg)
        if res["failed"]:
            QMessageBox.warning(self, tr("Delete order…"), msg)

    def _need_row(self):
        if self.sel_row is None:
            QMessageBox.information(self, "LEVEL7 MTM", tr("Choose an order line in the browser"))
        return self.sel_row

    def open_cutting(self):
        import subprocess
        r = self.sel_row
        if r and r["outputs"] and os.path.isfile(r["outputs"][0]):
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(r["outputs"][0])])
            else:
                QMessageBox.information(self, tr("Cutting files"), "\n".join(r["outputs"]))
        else:
            self.pages["GRAFIS markers"].open_out()

    def renest_selected(self):
        """Nest again (owner): never from the copy CAD saved before — the model of each order chosen is written
        again from the base pattern with today's settings (a new number), CAD saves it, then it is nested."""
        rids = self.browser.selected_rids() if hasattr(self, "browser") else []
        if not rids:
            r = self._need_row()
            if r is None:
                return
            rids = [r["rid"]]
        if QMessageBox.question(self, tr("Nest again"), tr("Nest again from the base pattern?") + f" ({len(rids)})\n\n"
                                + tr("The model of each order chosen is written again from the base pattern with "
                                     "today's settings (a new number _v2, _v3 …), CAD saves it and it is nested; the "
                                     "copy saved before is kept in the archive.")) != QMessageBox.Yes:
            return
        self.reread_orders(rids)

    def robot_retry_selected(self):
        """Let the robot take the selected order's model again (after GRAFIS was closed / a dialog answered)."""
        r = self._need_row()
        if r is None or not r.get("mdl"):
            return
        name = os.path.basename(r["mdl"])
        if " " in name or not name.isascii():            # written before names were made plain: write it anew
            self.write_model(True)
            return
        with self.ws.db.tx() as c:
            c.execute("DELETE FROM robot_jobs WHERE path=? AND status<>'done'", (r["mdl"],))
            self.ws.db.audit(c, self.ws.actor, "robot.retry", "file", os.path.basename(r["mdl"]))
        self.refresh_cad(force=True)
        self.status(tr("The robot takes the model again in a few seconds"))

    def nesting_selected(self):
        r = self._need_row()
        if r is not None and r["result"]:
            self.pages["GRAFIS markers"].show_marker(r["result"])
        elif r is not None:
            QMessageBox.information(self, "LEVEL7 Nesting", tr("This row has no marker yet"))

    def write_model(self, force=False):
        """Write the GRAFIS model of the order selected in the browser and say what happened."""
        from ..services.grafis_orders import order_mdl
        r = self.sel_row
        if r is None:
            self.pages["GRAFIS markers"].write_mdl()
            return
        rid, line = r["rid"], r["line"]
        self.run(lambda: order_mdl(self.ws, rid, force=force is True),
                 lambda g: (self.refresh_cad(force=True), self.pages["GRAFIS markers"].refresh(),
                            self.model_result(line, rid, g)))

    def model_result(self, line, rid, g):
        """The outcome of writing a GRAFIS model, with the next step as buttons."""
        from ..services.grafis_orders import order_mdl
        box = QMessageBox(self)
        box.setWindowTitle(tr("Write model"))
        path = g.get("path") or ""
        if g["status"] == "written":
            box.setIcon(QMessageBox.Information)
            box.setText(f"{tr('The GRAFIS model of the order line was written')}: {line}\n\n{path}\n\n"
                        f"{tr('Next: GRAFIS calculates the pieces and saves (robot or by hand).')}")
        elif g["status"] == "duplicate":
            box.setIcon(QMessageBox.Information)
            box.setText(f"{tr('This model was already written with the same values; it is waiting for GRAFIS')}:"
                        f"\n\n{path}")
        else:
            box.setIcon(QMessageBox.Warning)
            box.setText(f"{tr('The model was not written')} ({tr(g['status'])}): {line}\n\n"
                        + "\n".join("• " + p for p in g.get("problems", [])[:12]))
        again = box.addButton(tr("Write again"), QMessageBox.ActionRole) if g["status"] == "duplicate" else None
        opened = box.addButton(tr("Open in GRAFIS"), QMessageBox.ActionRole) if path and os.path.isfile(path) else None
        robot = box.addButton(tr("Robot: run now"), QMessageBox.ActionRole) if g["status"] in ("written", "duplicate") \
            else None
        box.addButton(tr("Close"), QMessageBox.RejectRole)
        box.exec()
        b = box.clickedButton()
        if b is None:
            return
        if b is again:
            self.run(lambda: order_mdl(self.ws, rid, force=True),
                     lambda g2: (self.refresh_cad(force=True), self.model_result(line, rid, g2)))
        elif b is opened:
            if hasattr(os, "startfile"):
                os.startfile(path)                   # .mdl opens in GRAFIS
            else:
                QMessageBox.information(self, tr("GRAFIS"), path)
        elif b is robot:
            self.pages["GRAFIS markers"].robot_now()

    def open_details(self):
        if self.sel_row is not None:
            self.current_rid = self.sel_row["rid"]
        self.goto("Order details")

    def open_setup(self):
        self.goto("GRAFIS markers")
        self.pages["GRAFIS markers"].settings_btn.setChecked(True)

    # ---- tabs
    def _close_tab(self, i):
        if i > 0:
            self.tabs.removeTab(i)

    def _tab_changed(self, i):
        p = self.current_page()
        if p is not None:
            self.guard(p.refresh)
        elif i == 0:
            self.refresh_cad()

    def current_page(self):
        w = self.tabs.currentWidget() if hasattr(self, "tabs") else None
        return next((self.pages[t] for t, s in self.wrappers.items() if s is w), None)

    def goto(self, title):
        if title in CustomersPage.INNER and title != "patterns":   # set up with the customer, in one place
            self.goto("Customers")
            self.pages["Customers"].show_tab(title)
            return
        w = self.wrappers[title]
        if self.tabs.indexOf(w) < 0:
            self.tabs.addTab(w, tr(title))
        if self.tabs.currentWidget() is w:
            self.guard(self.pages[title].refresh)
        self.tabs.setCurrentWidget(w)

    def toggle_theme(self):
        from PySide6.QtWidgets import QApplication
        from . import themes
        new = "light" if themes.name() == "dark" else "dark"
        self.ws.db.set_setting("ui.theme", new)
        themes.apply(QApplication.instance(), new)
        self._rebuild()

    def _rebuild(self):
        open_ = [t for t, s in self.wrappers.items() if self.tabs.indexOf(s) >= 0]
        cur = self.current_page()
        cur = cur.title if cur is not None else None
        for p in self.pages.values():
            if getattr(p, "timer", None) is not None:
                p.timer.stop()
        old = self.pages.get("GRAFIS markers")
        kept = {k: getattr(old, k, None) for k in ("watcher", "mdl_watcher")} if old is not None else {}
        self.build()
        new = self.pages.get("GRAFIS markers")
        for k, w in kept.items():                         # what the folders already handled stays handled
            nw = getattr(new, k, None)
            if w is not None and nw is not None and os.path.normcase(w.folder) == os.path.normcase(nw.folder):
                nw.state = w.state
        for t in open_:
            self.goto(t)
        if cur:
            self.goto(cur)
        else:
            self.tabs.setCurrentIndex(0)

    def closeEvent(self, e):
        self.updates.release()
        self.cad_timer.stop()
        for p in self.pages.values():                     # no new background work; the running one may finish
            if getattr(p, "timer", None) is not None:
                p.timer.stop()
        if getattr(self, "timer", None) is not None:
            self.timer.stop()
        self.pool.waitForDone(15000)
        super().closeEvent(e)

    def _before_update(self) -> bool:
        """Before the installer replaces the program: background work (nesting, import) must have finished."""
        if self.pool.activeThreadCount():
            if QMessageBox.question(self, tr("Updates"), tr("Work is still running. Wait for it to finish and "
                                                            "then install?")) != QMessageBox.Yes:
                return False
            self.pool.waitForDone(600000)
        for p in self.pages.values():
            if getattr(p, "timer", None) is not None:
                p.timer.stop()
        for t in (getattr(self, "timer", None), getattr(self, "cad_timer", None)):
            if t is not None:
                t.stop()
        return True

    def _resume_after_update(self):
        """The installer did not start: everything stopped for it runs again."""
        g = self.pages.get("GRAFIS markers")
        if g is not None and (g.watcher or g.mdl_watcher):
            g.timer.start(2000)
        if self.watcher:
            self.timer.start(self.watch_interval_s() * 1000)
        self.cad_timer.start(5000)

    def apply_role(self):
        for title, a in self.page_actions.items():
            a.setVisible(not (_BY_TITLE[title].developer_only and not self.developer))
        if not self.developer:
            for title, s in self.wrappers.items():
                i = self.tabs.indexOf(s)
                if i > 0 and _BY_TITLE[title].developer_only:
                    self.tabs.removeTab(i)
        self.statusBar().showMessage(f"{tr('Role')}: {tr('Pattern developer') if self.developer else tr('Operator')}")

    def set_role(self, dev):
        self.developer = dev
        self.ws.db.set_setting("ui.developer", dev)
        self.apply_role()
        if self.current_page():
            self.current_page().refresh()

    def set_lang(self, code):
        from PySide6.QtWidgets import QApplication
        set_language(code)
        self.ws.db.set_setting("ui.language", code)
        QApplication.instance().setLayoutDirection(Qt.RightToLeft if code == "ar" else Qt.LeftToRight)
        self._rebuild()

    def open_revision(self, rid, page="Order details"):
        self.current_rid = rid
        self.goto(page)

    def status(self, text):
        self.statusBar().showMessage(text, 10000)

    def model_again(self, rid: int):
        """An order corrected by hand: its model written again from the base pattern (a new number) — CAD saves it
        and it is nested again, also when it was nested or cut before."""
        from ..services.grafis_orders import model_again

        def done(g):
            self.refresh_cad(force=True)
            if g and g.get("path"):
                self.status(f"{tr('Model written again')}: {os.path.basename(g['path'])}")
            elif g and g.get("problems"):
                self.status(tr_msg(g["problems"][0])[:200])
        self.run(lambda: model_again(self.ws, rid), done)

    def guard(self, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - errors are shown, not hidden
            QMessageBox.warning(self, tr("Error"), str(e))
            return None

    def run(self, fn, on_done):
        self.status(tr("Working…"))
        w = Worker(fn)

        def done(res):
            self.status(tr("Done"))
            try:
                on_done(res)
            except RuntimeError as e:                   # the page that asked was made anew meanwhile
                if "already deleted" not in str(e):
                    raise
        w.signals.done.connect(done)
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
                    page.add_log(f"  {tr('GRAFIS')} {g['line_number']}: {tr(g['status'])} "
                                 f"{os.path.basename(g['path'] or '')} {'; '.join(g['problems'])}")
            page.refresh()
            self.refresh_cad()
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

    def watch_interval_s(self) -> int:
        """How often the ERP folder is checked for new XML orders (seconds, set by the owner)."""
        v = self.ws.db.get_setting("watch.interval_s")
        if v is None:                                        # set in minutes by an earlier version
            v = int(self.ws.db.get_setting("watch.interval_min", 1) or 1) * 60
        return max(5, int(v))

    def set_watch_interval(self, seconds: int):
        self.ws.db.set_setting("watch.interval_s", max(5, int(seconds)))
        if self.watcher:
            self.timer.start(self.watch_interval_s() * 1000)
        for w in (getattr(self, "erp_spin", None), getattr(self.pages.get("GRAFIS markers"), "erp_every", None)):
            if w is not None and w.value() != self.watch_interval_s():
                w.blockSignals(True)
                w.setValue(self.watch_interval_s())
                w.blockSignals(False)
        self._tick()

    def start_watch(self, folder):
        from ..services.grafis_orders import import_and_prepare
        sub = next((os.path.join(folder, e) for e in (os.listdir(folder) if os.path.isdir(folder) else [])
                    if e.lower() == "process" and os.path.isdir(os.path.join(folder, e))), None)
        if sub:                                              # the main folder chosen: XML are read from process
            folder = sub
        self.watcher = FolderWatcher(self.ws, folder, settle_seconds=3, handler=lambda p: import_and_prepare(self.ws, p),
                                     known=self.ws.already_read)     # files read before stay read
        self.ws.db.set_setting("watch.folder", folder)
        self.ws.db.set_setting("watch.enabled", True)
        from ..services.done_folder import sweep_finished, sweep_held
        moved = self.guard(lambda: sweep_finished(self.ws)) or []       # finished before: on to done now
        self.guard(lambda: sweep_held(self.ws))                          # needs attention: into error
        if moved:
            self.status(f"{tr('Finished XML (done)')}: {len(moved)}")
        self.timer.start(self.watch_interval_s() * 1000)
        self.status(f"{tr('Watch folder')}: {folder}")
        QTimer.singleShot(500, self.poll_watch)              # look at once, then on the owner's timer
        self._tick()

    ERP_BATCH = 25                     # XML files per look: 500 at once go in turns, the window stays free

    def poll_watch(self):
        """Look at the ERP folder in the background (reading an XML also writes its GRAFIS models): a big drop of
        files never freezes the window; they are taken ERP_BATCH at a time, one batch right after the other."""
        if not self.watcher or getattr(self, "_polling", False):
            return                                            # never twice at once (e.g. under an error box)
        self._polling = True
        w = self.watcher

        def done(outs):
            self._polling = False
            self._after_poll(w, outs or [])

        def failed(msg):
            self._polling = False
            self.status(msg[:200])
        job = Worker(lambda: w.poll(limit=self.ERP_BATCH))
        job.signals.done.connect(done)
        job.signals.failed.connect(failed)
        self.pool.start(job)

    def _after_poll(self, w, outs):
        from .. storage.db import now
        self.ws.db.set_setting("watch.last_check", now())
        if w is not self.watcher:                             # stopped / changed meanwhile
            return
        if w.more:                                            # files left over from a big drop: next batch now
            QTimer.singleShot(200, self.poll_watch)
        elif w.state.seen and not getattr(self, "_settle_pending", False):
            self._settle_pending = True                       # a new file still being copied: look again in 4 s,
            QTimer.singleShot(4000, self._settled_poll)       # not only at the next timer
        if outs:
            self.refresh_cad()
            page = self.pages["Orders"]
            for o in outs:
                page.add_log(f"[watch] {o.file_name}: {tr(o.status)} {o.message}")
                for g in o.grafis:
                    page.add_log(f"  {tr('GRAFIS')} {g['line_number']}: {tr(g['status'])} "
                                 f"{os.path.basename(g['path'] or '')} {'; '.join(g['problems'])}")
            page.refresh()

    def _settled_poll(self):
        self._settle_pending = False
        self.poll_watch()

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
            if self.current_page():
                self.current_page().refresh()
