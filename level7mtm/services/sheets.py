"""Excel (.xlsx) and CSV sheets for the size table and the piece options: written for the owner to fill in, read back
into the setup windows. Plain zip + XML (no extra library in the build)."""
from __future__ import annotations

import csv
import io
import re
import zipfile
from xml.sax.saxutils import escape

from defusedxml import ElementTree as ET

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _letters(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _num(text: str) -> str:
    """Excel keeps 1500 as 1500.0 (or 1.5E3): back to how it was typed."""
    try:
        f = float(text)
    except ValueError:
        return text
    return str(int(f)) if f.is_integer() else f"{f:g}"


def _xlsx_rows(data: bytes) -> list[list[str]]:
    z = zipfile.ZipFile(io.BytesIO(data))
    names = set(z.namelist())
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(_NS + "si"):
            shared.append("".join(t.text or "" for t in si.iter(_NS + "t")))
    sheet = "xl/worksheets/sheet1.xml"                  # the first sheet of the workbook
    try:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rid = wb.find(f"{_NS}sheets/{_NS}sheet").get(_REL + "id")
        for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
            if r.get("Id") == rid:
                t = r.get("Target").lstrip("/")
                sheet = t if t.startswith("xl/") else "xl/" + t
    except Exception:  # noqa: BLE001 - an unusual workbook: the usual first sheet
        pass
    rows: list[list[str]] = []
    for row in ET.fromstring(z.read(sheet)).iter(_NS + "row"):
        out: list[str] = []
        for c in row.iter(_NS + "c"):
            kind, v = c.get("t"), c.find(_NS + "v")
            if kind == "inlineStr":
                text = "".join(t.text or "" for t in c.iter(_NS + "t"))
            elif v is None or v.text is None:
                text = ""
            elif kind == "s":
                text = shared[int(v.text)]
            elif kind == "b":
                text = "1" if v.text == "1" else "0"
            elif kind in ("str", "e"):
                text = v.text
            else:
                text = _num(v.text)
            i = _col(c.get("r")) if c.get("r") else len(out)
            out.extend([""] * (i + 1 - len(out)))
            out[i] = text.strip()
        rows.append(out)
    return rows


def read_rows(path: str) -> list[list[str]]:
    """The rows of the first sheet of an .xlsx file, or of a .csv file, as text."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] == b"PK":
        return _xlsx_rows(data)
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), dialect)]


def write_xlsx(path: str, header: list[str], rows: list[list], rtl: bool = True):
    """A one-sheet workbook: a bold header, then the rows (numbers kept as numbers)."""
    def cell(ref, v, style=0):
        s = f' s="{style}"' if style else ""
        if isinstance(v, bool):
            return f'<c r="{ref}" t="b"{s}><v>{int(v)}</v></c>'
        if isinstance(v, (int, float)):
            return f'<c r="{ref}"{s}><v>{v}</v></c>'
        text = escape("" if v is None else str(v))
        return f'<c r="{ref}" t="inlineStr"{s}><is><t xml:space="preserve">{text}</t></is></c>'

    lines = []
    for r, vals in enumerate([header] + [list(x) for x in rows], 1):
        cells = "".join(cell(f"{_letters(i)}{r}", v, 1 if r == 1 else 0) for i, v in enumerate(vals))
        lines.append(f'<row r="{r}">{cells}</row>')
    widths = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{max(12, min(45, len(str(h)) + 6))}" '
                     'customWidth="1"/>' for i, h in enumerate(header))
    view = ' rightToLeft="1"' if rtl else ""
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             f'<worksheet xmlns="{_NS[1:-1]}"><sheetViews><sheetView workbookViewId="0"{view}>'
             '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
             f'<cols>{widths}</cols><sheetData>{"".join(lines)}</sheetData></worksheet>')
    files = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{_NS[1:-1]}" xmlns:r="{_REL[1:-1]}"><sheets>'
            '<sheet name="LEVEL7" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<styleSheet xmlns="{_NS[1:-1]}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        "xl/worksheets/sheet1.xml": sheet,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)


# ---- the columns of each sheet: English and Arabic headers are both understood -------------------------------

def _key(text: str) -> str:
    return re.sub(r"[\s_()\-/]+", "", (text or "").lower())


def _columns(header: list[str], wanted: dict[str, tuple[str, ...]]) -> dict[str, int]:
    keys = [_key(h) for h in header]
    out = {}
    for field, names in wanted.items():
        for n in names:
            if _key(n) in keys:
                out[field] = keys.index(_key(n))
                break
    return out


def _yes(text: str) -> bool:
    return _key(text) in ("1", "yes", "y", "true", "نعم", "✓", "x", "ok", "صح")


def _table(rows: list[list[str]], wanted: dict[str, tuple[str, ...]], must: tuple[str, ...]):
    """The header row (the first row naming the must-have columns) and the rows below it as {field: text}."""
    for n, header in enumerate(rows[:10]):
        cols = _columns(header, wanted)
        if all(m in cols for m in must):
            out = []
            for r in rows[n + 1:]:
                rec = {f: (r[i] if i < len(r) else "").strip() for f, i in cols.items()}
                if any(rec.values()):
                    out.append(rec)
            return out
    raise ValueError("The sheet has no header row with the columns: " + ", ".join(wanted[m][0] for m in must))


X_HEADER = ["Part", "X", "Name in GRAFIS", "GRAFIS default", "XML codes", "Value / formula", "Confirmed"]
X_COLS = {"part": ("Part", "الجزء", "part number", "رقم الجزء"), "x": ("X",),
          "name": ("Name in GRAFIS", "الاسم في GRAFIS", "name"),
          "codes": ("XML codes", "رموز XML", "XML code", "code", "codes"),
          "formula": ("Value / formula", "القيمة / المعادلة", "formula", "المعادلة"),
          "confirmed": ("Confirmed", "مؤكد")}


def x_sheet(rows: list[dict]) -> list[list]:
    """The size table as sheet rows: rows of {"part", "x", "name", "default", "codes", "formula", "confirmed"}."""
    return [[r["part"], r["x"], r["name"], r["default"] if r["default"] is not None else "", ",".join(r["codes"]),
             r["formula"], ("1" if r["confirmed"] else "0") if r["codes"] else ""] for r in rows]


def x_from_sheet(rows: list[list[str]]) -> list[dict]:
    """[{"part", "x", "codes", "formula", "confirmed"}] from a filled-in sheet; the part may be left out (one part) or
    written in the X like 002/X1."""
    out = []
    for rec in _table(rows, X_COLS, ("x", "codes")):
        m = re.match(r"\s*(?:(\d+)\s*/\s*)?X?\s*(\d+)\s*$", rec["x"], re.IGNORECASE)
        if not m:
            continue
        part = rec.get("part") or m.group(1)
        codes = [c.strip().upper() for c in re.split(r"[,،;\s]+", rec["codes"]) if c.strip()]
        out.append({"part": int(float(part)) if part and re.match(r"^\d+(\.0+)?$", part) else None,
                    "x": int(m.group(2)), "codes": codes, "formula": rec.get("formula", "") or "{M}",
                    "confirmed": _yes(rec["confirmed"]) if rec.get("confirmed") else True})
    return out


OPT_HEADER = ["Group code", "Group name (ar)", "Group name (en)", "Required", "Default value", "Value code",
              "Name (ar)", "Name (en)", "Name in the XML (Variation)", "Part numbers", "Copies", "Mirrored"]
OPT_COLS = {"group": ("Group code", "رمز المجموعة", "group"),
            "gar": ("Group name (ar)", "اسم المجموعة (عربي)"), "gen": ("Group name (en)", "اسم المجموعة (إنجليزي)"),
            "required": ("Required", "مطلوب"), "default": ("Default value", "القيمة الافتراضية"),
            "value": ("Value code", "رمز القيمة", "value"), "ar": ("Name (ar)", "الاسم (عربي)"),
            "en": ("Name (en)", "الاسم (إنجليزي)"),
            "erp": ("Name in the XML (Variation)", "الاسم في XML (Variation)", "Variation", "Name in the XML"),
            "parts": ("Part numbers", "أرقام البارتات", "parts"), "copies": ("Copies", "النسخ"),
            "mirrored": ("Mirrored", "المعكوسة")}


def options_sheet(groups: list[dict]) -> list[list]:
    """One row a value (a group without values: one row of its own)."""
    out = []
    for g in groups:
        head = [g["group"], g["label"].get("ar", ""), g["label"].get("en", ""), "1" if g.get("required") else "0",
                g.get("default") or ""]
        for v in g.get("values") or [{}]:
            out.append(head + [v.get("value", ""), v.get("label", {}).get("ar", ""), v.get("label", {}).get("en", ""),
                               "; ".join(v.get("erp_names", [])), v.get("parts_text", ""), v.get("copies_text", ""),
                               v.get("mirrored_text", "")])
    return out


def options_from_sheet(rows: list[list[str]]) -> list[dict]:
    """The option groups of a filled-in sheet, in the shape of options.option_table (plus the parts / copies /
    mirrored typed next to each value). A group's names, required and default are read from its first row that has
    them, so they need not be repeated on every value."""
    from .options import code
    groups: dict[str, dict] = {}
    last = ""
    for rec in _table(rows, OPT_COLS, ("group", "value")):
        gc = code(rec.get("group") or last)
        if not gc:
            continue
        last = gc
        g = groups.setdefault(gc, {"group": gc, "label": {"ar": "", "en": ""}, "required": None, "default": "",
                                   "values": []})
        g["label"]["ar"] = g["label"]["ar"] or rec.get("gar", "")
        g["label"]["en"] = g["label"]["en"] or rec.get("gen", "")
        if g["required"] is None and rec.get("required"):
            g["required"] = _yes(rec["required"])
        g["default"] = g["default"] or code(rec.get("default", ""))
        erp = [n.strip() for n in re.split(r"[;\n]", rec.get("erp", "")) if n.strip()]
        value = code(rec.get("value", "") or (erp[0] if erp else ""))
        if not value:
            continue
        v = {"value": value, "label": {"ar": rec.get("ar") or value, "en": rec.get("en") or value},
             "erp_names": erp}
        for k in ("parts", "copies", "mirrored"):
            if k in rec:
                v[f"{k}_text"] = rec[k]
        g["values"].append(v)
    out = []
    for g in groups.values():
        g["label"] = {"ar": g["label"]["ar"] or g["group"], "en": g["label"]["en"] or g["group"]}
        g["required"] = True if g["required"] is None else g["required"]
        out.append(g)
    return out
