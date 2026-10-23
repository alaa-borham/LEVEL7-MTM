"""LEVEL7 MTM look: a CAD workstation — near-black navy, pure cyan accent, sharp contrast and sharp corners (dark,
the default), and a light variant of the same family. The Cairo typeface is shipped with the program."""
from __future__ import annotations

import os

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

THEMES = {
    # sharp (owner): near-black depths, pure white text, clear lines between the layers, pure accents
    "dark": dict(window=(4, 9, 14), panel=(10, 19, 28), base=(2, 6, 10), text=(255, 255, 255),
                 muted=(168, 190, 206), button=(17, 32, 46), border=(46, 78, 104), sidebar=(0, 3, 7),
                 card=(12, 24, 35), ribbon=(8, 16, 24), canvas=(5, 11, 17), grid=(18, 34, 48), grid2=(34, 60, 82),
                 fabric=(14, 28, 40), selvedge=(96, 136, 162), accent=(0, 212, 255), on_accent=(0, 12, 20),
                 piece=(0, 212, 255), label=(235, 252, 255), ok=(0, 245, 122), warn=(255, 193, 7), bad=(255, 51, 85)),
    "light": dict(window=(232, 237, 242), panel=(250, 252, 254), base=(255, 255, 255), text=(0, 10, 20),
                  muted=(62, 82, 100), button=(226, 233, 240), border=(160, 180, 198), sidebar=(4, 16, 28),
                  card=(255, 255, 255), ribbon=(246, 249, 252), canvas=(244, 248, 251), grid=(222, 231, 238),
                  grid2=(190, 206, 220), fabric=(255, 255, 255), selvedge=(110, 140, 162), accent=(0, 114, 198),
                  on_accent=(255, 255, 255), piece=(0, 114, 198), label=(0, 34, 60), ok=(0, 150, 70),
                  warn=(200, 122, 0), bad=(214, 20, 52)),
}
# row colours per status: (background, text) — strong tints on dark, clear pastels on light
STATUS = {
    "dark": {"ok": ((0, 78, 46), (170, 255, 210)), "review": ((98, 68, 0), (255, 222, 130)),
             "blocking": ((112, 10, 32), (255, 184, 196)), "info": ((0, 62, 98), (165, 232, 255)),
             "neutral": ((20, 36, 50), (232, 240, 246))},
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
QPushButton:hover {{ border-color: {_rgb(acc)}; background: {_rgb(acc, 0.10)}; }}
QPushButton:pressed {{ background: {_rgb(acc, 0.30)}; }}
QPushButton:checked {{ border-color: {_rgb(acc)}; background: {_rgb(acc, 0.18)}; }}
QPushButton:disabled {{ color: {_rgb(mut, 0.75)}; border-color: {_rgb(c['border'], 0.7)}; }}
QPushButton#primary {{ background: {_rgb(acc)}; color: {_rgb(on)}; border-color: {_rgb(acc)}; font-weight: 700; }}
QPushButton#primary:hover {{ background: {_rgb(acc.lighter(115))}; }}
QPushButton#primary:disabled {{ background: {_rgb(acc, 0.18)}; color: {_rgb(mut, 0.7)}; border-color: {_rgb(acc, 0.25)}; }}
QPushButton#quick {{ background: {_rgb(c['card'])}; padding: 12px 14px; text-align: left; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
    background: {_rgb(c['base'])}; border: 1px solid {_rgb(c['border'])}; border-radius: 2px;
    padding: 3px 6px; selection-background-color: {_rgb(acc)}; selection-color: {_rgb(on)}; color: {_rgb(txt)}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {_rgb(acc)}; background: {_rgb(c['base'])}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {_rgb(c['panel'])}; border: 1px solid {_rgb(acc)};
    selection-background-color: {_rgb(acc)}; selection-color: {_rgb(on)}; }}
QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 2px; border: 1px solid {_rgb(mut)};
    background: {_rgb(c['base'])}; }}
