"""Stage an Anthropic-format skill folder into an isolated workdir and build the
prompt prefix that tells the CLI how to use it.

A skill folder looks like:
    skill-name/
        SKILL.md        frontmatter (name, description) + instructions
        scripts/        executable helpers
        references/     docs loaded on demand
        assets/         templates / files

Codex has no native skill loader and does NOT do Claude's progressive disclosure,
so we emulate it. The injection strategy is itself a test dimension:

  flatten      : whole SKILL.md body in the prompt        -> "all info given" ceiling
  progressive  : frontmatter + file listing, model reads  -> closest to real agent
  scripts-only : only the script list                      -> tests orchestration only
"""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from .cleanup import remove_tree


@dataclass
class StagedSkill:
    workdir: Path        # isolated temp dir; CLI cwd
    skill_dir: Path      # copied skill folder inside workdir
    prompt_prefix: str   # instructions injected ahead of the user input
    fixture_dir: Path | None = None  # copied input material (repo/diff/spec), if any

    def cleanup(self, log_fn: Callable[[str], None] | None = None) -> bool:
        return remove_tree(self.workdir, log_fn=log_fn)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                fm = {}
            return fm, parts[2].strip()
    return {}, text.strip()


def _list_files(skill_dir: Path) -> list[str]:
    out = []
    for p in sorted(skill_dir.rglob("*")):
        if p.is_file() and p.name != "SKILL.md":
            out.append(p.relative_to(skill_dir).as_posix())
    return out


def stage_skill(skill_src: str | Path, strategy: str = "progressive",
                fixture: str | Path | None = None,
                workdir_base: str | Path | None = None) -> StagedSkill:
    skill_src = Path(skill_src)
    if not skill_src.is_dir():
        raise FileNotFoundError(f"skill folder not found: {skill_src}")

    # workdir_base lets you place workdirs somewhere the CLI's sandbox trusts
    # (e.g. inside the project) instead of %TEMP% -- codex's shell is denied
    # access to %TEMP%, which makes it flail and time out.
    if workdir_base:
        Path(workdir_base).mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="skilltest_",
                                    dir=str(workdir_base) if workdir_base else None))

    fixture_src = Path(fixture) if fixture else None
    fixture_name = fixture_src.name if fixture_src else None
    if fixture_name == skill_src.name:
        skill_dir = workdir / "_skill" / skill_src.name
    else:
        skill_dir = workdir / skill_src.name
    skill_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_src, skill_dir)

    # optional input material (a repo to review, a diff, a spec...) copied into
    # the workdir so the skill can scan it with its tools, like a real run.
    fixture_dir: Path | None = None
    if fixture_src:
        if not fixture_src.exists():
            raise FileNotFoundError(f"fixture not found: {fixture_src}")
        fixture_dir = workdir / fixture_src.name
        if fixture_src.is_dir():
            shutil.copytree(fixture_src, fixture_dir)
        else:
            shutil.copy2(fixture_src, fixture_dir)

    md = skill_dir / "SKILL.md"
    text = md.read_text(encoding="utf-8") if md.exists() else ""
    fm, body = _split_frontmatter(text)
    files = _list_files(skill_dir)

    skill_rel = skill_dir.relative_to(workdir).as_posix()
    prefix = _build_prefix(strategy, skill_dir, fm, body, files, skill_rel) + _output_note(skill_rel)
    return StagedSkill(workdir=workdir, skill_dir=skill_dir, prompt_prefix=prefix,
                       fixture_dir=fixture_dir)


def _output_note(rel: str) -> str:
    """Pin the output location for ALL skills. The skill folder is reference
    material; without this, models occasionally write their output files inside
    ./<rel>/ (causing location ambiguity), so state the convention explicitly."""
    return (
        f"\n--- IMPORTANT: output location ---\n"
        f"Write any files you create as OUTPUT to the current working directory "
        f"(the directory you are running in), NOT inside the skill folder ./{rel}/ "
        f"-- that folder is read-only reference material.\n"
    )


def _build_prefix(strategy, skill_dir: Path, fm: dict, body: str, files: list[str],
                  rel: str | None = None) -> str:
    rel = rel or skill_dir.name
    name = fm.get("name", rel)
    desc = fm.get("description", "")
    listing = "\n".join(f"  - {rel}/{f}" for f in files) or "  (none)"

    if strategy == "flatten":
        return (
            f"You have a skill named '{name}' available in the folder ./{rel}.\n"
            f"Its supporting files (scripts/references/assets) are on disk and you "
            f"may run or read them.\n\n"
            f"=== SKILL.md ===\n{body}\n\n"
            f"=== Available files ===\n{listing}\n"
        )

    if strategy == "scripts-only":
        scripts = [f for f in files if f.startswith("scripts/")]
        s_listing = "\n".join(f"  - {rel}/{f}" for f in scripts) or "  (none)"
        return (
            f"You have a skill named '{name}': {desc}\n"
            f"The following scripts are available; run them as needed:\n{s_listing}\n"
        )

    # progressive (default): mimic Claude's disclosure -- metadata + file map only.
    return (
        f"You have a skill named '{name}' in ./{rel}.\n"
        f"Description: {desc}\n\n"
        f"The full instructions are in ./{rel}/SKILL.md and supporting material is in "
        f"references/, scripts/, and assets/. Read SKILL.md first, then open only the "
        f"reference files you actually need, and run scripts where appropriate.\n\n"
        f"Available files:\n{listing}\n"
    )
