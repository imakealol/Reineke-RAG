"""Streaming parser for MediaWiki XML exports.

We use :func:`xml.etree.ElementTree.iterparse` and clear each ``<page>``
element after emitting it — that's the only reason this can handle a
multi-GB wiki dump without eating memory.

The XML namespace prefix varies by export version (0.10, 0.11, …). We
strip it via a tag-suffix check so the same code handles every common
version we've seen in the wild.

Output is a stream of :class:`MediaWikiPage` records (latest revision
per page; old revisions are inspected only to pick the newest).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterator, Optional
from xml.etree import ElementTree as ET

from .errors import MediaWikiXMLError
from .schemas import MediaWikiPage


def _local(tag: str) -> str:
    """Return the local-name part of a ``{namespace}name`` tag."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_revision(rev: ET.Element) -> Dict[str, Optional[str]]:
    """Pull the fields we care about from one ``<revision>`` element."""
    out: Dict[str, Optional[str]] = {
        "id": None,
        "timestamp": None,
        "text": None,
    }
    for child in rev:
        tag = _local(child.tag)
        if tag == "id":
            out["id"] = (child.text or "").strip() or None
        elif tag == "timestamp":
            out["timestamp"] = (child.text or "").strip() or None
        elif tag == "text":
            # ``deleted="deleted"`` revisions carry no text — skip.
            if child.attrib.get("deleted") == "deleted":
                out["text"] = None
            else:
                out["text"] = child.text or ""
    return out


def _pick_latest_revision(rev_elems: list[ET.Element]) -> Optional[Dict[str, Optional[str]]]:
    """Return the highest-id revision's parsed dict, or ``None`` if none have an id."""
    best: Optional[Dict[str, Optional[str]]] = None
    best_id = -1
    for rev in rev_elems:
        parsed = _parse_revision(rev)
        raw = parsed["id"]
        if raw is None:
            continue
        try:
            rid = int(raw)
        except ValueError:
            continue
        if rid > best_id:
            best_id = rid
            best = parsed
    return best


def read_namespace_map(xml_path: Path) -> Dict[int, str]:
    """Extract the ``<siteinfo><namespaces>`` table from the export.

    Done in a separate streaming pass (front-of-file) so the main page
    loop doesn't have to track siteinfo state. Returns ``{ns_id: name}``;
    the main namespace 0 maps to the empty string by MediaWiki convention.
    """
    namespaces: Dict[int, str] = {}
    try:
        for _event, elem in ET.iterparse(str(xml_path), events=("end",)):
            if _local(elem.tag) == "namespace":
                key = elem.attrib.get("key")
                if key is None:
                    continue
                try:
                    ns_id = int(key)
                except ValueError:
                    continue
                namespaces[ns_id] = (elem.text or "").strip()
            elif _local(elem.tag) == "namespaces":
                # We have everything we need — bail out of the stream.
                elem.clear()
                break
    except ET.ParseError as exc:
        raise MediaWikiXMLError(f"Failed to read siteinfo from XML: {exc}") from exc
    return namespaces


def iter_pages(xml_path: Path) -> Iterator[MediaWikiPage]:
    """Yield one :class:`MediaWikiPage` per ``<page>`` in the export.

    Latest revision only — earlier revisions in the same page are
    inspected to pick the highest id but never yielded. Pages with no
    parseable revision (deleted / suppressed) are skipped.
    """
    if not xml_path.exists() or not xml_path.is_file():
        raise MediaWikiXMLError(f"XML export not found: {xml_path}")

    try:
        # iterparse with start/end events; we only act on end events.
        context = ET.iterparse(str(xml_path), events=("end",))
    except ET.ParseError as exc:
        raise MediaWikiXMLError(f"Could not open XML: {exc}") from exc

    for _event, elem in context:
        if _local(elem.tag) != "page":
            continue

        title: Optional[str] = None
        page_id_raw: Optional[str] = None
        ns_raw: Optional[str] = None
        is_redirect = False
        redirect_target: Optional[str] = None
        rev_elems: list[ET.Element] = []

        for child in elem:
            tag = _local(child.tag)
            if tag == "title":
                title = (child.text or "").strip() or None
            elif tag == "ns":
                ns_raw = (child.text or "").strip() or None
            elif tag == "id":
                page_id_raw = (child.text or "").strip() or None
            elif tag == "redirect":
                is_redirect = True
                redirect_target = child.attrib.get("title") or None
            elif tag == "revision":
                rev_elems.append(child)

        elem.clear()  # free memory before continuing

        if title is None or page_id_raw is None:
            # Malformed page — skip rather than fail the whole import.
            continue

        try:
            page_id = int(page_id_raw)
        except ValueError:
            continue

        try:
            namespace_id = int(ns_raw) if ns_raw is not None else 0
        except ValueError:
            namespace_id = 0

        latest = _pick_latest_revision(rev_elems)
        if latest is None or latest["id"] is None:
            continue
        revision_id = int(latest["id"])
        revision_timestamp = latest["timestamp"]
        raw_text = latest["text"] or ""

        yield MediaWikiPage(
            page_id=page_id,
            title=title,
            namespace_id=namespace_id,
            namespace_name=None,  # filled in by the service from the namespace map
            revision_id=revision_id,
            revision_timestamp=revision_timestamp,
            raw_text=raw_text,
            is_redirect=is_redirect,
            redirect_target=redirect_target,
        )
