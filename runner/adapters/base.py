"""CLI adapter interface + normalized result types.

Every AI CLI (codex, claude, gemini, ...) differs in four things:
  1. how to launch headless / non-interactive mode
  2. how the prompt is fed (argv / stdin / file)
  3. how output is parsed (plain text vs JSON, tool-call trace)
  4. exit / error semantics

An adapter hides all four behind `run()` and returns a `RunResult`. The runner,
assertions, and report code never touch CLI-specific details -- they only know
`RunResult`. Adding or swapping a CLI = adding one adapter file.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class RunOptions:
    model: str | None = None
    sandbox: str = "workspace-write"  # read-only | workspace-write | danger-full-access
    timeout_s: int = 300
    extra_args: list[str] = field(default_factory=list)
    verbose: bool = True            # adapters may stream step-by-step progress
    debug: bool = False             # extra-verbose (raw commands, agent chatter)
    # set by the runner: adapters call this to emit one progress line (no prefix);
    # the runner adds the unified "[run i/N]" prefix and tracks idle time.
    on_event: Callable[[str], None] | None = None
    # Adapter-populated, runner-read. The adapter pushes live counters here as
    # it observes them (currently `tokens_total`, `reasoning_tokens`); the
    # runner's heartbeat uses them to tell whether a silent CLI is actually
    # still working (token count climbing) or wedged (count flat). Optional --
    # adapters that don't push it just leave the dict empty.
    stats: dict = field(default_factory=dict)


@dataclass
class RunResult:
    """Normalized result of a single CLI run, identical shape across all CLIs."""

    stdout: str
    stderr: str
    exit_code: int
    final_message: str = ""              # the assistant's last message
    artifacts: list[Path] = field(default_factory=list)  # files produced in workdir
    tool_calls: list[dict] = field(default_factory=list)  # normalized trace
    events: list[dict] = field(default_factory=list)      # raw JSONL events (debug)
    latency_ms: int = 0
    tokens: dict | None = None
    metadata: dict = field(default_factory=dict)    # adapter-specific diagnostics
    error: str | None = None             # harness-level failure (timeout, crash, missing bin)

    @property
    def crashed(self) -> bool:
        return self.error is not None


class CliAdapter(Protocol):
    name: str

    def run(self, prompt: str, workdir: Path, opts: RunOptions) -> RunResult:
        ...
