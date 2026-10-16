"""Write an order's GRAFIS model (MDL): a copy of the base model with the order's X values filled in and, when
configured, the production parts chosen by the order's design options. Nobody types in GRAFIS.

* X values (blocks ``(3, part, 3, 1)``): the default entry (``_xxxxx_x``) and every size entry (``__HN40_0`` …) of
  a targeted X get the order value, so GRAFIS uses it whatever size it computes.
* Production parts (see ``parts.py``): exactly the configured parts are produced, with their material and normal /
  mirrored copies; every other part is set to "not produced".

Every block that is not changed is copied byte for byte. Changed blocks get a recomputed GRAFIS check value
(GRAFIS verifies it), and so does the file header.

Safety (nothing is written otherwise):
* the template's own check values are reproduced first, for the file header and for every block that changes;
* an X that is not in the part's table, or a part that is not in the model, is refused;
* the result is read back: X values, production parts, all check values, every other block identical.
"""
from __future__ import annotations

import os
import struct

from .container import (Container, block_bytes, computed_check, grafis_check, parse_container, stored_check,
                        BLOCK_MARK, TRAILER)
from .parts import PartsError, PartSpec, production, parts_payloads
from .xvalues import XTABLE, parse_xtable


class XWriteError(ValueError):
    pass


def rebuild(data: bytes, cont: Container, payloads: dict) -> bytes:
    """The container again in the same block order; blocks in ``payloads`` get the new payload (zero padded to
    8 bytes) and a new check value; the file length field and header check value are updated."""
    out = bytearray(data[:80])
    for b in cont.blocks:
        if b.bid not in payloads:
            out += block_bytes(data, b)
            continue
        p = payloads[b.bid]
        p = p + b"\x00" * ((-(32 + len(p))) % 8)
        head = struct.pack("<II", 0, (32 + len(p)) // 8) + BLOCK_MARK + struct.pack("<4I", *b.bid)
        out += struct.pack("<I", grafis_check(head + p)) + head[4:] + p
    out += TRAILER
    struct.pack_into("<I", out, 4, (len(out) - 8) // 8)
    struct.pack_into("<I", out, 0, 0)
    struct.pack_into("<I", out, 0, grafis_check(bytes(out[:80])))
    return bytes(out)


def build_order_model(data: bytes, values: dict[tuple[int, int], float],
                      parts: dict[int, PartSpec] | None = None) -> tuple[bytes, list[int]]:
    """``values``: {(part, X number): mm}; ``parts``: {part number: PartSpec} to produce, None = keep the model's.
    Returns (new file content, part numbers whose production changed)."""
    cont = parse_container(data)
    if cont.errors:
        raise XWriteError("template MDL is damaged: " + "; ".join(cont.errors))
    if grafis_check(b"\0\0\0\0" + data[4:80]) != struct.unpack_from("<I", data, 0)[0]:
        raise XWriteError("cannot reproduce the GRAFIS check value of the template's file header — not written")
    payloads: dict = {}
    by_part: dict[int, dict[int, float]] = {}
    for (part, num), v in values.items():
        by_part.setdefault(part, {})[num] = float(v)
    for part, xs in sorted(by_part.items()):
        b = cont.get(3, part, *XTABLE)
        if b is None:
            raise XWriteError(f"part {part:03d} has no X-value table")
        table = parse_xtable(b.payload)
        p = bytearray(b.payload)
        for num, v in xs.items():
            if num not in table:
                raise XWriteError(f"X{num} is not in the X-value table of part {part:03d}")
            for off in table[num].offsets:
                struct.pack_into("<f", p, off, v)
        payloads[b.bid] = bytes(p)
    changed_parts: list[int] = []
    if parts is not None:
        try:
            pp, changed_parts = parts_payloads(cont, parts)
        except PartsError as e:
            raise XWriteError(str(e)) from None
        payloads.update(pp)
    for b in cont.blocks:
        if b.bid in payloads and computed_check(data, b) != stored_check(data, b):
            raise XWriteError(f"cannot reproduce the GRAFIS check value of block {b.bid} — not written")
    result = rebuild(data, cont, payloads)
    verify(data, result, values, parts, set(payloads))
    return result, changed_parts


def write_xvalues(data: bytes, values: dict[tuple[int, int], float]) -> bytes:
    return build_order_model(data, values)[0]


def verify(template: bytes, result: bytes, values, parts, changed: set) -> None:
    a, b = parse_container(template), parse_container(result)
    if b.errors or [x.bid for x in a.blocks] != [y.bid for y in b.blocks]:
        raise XWriteError("written MDL does not have the template's block chain")
    if grafis_check(b"\0\0\0\0" + result[4:80]) != struct.unpack_from("<I", result, 0)[0]:
        raise XWriteError("file header check value wrong after writing")
    for x, y in zip(a.blocks, b.blocks):
        if computed_check(result, y) != stored_check(result, y):
            raise XWriteError(f"check value of block {y.bid} wrong after writing")
        if x.bid not in changed and block_bytes(template, x)[4:] != block_bytes(result, y)[4:]:
            raise XWriteError(f"block {x.bid} changed although nothing was written to it")
    for (part, num), v in values.items():
        xv = parse_xtable(b.get(3, part, *XTABLE).payload)[num]
        f = round(struct.unpack("<f", struct.pack("<f", v))[0], 3)
        got = [xv.default] + list(xv.sizes.values())
        if any(abs(g - f) > 1e-6 for g in got):
            raise XWriteError(f"part {part:03d} X{num}: read back {got}, expected {v}")
    if parts is not None:
        have = {n: s for n, (_, s) in production(b).items() if s is not None}
        want = {n: PartSpec(s.material.strip().upper(), s.copies, s.mirrored) for n, s in parts.items()}
        if have != want:
            raise XWriteError(f"production parts read back {sorted(have)} ≠ wanted {sorted(want)}")


def write_order_mdl(template_path: str, values: dict[tuple[int, int], float], out_path: str,
                    parts: dict[int, PartSpec] | None = None) -> list[int]:
    with open(template_path, "rb") as fh:
        data = fh.read()
    result, changed = build_order_model(data, values, parts)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp = out_path + ".part"          # GRAFIS / the watcher only ever sees a complete file
    with open(tmp, "wb") as fh:
        fh.write(result)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, out_path)
    return changed


def check_all(data: bytes) -> tuple[int, int]:
    """(blocks whose stored check value is reproduced, total blocks) — diagnostics."""
    cont: Container = parse_container(data)
    ok = sum(computed_check(data, b) == stored_check(data, b) for b in cont.blocks)
    return ok, len(cont.blocks)
