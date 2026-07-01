"""Pin down every assertion type so future refactors don't silently break
expected behavior. Each test exercises one passing case + one failing case
where it matters."""
from __future__ import annotations

from pathlib import Path

from runner.core.assertions import CheckResult, evaluate
from runner.core.runner import RunRecord

# `make_run_result` helper, copied locally so test_assertions stays
# importable as a plain module (conftest.py is auto-loaded by pytest but
# isn't a regular importable module).
from runner.adapters.base import RunResult


def make_run_result(*, stdout="", final_message="", tool_calls=None,
                    exit_code=0, events=None, latency_ms=100,
                    error=None, tokens=None):
    return RunResult(
        stdout=stdout, stderr="", exit_code=exit_code,
        final_message=final_message, tool_calls=tool_calls or [],
        events=events or [], latency_ms=latency_ms,
        tokens=tokens, error=error,
    )


def _eval(expect, result, workdir=None):
    """One-shot wrapper that returns the list of CheckResult."""
    return evaluate(expect, result, workdir or Path("."),
                    judge=None, exclude_dirs=[], allow_exec=False)


def test_exit_code():
    r = make_run_result(exit_code=0)
    [c] = _eval([{"exit_code": 0}], r)
    assert c.passed
    [c] = _eval([{"exit_code": 1}], r)
    assert not c.passed and "got 0" in c.detail


def test_file_exists_and_absent(tmp_workdir):
    (tmp_workdir / "out.txt").write_text("ok", encoding="utf-8")
    r = make_run_result()
    [c] = _eval([{"file_exists": "out.txt"}], r, tmp_workdir)
    assert c.passed
    [c] = _eval([{"file_exists": "missing.txt"}], r, tmp_workdir)
    assert not c.passed
    [c] = _eval([{"file_absent": "missing.txt"}], r, tmp_workdir)
    assert c.passed
    [c] = _eval([{"file_absent": "out.txt"}], r, tmp_workdir)
    assert not c.passed


def test_final_contains_does_not_scan_files(tmp_workdir):
    """Key contract: final_contains looks ONLY at final_message + stdout,
    not at produced files. Regression guard for the cub-code-review bug."""
    (tmp_workdir / "report.md").write_text("SUCCESS\n", encoding="utf-8")
    r = make_run_result(final_message="done", stdout="")
    [c] = _eval([{"final_contains": ["SUCCESS"]}], r, tmp_workdir)
    assert not c.passed   # SUCCESS lives only in the file, not in final


def test_output_contains_scans_files(tmp_workdir):
    """Key contract: output_contains is the only string check that DOES
    scan files. Mirror of test_final_contains_does_not_scan_files."""
    (tmp_workdir / "report.md").write_text("SUCCESS\n", encoding="utf-8")
    r = make_run_result(final_message="report written", stdout="")
    [c] = _eval([{"output_contains": ["SUCCESS"]}], r, tmp_workdir)
    assert c.passed


def test_regex_does_not_scan_files(tmp_workdir):
    """Key contract: regex looks at final + stdout only. Regression guard."""
    (tmp_workdir / "report.md").write_text("FAIL\n", encoding="utf-8")
    r = make_run_result(final_message="report written", stdout="")
    [c] = _eval([{"regex": r"(SUCCESS|FAIL)"}], r, tmp_workdir)
    assert not c.passed


def test_reads_file_works_across_adapters():
    """reads_file must accept both Claude-style {name: Read, input: {file_path}}
    and codex-style {name: shell_command, input: '{"command": "Get-Content ..."}'}.
    Regression guard for the cub-code-review false-negative on reads_file."""
    # Claude shape
    r1 = make_run_result(tool_calls=[
        {"name": "Read", "input": {"file_path": "/x/SKILL.md"}},
    ])
    [c] = _eval([{"reads_file": ["SKILL.md"]}], r1)
    assert c.passed

    # codex shape
    r2 = make_run_result(tool_calls=[
        {"name": "shell_command",
         "input": '{"command":"Get-Content -Raw .\\\\SKILL.md"}'},
    ])
    [c] = _eval([{"reads_file": ["SKILL.md"]}], r2)
    assert c.passed

    # missing
    r3 = make_run_result(tool_calls=[{"name": "Bash", "input": {"command": "ls"}}])
    [c] = _eval([{"reads_file": ["SKILL.md"]}], r3)
    assert not c.passed


