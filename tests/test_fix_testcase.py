"""Tests for fix-testcase's testcase-regeneration helpers."""
from __future__ import annotations

import json
from pathlib import Path

from runner.adapters.base import RunResult
from runner.core.fix_testcase import fix_testcase


class _YamlAdapter:
    name = "yaml-adapter"

    def __init__(self, final_message: str):
        self.final_message = final_message
        self.prompts = []
        self.workdirs = []

    def run(self, prompt, workdir, opts):
        self.prompts.append(prompt)
        self.workdirs.append(workdir)
        return RunResult(
            stdout="",
            stderr="",
            exit_code=0,
            final_message=self.final_message,
        )


def _write_trace(path: Path) -> None:
    path.write_text(
        json.dumps([
            {
                "case": "happy",
                "passed": False,
                "final_message": "The report names the stable output file.",
                "tool_sequence": [],
                "checks": [
                    {
                        "name": "output_contains=['old brittle sentence']",
                        "passed": False,
                        "skipped": False,
                        "detail": "missing: ['old brittle sentence']",
                    }
                ],
            }
        ]),
        encoding="utf-8",
    )


def test_fix_testcase_restores_existing_judge_when_llm_drops_it(tmp_skill, tmp_path):
    testcase = tmp_path / "case.yaml"
    testcase.write_text(
        f"name: happy\nskill: {tmp_skill.as_posix()}\ninput: hi\n"
        "expect:\n"
        "  - exit_code: 0\n"
        "  - output_contains: ['old brittle sentence']\n"
        "  - judge: report should describe the generated artifact correctly\n",
        encoding="utf-8",
    )
    trace = tmp_path / "trace.json"
    _write_trace(trace)
    adapter = _YamlAdapter(
        f"name: happy\nskill: {tmp_skill.as_posix()}\ninput: hi\n"
        "expect:\n"
        "  - exit_code: 0\n"
        "  - output_contains: ['artifact']\n"
    )

    yaml_text, docs, _, backup = fix_testcase(
        testcase,
        trace,
        adapter,
        workdir_base=tmp_path / "work",
    )

    assert backup is None
    assert docs[0]["expect"][-1] == {
        "judge": "report should describe the generated artifact correctly",
    }
    assert "judge: report should describe the generated artifact correctly" in yaml_text
    assert "semantic `judge:` assertions" in adapter.prompts[0]


def test_fix_testcase_restores_existing_command_when_llm_drops_it(tmp_skill, tmp_path):
    testcase = tmp_path / "case.yaml"
    testcase.write_text(
        f"name: happy\nskill: {tmp_skill.as_posix()}\ninput: hi\n"
        "expect:\n"
        "  - exit_code: 0\n"
        "  - output_contains: ['old brittle sentence']\n"
        "  - command:\n"
        "      run: python -m pytest\n"
        "      stdout_contains: ['passed']\n",
        encoding="utf-8",
    )
    trace = tmp_path / "trace.json"
    _write_trace(trace)
    adapter = _YamlAdapter(
        f"name: happy\nskill: {tmp_skill.as_posix()}\ninput: hi\n"
        "expect:\n"
        "  - exit_code: 0\n"
        "  - output_contains: ['artifact']\n"
    )

    yaml_text, docs, _, _ = fix_testcase(
        testcase,
        trace,
        adapter,
        workdir_base=tmp_path / "work",
    )

    assert docs[0]["expect"][-1] == {
        "command": {"run": "python -m pytest", "stdout_contains": ["passed"]},
    }
    assert "command:" in yaml_text
    assert "dynamic `command:` assertions" in adapter.prompts[0]


def test_fix_testcase_does_not_restore_command_that_failed(tmp_skill, tmp_path):
    testcase = tmp_path / "case.yaml"
    testcase.write_text(
        f"name: happy\nskill: {tmp_skill.as_posix()}\ninput: hi\n"
        "expect:\n"
        "  - exit_code: 0\n"
        "  - command:\n"
        "      run: python -m pytest\n"
        "      stdout_contains: ['passed']\n",
        encoding="utf-8",
    )
    trace = tmp_path / "trace.json"
    trace.write_text(
        json.dumps([
            {
                "case": "happy",
                "passed": False,
                "final_message": "Generated tests exist.",
                "tool_sequence": [],
                "checks": [
                    {
                        "name": "command:python -m pytest",
                        "passed": False,
                        "skipped": False,
                        "detail": "exit=1",
                    }
                ],
            }
        ]),
        encoding="utf-8",
    )
    adapter = _YamlAdapter(
        f"name: happy\nskill: {tmp_skill.as_posix()}\ninput: hi\n"
        "expect:\n"
        "  - exit_code: 0\n"
        "  - output_contains: ['artifact']\n"
    )

    _, docs, _, _ = fix_testcase(
        testcase,
        trace,
        adapter,
        workdir_base=tmp_path / "work",
    )

    assert not any("command" in item for item in docs[0]["expect"])
    assert "dynamic `command:` assertions that did not appear" not in adapter.prompts[0]
