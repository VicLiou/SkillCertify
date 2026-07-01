"""Tests for codex-tui adapter deterministic helpers."""
from __future__ import annotations

import json

from runner.adapters.codex.tui import CodexTuiAdapter


def test_codex_tui_launch_args_include_workspace_and_read_config_without_add_dir(tmp_path):
    args = CodexTuiAdapter._build_launch_args(["codex"], tmp_path, "skilltest_tui")

    assert args[:3] == ["codex", "-p", "skilltest_tui"]
    assert args[args.index("-C") + 1] == str(tmp_path)
    assert "--add-dir" not in args
    assert args[args.index("-c") + 1] == 'sandbox_permissions=["disk-full-read-access"]'


def test_codex_tui_rollout_metadata_reads_session_meta(tmp_path):
    rollout = tmp_path / "rollout-demo.jsonl"
    rollout.write_text(
        json.dumps({
            "type": "session_meta",
            "payload": {"id": "sess-1", "cwd": str(tmp_path)},
        }) + "\n",
        encoding="utf-8",
    )

    metadata = CodexTuiAdapter._rollout_metadata(rollout)

    assert metadata["rollout_path"] == str(rollout)
    assert metadata["session_id"] == "sess-1"
    assert metadata["rollout_cwd"] == str(tmp_path)