#!/usr/bin/env python3
"""Render a Markdown file to a paginated, well-styled PDF using WeasyPrint.

Reads the document's YAML front matter (title / subtitle / author / date) to
build a matching cover and page header/footer. The ``--client-logo`` /
``--client-name`` flags brand the cover with a customer logo strip; the
``--creator-*`` flags add an attribution block to the cover and a small
logo to the page footer.

If the markdown contains an architecture-schema image reference, it is
injected as its own landscape page. Other images (e.g. screenshots in
``docs/assets/screenshots/``) are rendered inline at full content width.

Usage:
    python scripts/md2pdf.py docs/HANDBUCH.md pdf/HANDBUCH.pdf
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import markdown
from weasyprint import HTML, CSS


# ---------------------------------------------------------------------------
# YAML front-matter parsing — tiny, format-tolerant, no external dep
# ---------------------------------------------------------------------------

@dataclass
class FrontMatter:
    title: str = "Reineke-RAG"
    subtitle: str = ""
    author: str = ""
    date: str = ""


def parse_front_matter(text: str) -> tuple[FrontMatter, str]:
    """Pull the first ``---`` block from ``text`` and return (FrontMatter, body).

    Supports `key: "quoted"` and `key: bare value`. Unknown keys are ignored.
    Falls back to defaults if no front matter is present.
    """
    fm = FrontMatter()
    if not text.startswith("---"):
        return fm, text
    end = text.find("\n---", 3)
    if end == -1:
        return fm, text
    block = text[3:end]
    body = text[end + 4 :].lstrip()
    for line in block.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip().strip('"').strip("'")
        if key in ("title", "subtitle", "author", "date"):
            setattr(fm, key, value)
    return fm, body


# ---------------------------------------------------------------------------
# CSS — paginated A4 with optional client logo / creator logo / dynamic
# footer text. The footer text reflects the document kind (e.g.
# "Reineke-RAG · Handbuch · Seite N / M").
# ---------------------------------------------------------------------------

def build_page_css(
    client_logo: Path | None,
    creator_logo: Path | None,
    *,
    footer_text: str,
    header_date: str,
) -> str:
    client_uri = (
        f"url('file://{client_logo.resolve()}')" if client_logo and client_logo.exists() else "none"
    )
    creator_uri = (
        f"url('file://{creator_logo.resolve()}')" if creator_logo and creator_logo.exists() else "none"
    )
    client_present = client_uri != "none"
    creator_present = creator_uri != "none"

    return f"""
@page {{
    size: A4;
    margin: 24mm 18mm 22mm 18mm;
    @top-left {{
        content: {('""' if client_present else '""')};
        background-image: {client_uri};
        background-repeat: no-repeat;
        background-size: contain;
        background-position: left center;
        width: 26mm;
        height: 12mm;
    }}
    @top-right {{
        content: "{header_date}";
        font-family: 'Helvetica', 'Arial', sans-serif;
        font-size: 8.5pt;
        color: #888;
        vertical-align: bottom;
    }}
    @bottom-left {{
        content: "";
        background-image: {creator_uri};
        background-repeat: no-repeat;
        background-size: contain;
        background-position: left bottom;
        width: 9mm;
        height: 10.5mm;
        opacity: 0.85;
    }}
    @bottom-center {{
        content: "{footer_text} · Seite " counter(page) " / " counter(pages);
        font-family: 'Helvetica', 'Arial', sans-serif;
        font-size: 9pt;
        color: #666;
    }}
}}

@page :first {{
    @top-left    {{ content: ""; background-image: none; }}
    @top-right   {{ content: ""; }}
    @bottom-left {{ content: ""; background-image: none; }}
    @bottom-center {{ content: ""; }}
}}

@page schema {{
    size: A4 landscape;
    margin: 18mm 12mm 16mm 12mm;
    @top-left {{
        content: "";
        background-image: {client_uri};
        background-repeat: no-repeat;
        background-size: contain;
        background-position: left center;
        width: 26mm;
        height: 12mm;
    }}
    @top-right {{
        content: "{header_date}";
        font-family: 'Helvetica', 'Arial', sans-serif;
        font-size: 8.5pt;
        color: #888;
        vertical-align: bottom;
    }}
    @bottom-left {{
        content: "";
        background-image: {creator_uri};
        background-repeat: no-repeat;
        background-size: contain;
        background-position: left bottom;
        width: 9mm;
        height: 10.5mm;
        opacity: 0.85;
    }}
    @bottom-center {{
        content: "Reineke-RAG · Architektur-Schema · Seite " counter(page);
        font-family: 'Helvetica', 'Arial', sans-serif;
        font-size: 9pt;
        color: #666;
    }}
}}
"""


CSS_TEMPLATE = """

