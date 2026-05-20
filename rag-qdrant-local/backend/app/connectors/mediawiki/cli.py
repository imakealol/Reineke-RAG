"""Command-line driver for the MediaWiki connector.

  python -m app.connectors.mediawiki.cli inspect-xml --xml-path …
  python -m app.connectors.mediawiki.cli import-xml --tenant … --project … …

``inspect-xml`` parses the export, counts things, prints JSON, writes
nothing. ``import-xml`` runs the full pipeline (or dry-run) through
:class:`MediaWikiImportService`.

The CLI deliberately mirrors the API request shape so admins can move
between the two without re-learning the contract.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import List, Optional

from ...database import init_db, session_scope
from ...path_security import resolve_safe_path
from .errors import MediaWikiError
from .normalizer import normalize_wikitext
from .schemas import MediaWikiWikiConfig
from .service import MediaWikiImportService
from .uploads import resolve_upload
from .xml_importer import iter_pages, read_namespace_map


# ---------------------------------------------------------------------------
# inspect-xml
# ---------------------------------------------------------------------------

def _cmd_inspect_xml(args: argparse.Namespace) -> int:
    xml_path = resolve_safe_path(args.xml_path)
    uploads_path = resolve_safe_path(args.uploads_path) if args.uploads_path else None

    allowed_ns = set(args.allowed_namespaces or [0])

    namespace_map = read_namespace_map(xml_path)

    pages_by_ns: Counter[int] = Counter()
    redirect_count = 0
    category_set: set[str] = set()
    file_set: set[str] = set()
    pages_kept = 0
    pages_total = 0

    for page in iter_pages(xml_path):
        pages_total += 1
        pages_by_ns[page.namespace_id] += 1
        if page.is_redirect:
            redirect_count += 1
        if page.namespace_id not in allowed_ns:
            continue
        if page.is_redirect and not args.include_redirects:
            continue
        normalised = normalize_wikitext(page.raw_text)
        category_set.update(normalised.categories)
        for f in normalised.linked_files:
            file_set.add(f.bare_filename)
        pages_kept += 1

    unresolved: List[str] = []
    if uploads_path and file_set:
        for fname in sorted(file_set):
            if not resolve_upload(uploads_path, fname).exists:
                unresolved.append(fname)

    report = {
        "xml_path": str(xml_path),
        "uploads_path": str(uploads_path) if uploads_path else None,
        "allowed_namespaces": sorted(allowed_ns),
        "include_redirects": args.include_redirects,
        "pages_total": pages_total,
        "pages_kept": pages_kept,
        "redirect_count": redirect_count,
        "pages_by_namespace": {
            str(k): {
                "name": namespace_map.get(k),
                "count": v,
            }
            for k, v in sorted(pages_by_ns.items())
        },
        "unique_categories": sorted(category_set),
        "unique_files": sorted(file_set),
        "unresolved_files": unresolved,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# import-xml
# ---------------------------------------------------------------------------

async def _run_import(args: argparse.Namespace) -> dict:
    init_db()
    service = MediaWikiImportService()
    wiki = MediaWikiWikiConfig(
        base_url=args.wiki_base_url,
        article_path=args.article_path,
        script_path=args.script_path,
    )
    with session_scope() as db:
        result = await service.import_xml(
            db,
            tenant=args.tenant,
            project=args.project,
            xml_path=args.xml_path,
            uploads_path=args.uploads_path,
            wiki=wiki,
            allowed_namespaces=list(args.allowed_namespaces or [0]),
            include_redirects=args.include_redirects,
            include_uploads=args.include_uploads,
            reindex_changed_only=args.reindex_changed_only,
            dry_run=args.dry_run,
        )
    return {
        "status": "ok",
        "mode": "xml",
        "dry_run": args.dry_run,
        "pages_seen": result.pages_seen,
        "pages_indexed": result.pages_indexed,
        "pages_skipped_namespace": result.pages_skipped_namespace,
        "pages_skipped_redirect": result.pages_skipped_redirect,
        "pages_skipped_unchanged": result.pages_skipped_unchanged,
        "files_seen": result.files_seen,
        "files_indexed": result.files_indexed,
        "files_skipped_unsupported": result.files_skipped_unsupported,
        "files_skipped_unchanged": result.files_skipped_unchanged,
        "unresolved_files": result.unresolved_files,
        "warnings": result.warnings,
        "errors": result.errors,
    }


def _cmd_import_xml(args: argparse.Namespace) -> int:
    report = asyncio.run(_run_import(args))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if not report["errors"] else 1


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.connectors.mediawiki.cli",
        description="MediaWiki connector — XML import driver.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # inspect-xml ---------------------------------------------------------
    p_inspect = sub.add_parser(
        "inspect-xml",
        help="Parse XML, count pages/files/categories. Writes nothing.",
    )
    p_inspect.add_argument("--xml-path", required=True)
    p_inspect.add_argument("--uploads-path", default=None)
    p_inspect.add_argument(
        "--allowed-namespaces", type=int, nargs="+", default=[0],
    )
    p_inspect.add_argument(
        "--include-redirects", action="store_true",
    )
    p_inspect.set_defaults(func=_cmd_inspect_xml)

    # import-xml ----------------------------------------------------------
    p_import = sub.add_parser(
        "import-xml",
        help="Run the XML import end-to-end. ``--dry-run`` to count only.",
    )
    p_import.add_argument("--tenant", required=True)
    p_import.add_argument("--project", required=True)
    p_import.add_argument("--xml-path", required=True)
    p_import.add_argument("--uploads-path", default=None)
    p_import.add_argument("--wiki-base-url", required=True)
    p_import.add_argument("--article-path", default="/wiki/$1")
    p_import.add_argument("--script-path", default="/w")
    p_import.add_argument(
        "--allowed-namespaces", type=int, nargs="+", default=[0],
    )
    p_import.add_argument(
        "--include-redirects", action="store_true",
    )
    p_import.add_argument(
        "--include-uploads", action="store_true",
    )
    p_import.add_argument(
        "--reindex-changed-only", action="store_true",
    )
    p_import.add_argument(
        "--dry-run", action="store_true",
    )
    p_import.set_defaults(func=_cmd_import_xml)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except MediaWikiError as exc:
        print(f"MediaWiki error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — top-level CLI safety net
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
