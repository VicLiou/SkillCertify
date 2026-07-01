"""Tests for human-readable report rendering."""
from __future__ import annotations

import json

from runner.adapters.base import RunResult
from runner.core.assertions import CheckResult
from runner.core.report import print_final_report, write_json, write_trace
from runner.core.runner import CaseReport, RunRecord, TestCase as RunnerTestCase


def _case(name: str) -> RunnerTestCase:
    return RunnerTestCase(name=name, skill="skills/demo", input="hi", runs=2)


def _record(index: int, *, passed: bool, check_name: str = "output_contains=['OK']",
            tool: str = "Read") -> RunRecord:
    result = RunResult(stdout="", stderr="", exit_code=0,
                       tool_calls=[{"name": tool}])
    checks = [CheckResult(check_name, passed)]
    return RunRecord(index=index, result=result, checks=checks)


def test_final_report_uses_summary_table_and_separate_failures(capsys):
    passing = CaseReport(
        _case("project-scope-review"),
        [_record(0, passed=True, tool="Read"),
         _record(1, passed=True, tool="Grep")],
        adapter_name="fake",
    )
    long_judge = (
        "judge='Expected behavior is to ask the user to choose review scope "
        "before scanning source files, loading review references, or producing "
        "a review conclusion.'"
    )
    partial = CaseReport(
        _case("missing-review-scope"),
        [_record(0, passed=False, check_name=long_judge),
         _record(1, passed=True, check_name=long_judge)],
        adapter_name="fake",
    )

    print_final_report([passing, partial])

    out = capsys.readouterr().out
    assert "Summary" in out
    assert "Totals" in out
    assert "Failures" in out
    assert "PARTIAL" in out
    assert "flow variance" in out
    assert "\u21b3" not in out
    assert "failed 1/2" in out
    assert "..." in out
    assert "--trace trace.json" in out




def test_report_writers_create_parent_dirs_and_trace_workdir(tmp_path):
    tc = _case("nested-output")
    result = RunResult(stdout="", stderr="", exit_code=0,
                       metadata={"rollout_path": "rollout.jsonl"})
    rec = RunRecord(index=0, result=result,
                    checks=[CheckResult("exit_code=0", True)],
                    workdir="kept-workdir")
    report = CaseReport(tc, [rec], adapter_name="fake")

    json_path = tmp_path / "reports" / "summary.json"
    trace_path = tmp_path / "traces" / "trace.json"
    write_json([report], json_path)
    write_trace([report], trace_path)

    assert json_path.is_file()
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace[0]["workdir"] == "kept-workdir"
    assert trace[0]["metadata"]["rollout_path"] == "rollout.jsonl"
