"""Convert raw MediaWiki markup into clean prose for embedding.

The scope is deliberately narrow: enough of the syntax to give the
embedder readable German text, with categories and file references
peeled off into metadata. This is *not* a faithful wikitext renderer
— we don't execute templates, evaluate Lua modules, expand parser
functions, or follow external URLs. Anything we can't handle is
removed or simplified.

Order of operations matters: well-delimited structures
(``<!-- -->``, ``<ref>``, ``{{template}}``, ``{| table |}``) are
stripped *before* the simpler regexes touch them. Otherwise an
unfortunate ``[[File:…]]`` inside a comment could be picked up as a
linked file even though it's commented out.

Returned :class:`NormalizedPage`:

  * ``text`` — markdown-ish prose
  * ``categories`` — extracted ``[[Category:…]]`` / ``[[Kategorie:…]]``
  * ``linked_files`` — extracted ``[[File:…]]`` / ``[[Datei:…]]`` / ``[[Image:…]]``
"""

from __future__ import annotations

import html
import re
from typing import List, Tuple

from .schemas import MediaWikiFileRef, NormalizedPage


# Namespace prefixes that appear as the first segment inside ``[[…]]``.
# Match is case-insensitive against this set. German DACH aliases come
# alongside the canonical English forms.
_CATEGORY_PREFIXES = ("category", "kategorie", "cat")
_FILE_PREFIXES = ("file", "datei", "image", "bild", "media")


# ---------------------------------------------------------------------------
# Stripping well-delimited structures
# ---------------------------------------------------------------------------

_RE_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_RE_REF_PAIRED = re.compile(r"<ref\b[^>]*>.*?</ref\s*>", re.DOTALL | re.IGNORECASE)
_RE_REF_SELFCLOSE = re.compile(r"<ref\b[^/]*/\s*>", re.IGNORECASE)
_RE_NOWIKI_PAIRED = re.compile(
    r"<nowiki\b[^>]*>(?P<inner>.*?)</nowiki\s*>", re.DOTALL | re.IGNORECASE
)
_RE_MAGIC_WORDS = re.compile(
    r"__(NOTOC|TOC|NOEDITSECTION|FORCETOC|NOTITLECONVERT|NOCONTENTCONVERT|NEWSECTIONLINK)__",
    re.IGNORECASE,
)
_RE_GENERIC_HTML_TAG = re.compile(
    r"</?(?:div|span|small|big|sub|sup|center|font|s|u|strike)\b[^>]*>",
    re.IGNORECASE,
)
_RE_BR = re.compile(r"<br\s*/?\s*>", re.IGNORECASE)


def _strip_html_comments_and_refs(text: str) -> str:
    text = _RE_HTML_COMMENT.sub("", text)
    text = _RE_REF_PAIRED.sub("", text)
    text = _RE_REF_SELFCLOSE.sub("", text)
    # <nowiki> content is preserved verbatim — its job was to hide
    # markup-like text from the parser, which we already aren't running.
    text = _RE_NOWIKI_PAIRED.sub(lambda m: m.group("inner"), text)
    text = _RE_MAGIC_WORDS.sub("", text)
    text = _RE_BR.sub("\n", text)
    text = _RE_GENERIC_HTML_TAG.sub("", text)
    return text


# ---------------------------------------------------------------------------
# Templates {{ ... }} — removed, with shallow nesting tolerance
# ---------------------------------------------------------------------------

def _strip_templates(text: str) -> str:
    """Remove ``{{template|...}}`` invocations, handling 2–3 levels of nesting.

    A true expander would resolve them; we don't have the template source
    and don't want to download or evaluate templates anyway. The MVP
    contract is "templates leave no trace in the embedded text" — that's
    cleaner than half-expanding them with placeholders.
    """
    # Iteratively peel off the innermost ``{{ ... }}`` until none remain
    # or we hit a safety bound.
    inner = re.compile(r"\{\{[^{}]*\}\}", re.DOTALL)
    for _ in range(8):
        new_text, n = inner.subn("", text)
        if n == 0:
            return new_text
        text = new_text
    return text


# ---------------------------------------------------------------------------
# Tables {| ... |}
# ---------------------------------------------------------------------------

