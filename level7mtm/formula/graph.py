"""Dependency graph for a set of named formulas.

Every formula is parsed once; the graph orders the evaluation by dependency, reports undefined variables and
circular definitions per formula, and never returns a value for a formula that could not be computed (no stale
or default values are substituted).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .expr import FormulaError, evaluate, parse, references


@dataclass
class FormulaResult:
    name: str
    formula: str
    value: float | None = None
    error: str | None = None
    depends_on: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and self.value is not None


@dataclass
class GraphResult:
    results: dict[str, FormulaResult]
    order: list[str]
    cycles: list[list[str]]
    undefined: dict[str, list[str]]          # variable -> formulas that use it

    @property
    def values(self) -> dict[str, float]:
        return {k: r.value for k, r in self.results.items() if r.ok}

    @property
    def errors(self) -> dict[str, str]:
        return {k: r.error for k, r in self.results.items() if r.error}


def solve(formulas: dict[str, str], inputs: dict[str, float], blocked: dict[str, str] | None = None) -> GraphResult:
    """Evaluate ``formulas`` (name -> text) given ``inputs`` (name -> value).

    ``blocked`` marks names that exist but cannot be computed here (name -> reason); formulas depending on
    them fail with that reason instead of an "undefined variable" error.
    """
    blocked = dict(blocked or {})
    results: dict[str, FormulaResult] = {}
    ast = {}
    deps: dict[str, set[str]] = {}
    for name, text in formulas.items():
        r = FormulaResult(name, text)
        results[name] = r
        try:
            ast[name] = parse(text)
            deps[name] = references(ast[name])
            r.depends_on = sorted(deps[name])
        except FormulaError as e:
            r.error = f"syntax: {e}"
            deps[name] = set()

    undefined: dict[str, list[str]] = {}
    for name, ds in deps.items():
        for d in ds:
            if d not in formulas and d not in inputs and d not in blocked:
                undefined.setdefault(d, []).append(name)

    # Tarjan's strongly connected components -> cycles
    index, low, on_stack, stack, comps = {}, {}, set(), [], []
    counter = [0]

    def strong(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in deps.get(v, ()):
            if w not in formulas:
                continue
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            comps.append(comp)

    import sys
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 10 * len(formulas) + 100))
    try:
        for v in formulas:
            if v not in index:
                strong(v)
    finally:
        sys.setrecursionlimit(old)
    cycles = [sorted(c) for c in comps if len(c) > 1 or (c[0] in deps.get(c[0], ()))]
    in_cycle = {v for c in cycles for v in c}
    for c in cycles:
        for v in c:
            if results[v].error is None:
                results[v].error = "circular definition: " + " -> ".join(c + [c[0]])

    order: list[str] = []
    seen: set[str] = set()
    for comp in comps:            # Tarjan emits components in reverse topological order (dependencies first)
        for v in comp:
            if v not in seen:
                seen.add(v)
                order.append(v)

    values: dict[str, float] = dict(inputs)
    for name in order:
        r = results[name]
        if r.error or name in in_cycle:
            continue
        failed = [d for d in deps[name] if d in formulas and not results[d].ok]
        if failed:
            r.error = "depends on failed: " + ", ".join(sorted(failed))
            continue
        bl = [d for d in deps[name] if d in blocked and d not in values]
        if bl:
            r.error = "; ".join(f"{d}: {blocked[d]}" for d in sorted(bl))
            continue
        miss = [d for d in deps[name] if d not in values and d not in formulas]
        if miss:
            r.error = "undefined: " + ", ".join(sorted(miss))
            continue
        try:
            r.value = evaluate(ast[name], values, r.formula)
            values[name] = r.value
        except FormulaError as e:
            r.error = str(e)
    return GraphResult(results, order, cycles, undefined)
