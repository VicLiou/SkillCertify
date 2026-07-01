"""Shared fixtures for the framework's own tests.

These tests deliberately do NOT call any LLM/CLI -- we mock the adapter
boundary so tests run in <1s and don't cost tokens. The point is to catch
regressions in the deterministic Python layer (assertions / report / trace
parsing / failure summarization / etc), not to test claude/codex output.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_skill(tmp_path: Path) -> Path:
    """A minimal skill folder usable by anything that needs `skill_dir`."""
    skill = tmp_path / "tiny-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: tiny-skill\ndescription: a tiny test skill\n---\n"
        "# Tiny\nWrite hello to out.txt.\n",
        encoding="utf-8",
    )
    return skill


@pytest.fixture
def tmp_workdir(tmp_path: Path) -> Path:
    """An empty workdir into which a fake RunResult's `produced_text` files
    could be placed."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    return workdir


def make_run_result(*, stdout: str = "", final_message: str = "",
                    tool_calls: list[dict] | None = None,
                    exit_code: int = 0, events: list[dict] | None = None,
                    latency_ms: int = 100, error: str | None = None,
                    tokens: dict | None = None):
    """Helper to assemble a fake RunResult without importing yet (avoids
    circular concerns in test discovery)."""
    from runner.adapters.base import RunResult
    return RunResult(
        stdout=stdout, stderr="", exit_code=exit_code,
        final_message=final_message, tool_calls=tool_calls or [],
        events=events or [], latency_ms=latency_ms,
        tokens=tokens, error=error,
    )
