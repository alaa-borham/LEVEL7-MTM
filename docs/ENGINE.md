# Engine reference

## Measurement model

Six layers are kept apart:

1. **Order values** as received (`raw_line`: code, source text, source unit) — never changed.
2. **Normalised measurements** (`normalized.measurements`): value in **mm**, kind (`length`, `girth`,
   `half_girth`, `flat_width`, `angle`, `other`), meaning, mapping status, manual edits with old value / reason.
3. **X values** (GRAFIS individual measures) computed from (2) through the versioned X table.
4. **Model variables** (Z values and LEVEL7 variables) — formulas of the model version.
5. **Seam lines** (pattern geometry) → **cut lines** (seam allowance per edge).
6. **Production geometry**: shrinkage compensation applied once, per material, in the grain frame.

A missing value is `None` (never 0). Ease is a model variable; it is not added on import.

## Formula language (`level7mtm/formula/expr.py`)

Numbers, `+ - * / ^`, parentheses, comparisons `< <= > >= = <>`, `and or not`, functions `min max abs sqrt round
floor ceil sin cos tan asin acos atan atan2 hypot clamp` (degrees), `if(c, a, b)` and GRAFIS
`CMDIF[c]{a}[c2]{b}{else}`. Names: inputs/variables, `P.x`/`P.y` (points), `len.<path>`, `edge.<piece>.<edge>`,
`option.<group>.<value>`. No Python evaluation, attribute access, files or network; length and depth limits.
Dependency graph: topological order, undefined names, cycles (Tarjan), failures propagate — no stale values.

## Geometry (`level7mtm/pattern`)

Points: `xy`, `from+dx/dy`, `polar`, `intersect`, `project`, `along`, `on_path`, `mirror`, `circle`.
Paths: `line`, `curve` (cubic by tangent angles), `bezier`, `chain` (with `-name` = reversed). Curves keep their
control points; flattening tolerance is a display/export parameter (0.05 mm), length measurement 0.005 mm.

## Seam allowance

Per edge, mitre joins with a limit (beyond it: bevel + review warning with position). Collapse or
self-intersection = blocking error with position. Nothing is repaired silently.

## Shrinkage

`expected_shrinkage s%` → × 1/(1−s/100); `compensation c%` → × (1+c/100); `already_applied_by_source` → × 1.
Factors are stored with each generation; LEVEL7 Nesting shrinkage must stay 0.

## Validation severities

`blocking` (approval impossible) · `review` (must be resolved before approval) · `info`.
Approval also requires: approved model version, approved mapping profile, confirmed X rows, confirmed
shrinkage, no unresolved import problems, latest calculation of the latest revision.

## Reproducibility

Every generation stores the model / X table / finishing / mapping document ids (immutable versions), engine
version, inputs and options. `regenerate_and_compare` recomputes and compares a SHA-256 fingerprint of all cut
and seam points rounded to **0.001 mm**. A regression test locks the engine-test result for order 9-53-1_2.

Numerical geometry precision (0.001 mm) is not manufacturing tolerance (per check, e.g. ±0.5–1 mm) and neither
says anything about fit — fit needs paper trial and sewn sample.
