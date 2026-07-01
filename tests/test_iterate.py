"""Tests for iterate loop safety and convergence behavior."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from runner.adapters.base import RunResult
from runner.core.assertions import CheckResult
from runner.core.runner import CaseReport, RunRecord, TestCase as RunnerTestCase
import runner.core.iterate as iter_mod


class _Adapter:
    name = "fake"


def _case() -> RunnerTestCase:
    return RunnerTestCase(name="case", skill="skills/demo", input="hi", runs=1)


def _report(tc: RunnerTestCase, records: list[RunRecord]) -> CaseReport:
    return CaseReport(case=tc, records=records, adapter_name="fake")


def _record(index: int, *, checks=None, tool="Read", error=None) -> RunRecord:
    result = RunResult(
        stdout="",
        stderr="",
        exit_code=1 if error else 0,
        tool_calls=[{"name": tool}],
        error=error,
    )
    return RunRecord(index=index, result=result, checks=checks or [])


def test_iterate_stops_on_skipped_only_failures(monkeypatch, tmp_path):
    tc = _case()
    fix_called = False

    def fake_run_testcase(*args, **kwargs):
        return _report(tc, [
            _record(0, checks=[CheckResult("command:pytest", False, skipped=True)])
        ])

    def fake_fix_skill(*args, **kwargs):
        nonlocal fix_called
        fix_called = True
        return [], RunResult("", "", 0), None

    monkeypatch.setattr(iter_mod, "run_testcase", fake_run_testcase)
    monkeypatch.setattr(iter_mod, "fix_skill", fake_fix_skill)

    outcomes = iter_mod.iterate(
        [tc], _Adapter(), Path("skills/demo"), Path("skills/architect"), _Adapter(),
        max_rounds=2, trace_dir=tmp_path,
    )

    assert len(outcomes) == 1
    assert outcomes[0].stop_reason == "no_actionable_failure_evidence"
    assert fix_called is False


def test_iterate_stops_when_fix_skill_makes_no_changes(monkeypatch, tmp_path):
    tc = _case()

    def fake_run_testcase(*args, **kwargs):
        return _report(tc, [
            _record(0, checks=[CheckResult("final_contains=['OK']", False)])
        ])

    monkeypatch.setattr(iter_mod, "run_testcase", fake_run_testcase)
    monkeypatch.setattr(
        iter_mod,
        "fix_skill",
        lambda *args, **kwargs: ([], RunResult("", "", 0), None),
    )

    outcomes = iter_mod.iterate(
        [tc], _Adapter(), Path("skills/demo"), Path("skills/architect"), _Adapter(),
        max_rounds=2, trace_dir=tmp_path,
    )

    assert len(outcomes) == 1
    assert outcomes[0].stop_reason == "no_changes"
    assert outcomes[0].fix_applied is False


def test_iterate_require_stable_flow_does_not_converge_on_flow_variance(monkeypatch, tmp_path):
    tc = _case()
    calls = 0
    fix_calls = []

    def fake_run_testcase(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _report(tc, [_record(0, tool="Read"), _record(1, tool="Grep")])
        return _report(tc, [_record(0, tool="Read"), _record(1, tool="Read")])

    def fake_fix_skill(*args, **kwargs):
        fix_calls.append(kwargs)
        change = SimpleNamespace(relpath=Path("SKILL.md"))
        return [change], RunResult("", "", 0), tmp_path / "backup"

    monkeypatch.setattr(iter_mod, "run_testcase", fake_run_testcase)
    monkeypatch.setattr(iter_mod, "fix_skill", fake_fix_skill)

    outcomes = iter_mod.iterate(
        [tc], _Adapter(), Path("skills/demo"), Path("skills/architect"), _Adapter(),
        max_rounds=2, runs_per_round=2, trace_dir=tmp_path,
        require_stable_flow=True,
    )

    assert len(outcomes) == 2
    assert outcomes[0].pass_rate == 1.0
    assert outcomes[0].unstable_cases == ["case"]
    assert outcomes[0].fix_applied is True
    assert fix_calls[0]["include_flow_instability"] is True
    assert outcomes[1].stop_reason == "converged"
    assert outcomes[1].unstable_cases == []


def test_iterate_rejects_require_stable_flow_with_one_run(tmp_path):
    tc = _case()

    try:
        iter_mod.iterate(
            [tc], _Adapter(), Path("skills/demo"), Path("skills/architect"), _Adapter(),
            max_rounds=1, runs_per_round=1, trace_dir=tmp_path,
            require_stable_flow=True,
        )
    except ValueError as exc:
        assert "runs_per_round >= 2" in str(exc)
    else:
        raise AssertionError("expected ValueError")
