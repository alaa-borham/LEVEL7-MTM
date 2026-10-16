# PyInstaller spec for LEVEL7 MTM (Windows 10/11 x64, onedir build).
# Build:  pyinstaller packaging/level7mtm.spec --noconfirm --clean
# Output: dist/LEVEL7MTM/LEVEL7MTM.exe (GUI) + level7mtm-cli.exe
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(ROOT, "level7mtm", "resources", "level7mtm.ico")
datas = [(os.path.join(ROOT, "level7mtm", "resources"), "level7mtm/resources"),
         (os.path.join(ROOT, "docs"), "docs")]
hidden = ["ezdxf", "defusedxml", "reportlab", "arabic_reshaper", "bidi", "bidi.algorithm"]
excl = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick",
        "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf", "tkinter", "matplotlib"]

gui = Analysis([os.path.join(ROOT, "packaging", "run_gui.py")], pathex=[ROOT], datas=datas, hiddenimports=hidden + ["PySide6.QtSvg"],
               excludes=excl, noarchive=False)
cli = Analysis([os.path.join(ROOT, "packaging", "run_cli.py")], pathex=[ROOT], datas=[], hiddenimports=hidden,
               excludes=excl + ["PySide6"], noarchive=False)
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="LEVEL7MTM", icon=ICON, console=False)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="level7mtm-cli", icon=ICON, console=True)
coll = COLLECT(gui_exe, gui.binaries, gui.datas, cli_exe, cli.binaries, cli.datas, name="LEVEL7MTM")
