"""Tests for the static testcase YAML validator."""
from __future__ import annotations

from pathlib import Path

import pytest

from runner.core.validate import validate_docs, validate_file


def _write(tmp_path: Path, content: str, name: str = "tc.yaml") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_validates_clean_file(tmp_path):
    skill = tmp_path / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
    yaml = tmp_path / "tc.yaml"
    yaml.write_text(
        f"name: happy\nskill: {skill}\ninput: hi\n"
        "expect:\n  - exit_code: 0\n  - file_exists: out.txt\n",
        encoding="utf-8",
    )
    issues = validate_file(yaml)
    assert issues == []


def test_flags_missing_required_fields(tmp_path):
    yaml = _write(tmp_path, "skill: x\n")
    issues = validate_file(yaml)
    messages = [i.message for i in issues]
    assert any("missing required field `name`" in m for m in messages)
    assert any("missing required field `input`" in m for m in messages)


def test_typo_in_assertion_key_gets_suggestion(tmp_path):
    skill = tmp_path / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("ok", encoding="utf-8")
    yaml = _write(tmp_path,
        f"name: t\nskill: {skill}\ninput: hi\n"
        "expect:\n  - outputs_contains: [foo]\n")
    issues = validate_file(yaml)
    typo_issue = [i for i in issues if "outputs_contains" in i.message]
    assert typo_issue
    assert typo_issue[0].hint and "output_contains" in typo_issue[0].hint


def test_yaml_syntax_error(tmp_path):
    # Unclosed quote => actual YAML syntax error.
    yaml = _write(tmp_path, 'name: "unclosed\nskill: x\n')
    issues = validate_file(yaml)
    assert len(issues) == 1
    assert "YAML syntax error" in issues[0].message


def test_missing_file(tmp_path):
    issues = validate_file(tmp_path / "nope.yaml")
    assert len(issues) == 1
    assert "not found" in issues[0].message


def test_skill_field_must_point_to_existing_folder(tmp_path):
    yaml = _write(tmp_path, "name: t\nskill: skills/missing\ninput: hi\n")
    issues = validate_file(yaml)
    assert any("skill folder not found" in i.message for i in issues)


def test_invalid_load_strategy_with_suggestion(tmp_path):
    skill = tmp_path / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("ok", encoding="utf-8")
    yaml = _write(tmp_path,
        f"name: t\nskill: {skill}\ninput: hi\nload_strategy: progresive\n")
    issues = validate_file(yaml)
    ls_issue = [i for i in issues if "load_strategy" in i.message]
    assert ls_issue
    assert ls_issue[0].hint and "progressive" in ls_issue[0].hint


def test_warns_on_sandbox_field(tmp_path):
    skill = tmp_path / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("ok", encoding="utf-8")
    yaml = _write(tmp_path,
        f"name: t\nskill: {skill}\ninput: hi\nsandbox: workspace-write\n")
    issues = validate_file(yaml)
    sb_issues = [i for i in issues if "sandbox" in i.message]
    assert sb_issues
    assert sb_issues[0].severity == "warning"


def test_expect_must_be_list(tmp_path):
    skill = tmp_path / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("ok", encoding="utf-8")
    yaml = _write(tmp_path,
        f"name: t\nskill: {skill}\ninput: hi\nexpect: not-a-list\n")
    issues = validate_file(yaml)
    assert any("expect must be a list" in i.message for i in issues)


def test_expect_item_must_be_single_key(tmp_path):
    skill = tmp_path / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("ok", encoding="utf-8")
    yaml = _write(tmp_path,
        f"name: t\nskill: {skill}\ninput: hi\n"
        "expect:\n  - file_exists: a\n    extra: b\n")
    issues = validate_file(yaml)
    assert any("single-key mapping" in i.message for i in issues)

def test_validate_docs_checks_generated_documents(tmp_path):
    skill = tmp_path / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("ok", encoding="utf-8")

    issues = validate_docs([{
        "name": "generated",
        "skill": str(skill),
        "input": "hi",
        "expect": [{"outputs_contains": ["foo"]}],
    }])

    assert any("outputs_contains" in i.message for i in issues)


def test_warns_when_fixture_input_targets_current_workspace(tmp_path):
    skill = tmp_path / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("ok", encoding="utf-8")
    fixture = tmp_path / "sample-repo"
    fixture.mkdir()

    issues = validate_docs([{
        "name": "bad-workspace",
        "skill": str(skill),
        "fixture": str(fixture),
        "input": "Review the current workspace.",
        "expect": [{"exit_code": 0}],
    }])

    workspace_issues = [i for i in issues if "current workspace" in i.message]
    assert workspace_issues
    assert workspace_issues[0].severity == "warning"
    assert "sample-repo" in (workspace_issues[0].hint or "")


def test_warns_when_input_uses_original_fixture_path(tmp_path):
    skill = tmp_path / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("ok", encoding="utf-8")
    fixture = tmp_path / "sample-repo"
    fixture.mkdir()

    issues = validate_docs([{
        "name": "bad-path",
        "skill": str(skill),
        "fixture": str(fixture),
        "input": f"Review {fixture.as_posix()}.",
        "expect": [{"exit_code": 0}],
    }])

    path_issues = [i for i in issues if "original fixture path" in i.message]
    assert path_issues
    assert path_issues[0].severity == "warning"


def test_allows_fixture_input_with_copied_directory(tmp_path):
    skill = tmp_path / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("ok", encoding="utf-8")
    fixture = tmp_path / "sample-repo"
    fixture.mkdir()

    issues = validate_docs([{
        "name": "good-path",
        "skill": str(skill),
        "fixture": str(fixture),
        "input": "Set PROJECT_PATH=./sample-repo and review it.",
        "expect": [{"exit_code": 0}],
    }])

    assert issues == []


def test_warns_on_internal_template_placeholder_assertion(tmp_path):
    skill = tmp_path / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("ok", encoding="utf-8")

    issues = validate_docs([{
        "name": "placeholder",
        "skill": str(skill),
        "input": "hi",
        "expect": [{"output_contains": ["PROJECT_SCOPE_REVIEW_ONLY", "SCAN_ROUND_COUNT"]}],
    }])

    placeholder_issues = [i for i in issues if "template placeholder" in i.message]
    assert placeholder_issues
    assert "SCAN_ROUND_COUNT" in (placeholder_issues[0].hint or "")
    assert "PROJECT_SCOPE_REVIEW_ONLY" not in (placeholder_issues[0].hint or "")




def test_expect_is_required_and_non_empty(tmp_path):
    skill = tmp_path / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("ok", encoding="utf-8")

    missing = validate_docs([{"name": "t", "skill": str(skill), "input": "hi"}])
    empty = validate_docs([{
        "name": "t", "skill": str(skill), "input": "hi", "expect": []
    }])

    assert any("missing required field `expect`" in i.message for i in missing)
    assert any("at least one assertion" in i.message for i in empty)



def test_warns_when_exit_code_assertion_is_implicit(tmp_path):
    skill = tmp_path / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("ok", encoding="utf-8")

    issues = validate_docs([{
        "name": "t",
        "skill": str(skill),
        "input": "hi",
        "expect": [{"final_contains": ["OK"]}],
    }])

    exit_issues = [i for i in issues if "exit_code" in i.message]
    assert exit_issues
    assert exit_issues[0].severity == "warning"
