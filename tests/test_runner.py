"""Tests for runner orchestration details."""
from __future__ import annotations

from runner.adapters.base import RunResult
from runner.core import runner


class _FakeAdapter:
    name = "fake"

    def run(self, prompt, workdir, opts):
        return RunResult(stdout="", stderr="", exit_code=0, final_message="hello")


def test_keep_failed_repairs_workdir_before_leaving_it(tmp_skill, tmp_path, monkeypatch):
    called = []

    def fake_ensure(path, *, log_fn=None):
        called.append(path)
        return True

    monkeypatch.setattr(runner, "ensure_tree_accessible", fake_ensure)
    tc = runner.TestCase(
        name="fails",
        skill=str(tmp_skill),
        input="say hello",
        runs=1,
        expect=[{"final_contains": ["NOPE"]}],
    )

    report = runner.run_testcase(
        tc,
        _FakeAdapter(),
        keep_failed_workdirs=True,
        workdir_base=tmp_path / "work",
        verbose=False,
    )

    assert report.pass_rate == 0
    assert len(called) == 1
    assert called[0].exists()