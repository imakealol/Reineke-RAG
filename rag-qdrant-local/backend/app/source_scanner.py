"""Walk a (validated) directory and classify supported source files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from .schemas import FileEntry
from .utils import file_modified_iso

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".html", ".htm"}

# Filenames / dirs to never traverse — backups, lock files, OS junk.
_IGNORED_DIR_NAMES = {".git", "__pycache__", ".DS_Store", "node_modules", ".idea"}

# Filename prefixes that mark transient junk we should never try to ingest:
#   "~$..."  → MS Office owner-lock files (open document marker)
#   "._..."  → macOS resource forks on non-HFS+ volumes
#   "."      → all dotfiles (including .DS_Store)
_IGNORED_FILE_PREFIXES = ("~$", "._", ".")


@dataclass
class ScanResult:
    supported: List[FileEntry]
    unsupported: List[FileEntry]
    file_types: Dict[str, int]

    @property
    def all_files(self) -> List[FileEntry]:
        return [*self.supported, *self.unsupported]


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


def scan_directory(root: Path, *, recursive: bool = True) -> ScanResult:
    """Enumerate files under `root`, splitting them into supported / unsupported.

    `root` MUST already have passed :func:`path_security.resolve_safe_path`
    — this function does not re-validate.
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

    return ScanResult(supported=supported, unsupported=unsupported, file_types=file_types)
