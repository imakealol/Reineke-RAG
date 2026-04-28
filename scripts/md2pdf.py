#!/usr/bin/env python3
"""Render a Markdown file to a paginated, well-styled PDF using WeasyPrint.

Usage:
    python scripts/md2pdf.py docs/TECHNISCHE_DOKUMENTATION.md pdf/TECHNISCHE_DOKUMENTATION.pdf
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import markdown
from weasyprint import HTML, CSS

CSS_TEMPLATE = """
@page {
    size: A4;
    margin: 22mm 18mm 24mm 18mm;
    @bottom-center {
        content: "Reineke-RAG · Technische Dokumentation · Seite " counter(page) " / " counter(pages);
        font-family: 'Helvetica', 'Arial', sans-serif;
        font-size: 9pt;
        color: #666;
    }
    @top-right {
        content: "Stand 2026-04-28";
        font-family: 'Helvetica', 'Arial', sans-serif;
        font-size: 8.5pt;
        color: #888;
    }
}

@page :first {
    @top-right { content: ""; }
    @bottom-center { content: ""; }
}

@page schema {
    size: A4 landscape;
    margin: 12mm 12mm 14mm 12mm;
    @bottom-center {
        content: "Reineke-RAG · Architektur-Schema · Seite " counter(page);
        font-family: 'Helvetica', 'Arial', sans-serif;
        font-size: 9pt;
        color: #666;
    }
}

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

TITLE_PAGE_HTML = """
<section class="titlepage">
    <h1>Reineke-RAG</h1>
    <div class="subtitle">Technische Dokumentation</div>
    <div class="subtitle" style="font-size:12pt; color:#666;">
        Lokales, multi-tenant Retrieval-Augmented-Generation-System<br/>
        (rag-qdrant-local)
    </div>
    <div class="badge">100 % offline · on-premise</div>
    <div class="meta">
        Reineke-Technik<br/>
        Stand 28. April 2026<br/>
        Code-Stand: 17 Module · 3.036 Python-Zeilen · 9 HTTP-Endpunkte
    </div>
</section>
"""


def render_schema_page(svg_path: Path) -> str:
    svg_text = svg_path.read_text(encoding="utf-8")
    # Remove XML declaration so it can be inlined inside HTML
    svg_text = re.sub(r"<\?xml[^?]*\?>", "", svg_text).strip()
    return (
        '<section class="schema-page">'
        f"{svg_text}"
        "</section>"
    )


def md_to_html(md_path: Path, schema_svg: Path | None) -> str:
    text = md_path.read_text(encoding="utf-8")

    # Strip YAML front matter if present (we render our own title page)
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :].lstrip()

    # Replace the architecture-schema image reference with a placeholder
    # so we can inject the schema as its own landscape page.
    schema_marker = "<!-- SCHEMA_PAGE -->"
    text = re.sub(
        r"!\[Architektur-Schema\]\([^)]+\)",
        schema_marker,
        text,
    )

    body_html = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists", "attr_list"],
    )

    schema_html = render_schema_page(schema_svg) if schema_svg else ""
    body_html = body_html.replace(
        f"<p>{schema_marker}</p>", schema_html
    ).replace(schema_marker, schema_html)

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Reineke-RAG · Technische Dokumentation</title>"
        "</head><body>"
        + TITLE_PAGE_HTML
        + body_html
        + "</body></html>"
    )


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
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Input not found: {args.input}", file=sys.stderr)
        return 2

    schema_svg = args.schema
    if schema_svg is None:
        guess = args.input.parent / "architecture-schema.svg"
        if guess.exists():
            schema_svg = guess

    html = md_to_html(args.input, schema_svg)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    HTML(string=html, base_url=str(args.input.parent)).write_pdf(
        target=str(args.output),
        stylesheets=[CSS(string=CSS_TEMPLATE)],
    )
    print(f"Wrote {args.output} ({args.output.stat().st_size / 1024:.1f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
