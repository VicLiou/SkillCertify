"""Use an LLM to draft a testcase YAML from a skill's SKILL.md.

This is a development-time convenience, not part of the deterministic
measurement path: it makes ONE LLM call (no skill staging, no workdir
isolation) to turn a SKILL.md into a starting-point testcase YAML, the same
way a human would paste the README's prompt into a chat LLM. The output
should still be reviewed before being trusted -- see README "??LLM ?Ｙ?
testcase YAML".
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..adapters.base import CliAdapter, RunOptions, RunResult
from .cleanup import remove_tree
from .skill_loader import stage_skill
from .validate import (
    _AMBIGUOUS_WORKSPACE_TERMS,
    _fixture_copied_name,
    _fixture_original_path_variants,
    KNOWN_ASSERTION_KEYS,
    validate_docs,
)

_INSTRUCTIONS = """\
You are generating testcase YAML for skill-auto-test.

Output contract:
- Return only YAML. Do not wrap it in Markdown and do not add explanations.
- Use one YAML document per testcase; separate multiple documents with `---`.
- Every document must be a mapping with `name`, `skill`, and `input`.
- Assertion keys such as `exit_code`, `output_contains`, `command`, and `judge` must appear as single-key mapping items under `expect:`. Never put assertion keys at document top level.
- Set `skill` to SKILL_PATH exactly.
- If FIXTURE_PATH is present, set `fixture` to FIXTURE_PATH exactly.
- Do not add a `sandbox` field.

Planning rules:
- `COVERAGE=happy`: generate exactly 1 common happy-path testcase.
- `COVERAGE=minimal`: generate 2 testcases: 1 happy path and 1 important edge/stop case.
- `COVERAGE=all`: generate 3-6 high-value testcases covering the major branches in SKILL.md.
- `BIAS=positive`: include only scenarios where the skill should complete successfully.
- `BIAS=negative`: include only scenarios where the skill should stop/refuse correctly.
- `BIAS=mixed`: include both successful and stop/refusal scenarios when SKILL.md defines both.
- If HINT conflicts with coverage or bias, follow HINT.

Fixture rules:
- If FIXTURE_SUMMARY is provided, base testcase inputs on the actual files listed there.
- FIXTURE_SUMMARY `runtime_reference` is the path visible to the skill after the fixture is copied into the test workdir.
- In `input`, refer to copied fixture files as `./<copied_name>` or `./<copied_name>/<relative file>`, not by the original absolute path.
- If the skill asks for `PROJECT_PATH`, a repository path, a workspace, an input folder, or a target file, explicitly point it at `runtime_reference` or a file under it.
- Every testcase with `fixture:` must mention the copied fixture directory in `input`; for project/review skills include a line such as `PROJECT_PATH=./<copied_name>`.
- When a fixture is present, do not ask the skill to operate on "current workspace" unless the empty harness workspace itself is the target; for project/review skills this is usually wrong.
- Do not invent fixture files that are not in FIXTURE_SUMMARY.
- If there is no FIXTURE_PATH, do not include a `fixture` field.
- Use FIXTURE_PROFILE to choose appropriate scenarios; for `git-review-project`, prefer explicit PROJECT_PATH + REVIEW_SCOPE inputs over vague workspace instructions.

Assertion rules:
- Prefer checks that run without extra CLI flags: `exit_code`, `file_exists`, `file_absent`, `output_contains`, `reads_file`.
- Always include `exit_code: 0` unless SKILL.md explicitly says the CLI should fail.
- Use `output_contains` for text that may be in final output, stdout, or generated files.
- When a testcase also has `judge:`, keep `output_contains` low-bar and stable: assert required option names, headings, filenames, or disposition labels, not full explanatory sentences.
- Use `final_contains` only for final assistant text. Use `stdout_contains` only for real stdout.
- Avoid `regex`, `flow_equals`, and brittle internal SKILL.md variable names.
- Do not assert template placeholders such as `SCAN_ROUND_COUNT`; assert rendered headings, filenames, statuses, dispositions, or other user-visible text instead.
- Avoid exact localized sentences in `output_contains` unless SKILL.md mandates that exact wording; localized prose varies across models and should usually be checked by `judge:` instead.
- Use `reads_file` only for files the skill should definitely read on that scenario.
- Use `tool_used` or `flow_contains` only when SKILL.md explicitly requires that tool behavior.
- Avoid `command` and `judge` by default because they require `--allow-exec` / `--judge` at run time. Include them only when HINT asks for them or there is no deterministic alternative.
- If you assert `file_exists`, the `input` must explicitly request that exact output path.

