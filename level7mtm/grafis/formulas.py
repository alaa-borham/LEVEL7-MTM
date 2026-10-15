"""Z-value formulas stored in GRAFIS MDL files.

Found in blocks ``(3, part, 6, 34)`` of ``1-S- alshiaka-2027.mdl`` (GRAFIS 26, signature variant VA); the layout
below was derived from that file only (335 records in 30 parts, chain closes exactly in every block):

    +0   u16  record length in 8-byte units
    +2   u16  flags     0x0000 normal formula; 0x0C00 seen on blank/placeholder rows (meaning unknown);
                        0x4C00 value measured in the construction ("AB" = distance, extra byte +12 = 2);
                        0xCC00 measured curve length ("GL", extra byte +12 = 1)
    +4   u16  Z number (Z1, Z2 ... per part)
    +6   u16  group  (probably the construction step that owns the value; constant over consecutive rows)
    +8   u16  formula length
    +10  u16  length of description + history text
    +12  12 bytes, zero except the AB/GL marker in the first byte
    +24  formula text (Windows-1256), padded with NUL/space to a multiple of 8 bytes
    then description (first line) and the edit history: lines ``#F:dd-mm-yy=<previous formula>``

Status of this interpretation: the formula text, Z number, description and history are confirmed by reading
(the descriptions match the Arabic labels shown in GRAFIS, according to the project owner); the flags and the
group field are probable/unknown. The construction program that turns these values into geometry is stored
in other blocks and is NOT decoded — reading formulas does not by itself reproduce the pattern.
"""
from __future__ import annotations

import re
import struct
from dataclasses import asdict, dataclass, field

from ..formula.expr import FormulaError, parse, references
from .container import Container, read_container

ENCODING = "cp1256"
FLAG_PLACEHOLDER = 0x0C00
FLAG_MEASURED = 0x4000
MEASURED_KINDS = {"AB": "distance measured in the construction", "GL": "curve length measured in the construction"}
_HISTORY = re.compile(r"^#F:(\d\d-\d\d-\d\d)=(.*)$")


@dataclass
class HistoryEntry:
    date: str          # dd-mm-yy as written by GRAFIS
    formula: str


@dataclass
class ZRecord:
    part: int
    z: int
    formula: str
    description: str
    flags: int
    group: int
    history: list[HistoryEntry] = field(default_factory=list)
    measured: str | None = None            # "AB" / "GL" when the value comes from the construction geometry
    parse_error: str | None = None
    references: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"Z{self.z}"

    @property
    def placeholder(self) -> bool:
        return self.flags == FLAG_PLACEHOLDER and self.formula.strip() in ("", "0") and not self.description


@dataclass
class PartFormulas:
    part: int
    records: list[ZRecord]
    errors: list[str] = field(default_factory=list)


def canonical(name: str) -> str:
    """X01 -> X1, Z05 -> Z5, XG01 stays XG1. Leading zeros are treated as the same variable (assumption A-3)."""
    m = re.fullmatch(r"(XG|X|Z)0*(\d+)", name)
    return f"{m.group(1)}{int(m.group(2))}" if m else name


def canonical_formula(text: str) -> str:
    return re.sub(r"\b(XG|X|Z)0+(\d+)\b", lambda m: f"{m.group(1)}{int(m.group(2))}", text)


def parse_records(payload: bytes, part: int) -> PartFormulas:
    out = PartFormulas(part, [])
    pos = 0
    while pos + 24 <= len(payload):
        units, flags, z, group, flen, clen = struct.unpack_from("<HHHHHH", payload, pos)
        if units < 3 or pos + units * 8 > len(payload):
            out.errors.append(f"part {part}: invalid record length {units} at offset {pos}")
            break
        rec = payload[pos:pos + units * 8]
        ftext = rec[24:24 + flen].split(b"\0", 1)[0].decode(ENCODING, "replace").strip()
        cstart = 24 + ((flen + 7) // 8) * 8
        ctext = rec[cstart:cstart + clen].split(b"\0", 1)[0].decode(ENCODING, "replace")
        lines = ctext.split("\r\n")
        description = lines[0].strip() if lines and not lines[0].startswith("#F:") else ""
        hist = []
        for ln in lines[1:] if description or (lines and not lines[0].startswith("#F:")) else lines:
            m = _HISTORY.match(ln)
            if m:
                hist.append(HistoryEntry(m.group(1), m.group(2)))
        r = ZRecord(part, z, ftext, description, flags, group, hist)
        if flags & FLAG_MEASURED and ftext in MEASURED_KINDS:
            r.measured = ftext
        else:
            try:
                r.references = sorted(canonical(v) for v in references(parse(canonical_formula(ftext))))
            except FormulaError as e:
                r.parse_error = e.message + (f" (position {e.pos + 1})" if e.pos is not None else "")
        out.records.append(r)
        pos += units * 8
    if pos != len(payload) and not out.errors:
        out.errors.append(f"part {part}: {len(payload) - pos} trailing bytes")
    return out


def read_formulas(source: str | Container) -> dict[int, PartFormulas]:
    cont = source if isinstance(source, Container) else read_container(source)
    return {b.bid[1]: parse_records(b.payload, b.bid[1]) for b in cont.find(3, 6, 34)}


def to_json(parts: dict[int, PartFormulas]) -> dict:
    return {str(k): {"part": v.part, "errors": v.errors, "records": [asdict(r) for r in v.records]}
            for k, v in sorted(parts.items())}
