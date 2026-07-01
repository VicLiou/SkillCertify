"""Have the `interactive-skill-architect` skill propose fixes to a
target skill based on test-failure evidence from a trace.json.

Dev-time loop: take a failing test report -> stage the architect skill +
the target skill into an isolated workdir -> let the architect run its
Phase O1-O3 (scan -> diagnose -> patch the target) -> diff the result
against the original. Dry-run prints the diff; --apply backs up & copies
the patched files into the target.

The constraint "don't change Hard Gates / Step order / 放行/停止 conditions"
is enforced two ways:
  1. Carried verbatim in the task prompt (so the architect's Phase O2
     diagnosis honors it explicitly).
  2. Sandbox: the architect can only write inside the staged workdir, so
     dry-run mode is safe no matter what the architect does.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..adapters.base import CliAdapter, RunResult
from .architect import (
    DEFAULT_ARCHITECT_SKILL, FileChange,
    backup_then_copy, diff_skill, invoke_architect,
)

# Phase O1 Step 3 routes inside interactive-skill-architect: which range of
# its 13-item quality checklist to run.
_SCOPE_TO_ARCHITECT_OPTION = {
    "full": "A",       # 全面健檢 (all 13 items)
    "focused": "B",    # 聚焦優化 (just the specific issue we describe)
    "style": "C",      # 風格對齊 (just item 5)
}


@dataclass
class FailureGroup:
    """Failures grouped by which assertion failed -- e.g. all 3 runs that
    failed `regex='(SUCCESS|FAIL)'` go together. Makes the prompt readable
    and helps the architect see a pattern rather than 3 isolated cases."""
    assertion: str
    cases: list[str]
    sample_detail: str
    sample_final_message: str
    sample_tool_calls: list[str]


def _load_trace(trace_path: Path) -> list[dict]:
    try:
        return json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"failed to load trace from {trace_path}: {e}")


def _add_failure_group(by_assertion: dict[str, FailureGroup], *,
                       assertion: str, case: str, detail: str,
                       final: str, tool_seq: list[str]) -> None:
    group = by_assertion.get(assertion)
    if group is None:
        by_assertion[assertion] = FailureGroup(
            assertion=assertion,
            cases=[case],
            sample_detail=detail,
            sample_final_message=final,
            sample_tool_calls=tool_seq[:20],
        )
    elif case not in group.cases:
        group.cases.append(case)


def _summarize_failures(trace: list[dict], target_case_substr: str | None = None,
                        *, include_flow_instability: bool = False
                        ) -> list[FailureGroup]:
    """Walk trace data and extract actionable evidence for the architect.

    Skipped checks are deliberately ignored: they usually mean the runner was
    invoked without --allow-exec or --judge, not that the skill should change.
    Crash/error-only runs are still actionable, so they become a run-level
    failure group even when no assertion result exists.
    """
    by_assertion: dict[str, FailureGroup] = {}
    flows_by_case: dict[str, Counter] = {}

    for run in trace:
        case = run.get("case", "?")
        if target_case_substr and target_case_substr not in case:
            continue

        tool_seq = run.get("tool_sequence", []) or []
        if include_flow_instability:
            key = tuple(str(item) for item in tool_seq)
            flows_by_case.setdefault(case, Counter())[key] += 1

        if run.get("passed"):
            continue

        final = (run.get("final_message") or "")[:300]
        failed_checks = [
            check for check in (run.get("checks") or [])
            if not check.get("passed") and not check.get("skipped")
        ]
        for check in failed_checks:
            _add_failure_group(
                by_assertion,
                assertion=check.get("name", "?"),
                case=case,
                detail=check.get("detail", ""),
                final=final,
                tool_seq=tool_seq,
            )

        if not failed_checks:
            error = run.get("error")
            exit_code = run.get("exit_code")
            if error or (exit_code not in (None, 0)):
                detail = str(error or f"exit_code={exit_code}")
                _add_failure_group(
                    by_assertion,
                    assertion="run failed before actionable assertions",
                    case=case,
                    detail=detail,
                    final=final,
                    tool_seq=tool_seq,
                )

    if include_flow_instability:
        for case, counts in flows_by_case.items():
            if len(counts) <= 1:
                continue
            total = sum(counts.values())
            top = []
            for seq, n in counts.most_common(3):
                label = " -> ".join(seq) if seq else "(no tools)"
                top.append(f"{n}x {label}")
            detail = (f"{total} run(s) used {len(counts)} distinct tool flows; "
                      f"most common: {'; '.join(top)}")
            sample_seq = list(counts.most_common(1)[0][0])
            _add_failure_group(
                by_assertion,
                assertion=f"flow instability: {case}",
                case=case,
                detail=detail,
                final="",
                tool_seq=sample_seq,
            )

    return list(by_assertion.values())


def _failures_to_markdown(groups: list[FailureGroup]) -> str:
    """Render failure groups into a human-readable markdown chunk that the
    architect skill can ingest as 'evidence of what's wrong'."""
    if not groups:
        return "(沒有失敗證據 — trace 裡所有 run 都通過了。)"
    parts = ["# 測試失敗證據", ""]
    for i, g in enumerate(groups, 1):
        parts.append(f"## 失敗模式 {i}:{g.assertion}")
        parts.append("")
        parts.append(f"- **受影響的 case** ({len(g.cases)} 個):"
                     + ", ".join(f"`{c}`" for c in g.cases))
        parts.append(f"- **斷言失敗詳情**:`{g.sample_detail}`")
        if g.sample_tool_calls:
            tools = " -> ".join(g.sample_tool_calls)
            parts.append(f"- **該次跑的工具序列**(節錄):`{tools}`")
        if g.sample_final_message:
            parts.append(f"- **該次的 final_message**(節錄):")
            parts.append("  > " + g.sample_final_message.replace("\n", "\n  > "))
        parts.append("")
    return "\n".join(parts)


