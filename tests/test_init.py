"""Tests for init_project (scaffolding a new skill-testing project)."""
from __future__ import annotations

import pytest

from runner.core.init_project import init_project


def test_init_empty_dir_creates_layout(tmp_path):
    target = tmp_path / "new-project"
    actions = init_project(target)
    assert (target / "skills").is_dir()
    assert (target / "testcases").is_dir()
    assert (target / "fixtures").is_dir()
    assert (target / ".gitignore").is_file()
    assert (target / "README.md").is_file()
    # README contains the project name placeholder substitution.
    assert "new-project" in (target / "README.md").read_text(encoding="utf-8")
    assert any("created skills/" in a for a in actions)


def test_init_refuses_non_empty_dir_without_force(tmp_path):
    target = tmp_path / "p"
    target.mkdir()
    (target / "junk.txt").write_text("hi", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        init_project(target)


def test_init_force_overrides(tmp_path):
    target = tmp_path / "p"
    target.mkdir()
    (target / "junk.txt").write_text("hi", encoding="utf-8")
    actions = init_project(target, force=True)
    # Original junk file is preserved (we don't wipe — just add).
    assert (target / "junk.txt").is_file()
    assert (target / "skills").is_dir()
    assert actions  # something was done


def test_init_with_example_copies_skill_and_testcase(tmp_path):
    target = tmp_path / "p"
    actions = init_project(target, with_example=True)
    assert (target / "skills" / "example-skill" / "SKILL.md").is_file()
    assert (target / "testcases" / "claude-example.yaml").is_file()
    assert any("example-skill" in a for a in actions)


def test_init_with_architect_copies_architect(tmp_path):
    target = tmp_path / "p"
    init_project(target, with_architect=True)
    arch = target / "tools" / "skills" / "interactive-skill-architect"
    assert arch.is_dir()
    assert (arch / "SKILL.md").is_file()
