"""GRAFIS X-value table of a part ("GRAFIS – X Values" window), block ``(3, part, 3, 1)``.

Layout (derived from ``1-S- alshiaka-2027.mdl`` parts 002 / 005 / 006 and checked against the owner's screenshot
of the X-value window: x1 ``_xxxxx_x = 1500``, ``__HN40_0 = 1320``; x3 ``shoulder--B`` …):

    record  u32 length in 8-byte units (3 for a size entry, 3 or more for a default entry with a label)
            u16 X number
            8 bytes size name ("        " = default entry shown as _xxxxx_x, or "__HN40_0" …)
            u8  0
            u8  label length
            f32 value (mm)
            u32 0
            [label, ``label length`` bytes, padded to the record end — the X name, e.g. "shoulder--B"]

The first record of every X is its default entry; the records after it are size-specific values.
Part 002 of the sample model holds X1 … X50.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .container import Container, read_container

XTABLE = (3, 1)          # block id (3, part, 3, 1)


@dataclass
class XValue:
    number: int
    name: str = ""
    default: float | None = None
    sizes: dict[str, float] = field(default_factory=dict)
    offsets: list[int] = field(default_factory=list)       # payload offsets of the f32 values (default first)

    @property
    def x(self) -> str:
        return f"X{self.number}"


class XTableError(ValueError):
    pass


def parse_xtable(p: bytes) -> dict[int, XValue]:
    out: dict[int, XValue] = {}
    pos = 0
    while pos < len(p):
        if pos + 24 > len(p):
            raise XTableError(f"X table: truncated record at {pos}")
        (units,) = struct.unpack_from("<I", p, pos)
        if units < 3 or pos + units * 8 > len(p):
            raise XTableError(f"X table: invalid record length {units} at {pos}")
        num = struct.unpack_from("<H", p, pos + 4)[0]
        size = p[pos + 6:pos + 14].decode("latin-1").strip()
        n = p[pos + 15]
        (val,) = struct.unpack_from("<f", p, pos + 16)
        xv = out.setdefault(num, XValue(num))
        if n and units > 3:
            xv.name = p[pos + 24:pos + 24 + min(n, units * 8 - 24)].decode("cp1256", "replace").strip()
        if size:
            xv.sizes[size] = round(val, 3)
        else:
            xv.default = round(val, 3)
        xv.offsets.append(pos + 16)
        pos += units * 8
    return out


def read_xvalues(source, part: int = 2) -> dict[int, XValue]:
    cont = source if isinstance(source, Container) else read_container(source)
    b = cont.get(3, part, *XTABLE)
    return parse_xtable(b.payload) if b is not None else {}