def _build_task_prompt(target_skill_name: str, scope: str,
                       failures_md: str, extra_constraint: str | None) -> str:
    """The prompt that drives interactive-skill-architect into optimize mode
    with the right scope, target, and constraints."""
    arch_option = _SCOPE_TO_ARCHITECT_OPTION[scope]
    scope_desc = {
        "full": "A. 全面健檢(13 項全做)",
        "focused": "B. 聚焦優化(只修跟下方失敗證據相關的部分)",
        "style": "C. 風格對齊(只做第 5 項)",
    }[scope]

    constraint_block = (
        "\n## 絕對不能動的部分(硬性限制,優先於 architect 預設規則)\n\n"
        "本次優化的範圍**只限**「規格表述、警告/Gotchas、輸出格式提醒、"
        "工具禁令、釐清補充說明」這類**追加性**修改。\n\n"
        "**禁止**:\n"
        "- 修改既有 Hard Gates 的條件 / 放行條件 / 停止條件\n"
        "- 改變 Step 的執行順序(Step 1 → N 的順序固定)\n"
        "- 刪除任何既有規則(包含模糊或不夠好的規則,也只能加註,不能刪)\n"
        "- 改變既有 disposition 選項清單\n"
        "- 改變 input contract 的「必須」要求\n"
        "\n**允許**:\n"
        "- 在 Gotchas/踩過的坑 段落加新條目\n"
        "- 在 Hard Gates 段落加全新的 Gate(放在現有 Gate 後面,不改既有)\n"
        "- 在 Template Compliance Gate 或類似約束區加新禁令(例如禁用某工具)\n"
        "- 在任何段落加釐清說明(用「加註」方式,不改原句)\n"
    )
    if extra_constraint:
        constraint_block += f"\n## 使用者額外指示\n\n{extra_constraint}\n"

    return (
        "\n\n=== 任務:基於測試失敗證據優化 target skill ===\n\n"
        f"請進入**優化模式**(Phase 0 路由直接選 B),對位於目前工作目錄下的 "
        f"`./{target_skill_name}/` 這個 skill 進行健檢與優化。\n\n"
        f"Phase O1 Step 3 的範圍選擇:**{scope_desc}** "
        f"(對應 architect 的選項 {arch_option})\n\n"
        "完成 Phase O3 後即可結束本回合(請直接在 workdir 內修改檔案,"
        "不需要詢問使用者確認,因為這個 workdir 是隔離環境)。\n\n"
        + constraint_block
        + "\n## 測試失敗證據(用於 Phase O2 診斷的輸入)\n\n"
        + failures_md
    )


def fix_skill(target_skill_dir: Path, architect_skill_dir: Path,
              adapter: CliAdapter, trace_path: Path, *,
              scope: str = "focused",
              case_filter: str | None = None,
              extra_constraint: str | None = None,
              model: str | None = None,
              timeout_s: int = 600,
              workdir_base: str | None = None,
              apply: bool = False,
              include_flow_instability: bool = False,
              ) -> tuple[list[FileChange], RunResult, Path | None]:
    """Run the architect against the target skill using failure evidence
    from trace_path.

    Returns (changes, result, backup_dir):
      - changes: files the architect modified (relative to target_skill_dir)
      - result: raw RunResult from the architect's adapter run
      - backup_dir: backup folder when apply=True and there were changes;
        None otherwise.

    apply=False (default) leaves target_skill_dir untouched. apply=True
    backs up the touched files into a sibling `<skill>.bak.<timestamp>/`
    folder and copies the architect's modified files into target_skill_dir."""
    if scope not in _SCOPE_TO_ARCHITECT_OPTION:
        raise ValueError(f"scope must be one of {list(_SCOPE_TO_ARCHITECT_OPTION)}, "
                         f"got {scope!r}")
    if not (target_skill_dir / "SKILL.md").exists():
        raise FileNotFoundError(f"target skill (with SKILL.md) not found: "
                                f"{target_skill_dir}")

    trace = _load_trace(trace_path)
    groups = _summarize_failures(
        trace, target_case_substr=case_filter,
        include_flow_instability=include_flow_instability,
    )
    if not groups:
        raise RuntimeError("no failed runs found in trace -- nothing to fix")

    failures_md = _failures_to_markdown(groups)
    task = _build_task_prompt(target_skill_dir.name, scope, failures_md,
                              extra_constraint)

    run, staged = invoke_architect(
        architect_skill_dir, task, adapter,
        target_skill_dir=target_skill_dir,
        model=model, timeout_s=timeout_s, workdir_base=workdir_base,
    )
    try:
        changes = diff_skill(target_skill_dir, run.target_in_workdir)
        backup_dir: Path | None = None
        if apply and changes:
            backup_dir = backup_then_copy(run.target_in_workdir,
                                          target_skill_dir, changes)
        return changes, run.result, backup_dir
    finally:
        staged.cleanup()
