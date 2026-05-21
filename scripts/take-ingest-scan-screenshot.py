#!/usr/bin/env python3
"""One-off: capture the ingest wizard after a scan, so the file-type
filter checkboxes are visible.

Picks the first ALLOWED_BASE_PATHS entry and the first known agent,
runs the scan, then screenshots."""
from __future__ import annotations

import sys
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright


def main(out_path: Path) -> int:
    base_url = "http://localhost:8000"

    # Pick the first existing agent + first allowed base path.
    pairs = requests.get(f"{base_url}/admin/api/tenants.json", timeout=10).json()
    if not pairs:
        print("No tenants — cannot scan.", file=sys.stderr)
        return 1
    pair = pairs[0]
    tenant, project = pair["tenant"], pair["project"]

    # /admin/api/health gives us allowed paths in the panel — easier to just
    # use a known one from the live system.
    allowed_path = "/Users/werner/Documents/reineke-watch"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            device_scale_factor=2,
            locale="de-DE",
        )
        page = ctx.new_page()
        page.goto(f"{base_url}/admin/ingest", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(800)

        # Fill agent + path, click scan
        page.evaluate("""
        ([t, p, path]) => {
          const sel = document.getElementById('agent-select');
          if (!sel) return;
          for (const opt of sel.options) {
            try {
              const v = JSON.parse(opt.value);
              if (Array.isArray(v) && v[0]===t && v[1]===p) {
                sel.value = opt.value;
                sel.dispatchEvent(new Event('change'));
                break;
              }
            } catch (_) {}
          }
          document.querySelector('input[name=path]').value = path;
        }
        """, [tenant, project, allowed_path])
        page.click('button[type="submit"]')
        # wait for scan response to swap in (badges show after the scan)
        page.wait_for_selector('#ingest-scan .badge, #ingest-scan .alert', timeout=20000)
        page.wait_for_timeout(800)

        page.screenshot(path=str(out_path), full_page=True)
        ctx.close()
        browser.close()
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path("docs/assets/screenshots/04b-ingest-scan-result.png")
    raise SystemExit(main(target))
