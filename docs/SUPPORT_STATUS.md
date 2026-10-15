# Support status (v0.1.0)

## Complete and tested
| Area | Status |
|---|---|
| Safe XML import (defusedxml, no DTD/entities, size/depth limits), namespaces, attributes, multi-line orders | done, tested |
| Versioned ERP mapping profile for the real OrderMaster format; unknown codes/fields → needs review | done, tested on 2 real files (profile status: draft) |
| Order identity, revisions (`_n`), duplicate files, conflicting same-revision data, late older revisions | done, tested |
| Manual measurement / option edits (recorded, invalidate calculations), manual cancel | done, tested |
| X-value table (X number, source codes, formula, status, evidence), editable, versioned | done, tested |
| GRAFIS MDL formula reader (335 Z records, descriptions, history, AB/GL detection) + report | done, tested on the supplied MDL |
| Restricted formula language incl. GRAFIS CMDIF, dependency graph, cycles, undefined names | done, tested |
| Parametric geometry: points, lines, curves, intersections, projection, path length/split, mirror | done, tested |
| Seam allowance per edge, mitre/bevel, collapse and self-intersection detection | done, tested |
| Shrinkage compensation per material along/across grain (expected shrinkage vs compensation) | done, tested |
| Validation: inputs, options, constraints, seam relations with per-pair difference, dimension checks | done, tested |
| Approval gate (no override), immutable versions, reproducible regeneration (fingerprint 0.001 mm) | done, tested |
| LEVEL7 Nesting hand-over (.l7n + contract v1), multi-order markers, copies, no duplicate jobs | done, tested incl. real nesting run |
| DXF (R2000, mm, layers, 1:1), PDF review sheet with stated scale | done, tested (DXF coordinates exact) |
| SQLite with migrations, transactions, audit log, backup + integrity check, restore | done, tested |
| Folder watch (stable-size wait, no deletion, duplicates, retry) | done, tested |
| PySide6 GUI, 11 screens, Arabic RTL / English, operator / developer roles, background work | done, offscreen smoke test |
| Windows build (PyInstaller + Inno Setup, desktop icon), CI with silent install test | see TEST_RESULTS.md |

## Not done / not supported
| Item | Reason |
|---|---|
| **Customer thobe model (alshiaka 2027)** | construction rules not available (see OWNER_RULES_REQUEST.md); only an engine-test model exists |
| GRAFIS construction program decoding (blocks 3,k,3,n) | binary, not decoded; formulas alone do not reproduce the pattern |
| AB / GL measured values, XG1–XG4 | need construction / definitions |
| Comparison against GRAFIS reference geometry per size | needs the construction first (reader of reference geometry exists in LEVEL7 Nesting) |
| Graphical point editor (drag points) | rules are edited as JSON + validated; manual geometric edits table exists (schema v2) but no UI yet |
| Posture / asymmetry corrections beyond R/L shoulder drop | need measurements + rules |
| ERP compatibility "complete" | only 2 samples of one ERP version |
| ASTM/AAMA DXF, PLT, ISO | not claimed; PLT/ISO stay in LEVEL7 Nesting |
| Production approval of any order | impossible until an approved model + confirmed X/shrinkage exist |
| Paper trial / sewn sample | owner, after a real model exists |
| Code-signed installer | no certificate |
