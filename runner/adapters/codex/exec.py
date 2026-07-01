"""OpenAI Codex CLI adapter.

Uses `codex exec` (non-interactive). Reference:
https://developers.openai.com/codex/cli/reference

Key flags used:
  codex exec -                        prompt read from stdin
  --cd <dir>                          workspace root (our isolated per-run workdir)
  --json                              newline-delimited JSON events -> tool_calls trace
  --output-last-message <file>        final assistant message -> clean assertion target
  --sandbox workspace-write           scripts may write artifacts, stay contained
  --model <m>                         pin model for reproducibility
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from ..base import RunOptions, RunResult
from .common import kill_process_tree, resolve_launcher


class CodexAdapter:
    name = "codex"

    def __init__(self, binary: str = "codex"):
        self.binary = binary

    def _launcher(self) -> list[str] | None:
        return resolve_launcher(self.binary)

    def build_command(self, launcher: list[str], workdir: Path, opts: RunOptions,
                      last_msg_file: Path) -> list[str]:
        cmd = launcher + [
            "exec",
            "--json",
            "--skip-git-repo-check",  # staged workdir is a temp dir, not a git repo
            "--cd", str(workdir),
            "--output-last-message", str(last_msg_file),
        ]
        # sandbox: "bypass" -> codex's --dangerously-bypass-approvals-and-sandbox,
        # which its docs bless for "isolated runners" (we always run in a throwaway
        # temp workdir). Otherwise codex's shell can't access the temp workdir and
        # spends the whole turn fighting "access denied". Any other value is passed
        # through as a normal --sandbox policy.
        if opts.sandbox == "bypass":
            cmd += ["--dangerously-bypass-approvals-and-sandbox"]
        else:
            cmd += ["--sandbox", opts.sandbox]
        if opts.model:
            cmd += ["--model", opts.model]
        cmd += opts.extra_args
        cmd += ["-"]  # prompt from stdin
        return cmd

    def run(self, prompt: str, workdir: Path, opts: RunOptions) -> RunResult:
        launcher = self._launcher()
        if launcher is None:
            return RunResult("", "", -1, error=(f"codex binary not found on PATH: {self.binary!r}. "
                                          f"tip: install codex CLI (https://developers.openai.com/codex/cli/), "
                                          f"or pass --binary <path-to-codex>. "
                                          f"Run `skill-test doctor` to verify."))
        last_msg = workdir / ".codex_last_message.txt"
        cmd = self.build_command(launcher, workdir, opts, last_msg)
        start = time.monotonic()
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(workdir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            return RunResult("", "", -1, error=f"codex binary not found: {self.binary!r}")

        try:
            stdout, stderr = proc.communicate(input=prompt, timeout=opts.timeout_s)
            code, err = proc.returncode, None
        except subprocess.TimeoutExpired:
            # `cmd /c codex.CMD` spawns child processes that survive killing just
            # the cmd parent and keep the pipes open (so a plain proc.kill() +
            # communicate() would hang again). Kill the whole tree.
            kill_process_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            code, err = -1, f"timeout after {opts.timeout_s}s (killed process tree)"
        except KeyboardInterrupt:
            # Ctrl+C on Windows doesn't propagate to the cmd/codex children;
            # tear the whole tree down explicitly before re-raising.
            kill_process_tree(proc)
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            raise

        latency_ms = int((time.monotonic() - start) * 1000)
        return self._parse(stdout, stderr, code, last_msg, latency_ms, err)

    def _parse(self, stdout, stderr, code, last_msg: Path, latency_ms, err) -> RunResult:
        events: list[dict] = []
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # non-JSON noise on stdout; ignore

        tool_calls: list[dict] = []
        tokens = None
        final_from_events = ""

        for e in events:
            etype = e.get("type")
            if etype == "turn.completed":
                if isinstance(e.get("usage"), dict):
                    tokens = e["usage"]
            elif etype == "item.completed":
                item = e.get("item", {})
                itype = item.get("type")
                if itype == "agent_message":
                    final_from_events = item.get("text", "") or final_from_events
                elif itype and itype not in self._NON_ACTION_ITEMS:
                    tool_calls.append(self._normalize_item(item))

        # final message: prefer --output-last-message file, fall back to events
        final = ""
        if last_msg.exists():
            final = last_msg.read_text(encoding="utf-8", errors="replace").strip()
            try:
                last_msg.unlink()
            except OSError:
                pass
        if not final:
            final = final_from_events

        return RunResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=code,
            final_message=final,
            tool_calls=tool_calls,
            events=events,
            latency_ms=latency_ms,
            tokens=tokens,
            error=err,
        )

    # item.type values that are NOT tool/actions (everything else counts as a
    # tool call for flow analysis: file_change, command_execution, mcp_tool_call,
    # web_search, patch_apply, ...).
    _NON_ACTION_ITEMS = {"agent_message", "reasoning", "error", "todo_list"}

    @staticmethod
    def _normalize_item(item: dict) -> dict:
        detail = {k: v for k, v in item.items() if k not in ("id", "status", "type")}
        return {"name": item.get("type"), "input": detail}
