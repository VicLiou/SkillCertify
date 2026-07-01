"""Shared helpers for invoking the `interactive-skill-architect` skill
on behalf of fix-skill / check-skill / new-skill.

The architect is itself a skill -- we stage it the same way as any skill
under test, drop the *target* skill into the architect's workdir alongside
it, then drive the architect with a single task prompt that pre-seeds its
Phase 0 routing and Phase O1 step-3 scope selection so it runs without
mid-flight user interaction.

This module owns:
  - the architect skill location default
  - the workdir layout (architect staged + target copied next to it)
  - file-level diff/backup logic (used by both fix and check when --apply)

It does NOT own the per-command task prompts -- each subcommand builds its
own (constraint blocks for fix-skill, "scaffold these answers" for new-skill,
etc.) so the architect's behavior stays adapter-and-command-specific.
"""
from __future__ import annotations

import difflib
import shutil
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

from ..adapters.base import CliAdapter, RunOptions, RunResult
from .skill_loader import stage_skill, StagedSkill

ARCHITECT_SKILL_RELATIVE = Path("tools") / "skills" / "interactive-skill-architect"
DEFAULT_ARCHITECT_SKILL = ARCHITECT_SKILL_RELATIVE.as_posix()
_REPO_ROOT = Path(__file__).resolve().parents[2]


def is_architect_skill(path: str | Path) -> bool:
    p = Path(path)
    return p.is_dir() and (p / "SKILL.md").is_file()


def architect_skill_candidates(explicit: str | Path | None = None,
                               cwd: Path | None = None) -> list[Path]:
    """Return architect skill lookup order for CLI commands.

    External skill-test projects should not need to copy the framework's
    architect skill. Search the current project first for override/local
    vendoring, then fall back to the skill-auto-test source tree.
    """
    if explicit:
        return [Path(explicit)]
    base = cwd or Path.cwd()
    candidates = [
        base / ARCHITECT_SKILL_RELATIVE,
        _REPO_ROOT / ARCHITECT_SKILL_RELATIVE,
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve(strict=False)).casefold()
        except OSError:
            key = str(candidate.absolute()).casefold()
        if key not in seen:
            seen.add(key)
            out.append(candidate)
    return out


def resolve_architect_skill(explicit: str | Path | None = None,
                            cwd: Path | None = None) -> Path:
    for candidate in architect_skill_candidates(explicit, cwd):
        if is_architect_skill(candidate):
            return candidate
    # Return the highest-priority path so downstream errors remain familiar.
    return architect_skill_candidates(explicit, cwd)[0]


def architect_skill_search_detail(explicit: str | Path | None = None,
                                  cwd: Path | None = None) -> str:
    return ", ".join(str(p) for p in architect_skill_candidates(explicit, cwd))


@dataclass
class ArchitectRun:
    """What the architect produced when invoked by one of our subcommands.

    target_in_workdir is None when the invocation didn't have a target skill
    (e.g. new-skill writes a fresh skill from scratch under workdir root,
    not as an edit to an existing copy)."""
    result: RunResult
    workdir: Path
    target_in_workdir: Path | None


def invoke_architect(architect_skill_dir: Path, task_prompt: str,
                     adapter: CliAdapter, *,
                     target_skill_dir: Path | None = None,
                     extra_skills: list[Path] | None = None,
                     model: str | None = None,
                     timeout_s: int = 600,
                     workdir_base: str | None = None,
                     ) -> tuple[ArchitectRun, StagedSkill]:
    """Stage the architect, optionally drop a target skill + extra
    blueprint skills into the same workdir, and run the adapter with
    `architect.prompt_prefix + task_prompt`.

    target_skill_dir: the skill being edited (fix-skill, check-skill).
    extra_skills:    additional skills the architect needs to *read*
                     (e.g. blueprint for new-skill A2 mode).

    Returns (ArchitectRun, staged) -- the caller MUST call staged.cleanup()
    once it has copied any needed output out of the workdir. (We don't auto-
    clean because some callers, like fix-skill --apply, need to read files
    from the workdir after the call returns.)"""
    if not architect_skill_dir.is_dir() or not (architect_skill_dir / "SKILL.md").exists():
        raise FileNotFoundError(
            f"architect skill (with SKILL.md) not found: {architect_skill_dir}")
    if target_skill_dir is not None and not target_skill_dir.is_dir():
        raise FileNotFoundError(f"target skill not found: {target_skill_dir}")
    for ex in extra_skills or []:
        if not ex.is_dir():
            raise FileNotFoundError(f"extra skill not found: {ex}")

    staged = stage_skill(architect_skill_dir, strategy="progressive",
                         workdir_base=workdir_base)
    try:
        target_in_workdir: Path | None = None
        if target_skill_dir is not None:
            target_in_workdir = staged.workdir / target_skill_dir.name
            shutil.copytree(target_skill_dir, target_in_workdir)
        for ex in extra_skills or []:
            shutil.copytree(ex, staged.workdir / ex.name)

        full_prompt = staged.prompt_prefix + task_prompt
        opts = RunOptions(model=model, timeout_s=timeout_s, verbose=False)
        result = adapter.run(full_prompt, staged.workdir, opts)
        if result.crashed:
            raise RuntimeError(result.error or "architect run failed")

        return ArchitectRun(result=result, workdir=staged.workdir,
                            target_in_workdir=target_in_workdir), staged
    except Exception:
        staged.cleanup()
        raise