html, body {
    font-family: 'Helvetica', 'Arial', sans-serif;
    font-size: 10pt;
    line-height: 1.45;
    color: #1a1a1a;
}

h1 {
    font-size: 20pt;
    color: #1f4e79;
    border-bottom: 2px solid #1f4e79;
    padding-bottom: 4pt;
    margin-top: 24pt;
    margin-bottom: 12pt;
    page-break-before: always;
}

h1:first-of-type {
    page-break-before: avoid;
}

h2 {
    font-size: 14pt;
    color: #1f4e79;
    margin-top: 18pt;
    margin-bottom: 6pt;
    border-bottom: 1px solid #cfd8e3;
    padding-bottom: 2pt;
}

h3 {
    font-size: 11.5pt;
    color: #2e7d32;
    margin-top: 12pt;
    margin-bottom: 4pt;
}

p {
    margin: 4pt 0 6pt 0;
    text-align: justify;
}

ul, ol {
    margin: 4pt 0 8pt 0;
    padding-left: 18pt;
}

li {
    margin-bottom: 2pt;
}

table {
    border-collapse: collapse;
    width: 100%;
    margin: 6pt 0 12pt 0;
    font-size: 9pt;
    page-break-inside: avoid;
}

th {
    background: #1f4e79;
    color: #fff;
    text-align: left;
    padding: 4pt 6pt;
    font-weight: bold;
    border: 0.5pt solid #1f4e79;
}

td {
    padding: 3.5pt 6pt;
    border: 0.5pt solid #cfd8e3;
    vertical-align: top;
}

tr:nth-child(even) td {
    background: #f5f8fb;
}

code {
    font-family: 'Menlo', 'Consolas', monospace;
    font-size: 8.8pt;
    background: #f1f3f5;
    padding: 1pt 3pt;
    border-radius: 2pt;
    color: #b22222;
}

pre {
    background: #f1f3f5;
    border-left: 3pt solid #1f4e79;
    padding: 8pt 10pt;
    font-family: 'Menlo', 'Consolas', monospace;
    font-size: 8.5pt;
    line-height: 1.35;
    overflow-x: auto;
    page-break-inside: avoid;
    border-radius: 2pt;
}

pre code {
    background: transparent;
    color: #1a1a1a;
    padding: 0;
}

blockquote {
    border-left: 3pt solid #bf8a00;
    margin: 6pt 0;
    padding: 4pt 10pt;
    background: #fff8e1;
    color: #444;
}

hr {
    border: none;
    border-top: 1px solid #cfd8e3;
    margin: 16pt 0;
}

img.architecture {
    width: 100%;
    height: auto;
    display: block;
    margin: 0 auto;
}

/* Inline figures: screenshots, diagrams, etc. Kept on the same page as
   their caption text wherever possible. Bordered + drop-shadow stays
   close to the actual admin UI look. */
.figure {
    margin: 8pt 0 12pt 0;
    text-align: center;
    page-break-inside: avoid;
}
.figure img {
    max-width: 100%;
    height: auto;
    border: 0.5pt solid #cfd8e3;
    border-radius: 2pt;
    box-shadow: 0 1pt 3pt rgba(0, 0, 0, 0.08);
}
.figure .caption {
    font-size: 8.5pt;
    color: #777;
    margin-top: 4pt;
    font-style: italic;
}

.titlepage {
    text-align: center;
    padding-top: 60mm;
    page-break-after: always;
}

.titlepage h1 {
    font-size: 30pt;
    border: none;
    page-break-before: avoid;
    margin: 0 0 8pt 0;
}

.titlepage .subtitle {
    font-size: 16pt;
    color: #444;
    margin-bottom: 60pt;
}

.titlepage .meta {
    font-size: 11pt;
    color: #555;
    margin-top: 80pt;
}

.titlepage .badge {
    display: inline-block;
    margin-top: 18pt;
    padding: 4pt 12pt;
    background: #1f4e79;
    color: #fff;
    border-radius: 4pt;
    font-size: 10pt;
    letter-spacing: 0.5pt;
}

.client-strip {
    text-align: center;
    margin-top: -8mm;
    margin-bottom: 12mm;
    padding-bottom: 6mm;
    border-bottom: 1px solid #e0e0e0;
}

