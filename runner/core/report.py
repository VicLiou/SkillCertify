"""Turn CaseReports into a human-readable stability summary + machine-readable JSON.

Stability = pass rate over N runs. The report also surfaces *where* runs diverge:
which checks fail how often, latency distribution, and crash count.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

from .runner import CaseReport


def _ensure_output_parent(path: Path) -> None:
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)


def _latency_stats(report: CaseReport) -> dict:
    lat = [r.result.latency_ms for r in report.records if not r.result.crashed]
    if not lat:
        return {}
    return {
        "min_ms": min(lat),
        "avg_ms": int(statistics.mean(lat)),
        "max_ms": max(lat),
        "p50_ms": int(statistics.median(lat)),
    }


def _check_failures(report: CaseReport) -> Counter:
    c: Counter = Counter()
    for rec in report.records:
        for chk in rec.checks:
            if chk.skipped or not chk.passed:
                c[chk.name] += 1
    return c


def _flow_distribution(report: CaseReport) -> Counter:
    """Group runs by the tool sequence they actually took. More than one distinct
    flow == the skill is taking different paths run-to-run (flow instability),
    even if every run still passes its checks."""
    c: Counter = Counter()
    for rec in report.records:
        if rec.result.crashed:
            continue
        seq = " -> ".join(tc.get("name") or "?" for tc in rec.result.tool_calls)
        c[seq or "(no tools)"] += 1
    return c


def summarize(report: CaseReport) -> dict:
    crashes = sum(1 for r in report.records if r.result.crashed)
    return {
        "name": report.case.name,
        "skill": report.case.skill,
        "strategy": report.case.load_strategy,
        "runs": len(report.records),
        "passed": report.pass_count,
        "pass_rate": round(report.pass_rate, 3),
        "crashes": crashes,
        "check_failures": dict(_check_failures(report)),
        "distinct_flows": len(_flow_distribution(report)),
        "flows": dict(_flow_distribution(report)),
        "latency": _latency_stats(report),
    }


def _clip(text: str, limit: int) -> str:
    """Collapse noisy assertion labels to one readable terminal line."""
    clean = " ".join(str(text).split())
    if len(clean) <= limit:
        return clean
    if limit <= 3:
        return clean[:limit]
    return clean[:limit - 3].rstrip() + "..."


def _result_label(summary: dict) -> str:
    if summary["pass_rate"] >= 1.0:
        return "PASS"
    if summary["passed"] == 0:
        return "FAIL"
    return "PARTIAL"


def _flow_label(summary: dict) -> str:
    n = summary["distinct_flows"]
    return "stable" if n <= 1 else f"{n} paths"


def _issue_label(summary: dict) -> str:
    issues = []
    if summary["crashes"]:
        n = summary["crashes"]
        issues.append(f"{n} crash" + ("es" if n != 1 else ""))
    if summary["check_failures"]:
        n = len(summary["check_failures"])
        issues.append(f"{n} check" + ("s" if n != 1 else ""))
    if summary["pass_rate"] >= 1.0 and summary["distinct_flows"] > 1:
        issues.append("flow variance")
    return ", ".join(issues) if issues else "ok"


def print_final_report(reports: list[CaseReport]) -> None:
    """Render a scannable end-of-run summary.

    The old format mixed the table row and long failure labels together. That
    made semantic `judge:` assertions nearly unreadable. Keep the table compact,
    then expand only failing cases in a separate section.
    """
    if not reports:
        return

    summaries = [summarize(rep) for rep in reports]
    case_width = min(max(24, *(len(s["name"]) for s in summaries)), 40)
    overall_pass = sum(s["passed"] for s in summaries)
    overall_total = sum(s["runs"] for s in summaries)
    overall_rate = (overall_pass / overall_total) if overall_total else 0.0
    overall_result = "PASS" if overall_rate >= 1.0 else "FAIL"

    print(f"\n===== FINAL REPORT =====")
    print(f"Cases: {len(reports)}")
    print()
    print("Summary")
    header = (f"  {'#':>2}  {'Result':<7}  {'Case':<{case_width}}  "
              f"{'Pass':>7}  {'Rate':>5}  {'Flows':>8}  Issues")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for idx, s in enumerate(summaries, start=1):
        name = _clip(s["name"], case_width)
        print(f"  {idx:>2}  {_result_label(s):<7}  {name:<{case_width}}  "
              f"{s['passed']:>3}/{s['runs']:<3}  "
              f"{s['pass_rate'] * 100:>4.0f}%  "
              f"{_flow_label(s):>8}  {_issue_label(s)}")

    print()
    print("Totals")
    print(f"  Runs passed : {overall_pass}/{overall_total} ({overall_rate * 100:.0f}%)")
    print(f"  Result      : {overall_result}")

    failing = [(idx, rep, summaries[idx - 1])
               for idx, rep in enumerate(reports, start=1)
               if summaries[idx - 1]["crashes"] or summaries[idx - 1]["check_failures"]]
    if not failing:
        if any(s["distinct_flows"] > 1 for s in summaries):
            print("  Note        : some passing cases used multiple flows; use --trace if flow stability matters.")
        return

    print()
    print("Failures")
    for idx, rep, s in failing:
        print(f"  {idx}. {rep.case.name}  {s['passed']}/{s['runs']} ({s['pass_rate'] * 100:.0f}%)")
        if s["crashes"]:
            print(f"     - crashes: {s['crashes']}/{s['runs']}")
        for name, n in sorted(s["check_failures"].items(), key=lambda x: -x[1]):
            print(f"     - {_clip(name, 110)}")
            print(f"       failed {n}/{s['runs']}")
    print("  Tip: rerun with --trace trace.json for full per-run details and untruncated assertion text.")


def print_report(report: CaseReport) -> None:
    s = summarize(report)
    bar_pass = s["passed"]
    total = s["runs"]
    print(f"\n=== {s['name']}  ({s['skill']}, {s['strategy']}) ===")
    print(f"  pass rate : {bar_pass}/{total}  ({s['pass_rate'] * 100:.0f}%)")
    if s["crashes"]:
        print(f"  crashes   : {s['crashes']}")
    if s["latency"]:
        l = s["latency"]
        print(f"  latency   : min {l['min_ms']}  p50 {l['p50_ms']}  max {l['max_ms']} ms")
    if s["check_failures"]:
        print("  flaky/failed checks:")
        for name, n in sorted(s["check_failures"].items(), key=lambda x: -x[1]):
            print(f"    - {name}: failed {n}/{total}")
    else:
        print("  all checks stable")

    flows = s["flows"]
    if s["distinct_flows"] <= 1:
        print(f"  flow      : stable (1 path)")
    else:
        print(f"  flow      : {s['distinct_flows']} DISTINCT paths (flow instability):")
        for seq, n in sorted(flows.items(), key=lambda x: -x[1]):
            print(f"    - {n:>3}x  {seq}")


def write_json(reports: list[CaseReport], path: Path) -> None:
    payload = [summarize(r) for r in reports]
    _ensure_output_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _decode_input(tc: dict) -> dict:
    """Tool-call input is a JSON string for codex shell calls, a dict for claude.
    Return it as a dict (best-effort); {} if it isn't structured args."""
    raw = tc.get("input")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _patch_files(patch) -> list[str]:
    files = []
    for line in str(patch).splitlines():
        for m in ("*** Add File: ", "*** Update File: ", "*** Delete File: "):
            if line.startswith(m):
                files.append(line[len(m):].strip())
    return files


