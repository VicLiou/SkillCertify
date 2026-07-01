"""Tests for LLM judge workdir handling."""
from __future__ import annotations

from runner.adapters.base import RunResult
from runner.core.judge import LlmJudge


class _RecordingAdapter:
    name = "judge-adapter"

    def __init__(self):
        self.workdirs = []

    def run(self, prompt, workdir, opts):
        self.workdirs.append(workdir)
        return RunResult(
            stdout="",
            stderr="",
            exit_code=0,
            final_message='{"verdict": "PASS", "reason": "ok"}',
        )


def test_llm_judge_uses_workdir_base(tmp_path):
    adapter = _RecordingAdapter()
    workdir_base = tmp_path / "judge-work"
    judge = LlmJudge(adapter, workdir_base=workdir_base)

    passed, detail = judge("criterion", "output")

    assert passed
    assert "PASS" in detail
    assert adapter.workdirs
    assert adapter.workdirs[0].parent == workdir_base
