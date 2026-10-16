"""LEVEL7 look for LEVEL7 MTM: the same palette and accent as LEVEL7 Nesting (one family), dark / light, a modern
style sheet (rounded controls, slim scroll bars), a sidebar, page headers and dashboard cards."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = (240, 160, 10)          # LEVEL7 orange (as LEVEL7 Nesting)

THEMES = {
    "dark": dict(window=(26, 27, 30), panel=(34, 36, 40), base=(40, 42, 46), text=(232, 233, 235),
                 muted=(150, 154, 160), button=(50, 53, 58), border=(56, 59, 64), sidebar=(20, 21, 24),
                 card=(36, 38, 42)),
    "light": dict(window=(243, 244, 246), panel=(255, 255, 255), base=(255, 255, 255), text=(28, 30, 33),
                  muted=(105, 110, 118), button=(236, 238, 241), border=(220, 223, 228), sidebar=(252, 252, 253),
                  card=(255, 255, 255)),
}
# row colours per status: (background, text) — tinted in the dark theme, pastel in the light theme
STATUS = {
    "dark": {"ok": ((28, 72, 52), (214, 245, 228)), "review": ((86, 68, 20), (255, 236, 190)),
             "blocking": ((96, 34, 38), (255, 214, 214)), "info": ((28, 52, 80), (210, 228, 255)),
             "neutral": ((48, 50, 55), (220, 222, 225))},
    "light": {"ok": ((210, 246, 224), (20, 60, 38)), "review": ((253, 236, 170), (80, 56, 0)),
              "blocking": ((254, 212, 212), (110, 20, 20)), "info": ((219, 236, 254), (20, 50, 90)),
              "neutral": ((240, 241, 243), (40, 42, 46))},
}
CURRENT = {"name": "dark"}


def name() -> str:
    return CURRENT["name"]


def colours(theme: str | None = None) -> dict:
    return {k: QColor(*v) for k, v in THEMES[theme or CURRENT["name"]].items()}


def status_colours(kind: str) -> tuple[QColor, QColor]:
    bg, fg = STATUS[CURRENT["name"]].get(kind, STATUS[CURRENT["name"]]["neutral"])
    return QColor(*bg), QColor(*fg)


def _rgb(c: QColor, a: float | None = None) -> str:
    return f"rgba({c.red()},{c.green()},{c.blue()},{a})" if a is not None else f"rgb({c.red()},{c.green()},{c.blue()})"


def stylesheet(c: dict) -> str:
    acc, txt, mut = QColor(*ACCENT), c["text"], c["muted"]
    return f"""
QMainWindow, QDialog {{ background: {_rgb(c['window'])}; }}
QWidget {{ color: {_rgb(txt)}; }}
QToolTip {{ background: {_rgb(c['panel'])}; color: {_rgb(txt)}; border: 1px solid {_rgb(c['border'])};
           border-radius: 6px; padding: 5px 8px; }}
QMenuBar {{ background: {_rgb(c['window'])}; padding: 2px 6px; }}
QMenuBar::item {{ padding: 5px 12px; border-radius: 6px; background: transparent; }}
QMenuBar::item:selected {{ background: {_rgb(acc, 0.18)}; }}
QMenu {{ background: {_rgb(c['panel'])}; border: 1px solid {_rgb(c['border'])}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {_rgb(acc, 0.22)}; color: {_rgb(txt)}; }}
QMenu::separator {{ height: 1px; background: {_rgb(c['border'])}; margin: 5px 8px; }}

QPushButton {{ background: {_rgb(c['button'])}; border: 1px solid {_rgb(c['border'])}; border-radius: 8px;
              padding: 7px 14px; color: {_rgb(txt)}; }}
QPushButton:hover {{ border-color: {_rgb(acc, 0.7)}; background: {_rgb(acc, 0.12)}; }}
QPushButton:pressed {{ background: {_rgb(acc, 0.25)}; }}
QPushButton:disabled {{ color: {_rgb(mut, 0.6)}; }}
QPushButton#primary {{ background: {_rgb(acc)}; color: rgb(25,25,25); border-color: {_rgb(acc)}; font-weight: 600; }}
QPushButton#primary:hover {{ background: rgb(250,180,40); }}
QPushButton#quick {{ background: {_rgb(c['card'])}; border: 1px solid {_rgb(c['border'])}; border-radius: 12px;
              padding: 14px 16px; font-size: 10.5pt; text-align: left; }}
