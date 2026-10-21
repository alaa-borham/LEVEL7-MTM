"""LEVEL7 MTM look: a CAD workstation — deep navy, vivid cyan accent, sharp contrast and sharp corners (dark, the
default), and a light variant of the same family. The Cairo typeface is shipped with the program."""
from __future__ import annotations

import os

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

THEMES = {
    "dark": dict(window=(9, 17, 25), panel=(13, 24, 34), base=(8, 15, 22), text=(242, 247, 250),
                 muted=(136, 160, 178), button=(19, 33, 46), border=(31, 54, 72), sidebar=(5, 11, 17),
                 card=(14, 26, 37), ribbon=(12, 22, 32), canvas=(10, 19, 28), grid=(20, 36, 50), grid2=(30, 52, 70),
                 fabric=(18, 33, 46), selvedge=(84, 118, 140), accent=(0, 194, 255), on_accent=(0, 18, 28),
                 piece=(0, 194, 255), label=(225, 248, 255), ok=(0, 230, 118), warn=(255, 179, 0), bad=(255, 61, 87)),
    "light": dict(window=(238, 242, 246), panel=(250, 252, 254), base=(255, 255, 255), text=(9, 22, 34),
                  muted=(84, 104, 122), button=(232, 238, 244), border=(196, 210, 222), sidebar=(13, 27, 40),
                  card=(255, 255, 255), ribbon=(246, 249, 252), canvas=(244, 248, 251), grid=(222, 231, 238),
                  grid2=(200, 214, 225), fabric=(255, 255, 255), selvedge=(120, 150, 170), accent=(0, 132, 199),
                  on_accent=(255, 255, 255), piece=(0, 132, 199), label=(6, 40, 64), ok=(0, 160, 80),
                  warn=(214, 140, 0), bad=(224, 36, 64)),
}
# row colours per status: (background, text) — strong tints on dark, clear pastels on light
STATUS = {
    "dark": {"ok": ((0, 64, 40), (150, 255, 200)), "review": ((80, 56, 0), (255, 214, 120)),
             "blocking": ((92, 14, 30), (255, 170, 184)), "info": ((0, 52, 80), (150, 226, 255)),
             "neutral": ((22, 36, 48), (214, 226, 234))},
    "light": {"ok": ((204, 246, 222), (0, 70, 36)), "review": ((255, 236, 176), (96, 60, 0)),
              "blocking": ((255, 210, 218), (128, 0, 24)), "info": ((206, 238, 255), (0, 56, 96)),
              "neutral": ((236, 241, 245), (30, 44, 56))},
}
CURRENT = {"name": "dark"}
FAMILY = {"name": "Segoe UI"}
FONTS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources", "fonts")


def name() -> str:
    return CURRENT["name"]


def colours(theme: str | None = None) -> dict:
    return {k: QColor(*v) for k, v in THEMES[theme or CURRENT["name"]].items()}


def colour(key: str) -> QColor:
    return QColor(*THEMES[CURRENT["name"]][key])


def status_colours(kind: str) -> tuple[QColor, QColor]:
    bg, fg = STATUS[CURRENT["name"]].get(kind, STATUS[CURRENT["name"]]["neutral"])
    return QColor(*bg), QColor(*fg)


def _rgb(c: QColor, a: float | None = None) -> str:
    return f"rgba({c.red()},{c.green()},{c.blue()},{a})" if a is not None else f"rgb({c.red()},{c.green()},{c.blue()})"


def load_fonts() -> str:
    """Register the shipped Cairo typeface; returns the family to use (Segoe UI if it cannot be loaded)."""
    if FAMILY["name"] == "Cairo":
        return "Cairo"
    ok = False
    if os.path.isdir(FONTS):
        for f in sorted(os.listdir(FONTS)):
            if f.lower().endswith(".ttf"):
                ok = QFontDatabase.addApplicationFont(os.path.join(FONTS, f)) >= 0 or ok
    if ok:
        FAMILY["name"] = "Cairo"
    return FAMILY["name"]


