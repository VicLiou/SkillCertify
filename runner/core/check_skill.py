"""Invoke `interactive-skill-architect` in full-health-check mode (the
13-item quality checklist from its references/quality-checklist.md) to
produce a quality report on a skill, without needing testcases or trace.

Unlike fix-skill, this is purely a *report*: the architect runs its
Phase O1 -> O2 diagnosis and stops; we surface its diagnosis report to
the user. There is no --apply path -- if the user wants to act on the
diagnosis, they can run fix-skill with a synthetic trace or just edit
manually based on the report.

(Why not auto-apply: the 13-item check covers things like 'description
clarity', 'reference depth', 'gotcha freshness' -- many of these are
judgement calls the user should review themselves, not blindly accept.)
"""
from __future__ import annotations

from pathlib import Path

from ..adapters.base import CliAdapter, RunResult
from .architect import invoke_architect


def _build_task_prompt(target_skill_name: str,
                       extra_constraint: str | None) -> str:
    constraint_block = ""
    if extra_constraint:
        constraint_block = (f"\n## 使用者額外指示\n\n{extra_constraint}\n")

    return (
        "\n\n=== 任務:對 target skill 進行 13 項品質健檢 ===\n\n"
        f"請進入**優化模式**(Phase 0 路由直接選 B),對位於目前工作目錄下的 "
        f"`./{target_skill_name}/` 這個 skill 進行健檢。\n\n"
        "Phase O1 Step 3 的範圍選擇:**A. 全面健檢**"
        "(對應 architect 的選項 A,跑完整 13 項品質檢查)\n\n"
        "**本次只做診斷,不做修改**:完成 Phase O2 的診斷報告後即可結束本回合,"
        "不需要進入 Phase O3 改檔(也就是不要動 workdir 內任何檔案)。"
        "完整 13 項診斷結果請直接放在你的 final response 裡,"
        "依 `assets/optimization-report-template.md` 的格式輸出。\n"
        + constraint_block
    )


def check_skill(target_skill_dir: Path, architect_skill_dir: Path,
                adapter: CliAdapter, *,
                extra_constraint: str | None = None,
                model: str | None = None,
                timeout_s: int = 600,
                workdir_base: str | None = None,
                ) -> RunResult:
    """Run the architect's 13-item health check against target_skill_dir.

    Returns the raw RunResult. The diagnosis report lives in
    result.final_message (the architect outputs the
    optimization-report-template-formatted 13-item table there)."""
    if not (target_skill_dir / "SKILL.md").exists():
        raise FileNotFoundError(f"target skill (with SKILL.md) not found: "
                                f"{target_skill_dir}")

    task = _build_task_prompt(target_skill_dir.name, extra_constraint)
    run, staged = invoke_architect(
        architect_skill_dir, task, adapter,
        target_skill_dir=target_skill_dir,
        model=model, timeout_s=timeout_s, workdir_base=workdir_base,
    )
    try:
        return run.result
    finally:
        staged.cleanup()
