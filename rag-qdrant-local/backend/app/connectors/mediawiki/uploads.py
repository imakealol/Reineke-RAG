"""Resolve a MediaWiki ``[[File:...]]`` reference to a real file on disk.

MediaWiki normally stores uploads under a hash-sharded layout to keep
single directories from blowing up. ``$wgHashedUploadDirectory = true``
is the default, so a file called ``Netzplan.pdf`` lives at
``images/a/ab/Netzplan.pdf`` where ``a`` and ``ab`` are the first one
and two characters of the MD5 of the *normalised* filename.

Normalisation rules MediaWiki applies before hashing the filename:

  * trim surrounding whitespace;
  * uppercase the first character;
  * replace spaces with underscores.

We do the same here. If the hashed path is missing on disk we fall back
to a flat ``images/<filename>`` lookup (some hand-curated exports use
the flat layout). Missing files are reported, never crash.

The connector never invents file content — it only resolves a path it
can locate; the existing ingestion service is responsible for reading
the bytes via the normal loader chain.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ResolvedUpload:
    bare_filename: str
    normalised_filename: str
    resolved_path: Optional[Path]   # None if not found

    @property
    def exists(self) -> bool:
        return self.resolved_path is not None


def _normalise_filename(name: str) -> str:
    """Apply MediaWiki's pre-hash filename normalisation.

    Returns the empty string for anything that looks like a path-traversal
    attempt (``..``, slash, backslash, NUL). Wikitext is untrusted input —
    a malicious page could embed ``[[File:../etc/passwd]]`` and we must
    not turn that into a file read outside the uploads dir.
    """
    if not name:
        return ""
    if any(c in name for c in ("..", "/", "\\", "\x00")):
        return ""
    n = name.strip()
    if not n:
        return n
    # Replace whitespace with underscore. MediaWiki turns multi-spaces
    # into a single underscore too — we approximate with str.replace,
    # which collapses naturally only on consecutive runs after split/join.
    n = "_".join(n.split())
    # First character uppercased.
    n = n[:1].upper() + n[1:]
    return n


def _hashed_subpath(filename: str) -> str:
    """Compute the MediaWiki hashed layout for a *normalised* filename.

    Mirrors ``HashedFileRepo::getHashPath`` in MediaWiki: take MD5 of the
    filename, prefix is ``<first-char>/<first-two-chars>``.
    """
    digest = hashlib.md5(filename.encode("utf-8")).hexdigest()
    return f"{digest[0]}/{digest[0:2]}"


def resolve_upload(uploads_root: Path, bare_filename: str) -> ResolvedUpload:
    """Look for ``bare_filename`` under ``uploads_root``.

    Tries:
      1. hashed layout — ``<root>/<a>/<ab>/<Normalised>``
      2. flat layout — ``<root>/<Normalised>``
      3. flat layout with the original (un-normalised) name as a courtesy

    Every candidate is verified to live *within* ``uploads_root`` after
    resolution — symlinks or path-traversal would otherwise let a
    malicious filename escape the uploads directory.

    Returns a :class:`ResolvedUpload` either way; ``resolved_path`` is
    ``None`` when none of the variants exist or all of them resolve
    outside ``uploads_root``.
    """
    uploads_root = uploads_root.resolve()
    normalised = _normalise_filename(bare_filename)

    def _safe_candidate(p: Path) -> Optional[Path]:
        if not p.is_file():
            return None
        resolved = p.resolve()
        try:
            resolved.relative_to(uploads_root)
        except ValueError:
            return None
        return resolved

    if normalised:
        sub = _hashed_subpath(normalised)
        for candidate in (uploads_root / sub / normalised, uploads_root / normalised):
            safe = _safe_candidate(candidate)
            if safe is not None:
                return ResolvedUpload(bare_filename, normalised, safe)

    # Last-ditch: the export may use a slightly different normalisation
    # (e.g. lowercase first letter) — try the raw name flat. The
    # normalised filename rejected traversal patterns earlier; do the
    # check here too in case ``bare_filename`` slipped past that.
    if bare_filename and not any(c in bare_filename for c in ("..", "/", "\\", "\x00")):
        safe = _safe_candidate(uploads_root / bare_filename)
        if safe is not None:
            return ResolvedUpload(bare_filename, normalised, safe)

    return ResolvedUpload(bare_filename, normalised, None)
