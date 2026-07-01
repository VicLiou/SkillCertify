"""Diagnostic helpers backing the `doctor` and `list` subcommands.

`doctor`: preflight check that answers "is my environment ready?" so new
users don't have to grep through README to find out what binaries / Python
packages / sample files they need.

`list`: scan the local project for skills and testcases, cross-reference
them so the user can see what they have to work with -- discoverability
for someone who just cloned the repo.

Both intentionally do NOT call any LLM / adapter. They're pure local
filesystem + shutil.which() checks, so they're fast (<1s) and safe (no
side effects). Suitable to run as the very first thing on a new clone."""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

import yaml

from .architect import architect_skill_search_detail, resolve_architect_skill


@dataclass
class CheckResult:
    """One row in the doctor's checklist."""
    name: str
    passed: bool
    detail: str          # short status text shown after the name
    required: bool       # if False, a failure is a warning not an error


def _check_python() -> CheckResult:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 10)
    return CheckResult(
        "Python 3.10+",
        ok,
        f"{v.major}.{v.minor}.{v.micro}",
        required=True,
    )


def _check_yaml() -> CheckResult:
    spec = find_spec("yaml")
    return CheckResult(
        "PyYAML",
        spec is not None,
        "installed" if spec else "missing (pip install -r requirements.txt)",
        required=True,
    )


def _check_binary(name: str, *, required: bool, install_url: str | None = None
                  ) -> CheckResult:
    path = shutil.which(name)
    if path:
        return CheckResult(name, True, path, required=required)
    tip = f"missing — install: {install_url}" if install_url else "missing"
    return CheckResult(name, False, tip, required=required)


def _check_optional_module(name: str, *, when: str,
                           module: str | None = None) -> CheckResult:
    import_name = module or name
    spec = find_spec(import_name)
    return CheckResult(
        f"{name} (optional)",
        spec is not None,
        "installed" if spec else f"missing — install with `pip install {name}` when {when}",
        required=False,
    )


def _check_architect_skill() -> CheckResult:
    p = resolve_architect_skill()
    ok = p.is_dir() and (p / "SKILL.md").exists()
    detail = str(p) if ok else (
        "missing; searched " + architect_skill_search_detail()
        + " (needed by fix-skill / check-skill / new-skill / iterate)"
    )
    return CheckResult(
        "architect skill",
        ok,
        detail,
        required=False,  # only needed for architect-driven commands; not for `run`
    )


def _count_skills(skills_root: Path) -> int:
    if not skills_root.is_dir():
        return 0
    return sum(1 for d in skills_root.iterdir()
               if d.is_dir() and (d / "SKILL.md").is_file())


def _count_testcases(testcases_root: Path) -> int:
    if not testcases_root.is_dir():
        return 0
    return sum(1 for f in testcases_root.glob("*.yaml")
               if not f.name.startswith("_"))


def _check_sample_content() -> CheckResult:
    n_skills = _count_skills(Path("skills"))
    n_tcs = _count_testcases(Path("testcases"))
    ok = n_skills > 0 and n_tcs > 0
    detail = f"{n_skills} skill(s) in skills/, {n_tcs} testcase(s) in testcases/"
    if not ok:
        detail += " — run `skill-test list` to see what you have"
    return CheckResult("sample skills + testcases", ok, detail, required=False)


def doctor() -> tuple[list[CheckResult], bool]:
    """Run the full diagnostic battery. Returns (rows, all_required_passed).
    Caller prints the rows; the second return tells `cmd_doctor` what exit
    code to use."""
    rows = [
        _check_python(),
        _check_yaml(),
        _check_binary("claude", required=False,
                      install_url="https://docs.claude.com/en/docs/claude-code/quickstart"),
        _check_binary("codex", required=False,
                      install_url="https://developers.openai.com/codex/cli/"),
        _check_optional_module(
            "pywinpty", module="winpty",
            when="using --adapter codex-tui on Windows",
        ),
        _check_optional_module("pytest", when="testcases use command: python -m pytest"),
        _check_architect_skill(),
        _check_sample_content(),
    ]
    # Also need *at least one* of claude / codex to do anything useful.
    has_claude = any(r.name == "claude" and r.passed for r in rows)
    has_codex = any(r.name == "codex" and r.passed for r in rows)
    if not (has_claude or has_codex):
        rows.append(CheckResult(
            "at least one CLI", False,
            "neither claude nor codex found — install at least one to run testcases",
            required=True,
        ))
    all_required_passed = all(r.passed for r in rows if r.required)
    return rows, all_required_passed


# ----------------------------------------------------------------------------
# `list` subcommand: scan skills/ + testcases/ and cross-reference.

@dataclass
class SkillEntry:
    name: str             # folder name
    path: Path
    has_scripts: bool
    has_references: bool
    has_assets: bool
    testcases: list[str]  # testcase yaml filenames pointing at this skill


@dataclass
class TestcaseEntry:
    filename: str
    path: Path
    skill: str | None     # the skill: field of the first non-empty doc
    n_cases: int          # number of testcase documents
    runs: int | None      # `runs` of the first doc, if set


def _list_skills(skills_root: Path) -> list[SkillEntry]:
    out: list[SkillEntry] = []
    if not skills_root.is_dir():
        return out
    for d in sorted(skills_root.iterdir()):
        if not d.is_dir() or not (d / "SKILL.md").is_file():
            continue
        out.append(SkillEntry(
            name=d.name, path=d,
            has_scripts=(d / "scripts").is_dir(),
            has_references=(d / "references").is_dir(),
            has_assets=(d / "assets").is_dir(),
            testcases=[],  # filled by cross-reference below
        ))
    return out


def _list_testcases(testcases_root: Path) -> list[TestcaseEntry]:
    out: list[TestcaseEntry] = []
    if not testcases_root.is_dir():
        return out
    for f in sorted(testcases_root.glob("*.yaml")):
        if f.name.startswith("_"):
            continue
        try:
            docs = [d for d in yaml.safe_load_all(f.read_text(encoding="utf-8"))
                    if d]
        except yaml.YAMLError:
            docs = []
        skill = docs[0].get("skill") if docs else None
        runs = docs[0].get("runs") if docs else None
        out.append(TestcaseEntry(
            filename=f.name, path=f, skill=skill,
            n_cases=len(docs), runs=runs,
        ))
    return out


def list_local(skills_root: Path = Path("skills"),
               testcases_root: Path = Path("testcases")
               ) -> tuple[list[SkillEntry], list[TestcaseEntry]]:
    skills = _list_skills(skills_root)
    testcases = _list_testcases(testcases_root)
    # Cross-reference: every testcase points at a skill -> link back so the
    # `list` output shows "skill X has testcases [a.yaml, b.yaml]".
    by_skill: dict[str, list[str]] = {}
    for tc in testcases:
        if not tc.skill:
            continue
        key = Path(tc.skill).name  # match by folder name, not full path
        by_skill.setdefault(key, []).append(tc.filename)
    for s in skills:
        s.testcases = by_skill.get(s.name, [])
    return skills, testcases
