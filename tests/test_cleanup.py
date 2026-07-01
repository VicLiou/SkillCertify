"""Tests for robust workdir cleanup."""
from __future__ import annotations

from pathlib import Path

from runner.core import cleanup
from runner.core.cleanup import ensure_tree_accessible, remove_tree


def test_remove_tree_removes_directory(tmp_path: Path):
    target = tmp_path / "skilltest_leftover"
    target.mkdir()
    (target / "out.txt").write_text("ok", encoding="utf-8")

    assert remove_tree(target)
    assert not target.exists()


def test_remove_tree_reports_final_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "skilltest_locked"
    target.mkdir()
    messages: list[str] = []

    def fail_rmtree(path, onerror=None):
        raise PermissionError("locked")

    monkeypatch.setattr(cleanup.shutil, "rmtree", fail_rmtree)
    monkeypatch.setattr(cleanup, "_repair_windows_permissions", lambda path: None)

    assert not remove_tree(target, retries=2, delay_s=0, log_fn=messages.append)
    assert target.exists()
    assert messages
    assert "failed to remove workdir" in messages[0]
    assert "locked" in messages[0]

def test_ensure_tree_accessible_repairs_and_checks_directory(tmp_path: Path, monkeypatch):
    target = tmp_path / "kept"
    target.mkdir()
    (target / "out.txt").write_text("ok", encoding="utf-8")
    repaired: list[Path] = []

    monkeypatch.setattr(cleanup, "_repair_windows_permissions", lambda path: repaired.append(path))

    assert ensure_tree_accessible(target)
    assert repaired == [target]


def test_ensure_tree_accessible_warns_when_still_unreadable(tmp_path: Path, monkeypatch):
    target = tmp_path / "kept"
    target.mkdir()
    messages: list[str] = []

    monkeypatch.setattr(cleanup, "_repair_windows_permissions", lambda path: None)
    monkeypatch.setattr(Path, "iterdir", lambda self: (_ for _ in ()).throw(PermissionError("denied")))

    assert not ensure_tree_accessible(target, log_fn=messages.append)
    assert "kept workdir may not be readable" in messages[0]
