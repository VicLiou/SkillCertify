"""Claude Code CLI adapter.

Uses `claude -p` (headless / print mode).

Key flags used:
  claude -p                          print (non-interactive) mode; prompt via stdin
  --output-format stream-json        newline-delimited JSON events -> tool_calls trace
  --verbose                          required for stream-json under -p
  --allowedTools <list>              pre-approve tools so headless runs don't block
                                     (preferred over --dangerously-skip-permissions)
  --model <m>                        pin model for reproducibility (e.g. opus / sonnet)
  --add-dir <dir>                    grant access to the staged workdir

NOTE: default uses an allowedTools whitelist, NOT --dangerously-skip-permissions.
The latter creates an unrestricted nested agent and is gated by Claude Code's
auto-mode classifier when spawned from another agent. Only enable skip_permissions
if you run this harness yourself in a trusted, isolated environment.

Working directory: Claude runs in `cwd`, so we launch the process with
cwd=workdir (the staged skill folder lives directly under it).
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from ..base import RunOptions, RunResult
from ..codex.common import kill_process_tree


class ClaudeAdapter:
    name = "claude"

    DEFAULT_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]

    def __init__(self, binary: str = "claude", allowed_tools: list[str] | None = None,
                 skip_permissions: bool = False, permission_mode: str | None = None):
        self.binary = binary
        self.allowed_tools = self.DEFAULT_TOOLS if allowed_tools is None else allowed_tools
        self.skip_permissions = skip_permissions  # opt-in; see module docstring
        self.permission_mode = permission_mode    # e.g. "acceptEdits"; None = default

    def build_command(self, workdir: Path, opts: RunOptions) -> list[str]:
        cmd = [
            self.binary, "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--add-dir", str(workdir),
        ]
        if self.skip_permissions:
            cmd += ["--dangerously-skip-permissions"]
        else:
            if self.allowed_tools:
                cmd += ["--allowedTools", " ".join(self.allowed_tools)]
            if self.permission_mode:
                cmd += ["--permission-mode", self.permission_mode]
        if opts.model:
            cmd += ["--model", opts.model]
        cmd += opts.extra_args
        return cmd

    def run(self, prompt: str, workdir: Path, opts: RunOptions) -> RunResult:
        cmd = self.build_command(workdir, opts)
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
            return RunResult("", "", -1, error=(
                f"claude binary not found: {self.binary!r}. "
                f"tip: install Claude Code (https://docs.claude.com/en/docs/claude-code/quickstart), "
                f"or pass --binary <path-to-claude>. "
                f"Run `skill-test doctor` to verify."))

        try:
            stdout, stderr = proc.communicate(input=prompt, timeout=opts.timeout_s)
            code, err = proc.returncode, None
        except subprocess.TimeoutExpired:
            # claude may spawn child processes (Bash-tool commands, MCP servers);
            # killing just this process leaves those orphaned, so kill the tree.
            kill_process_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            code, err = -1, f"timeout after {opts.timeout_s}s (killed process tree)"
        except KeyboardInterrupt:
            # On Windows, Ctrl+C goes only to the Python process -- the child
            # claude (and its grandchildren: Bash tool commands, MCP servers)
            # keep running unless we explicitly tear them down here.
            kill_process_tree(proc)
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            raise  # let main() catch and exit cleanly
        latency_ms = int((time.monotonic() - start) * 1000)
        return self._parse(stdout, stderr, code, latency_ms, err)

    def _parse(self, stdout, stderr, code, latency_ms, err) -> RunResult:
        events: list[dict] = []
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass

        tool_calls: list[dict] = []
        final = ""
        tokens = None

        for e in events:
            etype = e.get("type")
            if etype == "assistant":
                for block in e.get("message", {}).get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_calls.append({
                            "name": block.get("name"),
                            "input": block.get("input"),
                        })
            elif etype == "result":
                # final result event carries the assistant's last text + usage
                final = e.get("result", "") or final
                if isinstance(e.get("usage"), dict):
                    tokens = e["usage"]

        # fallback: last assistant text block if no result event
        if not final:
            for e in reversed(events):
                if e.get("type") == "assistant":
                    texts = [b.get("text", "") for b in e.get("message", {}).get("content", [])
                             if isinstance(b, dict) and b.get("type") == "text"]
                    if texts:
                        final = "\n".join(texts).strip()
                        break

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
