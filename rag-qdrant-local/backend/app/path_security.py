"""Path-traversal & allow-list enforcement.

The user must only be able to ingest files that physically reside under one
of the configured ALLOWED_BASE_PATHS. This module is the single choke point
for that check — every endpoint that accepts a user-supplied path must call
:func:`resolve_safe_path` before touching the filesystem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from .config import settings


# System paths that are always rejected, even if a sibling exists in
# ALLOWED_BASE_PATHS that would technically resolve through them.
_SYSTEM_DENY_LIST = (
    "/etc",
    "/root",
    "/home",
    "/var",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/opt",
)


class PathSecurityError(ValueError):
    """Raised when a user-supplied path is not allowed."""


def _is_within(child: Path, parent: Path) -> bool:
    """Return True iff `child` is `parent` or lives below it (resolved)."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_under_system_deny(path: Path, allowed_bases: Iterable[Path]) -> bool:
    """A path is denied if it sits under a system path *unless* an allowed
    base path is itself nested within that same system path."""
    s = str(path)
    for sysdir in _SYSTEM_DENY_LIST:
        if s == sysdir or s.startswith(sysdir + "/"):
            # If an allowed base is rooted in the same sysdir, the user
            # explicitly opted in — accept.
            if any(
                str(b) == sysdir or str(b).startswith(sysdir + "/")
                for b in allowed_bases
            ):
                return False
            return True
    return False


def get_allowed_base_paths() -> List[Path]:
    bases = settings.allowed_base_paths
    if not bases:
        raise PathSecurityError(
            "ALLOWED_BASE_PATHS is empty. Configure at least one base path "
            "in your .env before scanning or ingesting."
        )
    return bases


def resolve_safe_path(user_input: str) -> Path:
    """Resolve `user_input` and verify it lives under ALLOWED_BASE_PATHS.

    Steps:
      1. Reject empty or relative paths.
      2. Reject paths containing NUL bytes or `..` segments before resolution
         (early rejection produces clearer errors).
      3. ``Path.resolve(strict=False)`` to canonicalise (follows symlinks).
      4. Verify the resolved path lives within at least one allowed base.
      5. Verify it is not under a denied system path (unless explicitly
         covered by an allowed base).
    """
    if not user_input or not isinstance(user_input, str):
        raise PathSecurityError("Path must be a non-empty string.")

    if "\x00" in user_input:
        raise PathSecurityError("Path contains NUL byte.")

    candidate = Path(user_input)
    if not candidate.is_absolute():
        raise PathSecurityError(
            f"Path must be absolute, got '{user_input}'. Provide a fully "
            f"qualified path under one of the allowed base paths."
        )

    # Resolve symlinks / `..` / etc.
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathSecurityError(f"Could not resolve path: {exc}") from exc

    allowed = get_allowed_base_paths()

    if _is_under_system_deny(resolved, allowed):
        raise PathSecurityError(
            f"Path '{resolved}' is in a denied system location. "
            f"Configure an explicit ALLOWED_BASE_PATHS entry to override."
        )

    if not any(_is_within(resolved, base) for base in allowed):
        bases_str = ", ".join(str(b) for b in allowed)
        raise PathSecurityError(
            f"Path '{resolved}' is outside the configured ALLOWED_BASE_PATHS "
            f"({bases_str}). Either move the data or extend ALLOWED_BASE_PATHS."
        )

    return resolved


def assert_existing_dir(path: Path) -> Path:
    if not path.exists():
        raise PathSecurityError(f"Path does not exist: {path}")
    if not path.is_dir():
        raise PathSecurityError(f"Path is not a directory: {path}")
    return path