def _parse_ts(ts) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_escalated(tc: dict) -> bool:
    """Whether this tool call required sandbox escalation. codex-tui precomputes
    this directly on the call (covering apply_patch too, whose `input` is raw
    patch text rather than JSON args); other adapters fall back to decoding the
    legacy shell-call-only shape."""
    if "escalated" in tc:
        return bool(tc["escalated"])
    return _decode_input(tc).get("sandbox_permissions") == "require_escalated"


def _flow(tool_calls: list[dict]) -> list[dict]:
    """Derived, decoded per-step view: tool, command, escalated, files, timing.
    Sits between `tool_sequence` (names only) and `tool_calls` (raw)."""
    base = None
    for tc in tool_calls:
        base = _parse_ts(tc.get("ts"))
        if base:
            break
    steps = []
    for i, tc in enumerate(tool_calls):
        name = tc.get("name")
        step = {"step": i + 1, "tool": name}
        if name == "apply_patch":
            step["files"] = _patch_files(tc.get("input", ""))
        else:
            args = _decode_input(tc)
            if "command" in args:
                step["command"] = args.get("command")
        step["escalated"] = _is_escalated(tc)
        ts = _parse_ts(tc.get("ts"))
        if ts and base:
            step["at_ms"] = int((ts - base).total_seconds() * 1000)
        steps.append(step)
    return steps


