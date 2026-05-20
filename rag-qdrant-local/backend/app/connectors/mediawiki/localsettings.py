"""Extract the few wiki-config fields we care about from ``LocalSettings.php``.

We deliberately do NOT execute or even tokenise PHP. The connector only
needs three values to build URLs and locate uploads:

  * ``$wgServer``       — scheme + host (e.g. ``https://wiki.demo.local``)
  * ``$wgArticlePath``  — page-URL template (e.g. ``/wiki/$1``)
  * ``$wgScriptPath``   — base path of ``index.php`` (e.g. ``/w``)

Regex extraction over the file text is fine for this — it catches the
common single-line forms used in real-world ``LocalSettings.php``. If
the customer's settings are unusual, the importer falls back to whatever
values the user passes in the request body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class LocalSettings:
    """Subset of MediaWiki config relevant to URL/upload reconstruction."""

    server: Optional[str] = None
    article_path: Optional[str] = None
    script_path: Optional[str] = None


_PATTERN_SERVER = re.compile(
    r"""\$wgServer\s*=\s*(['"])(?P<value>[^'"]+)\1""", re.MULTILINE
)
_PATTERN_ARTICLE = re.compile(
    r"""\$wgArticlePath\s*=\s*(['"])(?P<value>[^'"]+)\1""", re.MULTILINE
)
_PATTERN_SCRIPT = re.compile(
    r"""\$wgScriptPath\s*=\s*(['"])(?P<value>[^'"]+)\1""", re.MULTILINE
)


def parse_localsettings_text(text: str) -> LocalSettings:
    """Parse the contents of a ``LocalSettings.php`` (or excerpt thereof)."""
    server = _extract(text, _PATTERN_SERVER)
    article_path = _extract(text, _PATTERN_ARTICLE)
    script_path = _extract(text, _PATTERN_SCRIPT)
    if server:
        server = server.rstrip("/")
    return LocalSettings(
        server=server,
        article_path=article_path,
        script_path=script_path,
    )


def parse_localsettings_file(path: Path) -> LocalSettings:
    """Read ``path`` and parse it. Missing file returns an all-None record so
    callers can decide whether that's fatal."""
    if not path.exists() or not path.is_file():
        return LocalSettings()
    try:
        return parse_localsettings_text(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        # Some installs ship Latin-1; tolerate both.
        return parse_localsettings_text(path.read_text(encoding="latin-1"))


def _extract(text: str, pattern: re.Pattern[str]) -> Optional[str]:
    m = pattern.search(text)
    return m.group("value") if m else None