def stylesheet(c: dict) -> str:
    acc, txt, mut, on = c["accent"], c["text"], c["muted"], c["on_accent"]
    return f"""
QMainWindow, QDialog {{ background: {_rgb(c['window'])}; }}
QWidget {{ color: {_rgb(txt)}; }}
QToolTip {{ background: {_rgb(c['panel'])}; color: {_rgb(txt)}; border: 1px solid {_rgb(acc)}; padding: 4px 7px; }}
QMenuBar {{ background: {_rgb(c['sidebar'])}; color: rgb(234,242,248); padding: 1px 4px; border-bottom: 1px solid {_rgb(c['border'])}; }}
QMenuBar::item {{ padding: 4px 11px; background: transparent; border-radius: 2px; }}
QMenuBar::item:selected {{ background: {_rgb(acc)}; color: {_rgb(on)}; }}
QMenu {{ background: {_rgb(c['panel'])}; border: 1px solid {_rgb(c['border'])}; padding: 4px; }}
QMenu::item {{ padding: 5px 22px 5px 14px; border-radius: 2px; }}
QMenu::item:selected {{ background: {_rgb(acc)}; color: {_rgb(on)}; }}
QMenu::item:disabled {{ color: {_rgb(mut, 0.6)}; }}
QMenu::separator {{ height: 1px; background: {_rgb(c['border'])}; margin: 4px 6px; }}

QPushButton {{ background: {_rgb(c['button'])}; border: 1px solid {_rgb(c['border'])}; border-radius: 3px;
              padding: 6px 13px; color: {_rgb(txt)}; }}
QPushButton:hover {{ border-color: {_rgb(acc)}; }}
QPushButton:pressed {{ background: {_rgb(acc, 0.30)}; }}
QPushButton:checked {{ border-color: {_rgb(acc)}; background: {_rgb(acc, 0.18)}; }}
QPushButton:disabled {{ color: {_rgb(mut, 0.6)}; }}
QPushButton#primary {{ background: {_rgb(acc)}; color: {_rgb(on)}; border-color: {_rgb(acc)}; font-weight: 700; }}
QPushButton#primary:hover {{ background: {_rgb(acc.lighter(115))}; }}
QPushButton#primary:disabled {{ background: {_rgb(acc, 0.18)}; color: {_rgb(mut, 0.7)}; border-color: {_rgb(acc, 0.25)}; }}
QPushButton#quick {{ background: {_rgb(c['card'])}; padding: 12px 14px; text-align: left; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
    background: {_rgb(c['base'])}; border: 1px solid {_rgb(c['border'])}; border-radius: 2px;
    padding: 3px 6px; selection-background-color: {_rgb(acc)}; selection-color: {_rgb(on)}; color: {_rgb(txt)}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {_rgb(acc)}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {_rgb(c['panel'])}; border: 1px solid {_rgb(acc)};
    selection-background-color: {_rgb(acc)}; selection-color: {_rgb(on)}; }}
QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 2px; border: 1px solid {_rgb(mut)};
    background: {_rgb(c['base'])}; }}
QCheckBox::indicator:checked {{ background: {_rgb(acc)}; border-color: {_rgb(acc)}; }}

QTableWidget, QTableView, QTreeWidget, QListWidget {{ background: {_rgb(c['base'])}; border: 1px solid {_rgb(c['border'])};
    gridline-color: {_rgb(c['border'])}; selection-background-color: {_rgb(acc, 0.35)}; selection-color: {_rgb(txt)};
    alternate-background-color: {_rgb(c['panel'])}; outline: 0; }}
QHeaderView::section {{ background: {_rgb(c['panel'])}; color: {_rgb(mut)}; border: none;
    border-bottom: 1px solid {_rgb(c['border'])}; padding: 5px 7px; font-weight: 700; }}
QTableCornerButton::section {{ background: {_rgb(c['panel'])}; border: none; }}
QTabWidget::pane {{ border: 1px solid {_rgb(c['border'])}; top: -1px; }}
QTabBar::tab {{ background: {_rgb(c['button'])}; padding: 6px 16px; border: 1px solid {_rgb(c['border'])};
    border-bottom: none; color: {_rgb(mut)}; margin: 3px 2px 0 2px; border-top-left-radius: 6px;
    border-top-right-radius: 6px; }}
QTabBar::tab:selected {{ background: {_rgb(c['canvas'])}; color: {_rgb(acc)}; border: 1px solid {_rgb(acc)};
    border-top: 3px solid {_rgb(acc)}; border-bottom: none; font-weight: 800; margin-top: 0; }}
QTabBar::tab:!selected:hover {{ color: {_rgb(txt)}; background: {_rgb(c['card'])}; }}
QTabBar::close-button {{ subcontrol-position: right; }}

QScrollBar:vertical {{ background: {_rgb(c['window'])}; width: 11px; margin: 0; }}
QScrollBar:horizontal {{ background: {_rgb(c['window'])}; height: 11px; margin: 0; }}
QScrollBar::handle {{ background: {_rgb(c['grid2'])}; min-height: 24px; min-width: 24px; border: 2px solid {_rgb(c['window'])}; }}
QScrollBar::handle:hover {{ background: {_rgb(acc)}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QStatusBar {{ background: {_rgb(c['sidebar'])}; color: rgb(170,190,205); border-top: 1px solid {_rgb(c['border'])}; }}
QStatusBar QLabel {{ color: rgb(170,190,205); padding: 0 8px; }}
QSplitter::handle {{ background: {_rgb(c['border'])}; }}
QDockWidget {{ color: {_rgb(txt)}; titlebar-close-icon: none; }}
QDockWidget::title {{ background: {_rgb(c['panel'])}; padding: 6px 10px; border-bottom: 1px solid {_rgb(c['border'])}; }}

/* ribbon */
QFrame#ribbon {{ background: {_rgb(c['ribbon'])}; border-bottom: 1px solid {_rgb(c['border'])}; }}
QFrame#ribbonGroup {{ }}
QLabel#ribbonTitle {{ color: {_rgb(mut)}; font-size: 8pt; letter-spacing: 1px; }}
QToolButton#ribbonButton {{ background: transparent; border: 1px solid transparent; border-radius: 2px; padding: 3px 6px;
    color: {_rgb(txt)}; font-size: 8.5pt; }}
QToolButton#ribbonButton:hover {{ border-color: {_rgb(acc)}; background: {_rgb(acc, 0.12)}; }}
QToolButton#ribbonButton::menu-indicator {{ image: none; width: 0px; }}
QToolButton#ribbonButton[hasMenu="true"] {{ padding-bottom: 9px; }}
QToolButton#ribbonButton:pressed {{ background: {_rgb(acc, 0.30)}; }}
QToolButton#ribbonButton:disabled {{ color: {_rgb(mut, 0.5)}; }}
QLabel#brand {{ font-size: 14pt; font-weight: 800; letter-spacing: 2px; }}
QLabel#brandSub {{ color: {_rgb(acc)}; font-size: 8pt; font-weight: 700; letter-spacing: 2px; }}
QLabel#brandMark {{ background: {_rgb(acc)}; color: {_rgb(on)}; font-weight: 800; font-size: 13pt; border-radius: 2px; }}

/* CAD panels */
QFrame#panelHead {{ background: {_rgb(c['panel'])}; border-bottom: 1px solid {_rgb(c['border'])}; }}
QLabel#panelTitle {{ font-weight: 700; font-size: 9pt; letter-spacing: 1px; color: {_rgb(txt)}; }}
QLabel#panelCount {{ color: {_rgb(acc)}; font-weight: 700; }}
QWidget#browser, QWidget#inspector {{ background: {_rgb(c['panel'])}; }}
QTreeWidget#orders {{ background: {_rgb(c['panel'])}; border: none; font-size: 9.5pt; }}
QTreeWidget#orders::item {{ padding: 3px 2px; }}
QTreeWidget#orders::item:selected {{ background: {_rgb(acc, 0.25)}; color: {_rgb(txt)}; }}
QLabel#secTitle {{ color: {_rgb(acc)}; font-size: 8pt; font-weight: 700; letter-spacing: 1px; padding-top: 6px; }}
QLabel#kvKey {{ color: {_rgb(mut)}; }}
QLabel#kvVal {{ font-weight: 700; }}
QLabel#bigTitle {{ font-size: 13pt; font-weight: 800; }}
QLabel#pill {{ border: 1px solid; border-radius: 2px; padding: 1px 8px; font-weight: 700; font-size: 8.5pt; }}
QFrame#hline {{ background: {_rgb(c['border'])}; max-height: 1px; min-height: 1px; }}
QFrame#tools, QFrame#hud {{ background: {_rgb(c['panel'], 0.94)}; border: 1px solid {_rgb(c['border'])}; border-radius: 3px; }}
QToolButton#tool {{ background: transparent; border: 1px solid transparent; border-radius: 2px; padding: 4px; }}
QToolButton#tool:hover {{ border-color: {_rgb(acc)}; }}
QToolButton#tool:checked {{ background: {_rgb(acc, 0.22)}; border-color: {_rgb(acc)}; }}
QLabel#hudValue {{ font-size: 13pt; font-weight: 800; }}
QLabel#hudValueAcc {{ font-size: 13pt; font-weight: 800; color: {_rgb(acc)}; }}
QLabel#hudLabel {{ color: {_rgb(mut)}; font-size: 8pt; }}
QProgressBar#busyBar {{ background: {_rgb(c['border'])}; border: none; border-radius: 3px; }}
QProgressBar#busyBar::chunk {{ background: {_rgb(acc)}; border-radius: 3px; }}
QLabel#busyText {{ color: {_rgb(acc)}; font-size: 8pt; font-weight: 700; }}
QLabel#sbDotOk {{ color: {_rgb(c['ok'])}; }}
QLabel#sbDotBad {{ color: {_rgb(c['bad'])}; }}

/* pages, cards, steps */
QLabel#pageTitle {{ font-size: 15pt; font-weight: 800; }}
QLabel#pageSub {{ color: {_rgb(mut)}; font-size: 9.5pt; }}
QFrame#card {{ background: {_rgb(c['card'])}; border: 1px solid {_rgb(c['border'])}; border-radius: 3px; }}
QLabel#cardValue {{ font-size: 22pt; font-weight: 800; color: {_rgb(acc)}; }}
QLabel#cardLabel {{ color: {_rgb(mut)}; font-size: 9pt; }}
QLabel#cardTitle {{ font-size: 10.5pt; font-weight: 700; }}
QFrame#step {{ background: {_rgb(c['panel'])}; border: 1px solid {_rgb(c['border'])}; border-radius: 3px; }}
QLabel#stepBadge {{ background: transparent; color: {_rgb(acc)}; border: 2px solid {_rgb(acc)};
    border-radius: 17px; font-weight: 800; font-size: 11pt; }}
QLabel#stepBadge[done="true"] {{ background: {_rgb(c['ok'])}; color: {_rgb(on)}; border-color: {_rgb(c['ok'])}; }}
QLabel#dotOk {{ color: {_rgb(c['ok'])}; font-size: 13pt; }}
QLabel#dotBad {{ color: {_rgb(c['bad'])}; font-size: 13pt; }}
QLabel#dotWarn {{ color: {_rgb(c['warn'])}; font-size: 13pt; }}
"""


def apply(app: QApplication, theme: str = "dark") -> None:
    """Fusion palette + style sheet + typeface for the whole application."""
    CURRENT["name"] = theme if theme in THEMES else "dark"
    c = colours()
    app.setStyle("Fusion")
    pal = QPalette()
    for role, key in ((QPalette.Window, "window"), (QPalette.Base, "base"), (QPalette.AlternateBase, "panel"),
                      (QPalette.Button, "button"), (QPalette.ToolTipBase, "panel")):
        pal.setColor(role, c[key])
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.ToolTipText,
                 QPalette.BrightText):
        pal.setColor(role, c["text"])
    pal.setColor(QPalette.PlaceholderText, c["muted"])
    pal.setColor(QPalette.Highlight, c["accent"])
    pal.setColor(QPalette.HighlightedText, c["on_accent"])
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, c["muted"])
    app.setPalette(pal)
    f = QFont(load_fonts(), 9)
    f.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(f)
    app.setStyleSheet(stylesheet(c))
