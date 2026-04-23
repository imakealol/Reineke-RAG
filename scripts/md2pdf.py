#!/usr/bin/env python3
"""Convert a Markdown file to a styled HTML file.

Handles the subset of Markdown used in Reineke-RAG docs: ATX headings (1-4),
paragraphs, bold/italic/code inline, fenced code blocks, GitHub-style tables,
unordered and ordered lists, blockquotes, horizontal rules, and links.

HTML output is print-oriented: A4, generous typography, page break before h1.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

CSS = r"""
@page { size: A4; margin: 20mm 18mm; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
  font-size: 10.5pt;
  line-height: 1.45;
  color: #1a1a1a;
  max-width: none;
}
h1, h2, h3, h4 { color: #0b2545; line-height: 1.25; margin-top: 1.2em; }
h1 {
  font-size: 22pt;
  border-bottom: 2px solid #0b2545;
  padding-bottom: 0.2em;
  margin-top: 0;
  page-break-before: always;
}
h1:first-of-type { page-break-before: avoid; }
h2 { font-size: 16pt; border-bottom: 1px solid #c7ccd5; padding-bottom: 0.15em; }
h3 { font-size: 13pt; }
h4 { font-size: 11.5pt; color: #2d3e5c; }
p { margin: 0.6em 0; }
a { color: #0f6fff; text-decoration: none; }
code {
  font-family: "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.92em;
  background: #f3f5f8;
  padding: 1px 4px;
  border-radius: 3px;
}
pre {
  background: #f3f5f8;
  border: 1px solid #e1e5eb;
  border-radius: 4px;
  padding: 10px 12px;
  overflow-x: auto;
  font-size: 9pt;
  line-height: 1.4;
  page-break-inside: avoid;
}
pre code { background: transparent; padding: 0; font-size: inherit; }
blockquote {
  margin: 0.8em 0;
  padding: 0.2em 0.9em;
  border-left: 3px solid #0f6fff;
  background: #f6faff;
  color: #3a4352;
}
ul, ol { margin: 0.5em 0 0.5em 1.4em; padding: 0; }
li { margin: 0.15em 0; }
hr { border: 0; border-top: 1px solid #c7ccd5; margin: 1.4em 0; }
table {
  border-collapse: collapse;
  width: 100%;
  font-size: 9.5pt;
  margin: 0.8em 0;
  page-break-inside: avoid;
}
th, td {
  border: 1px solid #c7ccd5;
  padding: 5px 8px;
  text-align: left;
  vertical-align: top;
}
th { background: #eaf0fa; color: #0b2545; font-weight: 600; }
tr:nth-child(even) td { background: #fafbfc; }
.cover {
  text-align: center;
  margin: 30mm 0 20mm;
  page-break-after: always;
}
.cover h1 { border: 0; font-size: 32pt; margin: 0; page-break-before: avoid; }
.cover .subtitle { font-size: 13pt; color: #5a6478; margin-top: 6pt; }
.cover .meta { font-size: 10pt; color: #8791a3; margin-top: 26pt; }
"""


def escape(s: str) -> str:
    return html.escape(s, quote=False)


def inline(text: str) -> str:
    """Inline formatting: code, bold, italic, links. Order matters."""

    # Protect inline code first so its content isn't re-parsed.
    placeholders: list[str] = []

    def stash_code(m: re.Match[str]) -> str:
        placeholders.append(f"<code>{escape(m.group(1))}</code>")
        return f"\x00CODE{len(placeholders) - 1}\x00"

    text = re.sub(r"`([^`]+)`", stash_code, text)

    # Escape remaining HTML special chars.
    text = escape(text)

    # Links: [text](url)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>',
        text,
    )

    # Bold **x**
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)

    # Italic *x* (but not ** which has already been replaced)
    text = re.sub(r"(?<![*])\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)

    # Restore code.
    for i, repl in enumerate(placeholders):
        text = text.replace(f"\x00CODE{i}\x00", repl)

    return text


HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE_RE = re.compile(r"^```")
TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?(\s*\|\s*:?-{2,}:?)*\s*\|?\s*$")
ORDERED_RE = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")
UNORDERED_RE = re.compile(r"^(\s*)[-*]\s+(.*)$")
BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$")
HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")


def parse_table_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def render(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)

    def close_list(stack: list[tuple[str, int]]) -> None:
        while stack:
            tag, _ = stack.pop()
            out.append(f"</{tag}>")

    list_stack: list[tuple[str, int]] = []  # (tag, indent)

    def flush_lists() -> None:
        close_list(list_stack)

    while i < n:
        line = lines[i]

        # Fenced code block.
        if FENCE_RE.match(line):
            flush_lists()
            i += 1
            buf: list[str] = []
            while i < n and not FENCE_RE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            out.append(f"<pre><code>{escape(chr(10).join(buf))}</code></pre>")
            continue

        # Horizontal rule.
        if HR_RE.match(line) and line.strip() != "":
            # But only if the previous line is blank (to avoid confusing it with
            # setext underlines — we don't support those anyway).
            flush_lists()
            out.append("<hr>")
            i += 1
            continue

        # ATX heading.
        m = HEADING_RE.match(line)
        if m:
            flush_lists()
            level = len(m.group(1))
            text = inline(m.group(2).rstrip("#").strip())
            out.append(f"<h{level}>{text}</h{level}>")
            i += 1
            continue

        # Table: current line starts with "|" AND the next line is a separator.
        if line.lstrip().startswith("|") and i + 1 < n and TABLE_SEP_RE.match(lines[i + 1]):
            flush_lists()
            header = parse_table_row(line)
            i += 2  # header + separator
            body_rows: list[list[str]] = []
            while i < n and lines[i].lstrip().startswith("|"):
                body_rows.append(parse_table_row(lines[i]))
                i += 1
            thead = "".join(f"<th>{inline(c)}</th>" for c in header)
            tbody = "".join(
                "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>"
                for row in body_rows
            )
            out.append(f"<table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>")
            continue

        # Blockquote (possibly multi-line).
        m = BLOCKQUOTE_RE.match(line)
        if m:
            flush_lists()
            buf = [m.group(1)]
            i += 1
            while i < n:
                mm = BLOCKQUOTE_RE.match(lines[i])
                if not mm:
                    break
                buf.append(mm.group(1))
                i += 1
            content = "<br>".join(inline(b) for b in buf)
            out.append(f"<blockquote>{content}</blockquote>")
            continue

        # Ordered list item.
        m = ORDERED_RE.match(line)
        if m:
            indent = len(m.group(1))
            item = inline(m.group(3))
            while list_stack and list_stack[-1][1] > indent:
                tag, _ = list_stack.pop()
                out.append(f"</{tag}>")
            if not list_stack or list_stack[-1][0] != "ol" or list_stack[-1][1] != indent:
                if list_stack and list_stack[-1][1] == indent:
                    tag, _ = list_stack.pop()
                    out.append(f"</{tag}>")
                out.append("<ol>")
                list_stack.append(("ol", indent))
            out.append(f"<li>{item}</li>")
            i += 1
            continue

        # Unordered list item.
        m = UNORDERED_RE.match(line)
        if m:
            indent = len(m.group(1))
            item = inline(m.group(2))
            while list_stack and list_stack[-1][1] > indent:
                tag, _ = list_stack.pop()
                out.append(f"</{tag}>")
            if not list_stack or list_stack[-1][0] != "ul" or list_stack[-1][1] != indent:
                if list_stack and list_stack[-1][1] == indent:
                    tag, _ = list_stack.pop()
                    out.append(f"</{tag}>")
                out.append("<ul>")
                list_stack.append(("ul", indent))
            out.append(f"<li>{item}</li>")
            i += 1
            continue

        # Blank line: closes lists, otherwise nothing.
        if line.strip() == "":
            flush_lists()
            i += 1
            continue

        # Paragraph (collect consecutive non-blank, non-special lines).
        flush_lists()
        buf = [line]
        i += 1
        while i < n:
            nxt = lines[i]
            if nxt.strip() == "":
                break
            if (
                HEADING_RE.match(nxt)
                or FENCE_RE.match(nxt)
                or HR_RE.match(nxt)
                or BLOCKQUOTE_RE.match(nxt)
                or ORDERED_RE.match(nxt)
                or UNORDERED_RE.match(nxt)
                or (
                    nxt.lstrip().startswith("|")
                    and i + 1 < n
                    and TABLE_SEP_RE.match(lines[i + 1])
                )
            ):
                break
            buf.append(nxt)
            i += 1
        out.append(f"<p>{inline(' '.join(s.strip() for s in buf))}</p>")

    flush_lists()
    return "\n".join(out)


def wrap(title: str, body_html: str, subtitle: str | None = None) -> str:
    cover = (
        f'<div class="cover"><h1>{escape(title)}</h1>'
        + (f'<div class="subtitle">{escape(subtitle)}</div>' if subtitle else "")
        + '<div class="meta">Reineke-RAG &mdash; 2026</div>'
        + "</div>"
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{escape(title)}</title>"
        f"<style>{CSS}</style></head><body>"
        + cover
        + body_html
        + "</body></html>"
    )


def convert(md_path: Path, html_path: Path, title: str, subtitle: str | None) -> None:
    md = md_path.read_text(encoding="utf-8")
    body = render(md)
    html_path.write_text(wrap(title, body, subtitle), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: md2pdf.py input.md output.html [title] [subtitle]", file=sys.stderr)
        sys.exit(2)
    inp = Path(sys.argv[1])
    outp = Path(sys.argv[2])
    title = sys.argv[3] if len(sys.argv) > 3 else inp.stem
    subtitle = sys.argv[4] if len(sys.argv) > 4 else None
    convert(inp, outp, title, subtitle)
    print(f"wrote {outp}")
