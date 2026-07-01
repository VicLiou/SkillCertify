"""Tests for load_cases — the friendly-error wrapper around YAML loading."""
from __future__ import annotations

import pytest

from runner.cli import cmd_run, load_cases


def test_load_cases_missing_file_is_friendly_error(tmp_path):
    with pytest.raises(RuntimeError, match="testcase file not found"):
        load_cases([str(tmp_path / "nope.yaml")])


def test_load_cases_invalid_yaml_is_friendly_error(tmp_path):
    p = tmp_path / "bad.yaml"
    # Unclosed quote => actual YAML syntax error.
    p.write_text('name: "unclosed\nskill: x\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="YAML"):
        load_cases([str(p)])


def test_load_cases_missing_field_is_friendly_error(tmp_path):
    p = tmp_path / "tc.yaml"
    p.write_text("name: t\nskill: x\n", encoding="utf-8")  # no `input`
    with pytest.raises(RuntimeError, match="missing required field"):
        load_cases([str(p)])


def test_load_cases_returns_testcases(tmp_path):
    p = tmp_path / "tc.yaml"
    p.write_text(
        "name: a\nskill: skills/foo\ninput: hi\n---\n"
        "name: b\nskill: skills/foo\ninput: hi\n",
        encoding="utf-8")
    cases = load_cases([str(p)])
    assert len(cases) == 2
    assert cases[0].name == "a"
    assert cases[1].name == "b"



def test_cmd_run_validates_before_adapter(tmp_path, capsys):
    skill = tmp_path / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("ok", encoding="utf-8")
    p = tmp_path / "bad.yaml"
    p.write_text(
        f"name: bad\nskill: {skill}\ninput: hi\nexpect: not-a-list\n",
        encoding="utf-8",
    )

    code = cmd_run([str(p), "--adapter", "claude"])

    assert code == 2
    assert "expect must be a list" in capsys.readouterr().err
