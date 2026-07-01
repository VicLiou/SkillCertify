"""Scaffold a new skill-auto-test project layout in a target directory.

The point: someone who wants to start a fresh skill-testing project
(maybe at $WORK, or a new feature branch) shouldn't have to mkdir + cp
each piece by hand. `skill-test init <path>` lays down:

  <path>/
  ├── skills/                  empty, with a placeholder .gitkeep
  ├── testcases/               empty
  ├── fixtures/                empty
  ├── tools/skills/            (only if --with-architect) -- copy of architect
  ├── .gitignore               sensible defaults (.work/, *.bak.*, etc.)
  └── README.md                tiny pointer to the main docs

If --with-example is passed, also copy this repo's example-skill +
claude-example.yaml so the new project starts with one working sample
the user can immediately run.

Source for the architect / example comes from THIS repo (the one running
init), so the user's new project gets a self-contained copy."""
from __future__ import annotations

import shutil
from pathlib import Path

# These are looked up relative to the runner package's repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ARCHITECT_SRC = _REPO_ROOT / "tools" / "skills" / "interactive-skill-architect"
_EXAMPLE_SKILL_SRC = _REPO_ROOT / "skills" / "example-skill"
_EXAMPLE_TESTCASE_SRC = _REPO_ROOT / "testcases" / "claude-example.yaml"

_GITIGNORE = """\
# skill-auto-test scratch
.work/
.iterate-traces/
fixtures/*-sample/

# automatic backups
*.bak.*

# editor / OS
.vscode/
.idea/
*.swp
.DS_Store
"""

_README_TEMPLATE = """\
# {name}

A skill-testing project using [skill-auto-test](https://github.com/...).

## Layout

- `skills/` — your skills under test (each is a folder with `SKILL.md`)
- `testcases/` — testcase YAML files (one per skill, usually)
- `fixtures/` — optional sample input projects for skills that need them
{architect_line}

## Quick start

```bash
# verify environment
skill-test doctor

# see what's here
skill-test list

# run an existing testcase
skill-test testcases/<name>.yaml --runs 5
```

Full docs: see the [skill-auto-test docs](../docs/) (or wherever you
keep them).
"""


def init_project(target: Path, *, with_architect: bool = False,
                 with_example: bool = False, force: bool = False) -> list[str]:
    """Scaffold target/ with skill-auto-test's expected layout.

    Returns a list of human-readable lines describing what was created.
    Raises FileExistsError if target exists and is non-empty (unless --force).
    """
    if target.exists() and any(target.iterdir()) and not force:
        raise FileExistsError(
            f"{target} already exists and is non-empty (use --force to proceed)")
    target.mkdir(parents=True, exist_ok=True)

    actions: list[str] = []

    # 1. Empty base dirs (with .gitkeep so git tracks them empty).
    for sub in ("skills", "testcases", "fixtures"):
        d = target / sub
        d.mkdir(exist_ok=True)
        (d / ".gitkeep").touch()
        actions.append(f"created {sub}/")

    # 2. .gitignore
    (target / ".gitignore").write_text(_GITIGNORE, encoding="utf-8")
    actions.append("wrote .gitignore")

    # 3. Optional: bring along the architect skill.
    architect_line = ""
    if with_architect:
        if not _ARCHITECT_SRC.is_dir():
            raise FileNotFoundError(
                f"architect skill source not found at {_ARCHITECT_SRC} "
                f"(this means the running skill-auto-test install is broken)")
        dst = target / "tools" / "skills" / "interactive-skill-architect"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(_ARCHITECT_SRC, dst)
        actions.append(f"copied architect to tools/skills/interactive-skill-architect/ "
                       f"(needed by fix-skill / check-skill / new-skill)")
        architect_line = ("- `tools/skills/interactive-skill-architect/` — "
                          "the architect skill the framework calls into\n")

    # 4. Optional: drop in the example skill + matching testcase.
    if with_example:
        if not _EXAMPLE_SKILL_SRC.is_dir():
            raise FileNotFoundError(
                f"example skill source not found at {_EXAMPLE_SKILL_SRC}")
        ex_dst = target / "skills" / "example-skill"
        if not ex_dst.exists():
            shutil.copytree(_EXAMPLE_SKILL_SRC, ex_dst)
            actions.append("copied skills/example-skill/ as a starter")
        if _EXAMPLE_TESTCASE_SRC.is_file():
            tc_dst = target / "testcases" / _EXAMPLE_TESTCASE_SRC.name
            if not tc_dst.exists():
                shutil.copy2(_EXAMPLE_TESTCASE_SRC, tc_dst)
                actions.append(f"copied {tc_dst.relative_to(target).as_posix()}")

    # 5. Project README pointing at docs.
    readme = _README_TEMPLATE.format(
        name=target.name, architect_line=architect_line)
    (target / "README.md").write_text(readme, encoding="utf-8")
    actions.append("wrote README.md")

    return actions