def test_tool_used():
    r = make_run_result(tool_calls=[
        {"name": "Bash", "input": {"command": "ls"}},
        {"name": "Read", "input": {"file_path": "/x"}},
    ])
    [c] = _eval([{"tool_used": "Bash"}], r)
    assert c.passed
    [c] = _eval([{"tool_used": "Edit"}], r)
    assert not c.passed


def test_tool_used_matches_tool_name_only():
    r = make_run_result(tool_calls=[
        {"name": "Read", "input": {"file_path": "notes-about-write.txt"}},
    ])
    [c] = _eval([{"tool_used": "Write"}], r)
    assert not c.passed


def test_stdout_contains_does_not_scan_final_message():
    r = make_run_result(stdout="", final_message="DONE_MARKER")
    [c] = _eval([{"stdout_contains": "DONE_MARKER"}], r)
    assert not c.passed


def test_flow_contains_is_subsequence():
    r = make_run_result(tool_calls=[
        {"name": "Read"}, {"name": "Bash"}, {"name": "Write"},
    ])
    [c] = _eval([{"flow_contains": ["Read", "Write"]}], r)
    assert c.passed  # subsequence, Bash in between is OK
    [c] = _eval([{"flow_contains": ["Write", "Read"]}], r)
    assert not c.passed  # wrong order


def test_flow_equals_is_exact():
    r = make_run_result(tool_calls=[{"name": "Read"}, {"name": "Write"}])
    [c] = _eval([{"flow_equals": ["Read", "Write"]}], r)
    assert c.passed
    [c] = _eval([{"flow_equals": ["Read"]}], r)
    assert not c.passed


def test_max_latency_ms():
    r = make_run_result(latency_ms=500)
    [c] = _eval([{"max_latency_ms": 1000}], r)
    assert c.passed
    [c] = _eval([{"max_latency_ms": 100}], r)
    assert not c.passed


def test_command_skipped_when_allow_exec_off(tmp_workdir):
    r = make_run_result()
    [c] = _eval([{"command": {"run": "echo hi"}}], r, tmp_workdir)
    assert c.skipped  # default allow_exec=False
    assert not c.passed


def test_judge_skipped_when_judge_none():
    r = make_run_result(final_message="hello")
    [c] = _eval([{"judge": "did it say hello?"}], r)
    assert c.skipped  # judge=None means skip
    assert not c.passed


def test_skipped_check_prevents_run_pass():
    rec = RunRecord(
        index=0,
        result=make_run_result(),
        checks=[CheckResult("command:echo hi", False, "exec disabled", skipped=True)],
    )
    assert not rec.passed



def test_empty_expect_fails_run():
    checks = _eval([], make_run_result())

    assert len(checks) == 1
    assert checks[0].name == "expect"
    assert not checks[0].passed



def test_nonzero_exit_without_explicit_exit_code_fails_run():
    result = make_run_result(exit_code=2, final_message="OK")
    checks = _eval([{"final_contains": ["OK"]}], result)
    rec = RunRecord(index=0, result=result, checks=checks)

    assert checks[0].name == "exit_code=0 (implicit)"
    assert not checks[0].passed
    assert not rec.passed



def test_explicit_nonzero_exit_can_pass():
    result = make_run_result(exit_code=2)
    checks = _eval([{"exit_code": 2}], result)
    rec = RunRecord(index=0, result=result, checks=checks)

    assert checks[0].passed
    assert rec.passed
