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

---

# Contract v2 — automatic chain through LEVEL7 Nesting's command line (phase 1, GRAFIS)

The two programs are installed and updated independently. They work together through files and through
LEVEL7 Nesting's command line only; neither imports the other's code.

| Step | LEVEL7 MTM | LEVEL7 Nesting |
|---|---|---|
| find the program | setting `nesting.cli`, else `level7-cli.exe` in `Program Files\LEVEL7 Nesting` or `<drive>:\LEVEL7 Nesting` | installed by its own setup |
| describe a model | (optional) `level7-cli info --json FILE` → `{"format": "level7-info", "format_version": 1, "level7_version", "size_names", "pieces": […]}` | since the integration branch; older versions print text only (MTM then derives the size key itself) |
| nest | `level7-cli nest <order>.mdl --width <order fabric width> --sizes <size>=<garments> --time <s> -o <order>.l7n` | exit 0 = valid marker; prints JSON stats (`length_mm`, `efficiency_total_width_pct`, `placed`, `unplaced`) |
| size key of an order model | `base:<first #GRDTAB entry>` (the model has only its base size) | lists that key in `size_names` since the integration branch, so the window can re-nest the saved `.l7n` |
| cutting files | `level7-cli export <order>.l7n --format plt --plt-preset algotex_grafis` / `--format iso --iso-preset bullmer_grafis` / `--format dxf` | writes the file; `device_tested: false` until a 1:1 test cut |
| look at the marker | starts `LEVEL7Nesting.exe <order>.l7n` | opens the file given on the command line |

Rules: LEVEL7 MTM never changes a file LEVEL7 Nesting wrote (it only reads the JSON it prints and, to draw the
marker, the saved `.l7n` — see below); LEVEL7 Nesting
never needs LEVEL7 MTM to run. A new field is added, never renamed; `format_version` changes only for a
breaking change.

## Marker shown in LEVEL7 MTM (read only)

The CAD workspace of LEVEL7 MTM draws the marker LEVEL7 Nesting saved: it opens the `.l7n` (ZIP, `project.json`,
format `level7-nesting-project` v1) read only and uses `markers[-1].fabric.width`, `.length`, `.placements`
(`piece_uid`, `size`, `x`, `y`, `rotation`, `mirrored`, `sx`, `sy`, `placed`) and `pieces[].sizes[size].cut` /
`.grain.points` / `.holes`, transformed exactly like LEVEL7 Nesting (mirror x → −x, rotate CCW, shrinkage scale,
translate). It never writes the file; editing the marker stays in LEVEL7 Nesting ("Show marker in LEVEL7 Nesting").

## Remnant (small pieces together)

Setting `cutting.remnant` (setup step 5, on by default): LEVEL7 MTM adds `--remnant` to `nest` when
`info --json` lists `"remnant"` in `features` (newer LEVEL7 Nesting); with an older LEVEL7 Nesting the marker
is nested as before and the log says so. LEVEL7 Nesting then packs the small pieces together so the fabric
left over is one large piece; the marker length never grows and the result passes its own check.

## Several orders in one marker (`--with`) — needed from LEVEL7 Nesting

Owner: small orders of one customer on the same fabric are nested together. Switched on for each customer
(setting `nesting.combine@<customer>` = `{"on": true, "most": 6}`, the customer's "Nesting settings" window;
off unless switched on). LEVEL7 MTM groups the models waiting for nesting that have the same customer, the same
`Fabric/@Number`, `Fabric/@Color` and `Fabric/@Width` of the XML and the same base pattern; an order without a
fabric number is always nested alone. Each model is checked on its own first (pattern, Test Run All Parts).

The call, sent only when `nest --help` shows `--with`:

    level7-cli nest A.mdl --width 1450 --time 60 --sizes S1=1 --with B.mdl S1=2 --with C.mdl S1=1 -o "A +2.l7n"

`--with FILE SIZE=QTY` (repeatable, two values): the pieces of FILE, size SIZE (a key of `info --json`
`size_names` of that file), QTY garments, are nested in the same marker as the first file's. The pieces of each
file stay apart (their names and labels say which file / order they come from, also in PLT / ISO). Everything
else (gaps, `--remnant`, JSON printed, `UNPLACED:` lines, exit code) as for one file.

With a LEVEL7 Nesting without `--with` every order is nested alone, as before.
