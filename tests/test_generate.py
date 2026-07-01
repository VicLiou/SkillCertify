"""Tests for generate.py's deterministic helpers (no LLM call)."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from runner.adapters.base import RunResult
from runner.core import generate as generate_mod
from runner.core.generate import (
    _build_fixture_task, _infer_fixture_profile, build_prompt, extract_yaml, generate_fixture,
    generate_testcase, parse_testcases,
)


def test_extract_yaml_strips_outer_fence():
    text = "```yaml\nname: x\nskill: skills/x\ninput: hello\n```"
    out = extract_yaml(text)
    assert out.startswith("name: x")
    assert "```" not in out


def test_extract_yaml_no_fence_passthrough():
    text = "name: x\nskill: skills/x\ninput: hello\n"
    out = extract_yaml(text)
    assert out == text.strip()


def test_parse_testcases_accepts_multidoc():
    text = "name: a\nskill: s\ninput: hello\n---\nname: b\nskill: s\ninput: hello\n"
    docs = parse_testcases(text)
    assert len(docs) == 2
    assert docs[0]["name"] == "a"
    assert docs[1]["name"] == "b"


def test_parse_testcases_rejects_empty():
    with pytest.raises(ValueError, match="no YAML documents"):
        parse_testcases("# just a comment\n")


def test_build_prompt_includes_required_sections(tmp_skill):
    prompt = build_prompt(tmp_skill, fixture=None,
                          coverage="all", bias="mixed", hint=None)
    assert "SKILL_PATH:" in prompt
    assert "COVERAGE: all" in prompt
    assert "BIAS: mixed" in prompt
    assert "FIXTURE_PATH:" not in prompt  # not given
    assert "HINT:" not in prompt          # not given
    # Frontmatter description should be in the prompt body somewhere
    assert "tiny" in prompt.lower()
    assert "When a testcase also has `judge:`" in prompt
    assert "Avoid exact localized sentences" in prompt


def test_build_prompt_with_fixture_and_hint(tmp_skill):
    prompt = build_prompt(tmp_skill, fixture="/some/fixture",
                          coverage="happy", bias="negative",
                          hint="only Chinese inputs")
    assert "FIXTURE_PATH: /some/fixture" in prompt
    assert "COVERAGE: happy" in prompt
    assert "BIAS: negative" in prompt
    assert "HINT: only Chinese inputs" in prompt


def test_build_prompt_includes_fixture_summary(tmp_skill, tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "README.md").write_text("sample fixture", encoding="utf-8")
    src = fixture / "src"
    src.mkdir()
    (src / "app.py").write_text("print('hello')", encoding="utf-8")

    prompt = build_prompt(tmp_skill, fixture=str(fixture),
                          coverage="happy", bias="positive")

    assert "FIXTURE_SUMMARY:" in prompt
    assert "README.md" in prompt
    assert "src/app.py" in prompt
    assert "sample fixture" in prompt


def test_build_prompt_rejects_bad_coverage(tmp_skill):
    with pytest.raises(ValueError, match="coverage"):
        build_prompt(tmp_skill, coverage="wide")


def test_build_prompt_rejects_bad_bias(tmp_skill):
    with pytest.raises(ValueError, match="bias"):
        build_prompt(tmp_skill, bias="lean-positive")


class _FixtureAdapter:
    name = "fixture-adapter"

    def __init__(self, filename: str):
        self.filename = filename
        self.workdirs = []
        self.prompts = []

    def run(self, prompt, workdir, opts):
        self.prompts.append(prompt)
        self.workdirs.append(workdir)
        (workdir / self.filename).write_text(self.filename, encoding="utf-8")
        return RunResult(stdout="", stderr="", exit_code=0)


class _YamlAdapter:
    name = "yaml-adapter"

    def __init__(self, final_message: str):
        self.final_message = final_message
        self.workdirs = []
        self.prompts = []

    def run(self, prompt, workdir, opts):
        self.prompts.append(prompt)
        self.workdirs.append(workdir)
        return RunResult(
            stdout="",
            stderr="",
            exit_code=0,
            final_message=self.final_message,
        )


def test_generate_testcase_uses_workdir_base(tmp_skill, tmp_path):
    adapter = _YamlAdapter(
        f"name: generated\nskill: {tmp_skill.as_posix()}\ninput: hi\nexpect:\n  - exit_code: 0\n"
    )
    workdir_base = tmp_path / "work"

    generate_testcase(tmp_skill, adapter, workdir_base=workdir_base)

    assert adapter.workdirs
    assert adapter.workdirs[0].parent == workdir_base


def test_generate_fixture_uses_workdir_base(tmp_skill, tmp_path):
    adapter = _FixtureAdapter("fixture.txt")
    out_dir = tmp_path / "fixture-out"
    workdir_base = tmp_path / "work"

    generate_fixture(tmp_skill, adapter, out_dir, workdir_base=workdir_base)

    assert adapter.workdirs
    assert adapter.workdirs[0].parent == workdir_base


def test_generate_fixture_replace_existing_removes_stale_files(tmp_skill, tmp_path):
    out_dir = tmp_path / "fixture-out"

    generate_fixture(tmp_skill, _FixtureAdapter("old.txt"), out_dir)
    generate_fixture(tmp_skill, _FixtureAdapter("new.txt"), out_dir,
                     replace_existing=True)

    assert sorted(p.name for p in out_dir.iterdir()) == ["new.txt"]

def test_generate_testcase_rejects_invalid_generated_yaml(tmp_skill):
    adapter = _YamlAdapter(
        f"name: bad\nskill: {tmp_skill.as_posix()}\ninput: hi\n"
        "expect:\n  - outputs_contains: [foo]\n"
    )

    with pytest.raises(ValueError, match="generated testcase failed validation"):
        generate_testcase(tmp_skill, adapter)


def test_fixture_task_requires_readme_and_real_files():
    task = _build_fixture_task("include a failing config")

    assert "write actual files" in task
    assert "README.fixture.md" in task
    assert "Do not run tests" in task
    assert "include a failing config" in task


def _make_git_review_skill(tmp_path):
    skill = tmp_path / "code-review-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "# Code Review\n"
        "Confirm PROJECT_PATH is a readable git repo before scanning.\n"
        "Ask for REVIEW_SCOPE before review.\n"
        "For COMMITTED_DIFF require DIFF_SOURCE and BASE_COMMIT.\n"
        "For WORKTREE_UNCOMMITTED require UNCOMMITTED_TARGET.\n",
        encoding="utf-8",
    )
    return skill


class _ProjectFixtureAdapter:
    name = "project-fixture-adapter"

    def __init__(self):
        self.workdirs = []
        self.prompts = []

    def run(self, prompt, workdir, opts):
        self.prompts.append(prompt)
        self.workdirs.append(workdir)
        (workdir / "README.fixture.md").write_text(
            "Tiny account service fixture. Use PROJECT_PATH with the copied fixture directory.",
            encoding="utf-8",
        )
        src = workdir / "src"
        src.mkdir()
        (src / "account.py").write_text(
            "def withdraw(balance, amount):\n    return balance - amount\n",
            encoding="utf-8",
        )
        return RunResult(stdout="", stderr="", exit_code=0)


def test_build_prompt_includes_fixture_runtime_reference(tmp_skill, tmp_path):
    fixture = tmp_path / "sample-fixture"
    fixture.mkdir()
    (fixture / "README.md").write_text("sample", encoding="utf-8")

    prompt = build_prompt(tmp_skill, fixture=str(fixture),
                          coverage="happy", bias="positive")

    assert "copied_name: sample-fixture" in prompt
    assert "runtime_reference: ./sample-fixture" in prompt
    assert "current workspace" in prompt


def test_infers_git_review_fixture_profile(tmp_path):
    skill = _make_git_review_skill(tmp_path)

    profile = _infer_fixture_profile(skill)
    task = _build_fixture_task(None, profile)

    assert profile.name == "git-review-project"
    assert profile.needs_git is True
    assert "FIXTURE_PROFILE: git-review-project" in task
    assert "git init/add/commit" in task


def test_generate_fixture_initializes_git_baseline_for_review_profile(tmp_path):
    git = shutil.which("git")
    if not git:
        pytest.skip("git is not installed")

    skill = _make_git_review_skill(tmp_path)
    out_dir = tmp_path / "fixture-out"
    workdir_base = tmp_path / "work"

    generate_fixture(
        skill,
        _ProjectFixtureAdapter(),
        out_dir,
        workdir_base=workdir_base,
    )

    assert (out_dir / ".git").is_dir()
    result = subprocess.run(
        [git, "rev-parse", "--verify", "HEAD"],
        cwd=out_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0


def test_generate_testcase_repairs_ambiguous_current_workspace_with_fixture(tmp_skill, tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "README.md").write_text("fixture", encoding="utf-8")
    adapter = _YamlAdapter(
        f"name: bad-workspace\nskill: {tmp_skill.as_posix()}\n"
        f"fixture: {fixture.as_posix()}\n"
        "input: Review the current workspace.\n"
        "expect:\n  - exit_code: 0\n"
    )

    yaml_text, docs, _ = generate_testcase(tmp_skill, adapter, fixture=str(fixture))

    assert "./fixture" in docs[0]["input"]
    assert "./fixture" in yaml_text


def test_generate_testcase_repairs_git_review_workspace_with_project_path(tmp_path):
    skill = _make_git_review_skill(tmp_path)
    fixture = tmp_path / "review-fixture"
    fixture.mkdir()
    (fixture / "README.md").write_text("fixture", encoding="utf-8")
    adapter = _YamlAdapter(
        f"name: review-workspace\nskill: {skill.as_posix()}\n"
        f"fixture: {fixture.as_posix()}\n"
        "input: Review the current workspace.\n"
        "expect:\n  - exit_code: 0\n"
    )

    _, docs, _ = generate_testcase(skill, adapter, fixture=str(fixture))

    assert "PROJECT_PATH=./review-fixture" in docs[0]["input"]

def test_generate_testcase_repairs_original_fixture_path_in_input(tmp_skill, tmp_path):
    fixture = tmp_path / "sample-repo"
    fixture.mkdir()
    (fixture / "README.md").write_text("fixture", encoding="utf-8")
    adapter = _YamlAdapter(
        f"name: bad-path\nskill: {tmp_skill.as_posix()}\n"
        f"fixture: {fixture.as_posix()}\n"
        f"input: Review {fixture.as_posix()}.\n"
        "expect:\n  - exit_code: 0\n"
    )

    yaml_text, docs, _ = generate_testcase(tmp_skill, adapter, fixture=str(fixture))

    assert docs[0]["input"] == "Review ./sample-repo."
    assert fixture.as_posix() not in docs[0]["input"]
    assert "./sample-repo" in yaml_text


def test_generate_testcase_rejects_placeholder_assertions(tmp_skill):
    adapter = _YamlAdapter(
        f"name: placeholder\nskill: {tmp_skill.as_posix()}\ninput: hi\n"
        "expect:\n  - output_contains: [SCAN_ROUND_COUNT]\n"
    )

    with pytest.raises(ValueError, match="template placeholder"):
        generate_testcase(tmp_skill, adapter)




def test_generate_fixture_replace_existing_uses_remove_tree(tmp_skill, tmp_path, monkeypatch):
    out_dir = tmp_path / "fixture-out"
    out_dir.mkdir()
    (out_dir / "stale.txt").write_text("old", encoding="utf-8")
    calls = []

    def fake_remove_tree(path):
        calls.append(path)
        shutil.rmtree(path)
        return True

    monkeypatch.setattr(generate_mod, "remove_tree", fake_remove_tree)

    generate_fixture(
        tmp_skill,
        _FixtureAdapter("new.txt"),
        out_dir,
        replace_existing=True,
    )

    assert calls == [out_dir]
    assert sorted(p.name for p in out_dir.iterdir()) == ["new.txt"]


def test_generate_fixture_replace_existing_fails_when_remove_tree_fails(
        tmp_skill, tmp_path, monkeypatch):
    out_dir = tmp_path / "fixture-out"
    out_dir.mkdir()
    (out_dir / "stale.txt").write_text("old", encoding="utf-8")

    monkeypatch.setattr(generate_mod, "remove_tree", lambda path: False)

    with pytest.raises(PermissionError, match="failed to replace fixture output"):
        generate_fixture(
            tmp_skill,
            _FixtureAdapter("new.txt"),
            out_dir,
            replace_existing=True,
        )

    assert (out_dir / "stale.txt").is_file()
    assert not (out_dir / "new.txt").exists()



def test_generate_testcase_repairs_top_level_assertion_keys(tmp_skill, tmp_path):
    adapter = _YamlAdapter(
        f"name: misplaced\nskill: {tmp_skill.as_posix()}\ninput: hi\n"
        "expect:\n"
        "  - exit_code: 0\n"
        "judge: report should satisfy the semantic criterion\n"
        "command:\n"
        "  run: python -m pytest\n"
    )

    yaml_text, docs, _ = generate_testcase(
        tmp_skill,
        adapter,
        workdir_base=tmp_path / "work",
    )

    assert "judge" not in docs[0]
    assert "command" not in docs[0]
    assert {"judge": "report should satisfy the semantic criterion"} in docs[0]["expect"]
    assert {"command": {"run": "python -m pytest"}} in docs[0]["expect"]
    assert "expect:" in yaml_text
    assert "judge: report should satisfy the semantic criterion" in yaml_text
