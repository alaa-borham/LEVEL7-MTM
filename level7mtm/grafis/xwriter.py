"""Write an order's X values into a copy of the GRAFIS model (MDL) — no typing in the X-value window.

Only the f32 values of the X-value tables (blocks ``(3, part, 3, 1)``) of the targeted X are changed, in place:
the default entry (``_xxxxx_x``) and every size entry (``__HN40_0`` …) of that X get the order value, so GRAFIS uses
it whatever size it computes. Record lengths, labels and every other block stay byte for byte as in the template.
The check value of each changed block is recomputed (GRAFIS verifies it); the file header does not change.

Safety (nothing is written otherwise):
* the template's own check values must be reproduced first, for the file header and for every block that changes;
* an X that is not in the part's table is refused;
* the result is read back: the values must be the order values, every other block identical, the checks valid.
"""
from __future__ import annotations

import os
import struct

from .container import (Container, block_bytes, computed_check, grafis_check, parse_container, stored_check)
from .xvalues import XTABLE, parse_xtable


class XWriteError(ValueError):
    pass


def write_xvalues(data: bytes, values: dict[tuple[int, int], float]) -> bytes:
    """``values``: {(part, X number): value in mm}. Returns the new file content."""
    cont = parse_container(data)
    if cont.errors:
        raise XWriteError("template MDL is damaged: " + "; ".join(cont.errors))
    if grafis_check(b"\0\0\0\0" + data[4:80]) != struct.unpack_from("<I", data, 0)[0]:
        raise XWriteError("cannot reproduce the GRAFIS check value of the template's file header — not written")
    out = bytearray(data)
    by_part: dict[int, dict[int, float]] = {}
    for (part, num), v in values.items():
        by_part.setdefault(part, {})[num] = float(v)
    for part, xs in sorted(by_part.items()):
        b = cont.get(3, part, *XTABLE)
        if b is None:
            raise XWriteError(f"part {part:03d} has no X-value table")
        if computed_check(data, b) != stored_check(data, b):
            raise XWriteError(f"cannot reproduce the GRAFIS check value of block {b.bid} — not written")
        table = parse_xtable(b.payload)
        for num, v in xs.items():
            if num not in table:
                raise XWriteError(f"X{num} is not in the X-value table of part {part:03d}")
            for off in table[num].offsets:
                struct.pack_into("<f", out, b.offset + 32 + off, v)
        struct.pack_into("<I", out, b.offset, 0)
        struct.pack_into("<I", out, b.offset, grafis_check(bytes(out[b.offset:b.offset + 32 + len(b.payload)])))
    result = bytes(out)
    verify(data, result, values)
    return result


def verify(template: bytes, result: bytes, values: dict[tuple[int, int], float]) -> None:
    a, b = parse_container(template), parse_container(result)
    if b.errors or len(a.blocks) != len(b.blocks) or len(template) != len(result) or template[:80] != result[:80]:
        raise XWriteError("written MDL does not have the template's structure")
    parts = {p for p, _ in values}
    for x, y in zip(a.blocks, b.blocks):
        if x.bid != y.bid or x.offset != y.offset:
            raise XWriteError("written MDL block chain differs from the template")
        if x.bid[0] == 3 and x.bid[1] in parts and x.bid[2:] == XTABLE:
            if computed_check(result, y) != stored_check(result, y):
                raise XWriteError(f"check value of block {y.bid} wrong after writing")
        elif block_bytes(template, x) != block_bytes(result, y):
            raise XWriteError(f"block {x.bid} changed although it holds no targeted X value")
    for (part, num), v in values.items():
        xv = parse_xtable(b.get(3, part, *XTABLE).payload)[num]
        f = struct.unpack("<f", struct.pack("<f", v))[0]
        got = [xv.default] + list(xv.sizes.values())
        if any(abs(g - round(f, 3)) > 1e-6 for g in got):
            raise XWriteError(f"part {part:03d} X{num}: read back {got}, expected {v}")


def write_order_mdl(template_path: str, values: dict[tuple[int, int], float], out_path: str) -> str:
    with open(template_path, "rb") as fh:
        data = fh.read()
    result = write_xvalues(data, values)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp = out_path + ".part"          # GRAFIS / the watcher only ever sees a complete file
    with open(tmp, "wb") as fh:
        fh.write(result)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, out_path)
    return out_path


def check_all(data: bytes) -> tuple[int, int]:
    """(blocks whose stored check value is reproduced, total blocks) — diagnostics."""
    cont: Container = parse_container(data)
    ok = sum(computed_check(data, b) == stored_check(data, b) for b in cont.blocks)
    return ok, len(cont.blocks)
