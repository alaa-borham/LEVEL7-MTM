"""Safe XML loading for ERP files.

* defusedxml with DTDs, entity declarations and external references forbidden;
* file size limit and element depth / count limits checked before the data is used;
* the encoding comes from the XML declaration (UTF-8 default); undecodable bytes are an error, not replaced.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from xml.etree.ElementTree import Element

import defusedxml.ElementTree as DET
from defusedxml import DefusedXmlException

MAX_BYTES = 10 * 1024 * 1024
MAX_DEPTH = 32
MAX_ELEMENTS = 200_000


class XmlError(ValueError):
    pass


@dataclass
class LoadedXml:
    root: Element
    data: bytes
    sha256: str
    encoding: str


def load_bytes(data: bytes) -> LoadedXml:
    if len(data) > MAX_BYTES:
        raise XmlError(f"file is larger than {MAX_BYTES // 1024 // 1024} MB")
    if not data.strip():
        raise XmlError("file is empty")
    m = re.match(rb'^\s*<\?xml[^>]*encoding=["\']([A-Za-z0-9._-]+)["\']', data)
    encoding = m.group(1).decode("ascii") if m else "UTF-8"
    try:
        root = DET.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as e:
        raise XmlError(f"forbidden XML construct: {e}") from None
    except DET.ParseError as e:
        raise XmlError(f"XML is damaged or incomplete: {e}") from None
    count = 0
    stack = [(root, 1)]
    while stack:
        el, d = stack.pop()
        count += 1
        if d > MAX_DEPTH:
            raise XmlError(f"XML nesting deeper than {MAX_DEPTH}")
        if count > MAX_ELEMENTS:
            raise XmlError(f"more than {MAX_ELEMENTS} elements")
        stack.extend((c, d + 1) for c in el)
    return LoadedXml(root, data, hashlib.sha256(data).hexdigest(), encoding)


def load_file(path: str) -> LoadedXml:
    with open(path, "rb") as fh:
        data = fh.read(MAX_BYTES + 1)
    return load_bytes(data)


def local(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def namespace(tag: str) -> str | None:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else None


def structure_report(root: Element) -> dict:
    """Inventory of the XML: element paths, how often they repeat, attributes and sample values."""
    paths: dict[str, dict] = {}
    namespaces = set()

    def walk(el, path):
        ns = namespace(el.tag)
        if ns:
            namespaces.add(ns)
        p = f"{path}/{local(el.tag)}" if path else local(el.tag)
        ent = paths.setdefault(p, {"count": 0, "attributes": {}, "text_samples": []})
        ent["count"] += 1
        for k, v in el.attrib.items():
            a = ent["attributes"].setdefault(local(k), [])
            if v not in a and len(a) < 5:
                a.append(v)
        if el.text and el.text.strip() and len(ent["text_samples"]) < 3:
            ent["text_samples"].append(el.text.strip()[:80])
        for c in el:
            walk(c, p)
    walk(root, "")
    return {"root": local(root.tag), "namespaces": sorted(namespaces), "paths": paths}
