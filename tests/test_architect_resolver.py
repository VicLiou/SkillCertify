"""Tests for architect skill auto-discovery."""
from __future__ import annotations

from runner.core.architect import (
    ARCHITECT_SKILL_RELATIVE,
    architect_skill_candidates,
    resolve_architect_skill,
)


def test_resolve_architect_falls_back_to_framework_bundle(tmp_path):
    resolved = resolve_architect_skill(cwd=tmp_path)

    assert resolved.name == "interactive-skill-architect"
    assert (resolved / "SKILL.md").is_file()
    assert resolved != tmp_path / ARCHITECT_SKILL_RELATIVE


def test_resolve_architect_prefers_project_local_copy(tmp_path):
    local = tmp_path / ARCHITECT_SKILL_RELATIVE
    local.mkdir(parents=True)
    (local / "SKILL.md").write_text("---\nname: interactive-skill-architect\n---\n", encoding="utf-8")

    assert resolve_architect_skill(cwd=tmp_path) == local


def test_architect_candidates_deduplicate_when_cwd_is_repo_root():
    candidates = architect_skill_candidates()
    keys = [str(p.resolve(strict=False)).casefold() for p in candidates]

    assert len(keys) == len(set(keys))