# --- diff / backup utilities (shared by fix-skill and any future "apply" path)

@dataclass
class FileChange:
    relpath: Path
    kind: str        # "modified" | "added" | "deleted"
    diff_text: str   # unified-diff hunk (empty for added/deleted)


def diff_skill(original_dir: Path, modified_dir: Path) -> list[FileChange]:
    """Recursively diff every file under modified_dir against original_dir
    (relative paths). Returns one FileChange per differing file."""
    changes: list[FileChange] = []
    orig_files = {p.relative_to(original_dir) for p in original_dir.rglob("*") if p.is_file()}
    mod_files = {p.relative_to(modified_dir) for p in modified_dir.rglob("*") if p.is_file()}

    for rel in sorted(orig_files | mod_files):
        orig_p = original_dir / rel
        mod_p = modified_dir / rel
        if rel not in mod_files:
            changes.append(FileChange(rel, "deleted", ""))
            continue
        if rel not in orig_files:
            content = _display_added_file(mod_p)
            changes.append(FileChange(rel, "added", content))
            continue
        orig_bytes = _safe_read_bytes(orig_p)
        mod_bytes = _safe_read_bytes(mod_p)
        if orig_bytes == mod_bytes:
            continue
        orig_text = _decode_utf8(orig_bytes)
        mod_text = _decode_utf8(mod_bytes)
        if orig_text is None or mod_text is None:
            diff = (f"Binary file changed "
                    f"({len(orig_bytes)} -> {len(mod_bytes)} bytes)\n")
        else:
            diff = "".join(difflib.unified_diff(
                orig_text.splitlines(keepends=True),
                mod_text.splitlines(keepends=True),
                fromfile=f"a/{rel.as_posix()}",
                tofile=f"b/{rel.as_posix()}",
            ))
        changes.append(FileChange(rel, "modified", diff))
    return changes


def _safe_read_bytes(p: Path) -> bytes:
    try:
        return p.read_bytes()
    except OSError:
        return b""


def _decode_utf8(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _display_added_file(p: Path) -> str:
    data = _safe_read_bytes(p)
    text = _decode_utf8(data)
    if text is None:
        return f"Binary file added ({len(data)} bytes)\n"
    return text


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]


def _unique_path(candidate: Path) -> Path:
    if not candidate.exists():
        return candidate
    for i in range(1, 1000):
        alt = candidate.with_name(f"{candidate.name}.{i}")
        if not alt.exists():
            return alt
    raise FileExistsError(f"could not find a unique backup path near {candidate}")


def backup_then_copy(modified_dir: Path, target_dir: Path,
                     changes: list[FileChange]) -> Path:
    """Back up the touched-files subset of target_dir into a sibling backup
    folder, then copy the corresponding modified files from modified_dir
    into target_dir. Returns the backup folder path for the caller to
    surface to the user."""
    backup_dir = _unique_path(target_dir.parent / f"{target_dir.name}.bak.{_timestamp()}")
    backup_dir.mkdir(parents=True, exist_ok=False)

    for ch in changes:
        if ch.kind in ("modified", "deleted"):
            src = target_dir / ch.relpath
            if src.exists():
                dst = backup_dir / ch.relpath
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

    for ch in changes:
        if ch.kind == "deleted":
            (target_dir / ch.relpath).unlink(missing_ok=True)
        else:
            src = modified_dir / ch.relpath
            dst = target_dir / ch.relpath
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return backup_dir
