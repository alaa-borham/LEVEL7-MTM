"""Restricted expression language for pattern rules.

One language serves two purposes:

* GRAFIS formulas imported from MDL files, e.g. ``X7/6.7+Z1`` or
  ``CMDIF[X4>=64]{51.5}[X4<64]{48}{50}``;
* LEVEL7 model rules, e.g. ``chest_half / 2 + ease.front`` or ``if(x > 10, 5, 0)``.

Nothing is ever passed to Python's ``eval``: the text is tokenised and parsed into a small tree that only
knows numbers, variables, + - * /, comparisons, ``and``/``or``/``not``, a fixed list of functions and the
GRAFIS ``CMDIF`` conditional. There is no attribute access, indexing, import, file or network access.

CMDIF grammar (as found in the MDL files):  ``CMDIF [cond] {value} ([cond] {value})* {else}``
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

MAX_LENGTH = 2000          # characters per formula
MAX_DEPTH = 60             # nesting depth of the parse tree


class FormulaError(ValueError):
    """Syntax or evaluation error; ``pos`` is the character offset when known."""

    def __init__(self, message: str, formula: str = "", pos: int | None = None):
        self.message = message
        self.formula = formula
        self.pos = pos
        where = f" at position {pos + 1}" if pos is not None else ""
        super().__init__(f"{message}{where}: {formula}" if formula else message)


class UndefinedVariable(FormulaError):
    def __init__(self, name: str, formula: str = ""):
        self.name = name
        super().__init__(f"undefined variable {name}", formula)


_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)
  | (?P<op><=|>=|<>|!=|==|&&|\|\||[-+*/^(),<>=\[\]{}&|!])
""", re.VERBOSE)

FUNCTIONS = {
    "min": (1, None, min),
    "max": (1, None, max),
    "abs": (1, 1, abs),
    "sqrt": (1, 1, math.sqrt),
    "round": (1, 2, lambda x, n=0: round(x, int(n))),
    "floor": (1, 1, math.floor),
    "ceil": (1, 1, math.ceil),
    "sin": (1, 1, lambda d: math.sin(math.radians(d))),      # degrees
    "cos": (1, 1, lambda d: math.cos(math.radians(d))),
    "tan": (1, 1, lambda d: math.tan(math.radians(d))),
    "asin": (1, 1, lambda v: math.degrees(math.asin(v))),
    "acos": (1, 1, lambda v: math.degrees(math.acos(v))),
    "atan": (1, 1, lambda v: math.degrees(math.atan(v))),
    "atan2": (2, 2, lambda y, x: math.degrees(math.atan2(y, x))),
    "hypot": (2, 2, math.hypot),
    "clamp": (3, 3, lambda v, lo, hi: max(lo, min(hi, v))),
}
KEYWORDS = {"and", "or", "not", "if", "CMDIF"}


@dataclass(frozen=True)
class Tok:
    kind: str
    text: str
    pos: int


def tokenize(src: str) -> list[Tok]:
    if len(src) > MAX_LENGTH:
        raise FormulaError(f"formula longer than {MAX_LENGTH} characters", src[:60] + "…")
    out, pos = [], 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m:
            raise FormulaError(f"unexpected character {src[pos]!r}", src, pos)
        kind = m.lastgroup
        if kind != "ws":
            out.append(Tok(kind, m.group(), pos))
        pos = m.end()
    out.append(Tok("end", "", len(src)))
    return out


# AST nodes are tuples: ("num", v) ("var", name) ("neg", a) ("not", a) ("bin", op, a, b)
# ("cmp", op, a, b) ("and", a, b) ("or", a, b) ("call", name, [args]) ("cond", [(c, v)...], else)

