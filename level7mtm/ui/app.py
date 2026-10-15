"""Desktop entry point."""
from __future__ import annotations

import os
import sys


def default_data_folder() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "LEVEL7 MTM")


def main(argv=None) -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ..services.workflow import Workspace
    from .i18n import set_language
    from .main_window import MainWindow

    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("LEVEL7 MTM")
    folder = os.environ.get("LEVEL7MTM_DATA") or default_data_folder()
    try:
        ws = Workspace(folder, actor=os.environ.get("USERNAME") or os.environ.get("USER") or "operator")
    except Exception as e:  # noqa: BLE001
        QMessageBox.critical(None, "LEVEL7 MTM", f"Cannot open data folder {folder}:\n{e}")
        return 1
    lang = ws.db.get_setting("ui.language", "ar")
    set_language(lang)
    app.setLayoutDirection(Qt.RightToLeft if lang == "ar" else Qt.LeftToRight)
    win = MainWindow(ws)
    win.show()
    if "--smoke-test" in argv:          # used by the installer test: start, render, quit
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
