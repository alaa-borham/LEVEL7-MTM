"""GRAFIS X-value table of a part ("GRAFIS – X Values" window), block ``(3, part, 3, 1)``.

Layout (derived from ``1-S- alshiaka-2027.mdl`` part 002 and checked against the owner's screenshot of the
X-value window: x1 ``_xxxxx_x = 1500``, ``__HN40_0 = 1320``; x3 ``shoulder--B`` …):

    record  u32 length in 8-byte units (3 or 5)
            u16 X number
            8 bytes size name ("        " = default entry shown as _xxxxx_x, or "__HN40_0" …)
            f32 value (mm)
            [length 5: 16 bytes label, NUL terminated — the X name, e.g. "shoulder--B"]

The first record of every X is its default entry; the records after it are size-specific values.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .container import Container, read_container


@dataclass
class XValue:
    number: int
    name: str = ""
    default: float | None = None
    sizes: dict[str, float] = field(default_factory=dict)

    @property
    def x(self) -> str:
        return f"X{self.number}"


def read_xvalues(source, part: int = 2) -> dict[int, XValue]:
    cont = source if isinstance(source, Container) else read_container(source)
    b = cont.get(3, part, 3, 1)
    out: dict[int, XValue] = {}
    if b is None:
        return out
    p = b.payload
    pos = 0
    while pos + 24 <= len(p):
        (units,) = struct.unpack_from("<I", p, pos)
        if units not in (3, 5) or pos + units * 8 > len(p):
            break
        num = struct.unpack_from("<H", p, pos + 4)[0]
        size = p[pos + 6:pos + 14].decode("latin-1").strip()
        (val,) = struct.unpack_from("<f", p, pos + 16)
        xv = out.setdefault(num, XValue(num))
        if units == 5:
            xv.name = p[pos + 24:pos + 40].split(b"\0", 1)[0].decode("cp1256", "replace").strip()
        if size:
            xv.sizes[size] = round(val, 3)
        else:
            xv.default = round(val, 3)
        pos += units * 8
    return out
