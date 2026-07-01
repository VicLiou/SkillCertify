"""OpenAI Codex app-server adapter (separate from the `codex exec` adapter).

Why this exists: on org/cloud-managed codex, `approval_policy` is forced to
OnRequest and `codex exec` is non-interactive, so any operation needing approval
is AUTO-REJECTED -> codex can't read+write unattended. The app-server protocol
instead SENDS approval requests to a client; this adapter is that client and
auto-approves them, which satisfies OnRequest while staying unattended.

Protocol: JSON-RPC over stdio, newline-delimited JSON (JSONL).
Reference: https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md

  initialize {clientInfo, capabilities}            -> result
  initialized (notification)
  thread/start {model, cwd, approvalPolicy, sandbox} -> {thread:{id}}
  turn/start {threadId, input:[{type:text,text}], ...}
  <server streams notifications>
  item/commandExecution/requestApproval  (server->client request, has id)  -> {decision:"accept"}
  item/fileChange/requestApproval        (server->client request, has id)  -> {decision:"accept"}
  item/completed {item}                  (notification)
  turn/completed {turn:{status, items}}  (notification) -> done
  thread/tokenUsage/updated {usage}

NOTE: the protocol is EXPERIMENTAL and version-dependent. Older codex builds use
applyPatchApproval/execCommandApproval with decision allow/deny. This adapter
handles both: it treats any server->client request whose method contains
"approval" (case-insensitive) as an approval and replies accept/allow.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

from ..base import RunOptions, RunResult
from .common import kill_process_tree, resolve_launcher

# map our sandbox names to the protocol's camelCase enum
_SANDBOX_MAP = {
    "workspace-write": "workspaceWrite",
    "danger-full-access": "dangerFullAccess",
    "read-only": "readOnly",
    "bypass": "dangerFullAccess",  # app-server has no bypass; full access + auto-approve
}

# item.type values that are NOT tool/actions (everything else = a tool call)
_NON_ACTION_ITEMS = {"agentMessage", "reasoning", "userMessage", "error", "todoList"}


class CodexAppServerAdapter:
    name = "codex-appserver"

    def __init__(self, binary: str = "codex", approval_policy: str = "onRequest"):
        self.binary = binary
        # onRequest => server asks us for approval => we auto-accept (the point).
        self.approval_policy = approval_policy

    def run(self, prompt: str, workdir: Path, opts: RunOptions) -> RunResult:
        launcher = resolve_launcher(self.binary)
        if launcher is None:
            return RunResult("", "", -1, error=(f"codex binary not found on PATH: {self.binary!r}. "
                                          f"tip: install codex CLI (https://developers.openai.com/codex/cli/), "
                                          f"or pass --binary <path-to-codex>. "
                                          f"Run `skill-test doctor` to verify."))

        cmd = launcher + ["app-server"]
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(workdir),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except FileNotFoundError:
            return RunResult("", "", -1, error=f"codex binary not found: {self.binary!r}")

        stderr_lines: list[str] = []
        threading.Thread(target=self._drain, args=(proc.stderr, stderr_lines),
                         daemon=True).start()

        timed_out = {"v": False}

        def on_timeout():
            timed_out["v"] = True
            kill_process_tree(proc)

        watchdog = threading.Timer(opts.timeout_s, on_timeout)
        watchdog.start()
        start = time.monotonic()
        try:
            res = self._drive(proc, prompt, workdir, opts)
        except Exception as e:  # noqa: BLE001 - surface any protocol error as a crash
            res = RunResult("", "", -1, error=f"app-server protocol error: {e}")
        finally:
            watchdog.cancel()
            kill_process_tree(proc)

        res.latency_ms = int((time.monotonic() - start) * 1000)
        res.stderr = "".join(stderr_lines)
        if timed_out["v"]:
            res.error = res.error or f"timeout after {opts.timeout_s}s (killed process tree)"
            res.exit_code = -1
        return res

    @staticmethod
    def _drain(stream, sink: list[str]) -> None:
        try:
            for line in stream:
                sink.append(line)
        except (OSError, ValueError):
            pass

    def _drive(self, proc: subprocess.Popen, prompt: str, workdir: Path,
               opts: RunOptions) -> RunResult:
        w, r = proc.stdin, proc.stdout
        sandbox = _SANDBOX_MAP.get(opts.sandbox, opts.sandbox)

        def send(obj: dict) -> None:
            w.write(json.dumps(obj) + "\n")
            w.flush()

        def thread_params() -> dict:
            p = {"cwd": str(workdir), "approvalPolicy": self.approval_policy,
                 "sandbox": sandbox}
            if opts.model:
                p["model"] = opts.model
            return p

        send({"method": "initialize", "id": 0, "params": {
            "clientInfo": {"name": "skill-auto-test", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True},
        }})

        events: list[dict] = []
        tool_calls: list[dict] = []
        final = ""
        tokens = None
        thread_id = None
        status = None

        for raw in r:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            events.append(msg)
            method = msg.get("method")

            # --- server -> client REQUEST (has both method and id) ---
            if method and msg.get("id") is not None:
                if "approval" in method.lower():
                    # auto-approve (accept for new protocol, allow for old)
                    send({"id": msg["id"], "result": {"decision": "accept"}})
                continue

            # --- notifications ---
            if method:
                if method == "item/completed":
                    item = msg.get("params", {}).get("item", {})
                    itype = item.get("type")
                    if itype == "agentMessage":
                        final = item.get("text", "") or final
                    elif itype and itype not in _NON_ACTION_ITEMS:
                        tool_calls.append(self._normalize_item(item))
                elif method in ("turn/completed", "turn/failed"):
                    turn = msg.get("params", {}).get("turn", {})
                    status = turn.get("status", "failed" if method.endswith("failed") else None)
                    for it in turn.get("items", []):
                        if it.get("type") == "agentMessage":
                            final = it.get("text", "") or final
                    break
                elif method == "thread/tokenUsage/updated":
                    u = msg.get("params", {}).get("usage")
                    if isinstance(u, dict):
                        tokens = u
                continue

            # --- response to one of OUR requests (has id, no method) ---
            rid = msg.get("id")
            if rid == 0:                       # initialize result
                send({"method": "initialized", "params": {}})
                send({"method": "thread/start", "id": 1, "params": thread_params()})
            elif rid == 1:                     # thread/start result
                thread_id = (msg.get("result", {}).get("thread", {}).get("id"))
                if not thread_id:
                    raise RuntimeError(f"no thread id in {msg!r}")
                send({"method": "turn/start", "id": 2, "params": {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    **thread_params(),
                }})
            # rid == 2 (turn/start ack): nothing to do, wait for turn/completed

        exit_code = 0 if status == "completed" else -1
        err = None if status == "completed" else f"turn status: {status!r}"
        return RunResult(
            stdout="", stderr="", exit_code=exit_code, final_message=final,
            tool_calls=tool_calls, events=events, tokens=tokens, error=err,
        )

    @staticmethod
    def _normalize_item(item: dict) -> dict:
        detail = {k: v for k, v in item.items() if k not in ("id", "status", "type")}
        return {"name": item.get("type"), "input": detail}
