"""Minimal reader for the GRAFIS block container (MDL / SCB).

The container layout is documented by LEVEL7 Nesting (docs/GRAFIS_FORMAT.md in alaa-borham/LEVEL7-Nesting),
where it was verified on several files. LEVEL7 MTM only needs read access to the text and formula blocks, so
this module re-implements the documented block chain (read-only) instead of depending on the nesting package.
Full piece geometry is read through LEVEL7 Nesting when it is installed (see ``level7mtm.grafis.reference``).

    0      u32  check value
    4      u32  (file size - 8) / 8
    8      "GRAFIS-Modelldatei" + 2-letter variant
    80     block chain: u32 check, u32 length in 8-byte units, "BLO5kenn", 4 x u32 id, payload
    end-8  "mdlEND_5"
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

SIGNATURE = b"GRAFIS-Modelldatei"
BLOCK_MARK = b"BLO5kenn"
TRAILER = b"mdlEND_5"
CHAIN_START = 80
MAX_FILE_SIZE = 200 * 1024 * 1024


class GrafisFormatError(Exception):
    pass


@dataclass
class Block:
    offset: int
    bid: tuple[int, int, int, int]
    payload: bytes


@dataclass
class Container:
    path: str
    size: int
    sha256: str
    variant: str
    blocks: list[Block]
    errors: list[str] = field(default_factory=list)

    def get(self, *bid: int) -> Block | None:
        for b in self.blocks:
            if b.bid == tuple(bid):
                return b
        return None

    def find(self, a: int, c: int | None = None, d: int | None = None) -> list[Block]:
        """All blocks with id (a, *, c, d); None matches anything."""
        return [b for b in self.blocks
                if b.bid[0] == a and (c is None or b.bid[2] == c) and (d is None or b.bid[3] == d)]

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_container(data: bytes, path: str = "<memory>") -> Container:
    if len(data) > MAX_FILE_SIZE:
        raise GrafisFormatError(f"file too large ({len(data)} bytes)")
    if len(data) < CHAIN_START + 8 or data[8:8 + len(SIGNATURE)] != SIGNATURE:
        raise GrafisFormatError("not a GRAFIS file: signature 'GRAFIS-Modelldatei' missing at offset 8")
    variant = data[8 + len(SIGNATURE):10 + len(SIGNATURE)].decode("latin-1")
    cont = Container(path, len(data), hashlib.sha256(data).hexdigest(), variant, [])
    (h1,) = struct.unpack_from("<I", data, 4)
    if h1 * 8 + 8 != len(data):
        cont.errors.append(f"length field {h1 * 8 + 8} != file size {len(data)} (truncated or damaged file)")
    end = len(data) - 8 if data[-8:] == TRAILER else len(data)
    if data[-8:] != TRAILER:
        cont.errors.append("trailer mdlEND_5 missing (truncated file?)")
    pos = CHAIN_START
    while pos < end:
        if pos + 32 > end:
            cont.errors.append(f"truncated block header at offset {pos}")
            break
        _check, units = struct.unpack_from("<II", data, pos)
        if data[pos + 8:pos + 16] != BLOCK_MARK:
            cont.errors.append(f"block marker missing at offset {pos}")
            break
        if units < 4 or pos + units * 8 > end:
            cont.errors.append(f"invalid block length {units} at offset {pos}")
            break
        bid = struct.unpack_from("<4I", data, pos + 16)
        cont.blocks.append(Block(pos, bid, bytes(data[pos + 32:pos + units * 8])))
        pos += units * 8
    return cont


def read_container(path: str) -> Container:
    with open(path, "rb") as fh:
        return parse_container(fh.read(), path)


CHECK_MOD = 1024000000


def grafis_check(data: bytes) -> int:
    """GRAFIS check value of a block (or of the 80-byte file header), computed with its own check field set to 0.

    Algorithm established by LEVEL7 Nesting (docs/GRAFIS_FORMAT.md "Check values"): the bytes read as signed
    16-bit little-endian words, word k (1-based) weighted by k, kept in a signed 32-bit integer from which
    1,024,000,000 is subtracted whenever the running total reaches it. GRAFIS verifies it on opening — a stale value
    makes GRAFIS report the file as corrupt.
    """
    if len(data) % 2:
        data += b"\0"
    words = struct.unpack(f"<{len(data) // 2}h", data)
    acc = 0
    for k, x in enumerate(words, 1):
        acc = _s32(acc + _s32(k * x))
        if acc >= CHECK_MOD:
            acc -= CHECK_MOD
    return acc & 0xFFFFFFFF


def _s32(v: int) -> int:
    return ((v + 0x80000000) & 0xFFFFFFFF) - 0x80000000


def block_bytes(data: bytes, b: Block) -> bytes:
    """The whole block (header + payload) as stored in the file."""
    return data[b.offset:b.offset + 32 + len(b.payload)]


def stored_check(data: bytes, b: Block) -> int:
    return struct.unpack_from("<I", data, b.offset)[0]


def computed_check(data: bytes, b: Block) -> int:
    return grafis_check(b"\0\0\0\0" + block_bytes(data, b)[4:])