.client-strip img {
    max-height: 18mm;
    max-width: 70mm;
    height: auto;
}

.client-strip .label {
    display: block;
    font-size: 8pt;
    letter-spacing: 1.5pt;
    color: #888;
    text-transform: uppercase;
    margin-bottom: 4pt;
}

.titlepage.with-client {
    padding-top: 0;
}

.titlepage.with-client h1 {
    margin-top: 16mm;
}

.attribution {
    margin-top: 36pt;
    font-size: 9.5pt;
    color: #666;
    line-height: 1.5;
}

.attribution .creator {
    color: #1f4e79;
    font-weight: bold;
}

.schema-page {
    page: schema;
    page-break-before: always;
    page-break-after: avoid;
    text-align: center;
}

.schema-page svg,
.schema-page img {
    width: 100%;
    max-height: 180mm;
    height: auto;
    display: block;
    margin: 0 auto;
}
"""


# ---------------------------------------------------------------------------
# Cover page
# ---------------------------------------------------------------------------

def title_page_html(
    fm: FrontMatter,
    *,
    client_logo: Path | None = None,
    client_name: str | None = None,
    creator_name: str | None = None,
    creator_company: str | None = None,
    creator_address: str | None = None,
    cover_badge: str = "100 % offline · on-premise",
    cover_meta_extra: str = "",
) -> str:
    client_strip = ""
    titlepage_class = "titlepage"
    if client_logo and client_logo.exists():
        client_strip = (
            '<div class="client-strip">'
            '<span class="label">Erstellt für</span>'
            f'<img src="{client_logo.resolve()}" alt="{client_name or "Kunde"}"/>'
            "</div>"
        )
        titlepage_class = "titlepage with-client"

    attribution = ""
    if creator_name or creator_company:
        creator_line = creator_name or ""
        company_line = creator_company or ""
        address_line = creator_address or ""
        attribution = (
            '<div class="attribution">'
            + (f'<span class="creator">{creator_line}</span><br/>' if creator_line else "")
            + (f"{company_line}<br/>" if company_line else "")
            + (f"{address_line}" if address_line else "")
            + "</div>"
        )

    # Title shown in the cover heading: just "Reineke-RAG"; subtitle is the
    # document kind ("Technische Dokumentation", "Handbuch").
    cover_title = "Reineke-RAG"
    cover_sub = ""
    if fm.title:
        # Drop the "Reineke-RAG · " prefix from the title to avoid duplication.
        # "Reineke-RAG · Handbuch" → "Handbuch"
        # "Reineke-RAG · Technische Dokumentation" → "Technische Dokumentation"
        cover_sub = fm.title
        if cover_sub.lower().startswith("reineke-rag"):
            # Strip the lead and the separator
            cover_sub = re.sub(r"^[\s·\-—]*reineke-rag[\s·\-—]*",
                               "", cover_sub, flags=re.IGNORECASE)

    sub_secondary = fm.subtitle or ""
    meta_block = (fm.date or "") + ("<br/>" + cover_meta_extra if cover_meta_extra else "")

    return f"""
<section class="{titlepage_class}">
    {client_strip}
    <h1>{cover_title}</h1>
    <div class="subtitle">{cover_sub}</div>
    {(f'<div class="subtitle" style="font-size:12pt; color:#666;">{sub_secondary}</div>'
       if sub_secondary else "")}
    <div class="badge">{cover_badge}</div>
    <div class="meta">{meta_block}</div>
    {attribution}