QCheckBox::indicator:checked {{ background: {_rgb(acc)}; border-color: {_rgb(acc)}; }}

QTableWidget, QTableView, QTreeWidget, QListWidget {{ background: {_rgb(c['base'])}; border: 1px solid {_rgb(c['border'])};
    gridline-color: {_rgb(c['border'], 0.8)}; selection-background-color: {_rgb(acc, 0.45)}; selection-color: {_rgb(txt)};
    alternate-background-color: {_rgb(c['panel'])}; outline: 0; }}
QHeaderView::section {{ background: {_rgb(c['button'])}; color: {_rgb(txt)}; border: none;
    border-bottom: 2px solid {_rgb(acc, 0.65)}; border-left: 1px solid {_rgb(c['border'], 0.6)}; padding: 5px 7px;
    font-weight: 800; }}
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
QStatusBar {{ background: {_rgb(c['sidebar'])}; color: rgb(214,228,238); border-top: 1px solid {_rgb(c['border'])}; }}
QStatusBar QLabel {{ color: rgb(214,228,238); padding: 0 8px; }}
QSplitter::handle {{ background: {_rgb(c['border'])}; width: 2px; height: 2px; }}
QDockWidget {{ color: {_rgb(txt)}; titlebar-close-icon: none; }}
QDockWidget::title {{ background: {_rgb(c['panel'])}; padding: 6px 10px; border-bottom: 1px solid {_rgb(c['border'])}; }}

/* ribbon */
QFrame#ribbon {{ background: {_rgb(c['ribbon'])}; border-bottom: 1px solid {_rgb(c['border'])}; }}
QFrame#ribbonGroup {{ }}
QLabel#ribbonTitle {{ color: {_rgb(acc)}; font-size: 8pt; font-weight: 700; letter-spacing: 1px; }}
QToolButton#ribbonButton {{ background: transparent; border: 1px solid transparent; border-radius: 2px; padding: 3px 6px;
    color: {_rgb(txt)}; font-size: 8.5pt; }}
QToolButton#ribbonButton:hover {{ border-color: {_rgb(acc)}; background: {_rgb(acc, 0.12)}; }}
QToolButton#ribbonButton::menu-indicator {{ image: none; width: 0px; }}
QToolButton#ribbonButton[hasMenu="true"] {{ padding-bottom: 9px; }}
QToolButton#ribbonButton:pressed {{ background: {_rgb(acc, 0.30)}; }}
QToolButton#ribbonButton:disabled {{ color: {_rgb(mut, 0.7)}; }}
QLabel#brand {{ font-size: 14pt; font-weight: 800; letter-spacing: 2px; }}
QLabel#brandSub {{ color: {_rgb(acc)}; font-size: 8pt; font-weight: 700; letter-spacing: 2px; }}
QLabel#brandMark {{ background: {_rgb(acc)}; color: {_rgb(on)}; font-weight: 800; font-size: 13pt; border-radius: 2px; }}

/* CAD panels */
QFrame#panelHead {{ background: {_rgb(c['button'])}; border-bottom: 2px solid {_rgb(acc, 0.65)}; }}
QLabel#panelTitle {{ font-weight: 700; font-size: 9pt; letter-spacing: 1px; color: {_rgb(txt)}; }}
QLabel#panelCount {{ color: {_rgb(acc)}; font-weight: 700; }}
QWidget#browser, QWidget#inspector {{ background: {_rgb(c['panel'])}; }}
QTreeWidget#orders {{ background: {_rgb(c['panel'])}; border: none; font-size: 9.5pt; }}
QTreeWidget#orders::item {{ padding: 3px 2px; }}
QTreeWidget#orders::item:selected {{ background: {_rgb(acc, 0.38)}; color: {_rgb(txt)}; }}
QTreeWidget#orders::item:hover {{ background: {_rgb(acc, 0.12)}; }}
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
QFrame#card {{ background: {_rgb(c['card'])}; border: 1px solid {_rgb(c['border'])}; border-top: 2px solid {_rgb(acc)};
    border-radius: 3px; }}
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
