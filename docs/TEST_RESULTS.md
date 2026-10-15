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

## Windows
Built and tested by `.github/workflows/windows-build.yml` on `windows-2022` (tests, PyInstaller, frozen CLI
end-to-end, GUI start, Inno Setup, silent install with Python removed from PATH, desktop shortcut). Results are
claimed only for runs that passed on GitHub Actions — see the Actions tab / artifact `test-evidence`.
Not yet done: installation on the owner's own Windows PC.

## Not tested (cannot be, yet)
Comparison with GRAFIS reference geometry, paper trial, sewn sample, cutting a 1:1 test piece — they need the
real thobe model.
