"""Production parts of a GRAFIS model (MDL): which parts are cut, in which material, how many normal / mirrored.

Stored twice, both are written (layout documented and verified by LEVEL7 Nesting, formats/grafis/mdl_writer.py):

* the part's #TLIST line in text block (2,1,0,0): production column ``  x O 01 01`` (material, normal copies,
  mirrored copies) or 12 spaces for a part that is not produced;
* the material record in block (3, part, 6, 6): a list of records ``u16 units, u16 type`` of ``8 * units``
  bytes; type 1 = material record: +4 u8 0xFF produced / 0x00 not, +5 material code, +6 u16 copies in total
  (normal + mirrored), +8 u16 mirrored, +10 u16 constant per material, +12 u16 1 = produced, +16 material name.

A material is written only when the model already has a part in it (its record is the pattern).
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass

from .container import Container
from .textsections import read_text

ENC = "cp1256"
TEXT_BLOCK = (2, 1, 0, 0)
MAT = (6, 6)
_PROD = re.compile(r"(?:  x ([A-Z]) (\d\d) (\d\d) | {12})(?=ID=)")


class PartsError(ValueError):
    pass


@dataclass(frozen=True)
class PartSpec:
    material: str = "O"
    copies: int = 1          # normal copies
    mirrored: int = 0        # mirrored copies


def _records(payload: bytes, bid) -> list[bytes]:
    out, k = [], 0
    while k < len(payload):
        if k + 4 > len(payload):
            raise PartsError(f"block {bid}: record list cut off")
        n = struct.unpack_from("<H", payload, k)[0]
        if n == 0:
            if payload[k:].strip(b"\x00"):
                raise PartsError(f"block {bid}: unknown record layout")
            break
        if k + 8 * n > len(payload):
            raise PartsError(f"block {bid}: record longer than the block")
        out.append(payload[k:k + 8 * n])
        k += 8 * n
    return out


def _material_record(payload: bytes, bid) -> bytes | None:
    return next((r for r in _records(payload, bid) if struct.unpack_from("<H", r, 2)[0] == 1 and len(r) >= 16),
                None)


def production(cont: Container) -> dict[int, tuple[str, PartSpec | None]]:
    """{part number: (name, PartSpec or None when not produced)} as the material records say."""
    tb = cont.get(*TEXT_BLOCK)
    if tb is None:
        raise PartsError("the model has no text block")
    out = {}
    for e in read_text(cont).parts:
        b = cont.get(3, e.number, *MAT)
        rec = _material_record(b.payload, b.bid) if b is not None else None
        spec = None
        if rec is not None and rec[4] == 0xFF:
            total, mir = struct.unpack_from("<2H", rec, 6)
            spec = PartSpec(chr(rec[5]).strip(), total - mir, mir)
        out[e.number] = (e.name, spec)
    return out


def parts_payloads(cont: Container, wanted: dict[int, PartSpec]) -> tuple[dict, list[int]]:
    """New payloads that make exactly the parts in ``wanted`` production parts. Returns (payloads, changed)."""
    tb = cont.get(*TEXT_BLOCK)
    if tb is None:
        raise PartsError("the model has no text block")
    ts = read_text(cont)
    if ts.sbinfo:
        raise PartsError("this is a marker file (SCB), not a model file (MDL)")
    entries = {e.number: e for e in ts.parts}
    missing = sorted(set(wanted) - set(entries))
    if missing:
        raise PartsError(f"part(s) {', '.join(f'{n:03d}' for n in missing)} not in the model's part list")
    patterns: dict[str, bytes] = {}
    for e in ts.parts:
        b = cont.get(3, e.number, *MAT)
        rec = _material_record(b.payload, b.bid) if b is not None else None
        if rec is not None:
            patterns.setdefault(chr(rec[5]).strip(), rec)
    cut = tb.payload.find(b"\x00")
    cut = len(tb.payload) if cut < 0 else cut
    text, tail = tb.payload[:cut].decode(ENC), tb.payload[cut:]      # tail (terminator, padding) kept as is
    lines = text.split("\r\n")
    payloads, changed = {}, []
    for num, e in entries.items():
        want = wanted.get(num)
        prod, mat = want is not None, (want.material.strip().upper() if want else None)
        n, m = (want.copies, want.mirrored) if want else (0, 0)
        if prod and (not mat or n < 0 or m < 0 or n + m == 0 or n > 99 or m > 99):
            raise PartsError(f"part {num:03d} {e.name}: material and 1…99 copies are needed")
        bid = (3, num) + MAT
        b = cont.get(*bid)
        own = _material_record(b.payload, bid) if b is not None else None
        tl = _PROD.search(e.raw)
        if tl is None:
            raise PartsError(f"part {num:03d} {e.name}: #TLIST line in an unknown layout")
        rec_state = (False, None, 0, 0)
        if own is not None and own[4] == 0xFF:
            ct, cm = struct.unpack_from("<2H", own, 6)
            rec_state = (True, chr(own[5]).strip(), ct, cm)
        tl_state = (True, tl.group(1), int(tl.group(2)) + int(tl.group(3)), int(tl.group(3))) if tl.group(1) \
            else (False, None, 0, 0)
        target = (True, mat, n + m, m) if prod else (False, None, 0, 0)
        if rec_state == tl_state == target:
            continue
        if b is None and prod:
            raise PartsError(f"part {num:03d} {e.name}: material block missing in the model")
        if b is not None and (prod or own is not None):
            recs = _records(b.payload, bid)
            pat = own if own is not None and (not prod or chr(own[5]).strip() == mat) else patterns.get(mat or "")
            if pat is None:
                raise PartsError(f"part {num:03d} {e.name}: material {mat!r} is not used by any part of this model, "
                                 f"so its GRAFIS record is not known")
            rec = bytearray(pat)
            rec[4] = 0xFF if prod else 0x00
            if prod:
                struct.pack_into("<2H", rec, 6, n + m, m)
            struct.pack_into("<H", rec, 12, 1 if prod else 0)
            rest = b.payload[sum(len(r) for r in recs):]
            k = next((i for i, r in enumerate(recs) if r == own), None) if own is not None else None
            recs = recs[:k] + [bytes(rec)] + recs[k + 1:] if k is not None else recs + [bytes(rec)]
            payloads[bid] = b"".join(recs) + rest
        idx = next((i for i, ln in enumerate(lines) if ln == e.raw), None)
        if idx is None:
            raise PartsError(f"part {num:03d} {e.name}: #TLIST line in an unknown layout")
        lines[idx] = _PROD.sub(f"  x {mat} {n:02d} {m:02d} " if prod else " " * 12, lines[idx], count=1)
        changed.append(num)
    new_text = "\r\n".join(lines)
    if new_text != text:
        payloads[TEXT_BLOCK] = new_text.encode(ENC) + (tail or b"\x00")
    return payloads, changed
