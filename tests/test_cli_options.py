"""Regression tests for CLI option exposure.

These checks intentionally stop at argparse help output. They catch the class
of regression where a core path supports an option, but a subcommand cannot
accept it from the user.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from runner import cli
from runner.adapters.base import RunResult
from runner.core.assertions import CheckResult
from runner.core.iterate import RoundOutcome
from runner.core.runner import CaseReport, RunRecord


@pytest.mark.parametrize(
    ("command", "expected_options"),
    [
        (cli.cmd_generate, ("--workdir-base",)),
        (cli.cmd_generate_fixture, ("--workdir-base",)),
        (cli.cmd_fix_testcase, ("--workdir-base",)),
        (
            cli.cmd_bootstrap,
            (
                "--gen-binary",
                "--binary",
                "--run-model",
                "--judge-model",
                "--judge-binary",
                "--workdir-base",
                "--require-stable-flow",
            ),
        ),
        (
            cli.cmd_iterate,
            (
                "--gen-binary",
                "--architect-model",
                "--judge-model",
                "--judge-binary",
                "--workdir-base",
                "--require-stable-flow",
            ),
        ),
    ],
)
def test_subcommand_help_exposes_supported_options(command, expected_options, capsys):
    with pytest.raises(SystemExit) as exc:
        command(["--help"])

    assert exc.value.code == 0
    out = capsys.readouterr().out
    for option in expected_options:
        assert option in out




def test_append_generation_hint_preserves_user_hint():
    hint = cli._append_generation_hint("focus on PROJECT scope", "include judge")

    assert hint == "focus on PROJECT scope\ninclude judge"

class _FakeAdapter:
    name = "fake"

    def __init__(self, binary=None):
        self.binary = binary

    def run(self, prompt, workdir, opts):
        return RunResult(stdout="", stderr="", exit_code=0)


def _passing_report(tc, adapter_name="fake"):
    result = RunResult(stdout="", stderr="", exit_code=0)
    return CaseReport(tc, [RunRecord(index=0, result=result, checks=[])],
                      adapter_name=adapter_name)


def _failing_report(tc, adapter_name="fake"):
    result = RunResult(stdout="", stderr="", exit_code=0)
    checks = [CheckResult("final_contains=['OK']", False)]
    return CaseReport(tc, [RunRecord(index=0, result=result, checks=checks)],
                      adapter_name=adapter_name)


def test_bootstrap_forwards_binary_model_and_workdir_options(
        tmp_skill, tmp_path, monkeypatch):
    seen = {}
    workdir_base = tmp_path / "work"
    fixture_out = tmp_path / "fixture"
    testcase_out = tmp_path / "testcase.yaml"

    def fake_generate_fixture(skill_dir, adapter, out_dir, **kwargs):
        seen["fixture"] = {
            "adapter_binary": adapter.binary,
            "model": kwargs["model"],
            "workdir_base": kwargs["workdir_base"],
            "replace_existing": kwargs["replace_existing"],
            "out_dir": out_dir,
        }
        return [Path("fixture.txt")], RunResult(stdout="", stderr="", exit_code=0)

    def fake_generate_testcase(skill_dir, adapter, **kwargs):
        seen["testcase"] = {
            "adapter_binary": adapter.binary,
            "fixture": kwargs["fixture"],
            "model": kwargs["model"],
            "workdir_base": kwargs["workdir_base"],
            "hint": kwargs["hint"],
        }
        yaml_text = (
            f"name: boot\nskill: {skill_dir.as_posix()}\ninput: hi\n"
            "expect:\n  - exit_code: 0\n  - judge: boot should be correct\n"
        )
        docs = [{
            "name": "boot",
            "skill": str(skill_dir),
            "input": "hi",
            "expect": [{"exit_code": 0}, {"judge": "boot should be correct"}],
        }]
        return yaml_text, docs, RunResult(stdout="", stderr="", exit_code=0)

    def fake_run_testcase(tc, adapter, judge=None, **kwargs):
        seen["run"] = {
            "adapter_binary": adapter.binary,
            "model": tc.options.model,
            "judge_model": judge.opts.model,
            "judge_binary": judge.adapter.binary,
            "judge_workdir_base": judge.workdir_base,
            "workdir_base": kwargs["workdir_base"],
            "allow_exec": kwargs["allow_exec"],
        }
        return _passing_report(tc, adapter.name)

    monkeypatch.setattr(cli, "ADAPTERS", {"fake": _FakeAdapter})
    monkeypatch.setattr(cli, "generate_fixture", fake_generate_fixture)
    monkeypatch.setattr(cli, "generate_testcase", fake_generate_testcase)
    monkeypatch.setattr(cli, "run_testcase", fake_run_testcase)

    code = cli.cmd_bootstrap([
        str(tmp_skill),
        "--gen-adapter", "fake",
        "--gen-binary", "gen.exe",
        "--adapter", "fake",
        "--binary", "run.exe",
        "--model", "gen-model",
        "--run-model", "run-model",
        "--allow-exec",
        "--judge",
        "--judge-adapter", "fake",
        "--judge-model", "judge-model",
        "--judge-binary", "judge.exe",
        "--workdir-base", str(workdir_base),
        "--fixture-out", str(fixture_out),
        "--testcase-out", str(testcase_out),
        "--force",
    ])

    assert code == 0
    assert seen["fixture"] == {
        "adapter_binary": "gen.exe",
        "model": "gen-model",
        "workdir_base": str(workdir_base),
        "replace_existing": True,
        "out_dir": fixture_out,
    }
    assert seen["testcase"] == {
        "adapter_binary": "gen.exe",
        "fixture": str(fixture_out),
        "model": "gen-model",
        "workdir_base": str(workdir_base),
        "hint": cli._BOOTSTRAP_EXEC_GENERATION_HINT + "\n" + cli._BOOTSTRAP_JUDGE_GENERATION_HINT,
    }
    assert seen["run"] == {
        "adapter_binary": "run.exe",
        "model": "run-model",
        "judge_model": "judge-model",
        "judge_binary": "judge.exe",
        "judge_workdir_base": str(workdir_base),
        "workdir_base": str(workdir_base),
        "allow_exec": True,
    }


def test_bootstrap_judge_requires_generated_judge_assertions(
        tmp_skill, tmp_path, monkeypatch, capsys):
    testcase_out = tmp_path / "case.yaml"
    called = {"run": False}

    def fake_generate_testcase(skill_dir, adapter, **kwargs):
        yaml_text = (
            f"name: boot\nskill: {skill_dir.as_posix()}\ninput: hi\n"
            "expect:\n  - exit_code: 0\n"
        )
        docs = [{
            "name": "boot",
            "skill": str(skill_dir),
            "input": "hi",
            "expect": [{"exit_code": 0}],
        }]
        return yaml_text, docs, RunResult(stdout="", stderr="", exit_code=0)

    def fake_run_testcase(*args, **kwargs):
        called["run"] = True
        return _passing_report(args[0])

    monkeypatch.setattr(cli, "ADAPTERS", {"fake": _FakeAdapter})
    monkeypatch.setattr(cli, "generate_testcase", fake_generate_testcase)
    monkeypatch.setattr(cli, "run_testcase", fake_run_testcase)

    code = cli.cmd_bootstrap([
        str(tmp_skill),
        "--no-fixture",
        "--gen-adapter", "fake",
        "--adapter", "fake",
        "--judge",
        "--judge-adapter", "fake",
        "--testcase-out", str(testcase_out),
        "--force",
    ])

    err = capsys.readouterr().err
    assert code == 1
    assert "--judge was requested" in err
    assert "boot" in err
    assert not testcase_out.exists()
    assert called["run"] is False

def test_iterate_forwards_binary_model_and_workdir_options(
        tmp_skill, tmp_path, monkeypatch):
    testcase = tmp_path / "case.yaml"
    testcase.write_text(
        f"name: iter\nskill: {tmp_skill}\ninput: hi\nexpect:\n  - exit_code: 0\n",
        encoding="utf-8",
    )
    workdir_base = tmp_path / "work"
    trace_dir = tmp_path / "traces"
    seen = {}

    def fake_iterate(cases, test_adapter, target_dir, architect_dir, gen_adapter,
                     **kwargs):
        seen.update({
            "case_model": cases[0].options.model,
            "test_binary": test_adapter.binary,
            "gen_binary": gen_adapter.binary,
            "judge_model": kwargs["judge"].opts.model,
            "judge_binary": kwargs["judge"].adapter.binary,
            "judge_workdir_base": kwargs["judge"].workdir_base,
            "workdir_base": kwargs["workdir_base"],
            "trace_dir": kwargs["trace_dir"],
            "architect_model": kwargs["architect_model"],
            "architect_timeout_s": kwargs["architect_timeout_s"],
            "require_stable_flow": kwargs["require_stable_flow"],
            "target_dir": target_dir,
            "architect_dir": architect_dir,
        })
        return [RoundOutcome(1, 1.0, 1, 1, None, False, stop_reason="converged")]

    monkeypatch.setattr(cli, "ADAPTERS", {"fake": _FakeAdapter})
    monkeypatch.setattr(cli, "iterate", fake_iterate)

    code = cli.cmd_iterate([
        str(testcase),
        "--skill", str(tmp_skill),
        "--adapter", "fake",
        "--binary", "run.exe",
        "--gen-adapter", "fake",
        "--gen-binary", "gen.exe",
        "--model", "run-model",
        "--architect-model", "arch-model",
        "--judge",
        "--judge-adapter", "fake",
        "--judge-model", "judge-model",
        "--judge-binary", "judge.exe",
        "--workdir-base", str(workdir_base),
        "--trace-dir", str(trace_dir),
        "--architect-skill", str(tmp_skill),
        "--architect-timeout-s", "77",
    ])

    assert code == 0
    assert seen == {
        "case_model": "run-model",
        "test_binary": "run.exe",
        "gen_binary": "gen.exe",
        "judge_model": "judge-model",
        "judge_binary": "judge.exe",
        "judge_workdir_base": str(workdir_base),
        "workdir_base": str(workdir_base),
        "trace_dir": trace_dir,
        "architect_model": "arch-model",
        "architect_timeout_s": 77,
        "require_stable_flow": False,
        "target_dir": tmp_skill,
        "architect_dir": tmp_skill,
    }


def test_generate_defaults_workdir_base_to_dot_work(tmp_skill, tmp_path, monkeypatch):
    seen = {}
    out_path = tmp_path / "generated.yaml"

    def fake_generate_testcase(skill_dir, adapter, **kwargs):
        seen["workdir_base"] = kwargs["workdir_base"]
        yaml_text = f"name: gen\nskill: {skill_dir.as_posix()}\ninput: hi\n"
        docs = [{"name": "gen", "skill": str(skill_dir), "input": "hi"}]
        return yaml_text, docs, RunResult(stdout="", stderr="", exit_code=0)

    monkeypatch.setattr(cli, "ADAPTERS", {"fake": _FakeAdapter})
    monkeypatch.setattr(cli, "generate_testcase", fake_generate_testcase)

    code = cli.cmd_generate([
        str(tmp_skill),
        "--adapter", "fake",
        "--out", str(out_path),
        "--force",
    ])

    assert code == 0
    assert seen["workdir_base"] == cli.DEFAULT_WORKDIR_BASE


def _write_runnable_testcase(tmp_path, tmp_skill):
    testcase = tmp_path / "case.yaml"
    testcase.write_text(
        f"name: run-case\nskill: {tmp_skill.as_posix()}\ninput: hi\nruns: 1\n"
        "expect:\n  - exit_code: 0\n",
        encoding="utf-8",
    )
    return testcase


def test_cmd_run_prints_success_next_steps(tmp_skill, tmp_path, monkeypatch, capsys):
    testcase = _write_runnable_testcase(tmp_path, tmp_skill)

    monkeypatch.setattr(cli, "ADAPTERS", {"fake": _FakeAdapter})
    monkeypatch.setattr(cli, "run_testcase", lambda tc, adapter, **kwargs: _passing_report(tc))

    code = cli.cmd_run([str(testcase), "--adapter", "fake", "--runs", "1"])

    err = capsys.readouterr().err
    assert code == 0
    assert ">>> next steps:" in err
    assert "skill-test validate" in err
    assert "--trace trace.json" in err


def test_cmd_run_prints_failure_next_steps_without_trace(tmp_skill, tmp_path, monkeypatch, capsys):
    testcase = _write_runnable_testcase(tmp_path, tmp_skill)

    monkeypatch.setattr(cli, "ADAPTERS", {"fake": _FakeAdapter})
    monkeypatch.setattr(cli, "run_testcase", lambda tc, adapter, **kwargs: _failing_report(tc))

    code = cli.cmd_run([str(testcase), "--adapter", "fake", "--runs", "3"])

    err = capsys.readouterr().err
    assert code == 1
    assert "--trace trace.json" in err
    assert "--keep-failed" in err
    assert "skill-test fix-skill" not in err


def test_cmd_run_prints_failure_next_steps_with_trace(tmp_skill, tmp_path, monkeypatch, capsys):
    testcase = _write_runnable_testcase(tmp_path, tmp_skill)
    trace = tmp_path / "trace.json"

    monkeypatch.setattr(cli, "ADAPTERS", {"fake": _FakeAdapter})
    monkeypatch.setattr(cli, "run_testcase", lambda tc, adapter, **kwargs: _failing_report(tc))

    code = cli.cmd_run([
        str(testcase), "--adapter", "fake", "--runs", "1", "--trace", str(trace)
    ])

    err = capsys.readouterr().err
    assert code == 1
    assert "skill-test fix-skill" in err
    assert "skill-test fix-testcase" in err
    assert str(trace) in err
    assert trace.is_file()


def test_iterate_rejects_testcase_skill_mismatch(tmp_skill, tmp_path, monkeypatch, capsys):
    other_skill = tmp_path / "other-skill"
    other_skill.mkdir()
    (other_skill / "SKILL.md").write_text("ok", encoding="utf-8")
    testcase = tmp_path / "case.yaml"
    testcase.write_text(
        f"name: bad\nskill: {tmp_skill.as_posix()}\ninput: hi\nexpect:\n  - exit_code: 0\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(cli, "ADAPTERS", {"fake": _FakeAdapter})
    code = cli.cmd_iterate([
        str(testcase),
        "--skill", str(other_skill),
        "--architect-skill", str(tmp_skill),
        "--adapter", "fake",
        "--gen-adapter", "fake",
    ])

    assert code == 2
    assert "skill mismatch" in capsys.readouterr().err


def test_iterate_validates_testcase_before_running(tmp_skill, tmp_path, monkeypatch, capsys):
    testcase = tmp_path / "bad.yaml"
    testcase.write_text(
        f"name: bad\nskill: {tmp_skill.as_posix()}\ninput: hi\nexpect: not-a-list\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(cli, "ADAPTERS", {"fake": _FakeAdapter})
    code = cli.cmd_iterate([
        str(testcase),
        "--skill", str(tmp_skill),
        "--architect-skill", str(tmp_skill),
        "--adapter", "fake",
        "--gen-adapter", "fake",
    ])

    assert code == 2
    assert "expect must be a list" in capsys.readouterr().err


def test_iterate_rejects_non_positive_numeric_options(tmp_skill, tmp_path):
    testcase = tmp_path / "case.yaml"
    testcase.write_text(
        f"name: bad\nskill: {tmp_skill.as_posix()}\ninput: hi\nexpect:\n  - exit_code: 0\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        cli.cmd_iterate([
            str(testcase),
            "--skill", str(tmp_skill),
            "--architect-skill", str(tmp_skill),
            "--runs-per-round", "0",
        ])




def test_iterate_returns_failure_when_flow_unstable_at_stop(tmp_skill, tmp_path, monkeypatch):
    testcase = tmp_path / "case.yaml"
    testcase.write_text(
        f"name: iter\nskill: {tmp_skill.as_posix()}\ninput: hi\nexpect:\n  - exit_code: 0\n",
        encoding="utf-8",
    )

    def fake_iterate(*args, **kwargs):
        return [RoundOutcome(
            1, 1.0, 2, 2, None, False,
            unstable_cases=["iter"], stop_reason="max_rounds",
        )]

    monkeypatch.setattr(cli, "ADAPTERS", {"fake": _FakeAdapter})
    monkeypatch.setattr(cli, "iterate", fake_iterate)

    code = cli.cmd_iterate([
        str(testcase),
        "--skill", str(tmp_skill),
        "--architect-skill", str(tmp_skill),
        "--adapter", "fake",
        "--gen-adapter", "fake",
        "--require-stable-flow",
        "--runs-per-round", "2",
    ])

    assert code == 1



def test_new_skill_rejects_path_like_name(capsys):
    code = cli.cmd_new_skill([
        "--name", "../bad",
        "--description", "bad",
    ])

    assert code == 2
    assert "folder name" in capsys.readouterr().err



def test_cmd_run_rejects_non_positive_runs(tmp_skill, tmp_path):
    testcase = _write_runnable_testcase(tmp_path, tmp_skill)

    with pytest.raises(SystemExit):
        cli.cmd_run([str(testcase), "--runs", "0"])

def test_cmd_run_help_exposes_require_stable_flow(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.cmd_run(["--help"])

    assert exc.value.code == 0
    assert "--require-stable-flow" in capsys.readouterr().out


def test_cmd_run_rejects_stable_flow_with_single_run(tmp_skill, tmp_path, monkeypatch, capsys):
    testcase = _write_runnable_testcase(tmp_path, tmp_skill)

    monkeypatch.setattr(cli, "ADAPTERS", {"fake": _FakeAdapter})

    code = cli.cmd_run([
        str(testcase),
        "--adapter", "fake",
        "--require-stable-flow",
    ])

    assert code == 2
    err = capsys.readouterr().err
    assert "--require-stable-flow needs at least 2 runs" in err
    assert "--runs 2" in err


def test_cmd_run_require_stable_flow_fails_on_flow_variance(
        tmp_skill, tmp_path, monkeypatch, capsys):
    testcase = _write_runnable_testcase(tmp_path, tmp_skill)

    def unstable_report(tc, adapter_name="fake"):
        records = [
            RunRecord(index=0, result=RunResult("", "", 0, tool_calls=[{"name": "Read"}]), checks=[]),
            RunRecord(index=1, result=RunResult("", "", 0, tool_calls=[{"name": "Grep"}]), checks=[]),
        ]
        return CaseReport(tc, records, adapter_name=adapter_name)

    monkeypatch.setattr(cli, "ADAPTERS", {"fake": _FakeAdapter})
    monkeypatch.setattr(cli, "run_testcase", lambda tc, adapter, **kwargs: unstable_report(tc))

    code = cli.cmd_run([
        str(testcase),
        "--adapter", "fake",
        "--runs", "2",
        "--require-stable-flow",
    ])

    assert code == 1
    err = capsys.readouterr().err
    assert "flow instability" in err
    assert "2 distinct flows" in err


def test_bootstrap_rejects_stable_flow_with_single_smoke_run(tmp_skill, capsys):
    code = cli.cmd_bootstrap([str(tmp_skill), "--require-stable-flow"])

    assert code == 2
    assert "--runs >= 2" in capsys.readouterr().err


def test_bootstrap_require_stable_flow_fails_on_flow_variance(
        tmp_skill, tmp_path, monkeypatch, capsys):
    fixture_out = tmp_path / "fixture"
    testcase_out = tmp_path / "case.yaml"

    def fake_generate_fixture(skill_dir, adapter, out_dir, **kwargs):
        return [Path("fixture.txt")], RunResult("", "", 0)

    def fake_generate_testcase(skill_dir, adapter, **kwargs):
        yaml_text = f"name: boot\nskill: {skill_dir.as_posix()}\ninput: hi\n"
        docs = [{"name": "boot", "skill": str(skill_dir), "input": "hi"}]
        return yaml_text, docs, RunResult("", "", 0)

    def fake_run_testcase(tc, adapter, **kwargs):
        records = [
            RunRecord(index=0, result=RunResult("", "", 0, tool_calls=[{"name": "Read"}]), checks=[]),
            RunRecord(index=1, result=RunResult("", "", 0, tool_calls=[{"name": "Grep"}]), checks=[]),
        ]
        return CaseReport(tc, records, adapter_name=adapter.name)

    monkeypatch.setattr(cli, "ADAPTERS", {"fake": _FakeAdapter})
    monkeypatch.setattr(cli, "generate_fixture", fake_generate_fixture)
    monkeypatch.setattr(cli, "generate_testcase", fake_generate_testcase)
    monkeypatch.setattr(cli, "run_testcase", fake_run_testcase)

    code = cli.cmd_bootstrap([
        str(tmp_skill),
        "--gen-adapter", "fake",
        "--adapter", "fake",
        "--runs", "2",
        "--require-stable-flow",
        "--fixture-out", str(fixture_out),
        "--testcase-out", str(testcase_out),
        "--force",
    ])

    assert code == 1
    assert "flow instability" in capsys.readouterr().err

def test_doctor_suggests_existing_local_testcase(tmp_path, monkeypatch, capsys):
    testcases = tmp_path / "testcases"
    testcases.mkdir()
    (testcases / "cub-code-review.yaml").write_text(
        "name: demo\nskill: skills/demo\ninput: hi\n",
        encoding="utf-8",
    )

    class Row:
        name = "Python 3.10+"
        passed = True
        detail = "3.13.2"
        required = True

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_doctor", lambda: ([Row()], True))

    code = cli.cmd_doctor([])

    out = capsys.readouterr().out
    assert code == 0
    assert "skill-test testcases/cub-code-review.yaml --runs 1" in out
    assert "claude-example.yaml" not in out

def test_iterate_uses_auto_architect_when_flag_omitted(tmp_skill, tmp_path, monkeypatch):
    testcase = tmp_path / "case.yaml"
    testcase.write_text(
        f"name: iter\nskill: {tmp_skill.as_posix()}\ninput: hi\nexpect:\n  - exit_code: 0\n",
        encoding="utf-8",
    )
    seen = {}

    def fake_iterate(cases, test_adapter, target_dir, architect_dir, gen_adapter,
                     **kwargs):
        seen["architect_dir"] = architect_dir
        return [RoundOutcome(1, 1.0, 1, 1, None, False, stop_reason="converged")]

    monkeypatch.setattr(cli, "ADAPTERS", {"fake": _FakeAdapter})
    monkeypatch.setattr(cli, "iterate", fake_iterate)

    code = cli.cmd_iterate([
        str(testcase),
        "--skill", str(tmp_skill),
        "--adapter", "fake",
        "--gen-adapter", "fake",
    ])

    assert code == 0
    assert seen["architect_dir"].name == "interactive-skill-architect"
    assert (seen["architect_dir"] / "SKILL.md").is_file()