_RE_TABLE_BLOCK = re.compile(r"\{\|.*?\|\}", re.DOTALL)


def _strip_table_attrs(cell: str) -> str:
    """Drop a leading ``style="..." |`` attribute prefix from a cell."""
    if "|" in cell:
        before, _, after = cell.partition("|")
        if "=" in before and '"' in before:
            return after.strip()
    return cell.strip()


def _convert_table_block(block: str) -> str:
    """Render one ``{| ... |}`` block as a markdown table, or fall back
    to a readable cell-per-line dump if the structure is too irregular."""
    lines = [ln.strip() for ln in block.splitlines()]
    rows: List[List[str]] = []
    current: List[str] = []
    current_is_header = False
    headers: List[str] = []
    have_headers = False

    def _flush() -> None:
        nonlocal headers, have_headers, current, current_is_header
        if not current:
            return
        if current_is_header and not have_headers:
            headers = [_strip_table_attrs(c) for c in current]
            have_headers = True
        else:
            rows.append([_strip_table_attrs(c) for c in current])
        current = []
        current_is_header = False

    for ln in lines:
        if ln.startswith("{|") or ln.startswith("|}"):
            continue
        if ln.startswith("|+"):
            # caption — drop for MVP
            continue
        if ln.startswith("|-"):
            _flush()
            continue
        if ln.startswith("!"):
            # header row, possibly with ``!!`` cell separators
            cells = re.split(r"!!", ln[1:])
            current.extend(cells)
            current_is_header = True
            continue
        if ln.startswith("|"):
            # data row, possibly with ``||`` cell separators
            cells = re.split(r"\|\|", ln[1:])
            current.extend(cells)
            continue
        # Continuation of a cell on the next physical line.
        if current:
            current[-1] = current[-1].rstrip() + " " + ln

    _flush()

    if not rows and not headers:
        return ""

    # Render markdown if we have a regular shape.
    if headers and rows and all(len(r) == len(headers) for r in rows):
        out = ["| " + " | ".join(headers) + " |"]
        out.append("| " + " | ".join(["---"] * len(headers)) + " |")
        out.extend("| " + " | ".join(r) + " |" for r in rows)
        return "\n".join(out)

    # Fallback: dump cells line-by-line so retrieval still sees them.
    flat: List[str] = []
    if headers:
        flat.append(" | ".join(headers))
    for r in rows:
        flat.append(" | ".join(r))
    return "\n".join(flat)


def _convert_tables(text: str) -> str:
    return _RE_TABLE_BLOCK.sub(lambda m: _convert_table_block(m.group(0)), text)


# ---------------------------------------------------------------------------
# Categories / file references — extracted, then removed from prose
# ---------------------------------------------------------------------------

# Matches a ``[[Prefix:Body]]`` reference, including pipe-segmented variants
# like ``[[File:X.pdf|thumb|caption]]``. Greedy until the first ``]]`` so
# nested wiki links are tolerated to one level.
_RE_NS_LINK = re.compile(r"\[\[(?P<prefix>[^\[\]:|]+):(?P<body>[^\[\]]+?)\]\]")


def _extract_categories_and_files(text: str) -> Tuple[str, List[str], List[MediaWikiFileRef]]:
    """Walk the text once, peeling off category and file refs."""
    categories: List[str] = []
    files: List[MediaWikiFileRef] = []

    def replace(m: re.Match[str]) -> str:
        prefix_raw = m.group("prefix").strip()
        body = m.group("body")
        pfx_lower = prefix_raw.lower()

        if pfx_lower in _CATEGORY_PREFIXES:
            # ``[[Category:IT-Sicherheit|sortkey]]`` — first segment is the name.
            name = body.split("|", 1)[0].strip()
            if name and name not in categories:
                categories.append(name)
            return ""

        if pfx_lower in _FILE_PREFIXES:
            # ``[[File:netzplan.pdf|thumb|alt=...|caption]]`` — name is first segment.
            name = body.split("|", 1)[0].strip()
            if name:
                files.append(
                    MediaWikiFileRef(
                        title=f"{prefix_raw}:{name}",
                        bare_filename=name,
                    )
                )
            return ""

        # Not a category/file — leave for the internal-link pass.
        return m.group(0)

    new_text = _RE_NS_LINK.sub(replace, text)
    return new_text, categories, files


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

