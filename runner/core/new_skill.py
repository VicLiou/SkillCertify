"""Invoke `interactive-skill-architect` in create mode to scaffold a
brand-new skill folder under `skills/<name>/`.

Strategy for "non-interactive create": architect's Phase 0 normally
routes to A1 (from-scratch) or A2 (from-blueprint), each of which then
asks Q1-Q6. We pre-seed all the answers in the task prompt so the
architect skips the Q&A and goes straight to file generation, the same
way fix-skill bypasses the architect's user-confirmation gates by saying
"the workdir is an isolated environment, no confirmation needed".

Required inputs (from CLI flags):
  - name
  - description
What's nice-to-have:
  - skill_type (one of the architect's pattern categories)
  - extra hint
  - blueprint skill (--from path) -> triggers A2 mode

If --from is given, we copy that blueprint into the workdir alongside
the architect so it can be read in Phase B1 (blueprint intake)."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..adapters.base import CliAdapter, RunResult
from .architect import invoke_architect


_SAFE_SKILL_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def skill_name_error(name: str) -> str | None:
    if not name or Path(name).name != name or "/" in name or "\\" in name:
        return "skill name must be a folder name, not a path"
    if not _SAFE_SKILL_NAME_RE.fullmatch(name):
        return "skill name must be kebab-case: lowercase letters, digits, and single hyphen separators"
    return None


def _build_task_prompt(name: str, description: str, *,
                       skill_type: str | None,
                       blueprint_name: str | None,
                       hint: str | None) -> str:
    if blueprint_name:
        mode_block = (
            "Phase 0 路由直接選 **A 建立新的 Skill**,然後在來源選擇直接走 "
            f"**A2 以既有 skill 為藍本衍生**。藍本 skill 位於目前工作目錄下的 "
            f"`./{blueprint_name}/`,請載入 `references/blueprint-intake.md` "
            "並對藍本做 Phase B0-B2 入料,然後依差異訪談的形式完成新 skill 設計。"
        )
    else:
        mode_block = (
            "Phase 0 路由直接選 **A 建立新的 Skill**,然後在來源選擇直接走 "
            "**A1 從零建立**。請載入 `references/create-mode.md` 並依其 "
            "Phase 1-4 流程執行。"
        )

    answers = [
        f"- **新 skill 名稱(Q1)**:`{name}`",
        f"- **新 skill 描述(Q2)**:{description}",
    ]
    if skill_type:
        answers.append(f"- **skill 類型/pattern(Q3 參考)**:{skill_type}")
    if hint:
        answers.append(f"- **使用者額外指示**:{hint}")

    return (
        "\n\n=== 任務:建立全新的 skill ===\n\n"
        + mode_block + "\n\n"
        "**重要:這次是無人值守模式**,使用者沒辦法即時回答問題。"
        "請把下面的「已決定的答案」直接當作 Phase 1 訪談的結果使用,"
        "**不要再用 AskUserQuestion 或純文字提問**——遇到沒提供的決策點,"
        "用業界最佳實踐自行合理決定。\n\n"
        "## 已決定的答案\n\n"
        + "\n".join(answers) + "\n\n"
        "## 產出位置\n\n"
        f"請把產出的 skill 完整檔案結構寫在工作目錄下的 `./{name}/` 子資料夾"
        "(也就是 workdir 根目錄底下建立 `{name}/SKILL.md`、`{name}/references/...`、"
        "`{name}/assets/...` 等)。**不要寫在 skill 模板自己的資料夾內**,"
        "因為那是架構師自身的唯讀參考。\n\n"
        "## Phase 4 自審\n\n"
        "完成檔案產出後請執行 Phase 4 自審(8 項,1-7 + 13),並把 "
        "`assets/self-review-report-template.md` 格式的表格寫在你的 "
        "final response 裡,讓使用者後續能 review。**0 FAIL 才算交付。**\n"
    )


def new_skill(name: str, description: str, out_root: Path,
              architect_skill_dir: Path, adapter: CliAdapter, *,
              skill_type: str | None = None,
              blueprint_skill: Path | None = None,
              hint: str | None = None,
              model: str | None = None,
              timeout_s: int = 900,
              workdir_base: str | None = None,
              ) -> tuple[Path, list[Path], RunResult]:
    """Scaffold a new skill at out_root/<name>/ via the architect's create
    mode. Returns (created_skill_dir, list of created file paths, result).

    Raises if the architect didn't produce a SKILL.md, since that's the
    minimum bar for a valid skill folder."""
    error = skill_name_error(name)
    if error:
        raise ValueError(error)
    if (out_root / name).exists():
        raise FileExistsError(f"skill folder already exists: {out_root / name} "
                              f"(remove or rename before re-running)")

    task = _build_task_prompt(
        name=name, description=description, skill_type=skill_type,
        blueprint_name=blueprint_skill.name if blueprint_skill else None,
        hint=hint,
    )

    run, staged = invoke_architect(
        architect_skill_dir, task, adapter,
        # No target_skill_dir -- new-skill is creating fresh, not editing.
        # If a blueprint was given, drop it in as an extra skill so the
        # architect can read it during Phase B1.
        extra_skills=[blueprint_skill] if blueprint_skill else None,
        model=model, timeout_s=timeout_s, workdir_base=workdir_base,
    )
    try:
        produced_dir = run.workdir / name
        if not produced_dir.is_dir() or not (produced_dir / "SKILL.md").exists():
            raise RuntimeError(
                f"architect did not produce ./{name}/SKILL.md in workdir. "
                f"final_message: {(run.result.final_message or '')[:500]!r}")

        # Copy the produced skill out to out_root/<name>/.
        out_root.mkdir(parents=True, exist_ok=True)
        dst = out_root / name
        shutil.copytree(produced_dir, dst)

        created_files = sorted(p.relative_to(dst)
                               for p in dst.rglob("*") if p.is_file())
        return dst, created_files, run.result
    finally:
        staged.cleanup()
