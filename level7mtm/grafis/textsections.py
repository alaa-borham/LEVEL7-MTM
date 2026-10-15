"""Text block (2,1,0,0) of GRAFIS MDL/SCB files: #GRDTAB, #TLIST, #TFLIST, #SBINFO.

Only what LEVEL7 MTM needs for reference data and reports. The full reader (geometry, placements) lives in
LEVEL7 Nesting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .container import Container, read_container

ENCODING = "cp1256"


@dataclass
class SizeEntry:
    index: int
    name: str
    ref: str | None


@dataclass
class PartEntry:
    number: int
    name: str
    production: bool
    material: str | None
    count: int | None
    mirrored: int | None
    piece_id: str
    raw: str


@dataclass
class MeasureRow:
    code: str          # T2M1 ...
    unit: str
    letter: str        # A, B ... AR
    values: list[str]  # one per GRDTAB entry


@dataclass
class TextInfo:
    version: str | None = None
    sizes: list[SizeEntry] = field(default_factory=list)
    parts: list[PartEntry] = field(default_factory=list)
    measure_tables: dict[int, list[MeasureRow]] = field(default_factory=dict)
    sbinfo: dict[str, str] = field(default_factory=dict)
    unparsed: list[str] = field(default_factory=list)


_PART = re.compile(r"^\s*[*>]?\s*(\d{3})\s+(x\s+)?(.*?)\s*(ID=[0-9A-Fa-f]+)(.*)$")
_PROD = re.compile(r"\bx\s+([A-Z])\s+(\d\d)\s+(\d\d)\s*$")


def read_text(source: str | Container) -> TextInfo:
    cont = source if isinstance(source, Container) else read_container(source)
    blk = cont.get(2, 1, 0, 0)
    info = TextInfo()
    if blk is None:
        info.unparsed.append("text block (2,1,0,0) missing")
        return info
    text = blk.payload.split(b"\0", 1)[0].decode(ENCODING, "replace")
    section, table_part, header = None, None, 0
    for line in text.split("\r\n"):
        if line.startswith("#"):
            tag = line.strip()
            section = tag
            if tag.startswith("#TFLIST") or tag.startswith("#BFLIST"):
                table_part = int(re.sub(r"\D", "", tag) or 0)
                info.measure_tables[table_part] = []
                header = 0
            continue
        if not line.strip():
            continue
        if section == "#VERSION":
            info.version = line.strip()
        elif section == "#GRDTAB":
            m = re.match(r"^\s*>\s*(\d+)\s+(\S+)(?:\s+=>\s+(\S+))?", line)
            if m:
                info.sizes.append(SizeEntry(int(m.group(1)), m.group(2), m.group(3)))
            else:
                info.unparsed.append(line)
        elif section == "#TLIST":
            m = _PART.match(line)
            if not m:
                info.unparsed.append(line)
                continue
            head = m.group(3)
            prod = _PROD.search(head)
            name = re.sub(r"\s+\d+\s+\d+\s*$", "", _PROD.sub("", head)).strip()
            name = re.sub(r"\s+\d+\s+\d+\s+[OF]\s*$", "", name).strip()
            info.parts.append(PartEntry(int(m.group(1)), name, bool(prod), prod.group(1) if prod else None,
                                        int(prod.group(2)) if prod else None, int(prod.group(3)) if prod else None,
                                        m.group(4)[3:], line))
        elif section and (section.startswith("#TFLIST") or section.startswith("#BFLIST")):
            cols = [c.strip() for c in line.split("\t")]
            if cols and cols[0] == "*":
                header += 1
                continue
            if len(cols) >= 4:
                info.measure_tables[table_part].append(MeasureRow(cols[0], cols[1], cols[2], cols[4:]))
        elif section == "#SBINFO":
            k, _, v = line.partition(" ")
            info.sbinfo[k.strip()] = v.strip()
    return info
