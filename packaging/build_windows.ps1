# Build LEVEL7 MTM for Windows 10/11 x64 (reproducible: pinned requirements).
# Requirements: Python 3.12 x64, Inno Setup 6 (iscc on PATH or default install dir).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python -m venv .venv-win
.\.venv-win\Scripts\python -m pip install --upgrade pip
.\.venv-win\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv-win\Scripts\python -m pip install -e . --no-deps
.\.venv-win\Scripts\python -m pytest -q
.\.venv-win\Scripts\pyinstaller packaging\level7mtm.spec --noconfirm --clean
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
$ver = (.\.venv-win\Scripts\python -c "import level7mtm; print(level7mtm.__version__)")
& $iscc "/DAppVersion=$ver" packaging\installer.iss
Write-Host "Installer: dist\installer\LEVEL7MTM-$ver-Setup.exe"
