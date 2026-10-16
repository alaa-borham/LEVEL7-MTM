# Phase 1 — facts, assumptions and open points

Status labels: **F** fact (read from a file / confirmed by the owner) · **A** assumption (used, visible, must be
confirmed) · **U** unknown (blocks the related feature).

## 1. ERP XML (two samples: `Order-9-52-1_0.xml`, `Order-9-53-1_2.xml`)

| Item | Finding | Status |
|---|---|---|
| Encoding | UTF-8 (declaration), no BOM | F |
| Root / namespaces | `OrderMaster`, no namespace, no DTD | F |
| Header | `@Number`, `@Reseller`, `@Reference="From level7"`, `@Order-Date` (`MM/DD/YYYY hh:mm:ss AM/PM`), `Remarks` | F |
| Repeated groups | `Orders/Order` (2 per file), `Variations/Variation`, `Programs/Program`, `Measurement/Type` (18) | F |
| Line number | `9-53-1_2` = reseller 9 · order 53 · line 1 · revision 2 | F (owner) |
| Changes | a changed order arrives again with a higher revision suffix | F (owner) |
| Cancellation | cancelled orders are not sent by the ERP; cancelling is a manual action in LEVEL7 MTM | F (owner) |
| Customer id | not present | F |
| Quantity | `Item/@Quantity` | F |
| Model code | `Item/@Number = 91001`, `Measurement/@Measurement-Table-Name = Saudi Thobe_TableMeasurement` | F |
| `Basic-Size="60 XL"` | relation to MDL sizes 52-S…62-S postponed by the owner; not used | U |
| Fabric width | `Fabric/@Width = 1450` — always taken from the order | F (owner) |
| Measurement unit | not in the file; centimetres | F (owner, for girths); A for lengths |
| Girths | CH, WA, HP, BO = **half circumference** | F (owner) |
| Product vs body | finished-garment measurements; GRAFIS base-file X values are only defaults — ease is not added again | F (owner) |
| `WB` / `SWS` | hem band width / plain sleeve width | F (owner) |
| `DSR` / `DSL` | right / left shoulder drop, may differ | F (owner) |
| `COS` / `COG` | neck size for `Collar Square` / `gallab Classic` | F (owner) |
| `Cuff Individuel Hight` | stored, not used for now | F (owner) |
| `Slim2`, `Athlete1` | 2 of 7 body-shape types, handled by MDL formulas | F (owner); the 7 names: U |
| Full list of design codes | promised later; unknown codes → "needs review" | U |
| AH, EB, WRS, JL, TKL, TKWD | meaning (girth / half / depth, start-end points) | U |

Mapping profile: `level7mtm/resources/mappings/level7_erp_ordermaster_v1.json` (status **draft** — two samples only).
ERP compatibility is **not** declared complete: only two files from one ERP version were seen.

## 2. LEVEL7 Nesting (`alaa-borham/LEVEL7-Nesting`, read)

* Mature PySide6 application: native GRAFIS MDL/SCB reader (geometry per size verified against GRAFIS exports),
  irregular nesting, PLT/DXF/ISO export, `.l7n` project files (`level7-nesting-project` v1, ZIP + JSON), CLI.
* No network API. Integration chosen: **exchange file** — LEVEL7 MTM writes a `.l7n` project the nesting
  application opens directly (one size per order line revision; see [NESTING_CONTRACT.md](NESTING_CONTRACT.md)).
  Verified by test: LEVEL7 Nesting loads the file and nests 2 orders, all pieces valid and traceable.
* PLT / ISO outputs stay in LEVEL7 Nesting (not re-implemented).

## 3. GRAFIS reference (`1-S- alshiaka-2027.mdl`, `alshiaka-2027-S-52.scb`)

| Item | Finding | Status |
|---|---|---|
| Container | `GRAFIS-ModelldateiVA`, 1705 blocks, chain complete | F |
| Sizes | `52-S … 60-S, 62-S` (no 61) + base `__HN49_0` (= 62-S values) | F |
| Parts | 102 entries (13 with production attributes): front1, back1, shoulder1, side, ashtack, sleeve SH-101, jabzour / kabak / collar / pocket variants | F |
| Geometry per size | readable with LEVEL7 Nesting's reader (all 10 sizes) | F |
| **Z formulas** | **335 formula records in 30 parts, blocks (3,part,6,34): formula, Arabic description, edit history** — decoded in `level7mtm/grafis/formulas.py`; report via `level7mtm mdl-report` | F |
| GRAFIS formula syntax | `+ − * /`, parentheses, `X n`, `Z n`, `XG n`, `CMDIF[cond]{v}[cond]{v}{else}` — evaluated by LEVEL7's own safe parser | F |
| Malformed formula | part 002 Z76 = `X35/` (history: `X35/2`) | F — owner to fix |
| AB / GL values | 10 values are *measured in the construction* (distance / curve length) — cannot be computed from formulas alone | F |
| XG1…XG4 | used (part 039–045) but not defined in the MDL | U |
| X numbers | per part, not global: part 002 X1 = length, part 005 uses X1/2 = collar length | F (from formulas) |
| Construction program | blocks (3,part,3,n): points/lines/curves that use the Z values — **not decoded** | U |
| SCB | marker of 52-S, 13 piece types with counts, width 1480, length 2072, 87.43 % | F |

**Consequence:** the variable layer of the GRAFIS model can be transferred mechanically (formulas are text), but
the construction (how points and curves are built from those values) is not readable yet. Reading the formulas
does not reproduce the pattern. The construction must be re-defined in LEVEL7's model format together with the
owner (or the construction blocks decoded — research, uncertain).

## 4. Assumptions in use (all visible in the program)

| Id | Assumption | Where |
|---|---|---|
| A-1 | Lengths in the XML are centimetres like the girths | mapping profile `unit` |
| A-2 | ~~XML values are finished-garment measurements~~ — confirmed by the owner (decision #15) | — |
| A-3 | `X01` = `X1` (leading zeros) in GRAFIS formulas | `grafis/formulas.canonical` |
| A-4 | X table for part 002 (X1 ← FL, X3 ← SH, X4 ← DSR …) — every row "proposed" | `resources/xvars` |
| A-5 | Engine-test construction and ease values | `thobe_engine_test_v0_1.json` (status `engine_test`, cannot be approved) |
| A-6 | Shrinkage 0 % until set | finishing settings, `confirmed: false` blocks approval |

## 5. Questions that block production use → [OWNER_RULES_REQUEST.md](OWNER_RULES_REQUEST.md)
