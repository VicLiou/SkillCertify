"""Deterministic test runner: run a testcase N times in isolated workdirs,
evaluate expectations each time, and aggregate into a stability report.

The runner itself has no randomness -- any variation in results comes from the
skill-under-test, which is exactly what we want to measure.
"""
from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters import CliAdapter, RunOptions, RunResult


def _log(msg: str) -> None:
    try:
        print(msg, file=sys.stderr, flush=True)
    except UnicodeEncodeError:
        enc = sys.stderr.encoding or "ascii"
        print(msg.encode(enc, "replace").decode(enc), file=sys.stderr, flush=True)
from .assertions import CheckResult, evaluate
from .cleanup import ensure_tree_accessible
from .skill_loader import stage_skill


@dataclass
class TestCase:
    name: str
    skill: str
    input: str
    runs: int = 10
    load_strategy: str = "progressive"
    fixture: str | None = None          # input material copied into the workdir
    expect: list[dict] = field(default_factory=list)
    options: RunOptions = field(default_factory=RunOptions)

    @classmethod
    def from_dict(cls, d: dict) -> "TestCase":
        opts = RunOptions(
            model=d.get("model"),
            sandbox=d.get("sandbox", "workspace-write"),
            timeout_s=d.get("timeout_s", 300),
            extra_args=d.get("extra_args", []),
        )
        return cls(
            name=d["name"],
            skill=d["skill"],
            input=d["input"],
            runs=d.get("runs", 10),
            load_strategy=d.get("load_strategy", "progressive"),
            fixture=d.get("fixture"),
            expect=d.get("expect", []),
            options=opts,
        )


@dataclass
class RunRecord:
    index: int
    result: RunResult
    checks: list[CheckResult]
    started_at: float = 0.0  # wall-clock epoch when this run started
    workdir: str | None = None

    @property
    def passed(self) -> bool:
        if self.result.crashed:
            return False
        has_exit_code_check = any(c.name.startswith("exit_code=") for c in self.checks)
        if self.result.exit_code != 0 and not has_exit_code_check:
            return False
        return all(c.passed and not c.skipped for c in self.checks)


@dataclass
class CaseReport:
    case: TestCase
    records: list[RunRecord]
    adapter_name: str = ""

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.records if r.passed)

    @property
    def pass_rate(self) -> float:
        return self.pass_count / len(self.records) if self.records else 0.0


def run_testcase(tc: TestCase, adapter: CliAdapter, judge=None,
                 keep_failed_workdirs: bool = False,
                 allow_exec: bool = False,
                 workdir_base: str | None = None,
                 verbose: bool = True) -> CaseReport:
    """Run a testcase tc.runs times. Multi-case context (which case is which)
    is shown by the caller via a banner printed before calling run_testcase;
    per-line logs here are just `[run i/N]` to keep lines short."""
    records: list[RunRecord] = []
    for i in range(tc.runs):
        staged = stage_skill(tc.skill, tc.load_strategy, fixture=tc.fixture,
                             workdir_base=workdir_base)
        prompt = f"{staged.prompt_prefix}\n\n=== Task ===\n{tc.input}\n"

        prefix = f"[run {i + 1}/{tc.runs}]"
        t0 = time.monotonic()
        started_at = time.time()
        last_event = [t0]

        # Fresh stats dict per run so deltas don't leak across runs.
        tc.options.stats = {}
        last_tokens = [0]

        # Unified per-run logging: the adapter emits prefix-less progress lines via
        # opts.on_event; we add the "[run i/N]" prefix and reset the idle timer.
        def _emit(msg: str) -> None:
            last_event[0] = time.monotonic()
            _log(f"{prefix}   {msg}")
        tc.options.on_event = _emit if verbose else None

        if verbose:
            _log(f"{prefix} start  (adapter={adapter.name})")
        # Smart heartbeat: only speak up when nothing has happened for a while
        # (a genuine stall), so it never interleaves an active event stream.
        # Include token delta when available -- a flat token count during the
        # idle window is strong evidence that the CLI is wedged (e.g. codex
        # hanging on an API response), not deep-reasoning.
        stop = threading.Event()
        if verbose:
            def _beat():
                while not stop.wait(10):
                    idle = time.monotonic() - last_event[0]
                    if idle >= 25:
                        cur = tc.options.stats.get("tokens_total")
                        delta_note = ""
                        if isinstance(cur, int):
                            delta = cur - last_tokens[0]
                            last_tokens[0] = cur
                            delta_note = f", +{delta} tokens since last"
                        _log(f"{prefix}   ...still running "
                             f"({int(time.monotonic() - t0)}s, "
                             f"no new events for {int(idle)}s{delta_note})")
                        last_event[0] = time.monotonic()  # throttle repeats
            threading.Thread(target=_beat, daemon=True).start()

        result = adapter.run(prompt, staged.workdir, tc.options)
        stop.set()
        tc.options.on_event = None

        exclude = [staged.skill_dir] + ([staged.fixture_dir] if staged.fixture_dir else [])
        checks = evaluate(tc.expect, result, staged.workdir, judge=judge,
                          exclude_dirs=exclude, allow_exec=allow_exec)
        rec = RunRecord(index=i, result=result, checks=checks,
                        started_at=started_at, workdir=str(staged.workdir))
        records.append(rec)

        # per-run result prints even under --quiet (only step events/heartbeat are
        # suppressed there); START/END come from the cli.
        status = "PASS" if rec.passed else ("CRASH" if result.crashed else "FAIL")
        _log(f"{prefix} {status} in {int(time.monotonic() - t0)}s")

        if keep_failed_workdirs and not rec.passed:
            ensure_tree_accessible(
                staged.workdir,
                log_fn=lambda m: _log(f"{prefix}   {m}"),
            )
            _log(f"{prefix} kept failed workdir: {staged.workdir}")
        else:
            cleaned = staged.cleanup(log_fn=lambda m: _log(f"{prefix}   {m}"))
            if not cleaned:
                _log(f"{prefix} kept workdir after cleanup failure: {staged.workdir}")

    return CaseReport(case=tc, records=records, adapter_name=adapter.name)
