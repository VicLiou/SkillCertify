"""Fast, no-LLM validation of testcase YAML files.

Catches the classes of mistakes that would otherwise only surface after
the adapter has started up (potentially minutes later, after API calls
have been made):
  - YAML syntax errors
  - missing required fields (name / skill / input)
  - skill: pointing at a non-existent folder
  - fixture: pointing at a non-existent path
  - expect: items malformed (not single-key dicts, unknown assertion keys)
  - load_strategy outside the three allowed values
  - common typos in assertion keys (suggests the closest valid one)

Used by:
  - `skill-test validate testcases/foo.yaml` -- standalone preflight
  - `cmd_run` could call this automatically, but doesn't (yet): we want
    `validate` to also work in isolation as a CI-friendly linter.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# Keep in sync with runner/core/assertions.py's `if key == ...` chain.
# If you add a new assertion key, add it here too or `validate` will flag
# legitimate usage as "unknown".
KNOWN_ASSERTION_KEYS: tuple[str, ...] = (
    "exit_code",
    "file_exists", "file_absent",
    "output_contains", "final_contains", "stdout_contains",
    "regex",
    "reads_file",
    "tool_used",
    "flow_contains", "flow_equals",
    "max_latency_ms",
    "command",
    "judge",
)

# Top-level fields a testcase YAML doc may have. Anything else triggers a
# warning (could be a typo). `sandbox` is explicitly blacklisted because
# adapters control it.
KNOWN_TOPLEVEL_FIELDS: tuple[str, ...] = (
    "name", "skill", "input",
    "runs", "load_strategy", "fixture", "model", "timeout_s",
    "extra_args", "expect",
)

VALID_LOAD_STRATEGIES = ("flatten", "progressive", "scripts-only")

_AMBIGUOUS_WORKSPACE_TERMS = (
    "current workspace",
    "current working directory",
    "working directory",
    "\u76ee\u524d workspace",
    "\u76ee\u524d\u5de5\u4f5c\u76ee\u9304",
    "\u7576\u524d workspace",
    "\u7576\u524d\u5de5\u4f5c\u76ee\u9304",
)

_CONTAINS_ASSERTION_KEYS = ("output_contains", "final_contains", "stdout_contains")
_TEMPLATE_PLACEHOLDER_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
_TEMPLATE_PLACEHOLDER_SUFFIXES = (
    "_COUNT",
    "_TOTAL",
    "_LIST",
    "_JSON",
    "_TEXT",
    "_VALUE",
    "_NUMBER",
    "_TABLE",
)
_ALLOWED_UPPERCASE_MARKERS = {
    "PROJECT_SCOPE_REVIEW_ONLY",
    "WORKTREE_UNCOMMITTED",
    "COMMITTED_DIFF",
    "UNCOMMITTED_TARGET",
    "BASE_COMMIT",
    "DIFF_SOURCE",
    "REVIEW_SCOPE",
    "NOT_APPLICABLE",
    "EXCLUDE_UNTRACKED",
}



@dataclass
class ValidationIssue:
    severity: str       # "error" | "warning"
    where: str          # human-readable location, e.g. "doc 2 / expect[3]"
    message: str
    hint: str | None = None


def _suggest(typo: str, valid: tuple[str, ...]) -> str | None:
    """Return the closest valid key, if any look like the typo."""
    matches = difflib.get_close_matches(typo, valid, n=1, cutoff=0.6)
    return matches[0] if matches else None


def _fixture_copied_name(fixture: str) -> str:
    normalized = fixture.replace("\\", "/").rstrip("/")
    if not normalized:
        return "fixture"
    return normalized.rsplit("/", 1)[-1] or "fixture"


def _fixture_original_path_variants(fixture: str) -> set[str]:
    return {
        variant for variant in {
            fixture,
            fixture.replace("\\", "/"),
            fixture.replace("/", "\\"),
        }
        if variant
    }


def _iter_string_assertions(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _looks_like_template_placeholder(value: str) -> bool:
    marker = value.strip().strip("`")
    if marker in _ALLOWED_UPPERCASE_MARKERS:
        return False
    return (
        bool(_TEMPLATE_PLACEHOLDER_RE.fullmatch(marker))
        and marker.endswith(_TEMPLATE_PLACEHOLDER_SUFFIXES)
    )



def _validate_doc(doc: dict, i: int) -> list[ValidationIssue]:
    where = f"doc {i + 1}"
    issues: list[ValidationIssue] = []

    if not isinstance(doc, dict):
        return [ValidationIssue("error", where,
                                f"top level is not a mapping (got {type(doc).__name__})")]

    # Required fields.
    for field in ("name", "skill", "input"):
        if field not in doc:
            issues.append(ValidationIssue(
                "error", where, f"missing required field `{field}`"))

    # Forbidden field.
    if "sandbox" in doc:
        issues.append(ValidationIssue(
            "warning", where,
            "do not set `sandbox`; the adapter chooses it. Remove this field."))

    # Unknown top-level field warnings.
    for k in doc:
        if k in KNOWN_TOPLEVEL_FIELDS or k == "sandbox":
            continue
        sug = _suggest(k, KNOWN_TOPLEVEL_FIELDS)
        hint = f"did you mean `{sug}`?" if sug else None
        issues.append(ValidationIssue(
            "warning", where, f"unknown top-level field `{k}`", hint=hint))

    # skill: must point at an existing folder with SKILL.md.
    skill_str = doc.get("skill")
    if isinstance(skill_str, str) and skill_str:
        skill_path = Path(skill_str)
        if not skill_path.is_dir():
            issues.append(ValidationIssue(
                "error", where,
                f"skill folder not found: {skill_str}",
                hint="run `skill-test list` to see available skills"))
        elif not (skill_path / "SKILL.md").is_file():
            issues.append(ValidationIssue(
                "error", where,
                f"{skill_str} is a folder but has no SKILL.md"))

    # fixture: if present, must exist and the prompt should target its
    # runtime copy instead of the harness workspace or original source path.
    fixture = doc.get("fixture")
    if isinstance(fixture, str) and fixture:
        if not Path(fixture).exists():
            issues.append(ValidationIssue(
                "error", where,
                f"fixture path not found: {fixture}",
                hint="if this skill doesn't need an external fixture, "
                     "delete the `fixture:` line entirely"))

        input_text = doc.get("input")
        if isinstance(input_text, str):
            copied_name = _fixture_copied_name(fixture)
            input_lower = input_text.lower()
            mentions_copied = copied_name.lower() in input_lower
            if (any(term in input_lower for term in _AMBIGUOUS_WORKSPACE_TERMS)
                    and not mentions_copied):
                issues.append(ValidationIssue(
                    "warning", where,
                    "input refers to the current workspace while a fixture is present",
                    hint=f"mention the copied fixture directory `{copied_name}` explicitly, "
                         f"for example PROJECT_PATH=./{copied_name}"))

            if fixture != copied_name:
                variants = _fixture_original_path_variants(fixture)
                if any(variant in input_text for variant in variants):
                    issues.append(ValidationIssue(
                        "warning", where,
                        "input embeds the original fixture path instead of the copied runtime directory",
                        hint=f"use `./{copied_name}` or files below it in `input`; "
                             "keep the original path only in the top-level `fixture:` field"))

    # load_strategy must be one of three values.
    ls = doc.get("load_strategy", "progressive")
    if ls not in VALID_LOAD_STRATEGIES:
        sug = _suggest(ls, VALID_LOAD_STRATEGIES)
        hint = f"did you mean `{sug}`?" if sug else \
               f"must be one of {', '.join(VALID_LOAD_STRATEGIES)}"
        issues.append(ValidationIssue(
            "error", where, f"invalid load_strategy: {ls!r}", hint=hint))

    # runs: positive integer
    runs = doc.get("runs")
    if runs is not None:
        if not isinstance(runs, int) or runs < 1:
            issues.append(ValidationIssue(
                "error", where,
                f"runs must be a positive integer, got {runs!r}"))

    # timeout_s: positive integer
    ts = doc.get("timeout_s")
    if ts is not None:
        if not isinstance(ts, int) or ts < 1:
            issues.append(ValidationIssue(
                "error", where,
                f"timeout_s must be a positive integer, got {ts!r}"))

    # expect: required non-empty list of single-key dicts with known keys.
    if "expect" not in doc:
        issues.append(ValidationIssue(
            "error", where,
            "missing required field `expect`",
            hint="add at least `expect: [{exit_code: 0}]` plus one behavior-specific assertion"))
    else:
        expect = doc.get("expect")
        if not isinstance(expect, list):
            issues.append(ValidationIssue(
                "error", where,
                f"expect must be a list (got {type(expect).__name__})"))
        elif not expect:
            issues.append(ValidationIssue(
                "error", where,
                "expect must contain at least one assertion",
                hint="add `exit_code: 0` and one assertion that proves the skill did the requested work"))
        else:
            has_exit_code = False
            for j, item in enumerate(expect):
                item_where = f"{where} / expect[{j + 1}]"
                if not isinstance(item, dict):
                    issues.append(ValidationIssue(
                        "error", item_where,
                        f"each expect entry must be a single-key mapping "
                        f"(got {type(item).__name__})"))
                    continue
                if len(item) != 1:
                    issues.append(ValidationIssue(
                        "error", item_where,
                        f"each expect entry must be a single-key mapping "
                        f"(got {len(item)} keys: {list(item)})"))
                    continue
                key = next(iter(item))
                if key == "exit_code":
                    has_exit_code = True
                if key not in KNOWN_ASSERTION_KEYS:
                    sug = _suggest(key, KNOWN_ASSERTION_KEYS)
                    hint = (f"did you mean `{sug}`?" if sug else
                            f"see docs/assertions.md for the full list")
                    issues.append(ValidationIssue(
                        "error", item_where,
                        f"unknown assertion key `{key}`", hint=hint))
                    continue

                if key in _CONTAINS_ASSERTION_KEYS:
                    suspicious = [
                        value for value in _iter_string_assertions(item[key])
                        if _looks_like_template_placeholder(value)
                    ]
                    if suspicious:
                        issues.append(ValidationIssue(
                            "warning", item_where,
                            "contains assertion looks like an internal template placeholder",
                            hint="assert rendered headings, filenames, statuses, or user-visible text instead of "
                                 f"{', '.join(suspicious)}"))
            if not has_exit_code:
                issues.append(ValidationIssue(
                    "warning", where,
                    "expect has no explicit `exit_code` assertion",
                    hint="the runner implicitly treats non-zero CLI exits as failures; add `exit_code: 0` for clearer reports"))
    return issues


def validate_docs(docs: list[object], *, source: str = "generated YAML") -> list[ValidationIssue]:
    """Validate already-parsed testcase YAML documents.

    This is shared by `validate_file` and generator preflight so generated
    testcase YAML is rejected before it is written to disk.
    """
    non_empty = [d for d in docs if d]
    if not non_empty:
        return [ValidationIssue(
            "error", source,
            "file has no testcase documents",
            hint="a testcase YAML must contain at least one document with "
                 "`name:`, `skill:`, `input:`")]

    issues: list[ValidationIssue] = []
    for i, doc in enumerate(non_empty):
        issues.extend(_validate_doc(doc, i))
    return issues


def validate_file(path: Path) -> list[ValidationIssue]:
    """Validate one testcase YAML file. Returns a flat list of issues
    across all documents in the file. Empty list = clean."""
    if not path.is_file():
        return [ValidationIssue("error", str(path), "file not found")]
    try:
        text = path.read_text(encoding="utf-8")
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError as e:
        return [ValidationIssue(
            "error", str(path),
            f"YAML syntax error: {e}",
            hint="check indentation, quoting, and `---` separators")]

    return validate_docs(docs, source=str(path))
