"""Run testcases, auto-fix the skill on failure, re-run -- repeat until
skill behavior converges or safety limits stop the loop.

Convergence model:
  Round N:
    1. Run all testcases (--runs-per-round configurable).
    2. Save the round trace.
    3. Stop successfully when all runs pass, and optionally when flows are stable.
    4. Stop when max rounds or no-improve budget is reached.
    5. Stop when failures are configuration-only/skipped-only or the architect
       makes no changes.
    6. Otherwise feed the round trace to fix-skill --apply and continue.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..adapters.base import CliAdapter
from .fix_skill import fix_skill
from .judge import LlmJudge
from .report import summarize, write_trace
from .runner import TestCase, run_testcase


@dataclass
class RoundOutcome:
    round_idx: int
    pass_rate: float                  # 0.0 ~ 1.0 over all runs this round
    cases_passed: int
    cases_total: int
    trace_path: Path | None           # where this round's trace was saved
    fix_applied: bool                 # did fix-skill actually modify files?
    fixed_files: list[str] = field(default_factory=list)
    max_distinct_flows: int = 0
    unstable_cases: list[str] = field(default_factory=list)
    stop_reason: str | None = None


def _aggregate_pass_rate(reports) -> tuple[float, int, int]:
    """Aggregate total individual runs passed / total runs scheduled."""
    passed = total = 0
    for rep in reports:
        for rec in rep.records:
            total += 1
            if rec.passed:
                passed += 1
    return (passed / total if total else 0.0), passed, total


def _flow_instability(reports) -> tuple[int, list[str]]:
    max_distinct = 0
    unstable: list[str] = []
    for rep in reports:
        s = summarize(rep)
        distinct = int(s["distinct_flows"])
        max_distinct = max(max_distinct, distinct)
        if distinct > 1:
            unstable.append(rep.case.name)
    return max_distinct, unstable


def _has_actionable_failure_evidence(reports, *, require_stable_flow: bool) -> bool:
    """Return whether fix-skill can receive useful evidence.

    Skipped-only failures are usually runner configuration problems, such as
    using `command:` without --allow-exec or `judge:` without --judge. Sending
    those to the architect makes it edit the skill for a harness setup issue.
    """
    if require_stable_flow:
        _, unstable_cases = _flow_instability(reports)
        if unstable_cases:
            return True

    for rep in reports:
        for rec in rep.records:
            if rec.passed:
                continue
            if rec.result.crashed or rec.result.error:
                return True
            if any((not chk.passed) and (not chk.skipped) for chk in rec.checks):
                return True
    return False


def _progress_score(rate: float, max_distinct_flows: int,
                    unstable_cases: list[str], *,
                    require_stable_flow: bool) -> tuple:
    if not require_stable_flow:
        return (rate,)
    # Higher is better. Once pass rate is tied, fewer unstable cases and fewer
    # distinct flows count as progress.
    return (rate, -len(unstable_cases), -max_distinct_flows)


def iterate(cases: list[TestCase], adapter: CliAdapter,
            target_skill_dir: Path, architect_skill_dir: Path,
            architect_adapter: CliAdapter, *,
            max_rounds: int = 3,
            no_improve_budget: int = 1,
            runs_per_round: int = 1,
            allow_exec: bool = False,
            judge: LlmJudge | None = None,
            workdir_base: str | None = None,
            trace_dir: Path | None = None,
            fix_scope: str = "focused",
            architect_model: str | None = None,
            architect_timeout_s: int = 600,
            require_stable_flow: bool = False,
            log_fn=None,
            ) -> list[RoundOutcome]:
    """Loop until pass rate hits 100%, max_rounds is reached, or progress stalls."""
    if not cases:
        raise ValueError("iterate() needs at least one testcase")
    if max_rounds < 1:
        raise ValueError("max_rounds must be >= 1")
    if no_improve_budget < 1:
        raise ValueError("no_improve_budget must be >= 1")
    if runs_per_round < 1:
        raise ValueError("runs_per_round must be >= 1")
    if require_stable_flow and runs_per_round < 2:
        raise ValueError("require_stable_flow needs runs_per_round >= 2")
    if trace_dir is not None:
        trace_dir.mkdir(parents=True, exist_ok=True)

    def _log(msg: str) -> None:
        if log_fn is not None:
            log_fn(msg)

    outcomes: list[RoundOutcome] = []
    consecutive_flat = 0
    previous_score: tuple | None = None

    for r in range(1, max_rounds + 1):
        _log(f"\n========== iterate round {r}/{max_rounds} ==========")

        reports = []
        for tc in cases:
            tc.runs = runs_per_round
            rep = run_testcase(tc, adapter, judge=judge,
                               allow_exec=allow_exec,
                               workdir_base=workdir_base, verbose=True)
            reports.append(rep)
            summary = summarize(rep)
            _log(f"  [{tc.name}] {summary['passed']}/{summary['runs']} "
                 f"({summary['pass_rate'] * 100:.0f}%)")

        rate, passed_n, total_n = _aggregate_pass_rate(reports)
        max_flows, unstable_cases = _flow_instability(reports)
        flow_ok = not unstable_cases

        round_trace = None
        if trace_dir is not None:
            round_trace = trace_dir / f"round-{r}.json"
            write_trace(reports, round_trace)
            _log(f"  trace saved -> {round_trace}")

        outcome = RoundOutcome(
            round_idx=r,
            pass_rate=rate,
            cases_passed=passed_n,
            cases_total=total_n,
            trace_path=round_trace,
            fix_applied=False,
            max_distinct_flows=max_flows,
            unstable_cases=unstable_cases,
        )

        if rate >= 1.0 and (flow_ok or not require_stable_flow):
            outcome.stop_reason = "converged"
            if require_stable_flow:
                _log(f"  converged: {passed_n}/{total_n} runs passed; flows stable")
            else:
                _log(f"  converged: {passed_n}/{total_n} runs passed")
            outcomes.append(outcome)
            break

        if rate >= 1.0 and require_stable_flow and not flow_ok:
            _log("  pass rate is 100%, but flow stability is required and "
                 f"{len(unstable_cases)} case(s) used multiple flows: "
                 + ", ".join(unstable_cases))

        if r == max_rounds:
            outcome.stop_reason = "max_rounds"
            _log(f"  max_rounds={max_rounds} reached; final pass rate "
                 f"{rate * 100:.0f}% ({passed_n}/{total_n})")
            outcomes.append(outcome)
            break

        score = _progress_score(rate, max_flows, unstable_cases,
                                require_stable_flow=require_stable_flow)
        if previous_score is not None:
            if score <= previous_score:
                consecutive_flat += 1
                _log("  no progress from previous round; "
                     f"flat rounds: {consecutive_flat}/{no_improve_budget}")
                if consecutive_flat >= no_improve_budget:
                    outcome.stop_reason = "no_improvement"
                    _log("  stopping: auto-fix is not making progress")
                    outcomes.append(outcome)
                    break
            else:
                consecutive_flat = 0
        previous_score = score

        if round_trace is None:
            raise RuntimeError(
                "iterate() requires trace_dir so each round's failures can be "
                "fed to fix-skill")

        if not _has_actionable_failure_evidence(
                reports, require_stable_flow=require_stable_flow):
            outcome.stop_reason = "no_actionable_failure_evidence"
            _log("  stopping: failures are skipped-only or configuration-only; "
                 "rerun with --allow-exec / --judge as needed before auto-fixing")
            outcomes.append(outcome)
            break

        _log(f"  invoking fix-skill on {target_skill_dir} ...")
        try:
            changes, _, backup_dir = fix_skill(
                target_skill_dir, architect_skill_dir, architect_adapter,
                round_trace, scope=fix_scope, model=architect_model,
                timeout_s=architect_timeout_s, workdir_base=workdir_base,
                apply=True, include_flow_instability=require_stable_flow,
            )
        except Exception as e:  # noqa: BLE001 - surface as a round outcome
            outcome.stop_reason = "fix_failed"
            _log(f"  fix-skill failed: {e}")
            outcomes.append(outcome)
            break

        if not changes:
            outcome.stop_reason = "no_changes"
            _log("  stopping: fix-skill completed but made no file changes")
            outcomes.append(outcome)
            break

        outcome.fix_applied = True
        outcome.fixed_files = [c.relpath.as_posix() for c in changes]
        outcomes.append(outcome)
        _log(f"  fix-skill modified {len(changes)} file(s); "
             f"backup -> {backup_dir.name if backup_dir else '(no backup)'}")

    return outcomes


def render_summary(outcomes: list[RoundOutcome]) -> str:
    """One-paragraph summary of the iterate run for human review."""
    if not outcomes:
        return "(no rounds ran)"
    last = outcomes[-1]
    lines = [f"Ran {len(outcomes)} round(s):"]
    for o in outcomes:
        marker = "PASS" if o.pass_rate >= 1.0 and not o.unstable_cases else "FAIL"
        fix_note = (f"  (fix-skill changed: {', '.join(o.fixed_files)})"
                    if o.fix_applied and o.fixed_files else "")
        flow_note = (f", flow unstable: {len(o.unstable_cases)} case(s)"
                     if o.unstable_cases else "")
        reason_note = f", stop={o.stop_reason}" if o.stop_reason else ""
        lines.append(f"  {marker} round {o.round_idx}: "
                     f"{o.cases_passed}/{o.cases_total} runs passed "
                     f"({o.pass_rate * 100:.0f}%{flow_note}{reason_note})"
                     f"{fix_note}")
    if last.pass_rate >= 1.0 and not last.unstable_cases:
        lines.append("\nFinal: ALL GREEN. skill is stable.")
    else:
        lines.append(f"\nFinal: {last.cases_passed}/{last.cases_total} runs "
                     f"passed ({last.pass_rate * 100:.0f}%). human review needed.")
    return "\n".join(lines)
