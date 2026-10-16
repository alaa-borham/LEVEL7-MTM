# LEVEL7 MTM

Windows desktop application (PySide6, Python, SQLite) that imports made-to-measure thobe orders from the ERP
(XML), checks them, computes the pattern of every order line with a parametric engine, validates it, lets the
operator review and approve it, and hands approved pieces to **LEVEL7 Nesting**.

> **Status (v0.1.0): the engine, workflow, import, validation, exports and nesting hand-over are implemented and
> tested. The customer's thobe pattern is NOT in the engine yet.** The included model `thobe-engine-test` is an
> engine test (it uses some real GRAFIS formulas but invented construction steps) and the program refuses to
> approve it for production. See [docs/SUPPORT_STATUS.md](docs/SUPPORT_STATUS.md) and the open questions in
> [docs/FACTS_AND_ASSUMPTIONS.md](docs/FACTS_AND_ASSUMPTIONS.md).

| Document | Content |
|---|---|
| [docs/STATUS_REPORT.md](docs/STATUS_REPORT.md) | Status of the single-order path: what works, what is missing |
| [docs/CATALOG_AND_RULES.md](docs/CATALOG_AND_RULES.md) | Products, styles, measurement definitions, unified mapping rules, piece plan |
| [docs/reports/FL_EXPERIMENT.md](docs/reports/FL_EXPERIMENT.md) | Same real order twice, only FL changed: geometry, seams, quantities, nesting |
| [docs/FACTS_AND_ASSUMPTIONS.md](docs/FACTS_AND_ASSUMPTIONS.md) | Phase-1 report: XML, GRAFIS files, nesting system, facts vs assumptions, open questions |
| [docs/OWNER_RULES_REQUEST.md](docs/OWNER_RULES_REQUEST.md) | The exact rules / confirmations needed from the pattern owner |
| [docs/دليل_التثبيت_والتشغيل.md](docs/دليل_التثبيت_والتشغيل.md) | Arabic installation and operation guide |
| [docs/دليل_إضافة_موديل.md](docs/دليل_إضافة_موديل.md) | Arabic guide: adding a model, editing rules, X variables |
| [docs/ENGINE.md](docs/ENGINE.md) | Measurement model, units, formula language, geometry, allowances, shrinkage, validation, reproducibility |
| [docs/NESTING_CONTRACT.md](docs/NESTING_CONTRACT.md) | Hand-over to LEVEL7 Nesting (`.l7n` + contract v1) |
| [docs/SUPPORT_STATUS.md](docs/SUPPORT_STATUS.md) | What is complete, what is not supported |
| [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md) | Test runs and what they prove (and do not prove) |
| [docs/facts-and-decisions.md](docs/facts-and-decisions.md) | Owner decisions log |

## Layout
```
level7mtm/erp          safe XML loading, versioned mapping profiles, importer, X-value table
level7mtm/grafis       GRAFIS container, text sections, Z-formula reader, MDL report
level7mtm/formula      restricted expression language (incl. GRAFIS CMDIF) + dependency graph
level7mtm/pattern      geometry, parametric model evaluation, seam allowance, shrinkage, serialisation
level7mtm/nesting      LEVEL7 Nesting exchange (.l7n) + contract
level7mtm/export       DXF (1:1, mm), PDF review sheet, SVG preview
level7mtm/storage      SQLite, migrations, backup/restore
level7mtm/services     catalog + unified rules, workflow (import → revision → generation → approval → nesting),
                       folder watcher, controlled experiments
level7mtm/ui           PySide6 GUI (Arabic/English, RTL)
level7mtm/resources    mapping profile, X table, model files, finishing settings, icon
packaging/             PyInstaller spec, Inno Setup script, build script
.github/workflows      Windows build: tests, EXE, installer, silent install test
```

## Run from source
```
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt && pip install -e . --no-deps
level7mtm-gui                          # desktop app
level7mtm import reference\erp\*.xml   # CLI
level7mtm calc 9-53-1_2
python -m pytest -q
```

## Build the installer (Windows)
`powershell -File packaging\build_windows.ps1` → `dist\installer\LEVEL7MTM-<ver>-Setup.exe`
(also built and install-tested by GitHub Actions, artifact `LEVEL7MTM-Setup`).

Customer GRAFIS files (`reference/grafis/*.mdl|*.scb`) are deliberately **not committed** (public repository);
tests that need them are skipped when they are absent.
