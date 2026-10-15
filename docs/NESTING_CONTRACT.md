# Hand-over to LEVEL7 Nesting — contract v1

**Transport:** one `.l7n` file per marker (ZIP). LEVEL7 Nesting opens it with *File → Open* (its own project
format, `level7-nesting-project` v1). Inside the same ZIP and next to it (`<name>.contract.json`) LEVEL7 MTM
writes the contract `level7mtm-nesting-contract` v1.

## project.json (read by LEVEL7 Nesting)

| Field | Content |
|---|---|
| `pieces[].uid` | `<model_id>:<piece>` e.g. `thobe-engine-test:front` |
| `pieces[].model` | model id (LEVEL7 Nesting multi-model convention) |
| `pieces[].sizes` | one entry per order line revision: `<model_id>:<line>_<revision>`, e.g. `…:9-53-1_2` |
| `sizes[*].cut` / `seam` | closed polylines, **mm, 1:1**, after shrinkage |
| `sizes[*].notches` | position on the cut line, inward direction, 5 mm |
| `sizes[*].grain` | grain line; `pieces[].grain_angle` in degrees |
| `default_count` / `default_mirrored` | copies per garment (normal / mirrored) |
| `allow_mirror` | true only when the model defines mirrored copies |
| `allowed_rotations` | from the model (default 0/180) |
| `cut_order.size_quantities` | garments per order line revision |
| `cut_order.material` | one material per marker |

## Contract (`level7mtm_contract.json`)

Per piece copy group: `piece_uid` (`<line>_<rev>:<piece>`), order number, line, revision, model id/version,
generation id, material, copies/mirrored per garment, garments, `total_copies = (copies+mirrored) × garments`,
rotation list, mirror rules, shrink factors applied, unit `mm`, `geometry_sha256` (cut+seam rounded 0.001 mm).
Header: `test_only`, `shrinkage_applied_by_mtm: true`, `nesting_shrink_must_be_zero: true`, fabric widths from
the orders.

## Rules enforced by LEVEL7 MTM

* only approved calculations of approved model versions (unless `--test`, which names the file
  `TEST-NOT-FOR-PRODUCTION-…` and sets `test_only`);
* cancelled / superseded revisions are refused;
* the same set of calculations is never written as a second job — the existing job is returned;
* left/right: mirrored copies only where the model defines them; never a mirror of a different right piece.

Job status (written / sent / done / failed, nesting job number) is recorded in LEVEL7 MTM (`nesting_jobs`).
