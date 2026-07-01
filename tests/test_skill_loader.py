"""Tests for skill/fixture staging edge cases."""
from __future__ import annotations

from runner.core.skill_loader import stage_skill


def test_stage_skill_handles_fixture_with_same_basename(tmp_path):
    skill = tmp_path / "same"
    fixture = tmp_path / "fixtures" / "same"
    skill.mkdir()
    fixture.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: same\n---\nUse fixture.\n",
        encoding="utf-8",
    )
    (fixture / "data.txt").write_text("fixture data", encoding="utf-8")

    staged = stage_skill(skill, "progressive", fixture=fixture,
                         workdir_base=tmp_path / "work")
    try:
        assert staged.skill_dir.relative_to(staged.workdir).as_posix() == "_skill/same"
        assert staged.fixture_dir == staged.workdir / "same"
        assert (staged.fixture_dir / "data.txt").is_file()
        assert "./_skill/same/SKILL.md" in staged.prompt_prefix
    finally:
        staged.cleanup()
