"""Research reader for the GRAFIS construction data (NOT a construction interpreter).

Findings on ``1-S- alshiaka-2027.mdl`` (see docs/research/GRAFIS_CONSTRUCTION.md):

* ``(3, part, 2, 0)``  construction protocol: fixed 32-byte records; their count equals the first number of the
  part's #TLIST line (part 002: 286, front1: 76). Records reference element ids (0x07D1 = 2001 …) that also
  appear as object ids in the evaluated geometry blocks ``(3, part, 7, size)``. Meaning of most record types: U.
* ``(3, part, 3, n)``  calls of GRAFIS library programs (``basis_d`` … ``GraPrg_GK_QS|QQ|FL|EK``) with labelled
  parameters ("Verschiebewert [mm]", "X-Abst.", "Länge [mm]" …). Only 4 programs occur in the whole file.
* ``(3, part, 6, 34)`` Z formulas (decoded, see formulas.py); ``(3, part, 6, 4)`` evaluated measurement table
  (#TFLIST values as float); ``(3, part, 6, 37)`` labelled measurement points A, B, C …

What a GRAFIS program does geometrically is defined inside GRAFIS, not in the MDL file.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from .container import Container, read_container


@dataclass
class ProtocolRecord:
    index: int
    a: int
    b: int
    raw: bytes

    @property
    def element_refs(self) -> list[int]:
        """u16 values in the element id range (2001 … 4000) — probable references."""
        vals = struct.unpack_from("<14H", self.raw, 4)
        return [v for v in vals if 2001 <= v <= 4000]


@dataclass
class ProgramStep:
    step: int
    program: str
    base: str
    parameters: list[tuple[str, float | None]] = field(default_factory=list)


def protocol(cont: Container, part: int) -> list[ProtocolRecord]:
    b = cont.get(3, part, 2, 0)
    if b is None:
        return []
    p = b.payload
    return [ProtocolRecord(i // 32, *struct.unpack_from("<HH", p, i), p[i:i + 32]) for i in range(0, len(p) - 31, 32)]


_PARAM = re.compile(rb"\x08\x00\x00\x00(.)\x00 {8}\x00\x28(.{4})\x00{4}(.{40})", re.S)


def program_steps(cont: Container, part: int) -> list[ProgramStep]:
    out = []
    for b in sorted((x for x in cont.find(3, 3) if x.bid[1] == part), key=lambda x: x.bid[3]):
        m = re.search(rb"(basis_[A-Za-z0-9_]+?)(GraPrg_[A-Za-z0-9_]+)", b.payload)
        if not m:
            continue
        st = ProgramStep(b.bid[3], m.group(2).decode("latin-1"), m.group(1).decode("latin-1"))
        for pm in _PARAM.finditer(b.payload):
            label = pm.group(3).decode("cp1252", "replace").strip()
            if not label or set(label) <= {"="}:
                continue
            (val,) = struct.unpack("<f", pm.group(2))
            st.parameters.append((label, val))
        out.append(st)
    return out


def summary(path: str) -> dict:
    cont = read_container(path)
    parts = sorted({b.bid[1] for b in cont.find(3, 2, 0)})
    return {p: {"protocol_records": len(protocol(cont, p)),
                "programs": [s.program for s in program_steps(cont, p)]} for p in parts}