class _Parser:
    def __init__(self, src: str):
        self.src = src
        self.toks = tokenize(src)
        self.i = 0
        self.depth = 0

    def peek(self) -> Tok:
        return self.toks[self.i]

    def take(self, text: str | None = None) -> Tok:
        t = self.toks[self.i]
        if text is not None and t.text != text:
            want = repr(text)
            got = repr(t.text) if t.kind != "end" else "end of formula"
            raise FormulaError(f"expected {want}, found {got}", self.src, t.pos)
        self.i += 1
        return t

    def _enter(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise FormulaError("formula nested too deeply", self.src, self.peek().pos)

    def parse(self):
        node = self.logic()
        t = self.peek()
        if t.kind != "end":
            raise FormulaError(f"unexpected {t.text!r}", self.src, t.pos)
        return node

    def logic(self):
        self._enter()
        node = self.conj()
        while self.peek().text in ("or", "||", "|"):
            self.take()
            node = ("or", node, self.conj())
        self.depth -= 1
        return node

    def conj(self):
        node = self.negation()
        while self.peek().text in ("and", "&&", "&"):
            self.take()
            node = ("and", node, self.negation())
        return node

    def negation(self):
        if self.peek().text in ("not", "!"):
            self.take()
            return ("not", self.negation())
        return self.comparison()

    def comparison(self):
        node = self.additive()
        t = self.peek()
        if t.text in ("<", "<=", ">", ">=", "=", "==", "<>", "!="):
            self.take()
            op = {"=": "==", "<>": "!="}.get(t.text, t.text)
            node = ("cmp", op, node, self.additive())
        return node

    def additive(self):
        node = self.term()
        while self.peek().text in ("+", "-"):
            op = self.take().text
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.unary()
        while self.peek().text in ("*", "/"):
            op = self.take().text
            node = ("bin", op, node, self.unary())
        return node

    def unary(self):
        if self.peek().text == "-":
            self.take()
            return ("neg", self.unary())
        if self.peek().text == "+":
            self.take()
            return self.unary()
        return self.power()

    def power(self):
        node = self.primary()
        if self.peek().text == "^":
            self.take()
            node = ("bin", "^", node, self.unary())
        return node

    def primary(self):
        self._enter()
        t = self.peek()
        if t.kind == "num":
            self.take()
            node = ("num", float(t.text))
        elif t.text == "(":
            self.take()
            node = self.logic()
            self.take(")")
        elif t.kind == "name" and t.text.upper() == "CMDIF":
            node = self.cmdif()
        elif t.kind == "name" and self.toks[self.i + 1].text == "(":
            node = self.call()
        elif t.kind == "name":
            if t.text in KEYWORDS:
                raise FormulaError(f"unexpected keyword {t.text!r}", self.src, t.pos)
            self.take()
            node = ("var", t.text)
        else:
            what = repr(t.text) if t.kind != "end" else "end of formula"
            raise FormulaError(f"unexpected {what}", self.src, t.pos)
        self.depth -= 1
        return node

    def call(self):
        name_tok = self.take()
        name = name_tok.text
        self.take("(")
        args = []
        if self.peek().text != ")":
            args.append(self.logic())
            while self.peek().text == ",":
                self.take()
                args.append(self.logic())
        self.take(")")
        if name == "if":
            if len(args) != 3:
                raise FormulaError("if() needs 3 arguments: if(condition, then, else)", self.src, name_tok.pos)
            return ("cond", [(args[0], args[1])], args[2])
        if name not in FUNCTIONS:
            raise FormulaError(f"unknown function {name!r}", self.src, name_tok.pos)
        lo, hi, _ = FUNCTIONS[name]
        if len(args) < lo or (hi is not None and len(args) > hi):
            raise FormulaError(f"wrong number of arguments for {name}()", self.src, name_tok.pos)
        return ("call", name, args)

    def cmdif(self):
        start = self.take()
        branches = []
        while self.peek().text == "[":
            self.take("[")
            cond = self.logic()
            self.take("]")
            self.take("{")
            val = self.logic()
            self.take("}")
            branches.append((cond, val))
        if not branches:
            raise FormulaError("CMDIF needs at least one [condition]{value}", self.src, start.pos)
        self.take("{")
        other = self.logic()
        self.take("}")
        return ("cond", branches, other)


def parse(src: str):
    if not src or not src.strip():
        raise FormulaError("empty formula", src)
    return _Parser(src).parse()


def references(node) -> set[str]:
    out: set[str] = set()

    def walk(n):
        k = n[0]
        if k == "var":
            out.add(n[1])
        elif k in ("neg", "not"):
            walk(n[1])
        elif k in ("bin", "cmp"):
            walk(n[2])
            walk(n[3])
        elif k in ("and", "or"):
            walk(n[1])
            walk(n[2])
        elif k == "call":
            for a in n[2]:
                walk(a)
        elif k == "cond":
            for c, v in n[1]:
                walk(c)
                walk(v)
            walk(n[2])
    walk(node)
    return out


def evaluate(node, env, formula: str = "") -> float:
    """Evaluate a parsed formula. ``env`` maps variable names to numbers (or is a callable lookup)."""
    lookup = env if callable(env) else (lambda name: env[name] if name in env else _missing(name, formula))

    def ev(n):
        k = n[0]
        if k == "num":
            return n[1]
        if k == "var":
            return float(lookup(n[1]))
        if k == "neg":
            return -ev(n[1])
        if k == "not":
            return 0.0 if ev(n[1]) else 1.0
        if k == "bin":
            a, b = ev(n[2]), ev(n[3])
            op = n[1]
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if op == "/":
                if b == 0:
                    raise FormulaError("division by zero", formula)
                return a / b
            if op == "^":
                return a ** b
        if k == "cmp":
            a, b, op = ev(n[2]), ev(n[3]), n[1]
            return float({"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b,
                          "==": abs(a - b) < 1e-9, "!=": abs(a - b) >= 1e-9}[op])
        if k == "and":
            return 1.0 if ev(n[1]) and ev(n[2]) else 0.0
        if k == "or":
            return 1.0 if ev(n[1]) or ev(n[2]) else 0.0
        if k == "call":
            fn = FUNCTIONS[n[1]][2]
            try:
                return float(fn(*[ev(a) for a in n[2]]))
            except (ValueError, OverflowError) as e:
                raise FormulaError(f"{n[1]}(): {e}", formula) from None
        if k == "cond":
            for c, v in n[1]:
                if ev(c):
                    return ev(v)
            return ev(n[2])
        raise FormulaError(f"internal: unknown node {k}", formula)

    val = ev(node)
    if math.isnan(val) or math.isinf(val):
        raise FormulaError("result is not a finite number", formula)
    return val


def _missing(name, formula):
    raise UndefinedVariable(name, formula)


def compute(src: str, env) -> float:
    return evaluate(parse(src), env, src)
