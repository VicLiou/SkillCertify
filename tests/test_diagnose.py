"""Tests for diagnose.py (powering `doctor` and `list`)."""
from __future__ import annotations

from pathlib import Path

from runner.core import diagnose
from runner.core.diagnose import doctor, list_local


def test_doctor_returns_rows():
    rows, _ = doctor()
    assert any(r.name == "Python 3.10+" for r in rows)
    # PyYAML must be installed (we got this far)
    yaml_row = [r for r in rows if r.name == "PyYAML"][0]
    assert yaml_row.passed


def test_doctor_python_check_passes_on_current_interpreter():
    rows, _ = doctor()
    py_row = [r for r in rows if r.name == "Python 3.10+"][0]
    assert py_row.passed  # if you can run pytest, you have 3.10+


def test_list_local_empty_dirs(tmp_path):
    """Pointed at empty dirs, list_local should return empty results without raising."""
    skills, testcases = list_local(tmp_path / "skills", tmp_path / "tcs")
    assert skills == []
    assert testcases == []


def test_list_local_finds_and_cross_references(tmp_path):
    skills_dir = tmp_path / "skills"
    tcs_dir = tmp_path / "tcs"
    skills_dir.mkdir()
    tcs_dir.mkdir()

    # one skill
    (skills_dir / "foo").mkdir()
    (skills_dir / "foo" / "SKILL.md").write_text("---\nname: foo\n---\n",
                                                 encoding="utf-8")
    (skills_dir / "foo" / "scripts").mkdir()
    # one testcase pointing at foo
    (tcs_dir / "foo-cases.yaml").write_text(
        "name: a\nskill: skills/foo\ninput: hi\nruns: 3\n",
        encoding="utf-8")
    # one testcase NOT pointing at a known skill -> still listed
    (tcs_dir / "orphan.yaml").write_text(
        "name: b\nskill: skills/missing\ninput: hi\n",
        encoding="utf-8")

    skills, testcases = list_local(skills_dir, tcs_dir)
    assert len(skills) == 1
    assert skills[0].name == "foo"
    assert skills[0].has_scripts
    assert not skills[0].has_references
    assert "foo-cases.yaml" in skills[0].testcases

    assert len(testcases) == 2
    foo_tc = [t for t in testcases if t.filename == "foo-cases.yaml"][0]
    assert foo_tc.runs == 3
    assert foo_tc.n_cases == 1


def test_list_local_ignores_underscore_prefixed(tmp_path):
    """_TEMPLATE.yaml and friends shouldn't show up in `list`."""
    tcs_dir = tmp_path / "tcs"
    tcs_dir.mkdir()
    (tcs_dir / "_TEMPLATE.yaml").write_text("name: ignored\n", encoding="utf-8")
    (tcs_dir / "real.yaml").write_text("name: r\nskill: x\ninput: hi\n",
                                       encoding="utf-8")
    skills, testcases = list_local(tmp_path / "no-skills", tcs_dir)
    assert [t.filename for t in testcases] == ["real.yaml"]

def test_doctor_checks_pywinpty_by_winpty_import_name(monkeypatch):
    checked: list[str] = []

    def fake_find_spec(name):
        checked.append(name)
        return object() if name in {"yaml", "winpty"} else None

    monkeypatch.setattr(diagnose, "find_spec", fake_find_spec)

    rows, _ = diagnose.doctor()

    pywinpty = [r for r in rows if r.name == "pywinpty (optional)"][0]
    assert pywinpty.passed
    assert "winpty" in checked
    assert "pywinpty" not in checked

def test_doctor_architect_falls_back_to_framework_bundle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    row = diagnose._check_architect_skill()

    assert row.passed
    assert "interactive-skill-architect" in row.detail
    assert "tools" in row.detail