_RE_INTERNAL_LINK = re.compile(r"\[\[([^\[\]|]+?)(?:\|([^\[\]]+?))?\]\]")
_RE_EXTERNAL_LINK = re.compile(r"\[(https?://\S+?)(?:\s+([^\]]+?))?\]")


def _replace_internal_link(m: re.Match[str]) -> str:
    target, label = m.group(1), m.group(2)
    return (label or target).strip()


def _replace_external_link(m: re.Match[str]) -> str:
    url, label = m.group(1), m.group(2)
    return (label or url).strip()


# ---------------------------------------------------------------------------
# Headings, bold/italic, lists
# ---------------------------------------------------------------------------

_RE_HEADING = re.compile(r"^(={1,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)


def _replace_heading(m: re.Match[str]) -> str:
    level = len(m.group(1))
    title = m.group(2).strip()
    return ("#" * level) + " " + title


_RE_BOLD_ITALIC = re.compile(r"'''''(.+?)'''''")
_RE_BOLD = re.compile(r"'''(.+?)'''")
_RE_ITALIC = re.compile(r"''(.+?)''")


def _replace_lists(text: str) -> str:
    """Convert ``*``/``#`` list markers to markdown ``-``/``1.`` markers.

    Nested levels keep their depth via two-space indent per level. Mixed
    ``*#`` prefixes are normalised to whichever marker matches the
    *deepest* character (good-enough for MVP — proper conversion needs a
    real tree parser).

    Run *before* heading conversion so that already-converted
    ``## Heading`` markdown isn't re-interpreted as a wiki list.
    """
    out_lines: List[str] = []
    for ln in text.splitlines():
        m = re.match(r"^([*#]+)\s+(.*)$", ln)
        if not m:
            out_lines.append(ln)
            continue
        markers, body = m.group(1), m.group(2)
        depth = len(markers) - 1
        indent = "  " * depth
        last = markers[-1]
        bullet = "1." if last == "#" else "-"
        out_lines.append(f"{indent}{bullet} {body}")
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Whitespace cleanup
# ---------------------------------------------------------------------------

_RE_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)
_RE_MULTI_BLANKS = re.compile(r"\n{3,}")


def _cleanup_whitespace(text: str) -> str:
    text = _RE_TRAILING_WS.sub("", text)
    text = _RE_MULTI_BLANKS.sub("\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def normalize_wikitext(raw: str) -> NormalizedPage:
    """Transform raw MediaWiki markup into a :class:`NormalizedPage`."""
    if not raw:
        return NormalizedPage(text="", categories=[], linked_files=[])

    text = raw

    # 1. structural strip — comments, refs, magic words, simple HTML tags
    text = _strip_html_comments_and_refs(text)

    # 2. templates — drop entirely (we don't have the template source)
    text = _strip_templates(text)

    # 3. tables — convert {| … |} to markdown or readable fallback
    text = _convert_tables(text)

    # 4. categories + files — extract to metadata, remove from prose
    text, categories, linked_files = _extract_categories_and_files(text)

    # 5. links — surviving [[…]] are internal page links; [http…] external
    text = _RE_INTERNAL_LINK.sub(_replace_internal_link, text)
    text = _RE_EXTERNAL_LINK.sub(_replace_external_link, text)

    # 6. lists FIRST — markers ``*`` / ``#`` are converted to ``-`` / ``1.``
    #    before headings, so that already-converted ``## Heading`` markdown
    #    isn't re-interpreted as a wiki numbered-list line.
    text = _replace_lists(text)

    # 7. headings + emphasis
    text = _RE_HEADING.sub(_replace_heading, text)
    text = _RE_BOLD_ITALIC.sub(r"**_\1_**", text)
    text = _RE_BOLD.sub(r"**\1**", text)
    text = _RE_ITALIC.sub(r"*\1*", text)

    # 8. HTML entities (``&auml;`` → ``ä``, ``&amp;`` → ``&``, …)
    text = html.unescape(text)

    # 9. whitespace
    text = _cleanup_whitespace(text)

    return NormalizedPage(text=text, categories=categories, linked_files=linked_files)
