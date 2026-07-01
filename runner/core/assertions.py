"""Expectation checks.

Two layers, by design:
  - hard checks  : deterministic, free, fast (files/exit/text/trace). Catch most.
  - judge check  : optional LLM-as-judge for genuinely semantic expectations only.
                   Off by default (cost + its own variance). Enable via runner.

Each `expect` item in a testcase is a single-key dict, e.g.
    - file_exists: out.pdf
    - exit_code: 0
    - stdout_contains: ["merged"]
    - regex: "\\d+ pages"
    - tool_used: shell
    - max_latency_ms: 60000
    - judge: "does the output correctly state how many pages were merged?"
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..adapters import RunResult


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    skipped: bool = False


def evaluate(expect: list[dict], res: RunResult, workdir: Path,
             judge=None, exclude_dirs: list[Path] | None = None,
             allow_exec: bool = False) -> list[CheckResult]:
    exclude_dirs = exclude_dirs or []
    if not expect:
        return [CheckResult("expect", False, "no assertions defined")]

    out: list[CheckResult] = []
    has_exit_code = False
    for item in expect:
        (key, val), = item.items()
        if key == "exit_code":
            has_exit_code = True
        out.append(_check_one(key, val, res, workdir, judge, exclude_dirs, allow_exec))
    if not has_exit_code and res.exit_code != 0:
        out.insert(0, CheckResult("exit_code=0 (implicit)", False,
                                  f"got {res.exit_code}"))
    return out


def _produced_text(workdir: Path, exclude_dirs: list[Path]) -> str:
    """Concatenate the text of files the model PRODUCED in the workdir.

    Excludes the staged skill folder and any fixture (input material) -- their
    contents are INPUT, not output, and would cause false positives."""
    parts: list[str] = []
    for p in workdir.rglob("*"):
        if not p.is_file():
            continue
        if any(p == d or d in p.parents for d in exclude_dirs):
            continue
        if p.name.startswith(".codex"):  # harness scratch (last-message, tui prompt)
            continue
        try:
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n".join(parts)


def _check_one(key, val, res: RunResult, workdir: Path, judge,
               exclude_dirs: list[Path], allow_exec: bool = False) -> CheckResult:
    label = f"{key}={val!r}"

    if key == "exit_code":
        return CheckResult(label, res.exit_code == val, f"got {res.exit_code}")

    if key == "file_exists":
        p = workdir / val
        return CheckResult(label, p.exists(), str(p))

    if key == "file_absent":
        p = workdir / val
        return CheckResult(label, not p.exists(), str(p))

    if key == "stdout_contains":
        needles = val if isinstance(val, list) else [val]
        hay = res.stdout
        missing = [n for n in needles if n not in hay]
        return CheckResult(label, not missing, f"missing: {missing}" if missing else "ok")

    if key == "final_contains":
        needles = val if isinstance(val, list) else [val]
        missing = [n for n in needles if n not in res.final_message]
        return CheckResult(label, not missing, f"missing: {missing}" if missing else "ok")

    if key == "output_contains":
        # searches everywhere the output could land: final message + stdout +
        # any file the model produced (skill folder excluded). Robust to skills
        # that sometimes write a file and sometimes reply inline.
        needles = val if isinstance(val, list) else [val]
        hay = res.final_message + "\n" + res.stdout + "\n" + _produced_text(workdir, exclude_dirs)
        missing = [n for n in needles if n not in hay]
        return CheckResult(label, not missing, f"missing: {missing}" if missing else "ok")

    if key == "regex":
        m = re.search(val, res.stdout + "\n" + res.final_message)
        return CheckResult(label, m is not None, "matched" if m else "no match")

    if key == "tool_used":
        used = any(str(tc.get("name", "")).lower() == str(val).lower()
                   for tc in res.tool_calls)
        return CheckResult(label, used, f"{len(res.tool_calls)} tool calls seen")

    if key == "reads_file":
        # verify the skill loaded specific files (by path substring), regardless of
        # HOW it loaded them (Claude's Read tool vs codex's shell `Get-Content`/`cat`
        # vs codex exec's command_execution items). Adapters disagree on shape:
        #   claude       -> {"name": "Read", "input": {"file_path": "..."}}
        #   codex-tui    -> {"name": "shell_command", "input": '{"command": "..."}'}
        #   codex exec   -> {"name": "command_execution", "input": {"command": "..."}}
        # so instead of only reading Read's structured file_path, we also scan every
        # other call's raw input (dict repr or JSON string) for the path substring --
        # this widens "read" to "read or referenced", which is the practical signal
        # available from a shell command line anyway.
        needles = val if isinstance(val, list) else [val]
        parts = []
        for tc in res.tool_calls:
            if tc.get("name") == "Read":
                inp = tc.get("input")
                if isinstance(inp, dict):
                    parts.append(str(inp.get("file_path", "")))
                continue
            parts.append(str(tc.get("input", "")))
        blob = "\n".join(parts)
        blob_norm = blob.replace("\\\\", "/").replace("\\", "/")

        def _seen(n: str) -> bool:
            return n in blob or n.replace("\\", "/") in blob_norm

        missing = [n for n in needles if not _seen(n)]
        return CheckResult(label, not missing,
                           f"missing: {missing}" if missing
                           else f"matched in {len(parts)} tool call(s)")

    if key == "flow_contains":
        # required tools must appear IN ORDER (subsequence), extra steps allowed.
        seq = [tc.get("name") for tc in res.tool_calls]
        want = list(val)
        i = 0
        for name in seq:
            if i < len(want) and name == want[i]:
                i += 1
        ok = i == len(want)
        return CheckResult(label, ok, f"actual: {seq}")

    if key == "flow_equals":
        # tool sequence must match exactly (strict; expect false positives if used loosely).
        seq = [tc.get("name") for tc in res.tool_calls]
        return CheckResult(label, seq == list(val), f"actual: {seq}")

    if key == "max_latency_ms":
        return CheckResult(label, res.latency_ms <= val, f"got {res.latency_ms}ms")

    if key == "command":
        # DYNAMIC verification: run a command in the workdir AFTER the skill, and
        # assert on its exit code / output. This is how you verify generated code
        # actually works (compiles, runs, passes its tests) -- not just that files
        # exist. Gated by --allow-exec because it executes model-produced code.
        spec = val if isinstance(val, dict) else {"run": val}
        run = spec["run"]
        name = f"command:{run}"
        if not allow_exec:
            return CheckResult(name, False, "exec disabled (use --allow-exec)",
                               skipped=True)
        want_code = spec.get("exit_code", 0)
        needles = spec.get("stdout_contains", [])
        needles = needles if isinstance(needles, list) else [needles]
        timeout = spec.get("timeout_s", 60)
        try:
            proc = subprocess.run(
                run, cwd=str(workdir), shell=True, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return CheckResult(name, False, f"timeout after {timeout}s")
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        missing = [n for n in needles if n not in out]
        ok = proc.returncode == want_code and not missing
        detail = f"exit={proc.returncode}" + (f", missing {missing}" if missing else "")
        return CheckResult(name, ok, detail)

    if key == "judge":
        if judge is None:
            return CheckResult(label, False, "judge disabled", skipped=True)
        output_text = res.final_message + "\n" + _produced_text(workdir, exclude_dirs)
        ok, why = judge(val, output_text)
        return CheckResult(label, ok, why)

    return CheckResult(label, False, f"unknown check: {key}")