</section>
"""


# ---------------------------------------------------------------------------
# Schema page
# ---------------------------------------------------------------------------

def render_schema_page(svg_path: Path) -> str:
    svg_text = svg_path.read_text(encoding="utf-8")
    # Remove XML declaration so it can be inlined inside HTML
    svg_text = re.sub(r"<\?xml[^?]*\?>", "", svg_text).strip()
    return (
        '<section class="schema-page">'
        f"{svg_text}"
        "</section>"
    )


# ---------------------------------------------------------------------------
# Body assembly
# ---------------------------------------------------------------------------

def md_to_html(
    md_path: Path,
    schema_svg: Path | None,
    *,
    title_html: str,
) -> str:
    text = md_path.read_text(encoding="utf-8")
    _, body = parse_front_matter(text)

    # Replace the architecture-schema image reference with a placeholder
    # so we can inject the schema as its own landscape page.
    schema_marker = "<!-- SCHEMA_PAGE -->"
    body = re.sub(
        r"!\[Architektur-Schema\]\([^)]+\)",
        schema_marker,
        body,
    )

    body_html = markdown.markdown(
        body,
        extensions=["tables", "fenced_code", "sane_lists", "attr_list"],
    )

    # Wrap inline images in a .figure container with their alt text as caption.
    # Skip the architecture schema (handled separately) and anything already
    # wrapped (e.g. by a previous run).
    def _wrap_img(match: re.Match) -> str:
        alt = match.group("alt") or ""
        src = match.group("src") or ""
        if "architecture" in src.lower():
            return match.group(0)
        return (
            f'<div class="figure">'
            f'<img src="{src}" alt="{alt}"/>'
            + (f'<div class="caption">{alt}</div>' if alt else "")
            + "</div>"
        )

    body_html = re.sub(
        r'<p><img alt="(?P<alt>[^"]*)" src="(?P<src>[^"]+)"\s*/></p>',
        _wrap_img,
        body_html,
    )

    schema_html = render_schema_page(schema_svg) if schema_svg else ""
    body_html = body_html.replace(
        f"<p>{schema_marker}</p>", schema_html
    ).replace(schema_marker, schema_html)

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Reineke-RAG · Dokumentation</title>"
        "</head><body>"
        + title_html
        + body_html
        + "</body></html>"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Markdown source")
    parser.add_argument("output", type=Path, help="PDF target")
    parser.add_argument(
        "--schema",
        type=Path,
        default=None,
        help="Optional SVG inserted as own landscape page",
    )
    parser.add_argument("--client-logo", type=Path, default=None,
                        help="Logo file (PNG/SVG) shown discreetly above the title")
    parser.add_argument("--client-name", type=str, default=None,
                        help="Client name (alt-text for the logo)")
    parser.add_argument("--creator-name", type=str, default=None,
                        help="Creator full name shown in attribution block")
    parser.add_argument("--creator-company", type=str, default=None,
                        help="Creator company name")
    parser.add_argument("--creator-address", type=str, default=None,
                        help="Creator address line")
    parser.add_argument("--creator-logo", type=Path, default=None,
                        help="Small creator logo placed in the page footer")
    parser.add_argument("--footer-text", type=str, default=None,
                        help="Page footer prefix. Default: derived from the doc title.")
    parser.add_argument("--header-date", type=str, default=None,
                        help="Date shown in the top-right of each page. "
                             "Default: front-matter 'date' (or 'Stand …').")
    parser.add_argument("--cover-meta", type=str, default="",
                        help="Extra line under the date on the cover page.")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Input not found: {args.input}", file=sys.stderr)
        return 2

    fm, _ = parse_front_matter(args.input.read_text(encoding="utf-8"))

    schema_svg = args.schema
    if schema_svg is None:
        guess = args.input.parent / "architecture-schema.svg"
        if guess.exists():
            schema_svg = guess

    # Derive footer text from the title if not overridden:
    #   "Reineke-RAG · Technische Dokumentation" → as-is
    #   "Reineke-RAG · Handbuch" → as-is
    footer_text = args.footer_text or fm.title or "Reineke-RAG"

    # Header date: use the front-matter date verbatim, but strip a leading
    # "Stand " (avoid double "Stand Stand 2026-...") and convert to ISO if it
    # already starts with "Stand <iso>"; otherwise leave it.
    header_date = args.header_date
    if header_date is None:
        raw_date = (fm.date or "").strip()
        # Common pattern: "Stand 21. Mai 2026" → keep
        if raw_date.lower().startswith("stand "):
            header_date = raw_date.replace("Stand", "Stand", 1)
        elif raw_date:
            header_date = f"Stand {raw_date}"
        else:
            header_date = ""

    title_html = title_page_html(
        fm,
        client_logo=args.client_logo,
        client_name=args.client_name,
        creator_name=args.creator_name,
        creator_company=args.creator_company,
        creator_address=args.creator_address,
        cover_meta_extra=args.cover_meta,
    )

    html = md_to_html(args.input, schema_svg, title_html=title_html)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    page_css = build_page_css(
        args.client_logo, args.creator_logo,
        footer_text=footer_text,
        header_date=header_date,
    )
    HTML(string=html, base_url=str(args.input.parent)).write_pdf(
        target=str(args.output),
        stylesheets=[CSS(string=page_css), CSS(string=CSS_TEMPLATE)],
    )
    print(f"Wrote {args.output} ({args.output.stat().st_size / 1024:.1f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
