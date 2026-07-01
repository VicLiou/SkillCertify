"""Tests for fix_skill's failure-summary and architect helpers."""
from __future__ import annotations

from pathlib import Path

from runner.core.architect import backup_then_copy, diff_skill, FileChange
from runner.core.fix_testcase import _backup_path
from runner.core.fix_skill import (
    _build_task_prompt, _failures_to_markdown, _summarize_failures,
)


def _trace_run(case: str, passed: bool, checks: list[dict], **kw) -> dict:
    base = {
        "case": case, "passed": passed,
        "final_message": kw.get("final_message", ""),
        "tool_sequence": kw.get("tool_sequence", []),
        "checks": checks,
    }
    return base


def test_summarize_failures_groups_by_assertion():
    trace = [
        _trace_run("case-a", False, checks=[
            {"name": "regex='X'", "passed": False, "skipped": False,
             "detail": "no match"},
            {"name": "exit_code=0", "passed": True, "skipped": False, "detail": "ok"},
        ]),
        _trace_run("case-b", False, checks=[
            {"name": "regex='X'", "passed": False, "skipped": False,
             "detail": "no match"},
        ]),
        _trace_run("case-c", True, checks=[]),  # passed, ignored
    ]
    groups = _summarize_failures(trace)
    # One failure pattern, two cases hitting it.
    assert len(groups) == 1
    assert groups[0].assertion == "regex='X'"
    assert sorted(groups[0].cases) == ["case-a", "case-b"]


def test_summarize_failures_case_filter():
    trace = [
        _trace_run("alpha-bug", False, checks=[
            {"name": "exit_code=0", "passed": False, "skipped": False,
             "detail": "got -1"}]),
        _trace_run("beta-bug", False, checks=[
            {"name": "exit_code=0", "passed": False, "skipped": False,
             "detail": "got -1"}]),
    ]
    groups = _summarize_failures(trace, target_case_substr="alpha")
    assert len(groups) == 1
    assert groups[0].cases == ["alpha-bug"]


def test_summarize_failures_empty_when_all_pass():
    trace = [_trace_run("x", True, checks=[])]
    assert _summarize_failures(trace) == []


def test_failures_to_markdown_handles_empty():
    out = _failures_to_markdown([])
    assert "沒有失敗證據" in out


def test_build_task_prompt_carries_constraints_and_failures(tmp_skill):
    failures_md = "# fake failure evidence"
    prompt = _build_task_prompt("tiny-skill", "focused", failures_md, None)
    # Constraint block is present.
    assert "禁止" in prompt
    assert "Hard Gates" in prompt
    # Failures markdown is appended.
    assert failures_md in prompt
    # Scope-specific text.
    assert "B" in prompt  # option B = focused


def test_build_task_prompt_with_extra_constraint(tmp_skill):
    prompt = _build_task_prompt("tiny", "focused", "x", "請不要改 Step 5")
    assert "請不要改 Step 5" in prompt


def test_diff_skill_detects_modify_add_delete(tmp_path):
    orig = tmp_path / "orig"
    mod = tmp_path / "mod"
    orig.mkdir(); mod.mkdir()
    (orig / "SKILL.md").write_text("hello\n", encoding="utf-8")
    (orig / "to-delete.md").write_text("bye\n", encoding="utf-8")
    (mod / "SKILL.md").write_text("hello\nadded\n", encoding="utf-8")
    (mod / "new-file.md").write_text("new\n", encoding="utf-8")

    changes = diff_skill(orig, mod)
    kinds = {c.kind: c.relpath.as_posix() for c in changes}
    assert kinds["modified"] == "SKILL.md"
    assert kinds["added"] == "new-file.md"
    assert kinds["deleted"] == "to-delete.md"


def test_diff_skill_returns_empty_when_identical(tmp_path):
    orig = tmp_path / "orig"
    mod = tmp_path / "mod"
    orig.mkdir(); mod.mkdir()
    (orig / "x").write_text("same", encoding="utf-8")
    (mod / "x").write_text("same", encoding="utf-8")
    assert diff_skill(orig, mod) == []


def test_diff_skill_handles_binary_files(tmp_path):
    orig = tmp_path / "orig"
    mod = tmp_path / "mod"
    orig.mkdir(); mod.mkdir()
    (orig / "asset.bin").write_bytes(b"\xff\xfeold")
    (mod / "asset.bin").write_bytes(b"\xff\xfenew")

    changes = diff_skill(orig, mod)

    assert len(changes) == 1
    assert changes[0].kind == "modified"
    assert "Binary file changed" in changes[0].diff_text


def test_backup_then_copy_writes_backup_and_applies(tmp_path):
    target = tmp_path / "tgt"
    mod = tmp_path / "mod"
    target.mkdir(); mod.mkdir()
    (target / "SKILL.md").write_text("original", encoding="utf-8")
    (mod / "SKILL.md").write_text("modified", encoding="utf-8")
    (mod / "NEW.md").write_text("new file", encoding="utf-8")

    changes = [
        FileChange(Path("SKILL.md"), "modified", "..."),
        FileChange(Path("NEW.md"), "added", "..."),
    ]
    backup = backup_then_copy(mod, target, changes)
    # Backup contains only the file that existed before.
    assert (backup / "SKILL.md").read_text(encoding="utf-8") == "original"
    assert not (backup / "NEW.md").exists()
    # Target now has the modified content + new file.
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "modified"
    assert (target / "NEW.md").read_text(encoding="utf-8") == "new file"


def test_summarize_failures_includes_crash_only_runs():
    trace = [{
        "case": "crashy",
        "passed": False,
        "error": "adapter timed out",
        "exit_code": 1,
        "checks": [],
        "tool_sequence": ["Read"],
        "final_message": "partial output",
    }]

    groups = _summarize_failures(trace)

    assert len(groups) == 1
    assert groups[0].assertion == "run failed before actionable assertions"
    assert groups[0].sample_detail == "adapter timed out"


def test_summarize_failures_can_include_flow_instability():
    trace = [
        {"case": "flowy", "passed": True, "tool_sequence": ["Read", "Grep"], "checks": []},
        {"case": "flowy", "passed": True, "tool_sequence": ["Read", "Read"], "checks": []},
    ]

    groups = _summarize_failures(trace, include_flow_instability=True)

    assert len(groups) == 1
    assert groups[0].assertion == "flow instability: flowy"
    assert "2 distinct tool flows" in groups[0].sample_detail




def test_backup_then_copy_uses_unique_backup_dirs(tmp_path):
    target = tmp_path / "tgt"
    mod = tmp_path / "mod"
    target.mkdir(); mod.mkdir()
    (target / "SKILL.md").write_text("one", encoding="utf-8")
    (mod / "SKILL.md").write_text("two", encoding="utf-8")
    changes = [FileChange(Path("SKILL.md"), "modified", "...")]

    first = backup_then_copy(mod, target, changes)
    (mod / "SKILL.md").write_text("three", encoding="utf-8")
    second = backup_then_copy(mod, target, changes)

    assert first != second
    assert first.is_dir()
    assert second.is_dir()



def test_fix_testcase_backup_path_is_unique(tmp_path):
    testcase = tmp_path / "case.yaml"
    testcase.write_text("old", encoding="utf-8")

    first = _backup_path(testcase)
    first.write_text("backup", encoding="utf-8")
    second = _backup_path(testcase)

    assert first != second
    assert second.parent == testcase.parent