def _escalations(tool_calls: list[dict]) -> int:
    return sum(1 for tc in tool_calls if _is_escalated(tc))


def _token_timeline(events: list[dict]) -> list[dict]:
    """Snapshots of cumulative token counters across the run, in order. Codex
    emits a `token_count` event_msg after each model turn; the timeline lets
    you see whether the model was actually working (counts climbing) or stuck
    (counts flat) at any point -- especially during long stretches of silence
    in the per-step log. Returns [] for adapters that don't emit these."""
    out: list[dict] = []
    base = None
    last_total = None
    for e in events:
        if not isinstance(e, dict) or e.get("type") != "event_msg":
            continue
        p = e.get("payload", {})
        if not isinstance(p, dict) or p.get("type") != "token_count":
            continue
        ts = _parse_ts(e.get("timestamp"))
        if ts is None:
            continue
        info = p.get("info")
        if not isinstance(info, dict):
            continue
        ttu = info.get("total_token_usage")
        if not isinstance(ttu, dict):
            continue
        total = ttu.get("total_tokens")
        reasoning = ttu.get("reasoning_output_tokens")
        if not isinstance(total, int):
            continue
        # Skip identical-total samples after the first -- collapses long flat
        # stretches that would otherwise bloat the trace.
        if last_total is not None and total == last_total:
            continue
        last_total = total
        if base is None:
            base = ts
        out.append({
            "at_ms": int((ts - base).total_seconds() * 1000),
            "total_tokens": total,
            "reasoning_tokens": reasoning if isinstance(reasoning, int) else None,
        })
    return out


def _tokens_total(tokens) -> int | None:
    """Single comparable token count across adapters (claude vs codex shapes)."""
    if not isinstance(tokens, dict):
        return None
    info = tokens.get("info")
    if isinstance(info, dict):
        ttu = info.get("total_token_usage")
        if isinstance(ttu, dict) and isinstance(ttu.get("total_tokens"), int):
            return ttu["total_tokens"]
    if isinstance(tokens.get("total_tokens"), int):
        return tokens["total_tokens"]
    i, o = tokens.get("input_tokens"), tokens.get("output_tokens")
    if isinstance(i, int) or isinstance(o, int):
        return (i or 0) + (o or 0)
    return None


def write_trace(reports: list[CaseReport], path: Path) -> None:
    """Per-run detail: the actual flow each run took. Three levels of tool detail:
    `tool_sequence` (names) -> `flow` (decoded steps) -> `tool_calls` (raw)."""
    out = []
    for rep in reports:
        total = len(rep.records)
        for rec in rep.records:
            res = rec.result
            started = (datetime.fromtimestamp(rec.started_at).isoformat(timespec="seconds")
                       if rec.started_at else None)
            out.append({
                "case": rep.case.name,
                "run": rec.index + 1,
                "run_label": f"{rec.index + 1}/{total}",
                "adapter": rep.adapter_name,
                "model": rep.case.options.model,
                "load_strategy": rep.case.load_strategy,
                "started_at": started,
                "passed": rec.passed,
                "exit_code": res.exit_code,
                "error": res.error,
                "latency_ms": res.latency_ms,
                "workdir": rec.workdir,
                "escalations": _escalations(res.tool_calls),
                "tokens_total": _tokens_total(res.tokens),
                "tokens": res.tokens,
                "metadata": res.metadata,
                "tool_sequence": [tc.get("name") for tc in res.tool_calls],
                "flow": _flow(res.tool_calls),
                "token_timeline": _token_timeline(res.events),
                "final_message": res.final_message,
                "tool_calls": res.tool_calls,
                "checks": [
                    {"name": c.name, "passed": c.passed,
                     "skipped": c.skipped, "detail": c.detail}
                    for c in rec.checks
                ],
            })
    _ensure_output_parent(path)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
