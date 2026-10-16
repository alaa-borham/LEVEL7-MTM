"""Online update: background check at start-up and every few hours, download with progress, silent install.
Ported from LEVEL7 Nesting. Settings: ``updates.auto`` (default on) and ``updates.skip`` (a version not wanted)."""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox, QProgressDialog

from .. import __version__, updater
from .i18n import tr

CHECK_INTERVAL_MS = 6 * 3600 * 1000
FIRST_CHECK_DELAY_MS = 5000


class CheckThread(QThread):
    done = Signal(object, str)          # (UpdateInfo | None, error text)

    def run(self):
        try:
            self.done.emit(updater.check_latest(), "")
        except Exception as exc:  # noqa: BLE001 - offline etc., reported to the caller
            self.done.emit(None, str(exc))


class DownloadThread(QThread):
    progress = Signal(int, int)
    done = Signal(str, str)             # (path, error text)

    def __init__(self, info):
        super().__init__()
        self.info, self.cancel = info, False

    def run(self):
        try:
            p = updater.download(self.info, progress=lambda d, t: self.progress.emit(d, t),
                                 cancelled=lambda: self.cancel)
            self.done.emit(p, "")
        except Exception as exc:  # noqa: BLE001
            self.done.emit("", str(exc))


class UpdateManager(QObject):
    """Owned by the main window. ``before_install()`` lets the window finish work; returns False to abort."""

    def __init__(self, window, db, before_install, auto_start: bool = True):
        super().__init__(window)
        self.w, self.db, self.before_install = window, db, before_install
        self.thread = self.dl = None
        self.manual = False
        self.notified: set[str] = set()
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.check(False))
        if auto_start:
            if self.auto_enabled():
                QTimer.singleShot(FIRST_CHECK_DELAY_MS, lambda: self.check(False))
            self.timer.start(CHECK_INTERVAL_MS)

    def auto_enabled(self) -> bool:
        return bool(self.db.get_setting("updates.auto", True))

    def set_auto(self, on: bool):
        self.db.set_setting("updates.auto", bool(on))

    def check(self, manual: bool):
        if (not manual and not self.auto_enabled()) or (self.thread and self.thread.isRunning()) or self.dl:
            return
        self.manual = manual
        if manual:
            self.w.statusBar().showMessage(tr("Checking for updates…"))
        self.thread = CheckThread()
        self.thread.done.connect(self._checked)
        self.thread.start()

    def _checked(self, info, err):
        manual, self.manual = self.manual, False
        if err:                                  # offline etc.: silent for automatic checks
            if manual:
                QMessageBox.warning(self.w, tr("Updates"), f"{tr('Update failed')}: {err}")
            return
        if info is None:
            if manual:
                QMessageBox.information(self.w, tr("Updates"), f"{tr('LEVEL7 MTM is up to date')} ({__version__})")
            return
        if not manual and (info.version == self.db.get_setting("updates.skip", "") or info.version in self.notified):
            return
        self.notified.add(info.version)
        self.w.statusBar().showMessage(f"{tr('New version available')}: {info.version}")
        self._notify(info)

    def _notify(self, info):
        box = QMessageBox(self.w)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(tr("Updates"))
        box.setText(f"{tr('New version available')}: {info.version}\n{tr('Installed version')}: {__version__}")
        box.setInformativeText(tr("The update is downloaded, checked and installed; LEVEL7 MTM then starts again. "
                                  "Orders, settings and rules are kept."))
        if info.notes:
            box.setDetailedText(info.notes[:1500] + ("…" if len(info.notes) > 1500 else ""))
        install = box.addButton(tr("Install now"), QMessageBox.AcceptRole)
        box.addButton(tr("Later"), QMessageBox.RejectRole)
        skip = box.addButton(tr("Skip this version"), QMessageBox.DestructiveRole)
        auto = QCheckBox(tr("Check for updates automatically"))
        auto.setChecked(self.auto_enabled())
        box.setCheckBox(auto)
        box.exec()
        self.set_auto(auto.isChecked())
        if box.clickedButton() is skip:
            self.db.set_setting("updates.skip", info.version)
        elif box.clickedButton() is install:
            self._download(info)

    def _download(self, info):
        self.dl = DownloadThread(info)
        dlg = QProgressDialog(f"{tr('Downloading')} {info.version}…", tr("Cancel"), 0, 100, self.w)
        dlg.setWindowTitle(tr("Updates"))
        dlg.setMinimumDuration(0)
        dl = self.dl
        dlg.canceled.connect(lambda: setattr(dl, "cancel", True))

        def prog(d, t):
            dlg.setMaximum(t or 0)
            dlg.setValue(d if t else 0)
        self.dl.progress.connect(prog)
        self.dl.done.connect(lambda p, e: self._downloaded(dlg, p, e))
        self.dl.start()

    def _downloaded(self, dlg, path, err):
        canceled = self.dl.cancel
        self.dl = None
        dlg.close()
        if err:
            if not canceled:
                QMessageBox.warning(self.w, tr("Updates"), f"{tr('Update failed')}: {err}")
            return
        if not self.before_install():
            return
        try:
            updater.start_installer(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self.w, tr("Updates"), f"{tr('Update failed')}: {exc}")
            return
        QApplication.instance().quit()

    def release(self):
        """Window closes: stop the timer; wait briefly for a running check / download."""
        self.timer.stop()
        for t in (self.thread, self.dl):
            if t is not None and t.isRunning():
                t.wait(1500)
