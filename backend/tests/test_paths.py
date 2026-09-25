"""Path sandbox tests -- the security boundary of the application."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.paths import (
    PathSecurityError,
    assert_authorized_root,
    normalize_rel_path,
    safe_path,
    to_rel_path,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a/b.md", "a/b.md"),
        ("./a/b.md", "a/b.md"),
        ("a\\b.md", "a/b.md"),
        (".", "."),
        ("", "."),
    ],
)
def test_normalize_accepts_relative_paths(raw: str, expected: str) -> None:
    assert normalize_rel_path(raw).as_posix() == expected


@pytest.mark.parametrize(
    "raw",
    [
        "../secrets.md",
        "a/../../secrets.md",
        "/etc/passwd",
        "C:/Windows/system.ini",
        "a/\x00b.md",
    ],
)
def test_normalize_rejects_escapes(raw: str) -> None:
    with pytest.raises(PathSecurityError):
        normalize_rel_path(raw)


def test_safe_path_resolves_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "architecture").mkdir(parents=True)
    target = safe_path(root, "architecture/components/memory.md")
    assert str(target).startswith(str(root.resolve()))


def test_safe_path_blocks_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:  # pragma: no cover - platform without symlink privileges
        pytest.skip("symlinks unavailable")

    with pytest.raises(PathSecurityError):
        safe_path(root, "link/secret.md")


def test_assert_authorized_root_enforces_allowlist(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()

    assert assert_authorized_root(allowed, [str(allowed)]) == allowed.resolve()
    with pytest.raises(PathSecurityError):
        assert_authorized_root(outside, [str(allowed)])


def test_assert_authorized_root_rejects_missing(tmp_path: Path) -> None:
    with pytest.raises(PathSecurityError):
        assert_authorized_root(tmp_path / "nope", [])


def test_to_rel_path(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "a").mkdir(parents=True)
    assert to_rel_path(root, root / "a" / "b.md") == "a/b.md"
