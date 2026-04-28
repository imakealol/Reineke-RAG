"""Path-security: allow-list, traversal, system path rejection."""

from pathlib import Path

import pytest

from app.path_security import PathSecurityError, resolve_safe_path


def test_allowed_path_accepted(sandbox_root: Path):
    target = sandbox_root / "customer-a" / "docs"
    target.mkdir(parents=True, exist_ok=True)
    resolved = resolve_safe_path(str(target))
    assert resolved == target.resolve()


def test_path_must_be_absolute():
    with pytest.raises(PathSecurityError, match="absolute"):
        resolve_safe_path("relative/path")


def test_traversal_outside_allow_list_rejected(sandbox_root: Path):
    # `..` resolves outside the sandbox.
    bad = str(sandbox_root / ".." / "..")
    with pytest.raises(PathSecurityError, match="outside the configured"):
        resolve_safe_path(bad)


def test_system_path_rejected(sandbox_root: Path):
    for sysdir in ("/etc", "/etc/passwd", "/root", "/var/log"):
        with pytest.raises(PathSecurityError):
            resolve_safe_path(sysdir)


def test_nul_byte_rejected():
    with pytest.raises(PathSecurityError, match="NUL"):
        resolve_safe_path("/mnt/rag-data/foo\x00bar")


def test_empty_path_rejected():
    with pytest.raises(PathSecurityError):
        resolve_safe_path("")
