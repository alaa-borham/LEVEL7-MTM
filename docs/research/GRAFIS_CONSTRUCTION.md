# GRAFIS construction data — research status (2026-10-16)

File: `1-S- alshiaka-2027.mdl` (SHA-256 8581a243…). Reader: `level7mtm/grafis/construction.py` (research only).

## Confirmed (C)
| Block | Content | Evidence |
|---|---|---|
| `(3,p,2,0)` | construction protocol, fixed **32-byte records** | record count = first #TLIST number for **72/72 parts** (part 002: 286, front1: 76) |
| `(3,p,3,n)` | calls of GRAFIS library programs with labelled parameters | text labels ("Verschiebewert [mm]", "X-Abst.", "Länge [mm]", "Schlepprichtung [°]"), float values |
| `(3,p,6,34)` | Z formulas, descriptions, history | decoded (formulas.py), 335 records |
| `(3,p,6,4)` | evaluated measurement table (#TFLIST values as float) | part 002 values 159.67 / 165.83 / 409.06 … = T2M A/B/E (rounded) |
| `(3,p,6,37)` | labelled measurement points A, B, C … | labels + element ids |
| `(3,p,7,s)` | evaluated geometry per size | verified by LEVEL7 Nesting against GRAFIS exports |

Only **4 GRAFIS programs** occur in the whole file: `GraPrg_GK_QS_x001` (shift lines, 81 steps),
`GraPrg_GK_QQ_x001` (curve shift lines, 50), `GraPrg_GK_FL_x001` (piece / grain / "Flächen", 42),
`GraPrg_GK_EK_x004` (7). All are called from the GRAFIS library `basis_d` with base `___N38_0`.

## What this means
1. The thobe is built in GRAFIS as **base construction + shift lines (Verschiebelinien)**: the Z values computed
   from the measurements move parts of the contour by "Verschiebewert" in a direction. This is GRAFIS's own MTM
   method, and it is reproducible in principle.
2. The protocol records reference element ids that are the same ids as in the evaluated geometry, so each
   record type can be identified by comparing its inputs with its result across the 10 sizes.
3. **Blocker:** what each library program does geometrically (e.g. how GK_QQ distributes a shift along a
   curve) is implemented inside GRAFIS, not stored in the MDL. It has to be inferred from input/output
   pairs or described by the owner. The base `basis_d ___N38_0` and the X-value set `250-Werte-OPTIMASS` are
   GRAFIS installation data, not in the file.
4. About 60 protocol record types occur; their meaning is unknown (U).

## Estimate and recommendation
* Full automatic decoding: research with uncertain end (≈ 60 record types + 4 library programs inferred
  from outputs). Not recommended as the only path.
* **Recommended hybrid:** decode one simple piece (front1, 76 records) far enough to read its points and
  shift-line parameters, use the stored GRAFIS geometry of all 10 sizes as the reference, and transfer the
  rules into the LEVEL7 model format with the owner's screenshots of the same steps. Every transferred piece
  is accepted only when it matches the GRAFIS geometry of all 10 sizes within a stated tolerance.