QPushButton#quick:hover {{ border-color: {_rgb(acc)}; background: {_rgb(acc, 0.10)}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
    background: {_rgb(c['base'])}; border: 1px solid {_rgb(c['border'])}; border-radius: 7px;
    padding: 4px 7px; selection-background-color: {_rgb(acc, 0.55)}; color: {_rgb(txt)}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {_rgb(acc)}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {_rgb(c['panel'])}; border: 1px solid {_rgb(c['border'])};
    border-radius: 8px; selection-background-color: {_rgb(acc, 0.3)}; padding: 4px; }}
QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {_rgb(mut, 0.8)};
    background: {_rgb(c['base'])}; }}
QCheckBox::indicator:checked {{ background: {_rgb(acc)}; border-color: {_rgb(acc)}; }}

QTableWidget, QTableView {{ background: {_rgb(c['base'])}; border: 1px solid {_rgb(c['border'])};
    border-radius: 10px; gridline-color: {_rgb(c['border'])}; selection-background-color: {_rgb(acc, 0.32)};
    selection-color: {_rgb(txt)}; alternate-background-color: {_rgb(c['panel'])}; }}
QHeaderView::section {{ background: {_rgb(c['panel'])}; color: {_rgb(mut)}; border: none;
    border-bottom: 1px solid {_rgb(c['border'])}; padding: 7px 8px; font-weight: 600; }}
QTableCornerButton::section {{ background: {_rgb(c['panel'])}; border: none; }}
QTabWidget::pane {{ border: 1px solid {_rgb(c['border'])}; border-radius: 10px; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 8px 16px; border: none; color: {_rgb(mut)}; }}
QTabBar::tab:selected {{ color: {_rgb(txt)}; border-bottom: 2px solid {_rgb(acc)}; font-weight: 600; }}
QTabBar::tab:hover {{ color: {_rgb(txt)}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {_rgb(mut, 0.40)}; border-radius: 4px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {_rgb(acc, 0.8)}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QStatusBar {{ background: {_rgb(c['window'])}; color: {_rgb(mut)}; }}
QSplitter::handle {{ background: {_rgb(c['border'])}; }}

/* sidebar */
QFrame#sidebar {{ background: {_rgb(c['sidebar'])}; border: none; }}
QLabel#brand {{ font-size: 17pt; font-weight: 800; letter-spacing: 1px; }}
QLabel#brandSub {{ color: {_rgb(acc)}; font-size: 10pt; font-weight: 700; letter-spacing: 3px; }}
QLabel#version {{ color: {_rgb(mut)}; font-size: 8.5pt; }}
QListWidget#nav {{ background: transparent; border: none; outline: 0; font-size: 10.5pt; }}
QListWidget#nav::item {{ padding: 8px 12px; margin: 1px 6px; border-radius: 8px; }}
QListWidget#nav::item:hover {{ background: {_rgb(acc, 0.10)}; }}
QListWidget#nav::item:selected {{ background: {_rgb(acc, 0.20)}; color: {_rgb(txt)}; font-weight: 600; }}
QToolButton#sideButton {{ background: transparent; border: 1px solid {_rgb(c['border'])}; border-radius: 8px;
    padding: 6px 10px; }}
QToolButton#sideButton:hover {{ border-color: {_rgb(acc)}; }}

/* page header and cards */
QLabel#pageTitle {{ font-size: 17pt; font-weight: 700; }}
QLabel#pageSub {{ color: {_rgb(mut)}; font-size: 10pt; }}
QFrame#card {{ background: {_rgb(c['card'])}; border: 1px solid {_rgb(c['border'])}; border-radius: 14px; }}
QLabel#cardValue {{ font-size: 24pt; font-weight: 800; }}
QLabel#cardLabel {{ color: {_rgb(mut)}; font-size: 9.5pt; }}
QLabel#cardTitle {{ font-size: 11pt; font-weight: 700; }}
QLabel#dotOk {{ color: rgb(46,204,113); font-size: 13pt; }}
QLabel#dotBad {{ color: rgb(231,76,60); font-size: 13pt; }}
QLabel#dotWarn {{ color: {_rgb(acc)}; font-size: 13pt; }}
"""


def apply(app: QApplication, theme: str = "dark") -> None:
    """Fusion palette + style sheet for the whole application."""
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
    pal.setColor(QPalette.Highlight, QColor(*ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor(25, 25, 25))
    for grp in (QPalette.Disabled,):
        for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            pal.setColor(grp, role, c["muted"])
    app.setPalette(pal)
    f = QFont("Segoe UI", 10)
    f.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(f)
    app.setStyleSheet(stylesheet(c))
