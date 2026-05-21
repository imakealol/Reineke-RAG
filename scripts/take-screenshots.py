#!/usr/bin/env python3
"""Capture screenshots of the running Reineke-RAG admin UI for the Handbuch.

Requires a running backend at http://localhost:8000.

Usage:
    python scripts/take-screenshots.py docs/assets/screenshots/
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


# (path, filename, full_page, viewport_h, pre_js)
PAGES = [
    ("/admin/",
        "01-uebersicht.png",       True,  1100, None),
    ("/admin/agenten",
        "02-agenten.png",          True,  900,  None),
    # Click the first "Prompt"-toggle so the agent edit panel is open.
    ("/admin/agenten",
        "02b-agent-prompt.png",    True,  1100,
        "document.querySelector('.js-prompt-toggle')?.click();"),
    # Documents page — limit to viewport so the long table stays one screen.
    ("/admin/dokumente?tenant=reineke&project=watch",
        "03-dokumente.png",        False, 900,  None),
    ("/admin/ingest",
        "04-ingest-wizard.png",    True,  900,  None),
    ("/admin/zeitplan",
        "05-zeitplan.png",         True, 1000,  None),
    ("/admin/jobs",
        "06-ingest-verlauf.png",   True,  900,  None),
    ("/admin/logs",
        "07-logs.png",             True, 1000,  None),
    # Configuration page is long — viewport-only capture.
    ("/admin/konfiguration",
        "08-konfiguration.png",    False, 1100, None),
]


def main(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    base_url = "http://localhost:8000"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for path, filename, full_page, viewport_h, pre_js in PAGES:
            ctx = browser.new_context(
                viewport={"width": 1440, "height": viewport_h},
                device_scale_factor=2,
                locale="de-DE",
            )
            page = ctx.new_page()
            print(f"  {path} → {filename}")
            page.goto(base_url + path, wait_until="networkidle", timeout=15000)
            # Give htmx swaps a moment to settle.
            page.wait_for_timeout(1500)
            if pre_js:
                page.evaluate(pre_js)
                page.wait_for_timeout(700)
            page.screenshot(
                path=str(out_dir / filename),
                full_page=full_page,
            )
            ctx.close()
        browser.close()
    print(f"\nWrote {len(PAGES)} screenshot(s) to {out_dir}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    target = Path(args[0]) if args else Path("docs/assets/screenshots")
    raise SystemExit(main(target))
