# Test results

## Linux (development container, Python 3.13, PySide6 6.12 offscreen) — 2026-10-15
`python -m pytest -q -rs` → **97 passed, 0 skipped** (with the customer MDL/SCB present locally and the
LEVEL7 Nesting source checked out next to the repository).

In the public CI the customer GRAFIS files are absent, so the 3 MDL/SCB tests are skipped there.

| Group | What is proven |
|---|---|
| test_xml_import | both real ERP files; damaged / truncated / empty file; DTD, entities, external entities refused; depth limit; wrong root; missing / unknown / invalid / out-of-range values; unit change via profile; namespaces; conflicting options; structure report |
| test_formula | GRAFIS formulas incl. CMDIF; malformed `X35/` and injection attempts refused; limits; graph order, cycles, undefined, failure propagation |
| test_geometry | intersections, projection, mirror, curve length (quarter circle ±0.03 %), split, per-edge allowance, non-closed seam, allowance collapse, self-intersection |
| test_pattern_model | fixed fingerprint for order 9-53-1_2; one measurement → expected dimension change only; R/L shoulder drop (real GRAFIS formulas); small / large / large belly; out of range; missing input; unknown / unimplemented options; option changes piece list; circular variables / points; negative allowance; curve failure; seam relations with per-pair difference |
| test_workflow | duplicate file; new revision supersedes; late older revision; same revision different data = conflict; failed file recorded; unknown option → needs review; regeneration identical; edit invalidates; engine-test model cannot be approved / sent; full approval path (test-approved copies) → nesting file → no duplicate job → completed; new model version does not change old results; versions immutable; cancelled order not sent; backup / restore; newer schema refused |
| test_exchange_export | shrinkage definitions and grain direction; unapproved exchange refused; contract counts (left/right as mirrored copies); geometry unchanged in exchange; **LEVEL7 Nesting opens the file and nests 18 pieces of 2 orders, valid**; DXF units/layers/coordinates exact; PDF |
| test_grafis_and_watcher | 335 formulas / descriptions / history / AB-GL from the MDL; sizes / parts / SBINFO; non-GRAFIS and truncated files; watcher waits for stable size, never deletes, no double processing, retry |
| test_ui_smoke | all 11 screens in Arabic and English open with real data; preview drawn; approval button disabled for the engine-test model |

## Windows (GitHub Actions `windows-2022`, Python 3.12 x64) — run 38898295748, commit e3858fe: **success**
https://github.com/alaa-borham/LEVEL7-MTM/actions/runs/38898295748

| Step | Result |
|---|---|
| Unit + integration tests incl. LEVEL7 Nesting run and GUI smoke test (offscreen) | passed (MDL/SCB tests skipped: customer files not in the repository) |
| PyInstaller build (GUI + CLI) | passed |
| Frozen end-to-end: import 2 real XML → calculate → DXF → PDF → TEST nesting file; production send of a non-approved order refused; GUI starts and stays up | passed |
| Inno Setup installer | built (artifact `LEVEL7MTM-Setup`) |
| Silent install on the clean runner: program files, **desktop shortcut**, installed CLI import + calculation **with Python removed from PATH**, installed GUI starts | passed |

The first run (38817779447) failed only because the script ended with the (intended) non-zero exit code of the
refused production send; fixed in e3858fe.
Not yet done: installation on the owner's own Windows PC.

## Not tested (cannot be, yet)
Comparison with GRAFIS reference geometry, paper trial, sewn sample, cutting a 1:1 test piece — they need the
real thobe model.