Scenario rules:
- A positive testcase should pass when the skill performs the requested work correctly.
- A negative testcase should pass when the skill stops/refuses exactly as SKILL.md requires.
- Do not create sabotage testcases whose assertions are expected to fail.
- Use human-visible output markers from SKILL.md templates when available, not internal implementation labels.

Recommended YAML shape:

name: my-skill-happy-path
skill: SKILL_PATH
load_strategy: progressive
runs: 5
input: |
  Ask the skill to perform one realistic task. Name any expected output files explicitly.
expect:
  - exit_code: 0
  - output_contains: ["stable marker from expected output"]

Before returning, self-check:
- YAML parses.
- Every `expect` entry has exactly one key.
- All assertion keys are spelled exactly as documented.
- Required fields are present.
- Fixture and file paths are consistent with the rules above.
- If `fixture:` is present, each `input` clearly targets `./<copied_name>` or a file below it.
"""

_VALID_COVERAGE = ("all", "happy", "minimal")
_VALID_BIAS = ("positive", "negative", "mixed")


_FIXTURE_SUMMARY_MAX_FILES = 40
_FIXTURE_SUMMARY_MAX_EXCERPTS = 6
_FIXTURE_SUMMARY_EXCERPT_CHARS = 900
_FIXTURE_SUMMARY_TOTAL_CHARS = 7000
_TEXT_SUFFIXES = {
    ".cfg", ".csv", ".diff", ".ini", ".json", ".jsonl", ".log",
    ".md", ".py", ".rst", ".toml", ".tsv", ".txt", ".xml",
    ".yaml", ".yml",
}
_SKIP_FIXTURE_PARTS = {".git", ".hg", ".svn", "__pycache__", "node_modules"}


@dataclass(frozen=True)
class FixtureProfile:
    name: str
    summary: str
    needs_git: bool = False
    needs_baseline_commit: bool = False
    guidance: tuple[str, ...] = ()


_GENERIC_FIXTURE_PROFILE = FixtureProfile(
    name="generic-files",
    summary="General fixture files inferred from the skill instructions.",
    guidance=(
        "Create representative files that exercise the main skill workflow.",
        "Keep README.fixture.md specific enough for testcase generation to reuse it.",
    ),
)


def _infer_fixture_profile(skill_dir: Path) -> FixtureProfile:
    """Infer fixture post-processing needs from SKILL.md.

    This stays intentionally conservative. The generator still receives the
    full SKILL.md, but the profile gives it a deterministic nudge for common
    families such as code-review skills that require a real git repository.
    """
    skill_md = skill_dir / "SKILL.md"
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return _GENERIC_FIXTURE_PROFILE

    t = text.lower()
    git_review_markers = (
        "project_path",
        "review_scope",
        "diff_source",
        "base_commit",
        "uncommitted_target",
        "committed_diff",
        "worktree_uncommitted",
    )
    if any(marker in t for marker in git_review_markers) or (
        "git repo" in t and ("review" in t or "diff" in t)
    ):
        return FixtureProfile(
            name="git-review-project",
            summary="Git-backed source project for code-review or diff-review skills.",
            needs_git=True,
            needs_baseline_commit=True,
            guidance=(
                "Create ordinary source-project files only; the harness will initialize .git and commit the initial baseline after copying.",
                "Include at least one realistic reviewable issue in committed source files so PROJECT-scope review tests have a concrete finding.",
                "In README.fixture.md, describe the intended review target and mention that testcase input should set PROJECT_PATH to the copied fixture directory.",
                "For DIFF scenarios, describe which file can be edited after the baseline commit instead of trying to run git commands now.",
            ),
        )

    if any(marker in t for marker in ("openapi", "swagger", "endpoint", "api request", "http ")):
        return FixtureProfile(
            name="api-spec",
            summary="Small API specification and request/response examples.",
            guidance=(
                "Create a compact API spec plus representative payload examples.",
                "Include one edge case the skill should notice or validate.",
            ),
        )

    if any(marker in t for marker in ("csv", "json", "yaml", "dataset", "data file", "spreadsheet")):
        return FixtureProfile(
            name="data-files",
            summary="Structured data files with normal and edge-case records.",
            guidance=(
                "Use small UTF-8 data files with clear headers or keys.",
                "Include at least one boundary or malformed-but-realistic record when useful.",
            ),
        )

    if any(marker in t for marker in ("markdown", "document", "template", "docx", "pdf", "form")):
        return FixtureProfile(
            name="document-set",
            summary="Document/template fixture with labeled expected fields or sections.",
            guidance=(
                "Prefer text/Markdown fixtures unless the skill specifically needs binary documents.",
                "Label the main sections and expected edge cases in README.fixture.md.",
            ),
        )

    return _GENERIC_FIXTURE_PROFILE


def _format_profile_for_prompt(profile: FixtureProfile) -> str:
    lines = [
        f"FIXTURE_PROFILE: {profile.name}",
        f"FIXTURE_PROFILE_SUMMARY: {profile.summary}",
    ]
    if profile.needs_git:
        lines.append("FIXTURE_PROFILE_NEEDS_GIT: true")
    if profile.needs_baseline_commit:
        lines.append("FIXTURE_PROFILE_NEEDS_BASELINE_COMMIT: true")
    if profile.guidance:
        lines.append("FIXTURE_PROFILE_GUIDANCE:")
        lines.extend(f"- {item}" for item in profile.guidance)
    return "\n".join(lines) + "\n"


def _is_probably_text(path: Path) -> bool:
    if path.suffix.lower() in _TEXT_SUFFIXES:
        return True
    try:
        sample = path.read_bytes()[:512]
    except OSError:
        return False
    return b"\0" not in sample


def _fixture_rel(path: Path, root: Path) -> str:
    if root.is_file():
        return root.name
    return path.relative_to(root).as_posix()


def _summarize_fixture(fixture: str | None) -> str:
    """Return a compact UTF-8 fixture summary for the generation prompt.

    The generator previously only saw the fixture path, which made it invent
    files. A bounded file list plus a few excerpts gives it enough grounding
    without dumping a whole repository into the prompt.
    """
    if not fixture:
        return ""

    root = Path(fixture)
    copied_name = root.name or "fixture"
    lines = [
        "FIXTURE_SUMMARY:",
        f"root: {root.as_posix()}",
        f"copied_name: {copied_name}",
        f"runtime_reference: ./{copied_name}",
    ]
    if not root.exists():
        lines.append("status: path not found at prompt-build time")
        return "\n".join(lines) + "\n"

    files = [root] if root.is_file() else [
        p for p in sorted(root.rglob("*"))
        if p.is_file() and not any(part in _SKIP_FIXTURE_PARTS for part in p.parts)
    ]
    shown = files[:_FIXTURE_SUMMARY_MAX_FILES]
    lines.append(f"type: {'file' if root.is_file() else 'directory'}")
    if root.is_dir() and (root / ".git").is_dir():
        lines.append("git_repo: true")
    lines.append(f"file_count: {len(files)}")
    lines.append("files:")
    for p in shown:
        rel = _fixture_rel(p, root)
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        lines.append(f"- {rel} ({size} bytes)")
    if len(files) > len(shown):
        lines.append(f"- ... {len(files) - len(shown)} more file(s) omitted")

    excerpts = []
    for p in shown:
        if len(excerpts) >= _FIXTURE_SUMMARY_MAX_EXCERPTS:
            break
        if not _is_probably_text(p):
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        content = content.strip()
        if not content:
            continue
        rel = _fixture_rel(p, root)
        if len(content) > _FIXTURE_SUMMARY_EXCERPT_CHARS:
            content = content[:_FIXTURE_SUMMARY_EXCERPT_CHARS].rstrip() + "\n..."
        excerpts.append(f"--- {rel} ---\n{content}")

    if excerpts:
        lines.append("excerpts:")
        lines.extend(excerpts)

    summary = "\n".join(lines) + "\n"
    if len(summary) > _FIXTURE_SUMMARY_TOTAL_CHARS:
        summary = summary[:_FIXTURE_SUMMARY_TOTAL_CHARS].rstrip() + "\n...\n"
    return summary


def _format_validation_issues(issues) -> str:
    lines = []
    for issue in issues:
        line = f"[{issue.severity}] {issue.where}: {issue.message}"
        if issue.hint:
            line += f" (hint: {issue.hint})"
        lines.append(line)
    return "\n".join(lines)


class _GeneratedYamlDumper(yaml.SafeDumper):
    pass


def _represent_generated_string(dumper, value: str):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_GeneratedYamlDumper.add_representer(str, _represent_generated_string)


def _dump_generated_yaml(docs: list[dict]) -> str:
    return yaml.dump_all(
        docs,
        Dumper=_GeneratedYamlDumper,
        sort_keys=False,
        allow_unicode=True,
    ).rstrip()


def _fixture_runtime_target_line(profile: FixtureProfile, copied_name: str) -> str:
    runtime_ref = f"./{copied_name}"
    if profile.name == "git-review-project":
        return f"Use PROJECT_PATH={runtime_ref} as the target project path."
    return f"Use the copied fixture directory {runtime_ref} as the target input path."

def _repair_misplaced_expect_assertions(docs: list[dict]) -> bool:
    """Move assertion keys accidentally emitted at document top level.

    LLMs sometimes return `judge:` or `command:` beside `name`/`skill` even
    though testcase assertions must live under `expect:`. This repair is
    deterministic and limited to known assertion keys; validation still catches
    malformed `expect` values and truly unknown fields.
    """
    assertion_keys = set(KNOWN_ASSERTION_KEYS)
    changed = False
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        expect = doc.get("expect")
        if expect is None:
            expect = []
            doc["expect"] = expect
        if not isinstance(expect, list):
            continue
        for key in list(doc):
            if key not in assertion_keys:
                continue
            expect.append({key: doc.pop(key)})
            changed = True
    return changed


def _repair_generated_fixture_inputs(docs: list[dict], *, skill_dir: Path) -> bool:
    """Repair narrow fixture-reference mistakes in generated testcase YAML.

    The generator already receives explicit fixture rules, but LLMs still
    occasionally write inputs such as "review the current workspace" even
    though the fixture will be copied into the isolated workdir. Keep this
    deterministic and narrow: only fix runtime fixture references, then let
    validation catch everything else.
    """
    profile = _infer_fixture_profile(skill_dir)
    changed = False
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        fixture = doc.get("fixture")
        input_text = doc.get("input")
        if not isinstance(fixture, str) or not fixture:
            continue
        if not isinstance(input_text, str):
            continue

        copied_name = _fixture_copied_name(fixture)
        runtime_ref = f"./{copied_name}"
        fixed = input_text

        for variant in sorted(_fixture_original_path_variants(fixture), key=len, reverse=True):
            if variant and variant != copied_name:
                fixed = fixed.replace(variant, runtime_ref)

        fixed_lower = fixed.lower()
        mentions_copied = copied_name.lower() in fixed_lower
        if (any(term in fixed_lower for term in _AMBIGUOUS_WORKSPACE_TERMS)
                and not mentions_copied):
            fixed = fixed.rstrip() + "\n\n" + _fixture_runtime_target_line(profile, copied_name) + "\n"

        if fixed != input_text:
            doc["input"] = fixed
            changed = True
    return changed


def build_prompt(skill_dir: Path, fixture: str | None = None, *,
                 coverage: str = "all", bias: str = "mixed",
                 hint: str | None = None) -> str:
    """Build the testcase-generation prompt with bounded grounding context."""
    if coverage not in _VALID_COVERAGE:
        raise ValueError(f"coverage must be one of {_VALID_COVERAGE}, got {coverage!r}")
    if bias not in _VALID_BIAS:
        raise ValueError(f"bias must be one of {_VALID_BIAS}, got {bias!r}")

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"SKILL.md not found in {skill_dir}")
    skill_md_content = skill_md.read_text(encoding="utf-8")
    profile = _infer_fixture_profile(skill_dir)

    fixture_line = f"FIXTURE_PATH: {fixture}\n" if fixture else ""
    fixture_summary = _summarize_fixture(fixture)
    hint_line = f"HINT: {hint}\n" if hint else ""
    profile_block = _format_profile_for_prompt(profile)
    input_block = (
        "--- INPUT PACKAGE ---\n"
        f"SKILL_PATH: {skill_dir.as_posix()}\n"
        f"{fixture_line}"
        f"COVERAGE: {coverage}\n"
        f"BIAS: {bias}\n"
        f"{hint_line}"
        f"{profile_block}"
        f"{fixture_summary}"
        "\nSKILL.md:\n"
        f"{skill_md_content}\n"
        "--- END INPUT PACKAGE ---\n\n"
        "Use only the INPUT PACKAGE above as source material for this generation.\n\n"
    )
    return input_block + _INSTRUCTIONS


_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n(.*)\n```\s*$", re.DOTALL)


def extract_yaml(text: str) -> str:
    """Strip a single outer Markdown code fence, if the LLM added one despite
    being told not to."""
    text = text.strip()
    m = _FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()
    return text


def parse_testcases(yaml_text: str) -> list[dict]:
    """Validate the text is legal YAML with at least one non-empty document.
    Raises ValueError/yaml.YAMLError on failure -- caller should not write the
    file if this raises."""
    docs = [d for d in yaml.safe_load_all(yaml_text) if d]
    if not docs:
        raise ValueError("LLM output contained no YAML documents")
    return docs


def _replace_directory_contents(path: Path) -> None:
    """Remove existing fixture output after a replacement has succeeded.

    Refuse obvious foot-guns: the current working directory and filesystem
    roots are not valid fixture output directories to clear. Delete the whole
    output directory through remove_tree() so Windows .git objects and
    read-only files get the same permission repair/retry path as workdirs.
    """
    if not path.exists():
        return
    if not path.is_dir() or path.is_symlink():
        raise NotADirectoryError(f"fixture output is not a directory: {path}")

    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    roots = {Path(anchor).resolve() for anchor in (cwd.anchor, resolved.anchor)
             if anchor}
    if resolved == cwd or resolved in roots:
        raise ValueError(f"refusing to replace unsafe fixture output directory: {path}")

    if not remove_tree(path):
        raise PermissionError(f"failed to replace fixture output directory: {path}")


def generate_testcase(skill_dir: Path, adapter: CliAdapter,
                      fixture: str | None = None, model: str | None = None,
                      timeout_s: int = 180, *,
                      workdir_base: str | Path | None = None,
                      coverage: str = "all", bias: str = "mixed",
                      hint: str | None = None
                      ) -> tuple[str, list[dict], RunResult]:
    """Make one LLM call to draft a testcase YAML. Returns (cleaned YAML text,
    parsed documents, raw RunResult). Does not touch testcases/ -- caller
    decides where to write."""
    prompt = build_prompt(skill_dir, fixture, coverage=coverage, bias=bias, hint=hint)
    if workdir_base:
        Path(workdir_base).mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(
        prefix="skilltest_gen_",
        dir=str(workdir_base) if workdir_base else None,
    ))
    try:
        opts = RunOptions(model=model, timeout_s=timeout_s, verbose=False)
        result = adapter.run(prompt, workdir, opts)
    finally:
        remove_tree(workdir)

    if result.crashed:
        raise RuntimeError(result.error or "generation failed")

    yaml_text = extract_yaml(result.final_message or "")
    docs = parse_testcases(yaml_text)  # raises if invalid
    repaired = False
    repaired |= _repair_misplaced_expect_assertions(docs)
    repaired |= _repair_generated_fixture_inputs(docs, skill_dir=skill_dir)
    if repaired:
        yaml_text = _dump_generated_yaml(docs)
    issues = validate_docs(docs, source="generated testcase")
    if issues:
        raise ValueError(
            "generated testcase failed validation:\n"
            + _format_validation_issues(issues)
        )
    return yaml_text, docs, result


# --- generate-fixture: an LLM-written example input project ------------------

_FIXTURE_SCRATCH_PREFIXES = (".codex",)


def _collect_new_files(workdir: Path, skill_dir: Path) -> list[Path]:
    """Files the model wrote, relative to workdir -- excludes the copied-in
    skill folder (read-only reference) and our own scratch files."""
    out = []
    for p in sorted(workdir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(workdir)
        if rel.parts[0] == skill_dir.name:
            continue
        if rel.name.startswith(_FIXTURE_SCRATCH_PREFIXES):
            continue
        out.append(rel)
    return out


def _build_fixture_task(hint: str | None,
                        profile: FixtureProfile | None = None) -> str:
    profile = profile or _GENERIC_FIXTURE_PROFILE
    task = (
        "\n\n=== TASK: create fixture input material ===\n"
        "Create a small but realistic fixture that can be used later as the "
        "`fixture:` input for testing this skill. You must write actual files "
        "in the current working directory; do not merely describe them.\n\n"
        "Skill-derived fixture profile:\n"
        f"{_format_profile_for_prompt(profile)}\n"
        "Rules:\n"
        "- Do not write inside the skill folder. It is read-only reference material.\n"
        "- Keep the fixture compact: usually 3-8 files, enough to exercise the skill.\n"
        "- Include `README.fixture.md` explaining what the fixture represents, "
        "the main files, and one realistic task a user would ask the skill to do.\n"
        "- If the skill reviews or edits code, create a tiny project with at least "
        "one intentional issue and any minimal tests/config needed to understand it.\n"
        "- If the skill processes documents/data/templates, create representative "
        "text/JSON/YAML/Markdown/CSV files with clear labels and expected edge cases.\n"
        "- Prefer UTF-8 text files. Avoid binary placeholders unless the skill "
        "specifically requires binary input.\n"
        "- Do not run tests, builds, lint, package installs, or network commands.\n"
        "- Finish with a concise final message listing the files you created.\n"
    )
    if profile.needs_git:
        task += (
            "\nGit fixture post-processing:\n"
            "- Write normal project files only; do not create `.git` metadata and do not run git commands.\n"
            "- After your files are copied, the harness will run git init/add/commit to create an initial baseline commit.\n"
            "- README.fixture.md should mention the fixture is intended to be used as a git repository after post-processing.\n"
        )
    if hint:
        task += f"\nAdditional user hint:\n{hint}\n"
    return task


def _run_git(git: str, cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [git, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"failed to initialize git fixture: git {' '.join(args)}"
            + (f": {detail}" if detail else "")
        )
    return result


def _ensure_git_fixture(out_dir: Path, profile: FixtureProfile) -> None:
    if not profile.needs_git:
        return

    git = shutil.which("git")
    if not git:
        raise RuntimeError(
            f"fixture profile `{profile.name}` requires a git repository, "
            "but `git` was not found on PATH"
        )

    if not (out_dir / ".git").is_dir():
        _run_git(git, out_dir, ["init"])

    _run_git(git, out_dir, ["config", "user.email", "skill-auto-test@example.invalid"])
    _run_git(git, out_dir, ["config", "user.name", "skill-auto-test"])

    has_head = subprocess.run(
        [git, "rev-parse", "--verify", "HEAD"],
        cwd=out_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).returncode == 0
    if has_head:
        return

    _run_git(git, out_dir, ["add", "-A"])
    status = _run_git(git, out_dir, ["status", "--porcelain"])
    if not status.stdout.strip():
        raise RuntimeError("git fixture profile produced no files to commit")
    _run_git(git, out_dir, ["commit", "-m", "initial fixture baseline"])


def generate_fixture(skill_dir: Path, adapter: CliAdapter, out_dir: Path,
                     hint: str | None = None, model: str | None = None,
                     timeout_s: int = 240, *,
                     workdir_base: str | Path | None = None,
                     replace_existing: bool = False
                     ) -> tuple[list[Path], RunResult]:
    """Have the adapter actually WRITE an example input project (not just
    return text) into a fresh workdir, then copy what it wrote into out_dir.
    Returns (file paths relative to out_dir, raw RunResult). Raises if the
    model errors out or writes nothing."""
    profile = _infer_fixture_profile(skill_dir)
    staged = stage_skill(skill_dir, strategy="flatten", workdir_base=workdir_base)
    try:
        prompt = staged.prompt_prefix + _build_fixture_task(hint, profile)
        opts = RunOptions(model=model, timeout_s=timeout_s, verbose=False)
        result = adapter.run(prompt, staged.workdir, opts)
        if result.crashed:
            raise RuntimeError(result.error or "fixture generation failed")

        rel_files = _collect_new_files(staged.workdir, staged.skill_dir)
        if not rel_files:
            raise RuntimeError("model did not write any fixture files")

        if replace_existing:
            _replace_directory_contents(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for rel in rel_files:
            dst = out_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged.workdir / rel, dst)
        _ensure_git_fixture(out_dir, profile)
        return rel_files, result
    finally:
        staged.cleanup()



