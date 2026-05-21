"""Walk a (validated) directory and classify supported source files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .schemas import FileEntry
from .utils import file_modified_iso

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".html",
    ".htm",
    ".pptx",
    ".odt",
}

# Filenames / dirs to never traverse — backups, lock files, OS junk.
_IGNORED_DIR_NAMES = {".git", "__pycache__", ".DS_Store", "node_modules", ".idea"}

# Filename prefixes that mark transient junk we should never try to ingest:
#   "~$..."  → MS Office owner-lock files (open document marker)
#   "._..."  → macOS resource forks on non-HFS+ volumes
#   "."      → all dotfiles (including .DS_Store)
_IGNORED_FILE_PREFIXES = ("~$", "._", ".")


@dataclass
class MediaWikiExportHint:
    """Marker that a scanned directory looks like a MediaWiki export.

    The wizard can use this to branch its UI: instead of the file-type
    filter, show a wiki-import form pre-filled with the detected paths
    (and, when ``LocalSettings.example.php`` is present, the URL config).
    """

    xml_path: str
    uploads_path: Optional[str]
    localsettings_path: Optional[str]
    base_url: Optional[str]
    article_path: Optional[str]
    script_path: Optional[str]


@dataclass
class ScanResult:
    supported: List[FileEntry]
    unsupported: List[FileEntry]
    file_types: Dict[str, int]
    mediawiki_hint: Optional[MediaWikiExportHint] = None

    @property
    def all_files(self) -> List[FileEntry]:
        return [*self.supported, *self.unsupported]

    def filter_to_extensions(self, allowed: Iterable[str]) -> "ScanResult":
        """Return a new ScanResult restricted to ``allowed`` extensions.

        - Comparison is case-insensitive; values are expected with the leading
          dot (e.g. ``".pdf"``) but tolerate either form for safety.
        - Files of other supported types move into ``unsupported`` so they
          still show up in scan reports as "found but skipped".
        - ``file_types`` is filtered to match, so badge counts in the UI
          reflect what will actually be processed.
        """
        allowed_norm: set[str] = set()
        for e in allowed:
            if not e:
                continue
            v = e.strip().lower()
            if not v:
                continue
            if not v.startswith("."):
                v = "." + v
            allowed_norm.add(v)

        kept: List[FileEntry] = []
        dropped: List[FileEntry] = []
        for f in self.supported:
            if f.extension.lower() in allowed_norm:
                kept.append(f)
            else:
                dropped.append(f)

        return ScanResult(
            supported=kept,
            unsupported=[*self.unsupported, *dropped],
            file_types={
                ext: n for ext, n in self.file_types.items()
                if ext.lower() in allowed_norm
            },
        )


def _iter_files(root: Path, recursive: bool) -> Iterable[Path]:
    if recursive:
        for p in root.rglob("*"):
            if p.is_file() and not _is_in_ignored_dir(p) and not _is_junk_file(p):
                yield p
    else:
        for p in root.iterdir():
            if p.is_file() and not _is_junk_file(p):
                yield p


def _is_in_ignored_dir(p: Path) -> bool:
    parts = set(p.parts)
    return bool(parts & _IGNORED_DIR_NAMES)


def _is_junk_file(p: Path) -> bool:
    """Skip Office lock files (`~$...`), macOS resource forks (`._...`),
    dotfiles, and zero-byte placeholders."""
    name = p.name
    if name.startswith(_IGNORED_FILE_PREFIXES):
        return True
    try:
        if p.stat().st_size == 0:
            return True
    except OSError:
        return True
    return False


def _classify(path: Path) -> Tuple[bool, str]:
    ext = path.suffix.lower()
    return (ext in SUPPORTED_EXTENSIONS, ext)


_MEDIAWIKI_XML_SNIFF_BYTES = 4096


def _looks_like_mediawiki_xml(path: Path) -> bool:
    """Cheap sniff: does the first few KB of ``path`` contain a
    ``<mediawiki`` root element? Skips the XML prolog and namespace prefix
    matters not — substring match is enough."""
    try:
        with open(path, "rb") as f:
            head = f.read(_MEDIAWIKI_XML_SNIFF_BYTES)
    except OSError:
        return False
    return b"<mediawiki" in head


def detect_mediawiki_export(root: Path) -> Optional[MediaWikiExportHint]:
    """Return a :class:`MediaWikiExportHint` if ``root`` looks like a
    MediaWiki XML export, else ``None``.

    Detection rule:

      * at least one ``*.xml`` file directly under ``root`` whose head
        contains ``<mediawiki`` (XML namespace prefix is tolerated)
      * an ``images/`` or ``uploads/`` directory next to it (optional —
        the hint is still emitted without uploads, just with a ``None``
        uploads_path)

    If a ``LocalSettings*.php`` is present in the same root, we parse it
    for ``$wgServer`` / ``$wgArticlePath`` / ``$wgScriptPath`` and
    pre-fill the hint so the wizard can avoid asking the operator for
    values the customer already supplied.
    """
    if not root.is_dir():
        return None

    # Find an .xml file that smells like a MediaWiki export. Pick the
    # newest if several exist (typical when re-exports accumulate).
    candidates: List[Tuple[Path, float]] = []
    for entry in root.iterdir():
        if not entry.is_file() or entry.suffix.lower() != ".xml":
            continue
        if not _looks_like_mediawiki_xml(entry):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        candidates.append((entry, mtime))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    xml_path = candidates[0][0]

    uploads_path: Optional[Path] = None
    for name in ("images", "uploads"):
        d = root / name
        if d.is_dir():
            uploads_path = d
            break

    base_url: Optional[str] = None
    article_path: Optional[str] = None
    script_path: Optional[str] = None
    localsettings_path: Optional[Path] = None
    for ls_candidate in root.glob("LocalSettings*.php"):
        if ls_candidate.is_file():
            localsettings_path = ls_candidate
            # Local import to keep the scanner self-contained; the parser
            # has no side effects on the FS and tolerates missing fields.
            from .connectors.mediawiki.localsettings import parse_localsettings_file
            cfg = parse_localsettings_file(ls_candidate)
            base_url = cfg.server
            article_path = cfg.article_path
            script_path = cfg.script_path
            break

    return MediaWikiExportHint(
        xml_path=str(xml_path),
        uploads_path=str(uploads_path) if uploads_path else None,
        localsettings_path=str(localsettings_path) if localsettings_path else None,
        base_url=base_url,
        article_path=article_path,
        script_path=script_path,
    )


def scan_directory(root: Path, *, recursive: bool = True) -> ScanResult:
    """Enumerate files under `root`, splitting them into supported / unsupported.

    `root` MUST already have passed :func:`path_security.resolve_safe_path`
    — this function does not re-validate.

    Also detects MediaWiki export signatures at the top level of ``root``
    so the admin wizard can branch its UI accordingly.
    """
    supported: List[FileEntry] = []
    unsupported: List[FileEntry] = []
    file_types: Dict[str, int] = {}

    for p in _iter_files(root, recursive=recursive):
        try:
            stat = p.stat()
        except OSError:
            continue

        is_supported, ext = _classify(p)
        if is_supported:
            file_types[ext] = file_types.get(ext, 0) + 1

        entry = FileEntry(
            path=str(p),
            file_name=p.name,
            extension=ext,
            size_bytes=stat.st_size,
            modified_at=file_modified_iso(p),
            supported=is_supported,
        )
        (supported if is_supported else unsupported).append(entry)

    mediawiki_hint = detect_mediawiki_export(root)

    return ScanResult(
        supported=supported,
        unsupported=unsupported,
        file_types=file_types,
        mediawiki_hint=mediawiki_hint,
    )
