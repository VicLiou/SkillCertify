"""Regenerate a testcase YAML to address quality issues exposed by a
failing trace -- the "skill is fine, testcase is wrong" path.

Distinct from fix-skill, which assumes the skill needs to change. Here
we assume the testcase's assertions are at fault (e.g. asserted a regex
that never matches because the skill writes the verdict into a file,
not into final_message). We feed the failure pattern as a HINT to the
generate-testcase prompt so the LLM produces a corrected version.

This module re-uses runner/core/generate.py's generate_testcase() with
a synthesized hint; it does NOT invoke the architect (the architect is
for skills, not testcases)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..adapters.base import CliAdapter, RunResult
from .fix_skill import FailureGroup, _failures_to_markdown, _load_trace, _summarize_failures
from .generate import _dump_generated_yaml, generate_testcase
from .validate import validate_docs


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]


def _backup_path(target_testcase: Path) -> Path:
    candidate = target_testcase.with_suffix(
        f".bak.{_timestamp()}{target_testcase.suffix}")
    if not candidate.exists():
        return candidate
    for i in range(1, 1000):
        alt = target_testcase.with_suffix(
            f".bak.{_timestamp()}.{i}{target_testcase.suffix}")
        if not alt.exists():
            return alt
    raise FileExistsError(f"could not find a unique backup path near {target_testcase}")


def _load_testcase_docs(testcase_yaml: Path) -> list[dict]:
    docs = [
        d for d in yaml.safe_load_all(testcase_yaml.read_text(encoding="utf-8"))
        if d
    ]
    if not docs:
        raise RuntimeError(f"testcase file is empty or invalid: {testcase_yaml}")
    return docs


def _extract_skill_path_from_docs(docs: list[dict], testcase_yaml: Path) -> Path:
    skill_str = docs[0].get("skill")
    if not skill_str:
        raise RuntimeError(f"testcase {testcase_yaml} has no `skill:` field")
    return Path(skill_str)


def _extract_skill_path(testcase_yaml: Path) -> Path:
    """Read the existing testcase to find which skill it points at, so we
    can re-invoke generate_testcase against the right SKILL.md."""
    return _extract_skill_path_from_docs(
        _load_testcase_docs(testcase_yaml), testcase_yaml)


def _expect_items(doc: dict, key: str) -> list[Any]:
    expect = doc.get("expect")
    if not isinstance(expect, list):
        return []

    values: list[Any] = []
    for item in expect:
        if not isinstance(item, dict) or len(item) != 1:
            continue
        if key not in item:
            continue
        values.append(item[key])
    return values


def _iter_judges(doc: dict) -> list[str]:
    judges: list[str] = []
    for value in _expect_items(doc, "judge"):
        if isinstance(value, str) and value.strip():
            judges.append(value)
    return judges


def _command_run(value: Any) -> str | None:
    if isinstance(value, str):
        run = value.strip()
        return run or None
    if isinstance(value, dict):
        run = value.get("run")
        if isinstance(run, str) and run.strip():
            return run
    return None


def _iter_commands(doc: dict) -> list[Any]:
    commands: list[Any] = []
    for value in _expect_items(doc, "command"):
        if _command_run(value):
            commands.append(value)
    return commands


def _format_existing_judges_for_hint(existing_docs: list[dict]) -> str:
    lines: list[str] = []
    for i, doc in enumerate(existing_docs, 1):
        judges = _iter_judges(doc)
        if not judges:
            continue
        name = doc.get("name") or f"document {i}"
        lines.append(f"- {name}:")
        lines.extend(f"  - {judge}" for judge in judges)
    return "\n".join(lines)


def _format_existing_commands_for_hint(existing_docs: list[dict],
                                       failed_by_case: dict[str, set[str]]) -> str:
    lines: list[str] = []
    for i, doc in enumerate(existing_docs, 1):
        name = str(doc.get("name") or f"document {i}")
        failed = failed_by_case.get(name, set())
        commands = []
        for command in _iter_commands(doc):
            run = _command_run(command)
            if run and f"command:{run}" not in failed:
                commands.append(run)
        if not commands:
            continue
        lines.append(f"- {name}:")
        lines.extend(f"  - {command}" for command in commands)
    return "\n".join(lines)


def _expect_has_key(doc: dict, key: str) -> bool:
    if key == "judge":
        return bool(_iter_judges(doc))
    if key == "command":
        return bool(_iter_commands(doc))
    return bool(_expect_items(doc, key))


def _values_by_name_and_index(
        existing_docs: list[dict], key: str,
        ) -> tuple[dict[str, list[Any]], list[tuple[str | None, list[Any]]]]:
    by_name: dict[str, list[Any]] = {}
    by_index: list[tuple[str | None, list[Any]]] = []
    extractor = _iter_judges if key == "judge" else _iter_commands
    for doc in existing_docs:
        values = extractor(doc)
        name = doc.get("name")
        source_name = name if isinstance(name, str) and name else None
        by_index.append((source_name, values))
        if source_name:
            by_name[source_name] = values
    return by_name, by_index


def _failed_command_labels_by_case(groups: list[FailureGroup]) -> dict[str, set[str]]:
    failed: dict[str, set[str]] = {}
    for group in groups:
        if not group.assertion.startswith("command:"):
            continue
        for case in group.cases:
            failed.setdefault(case, set()).add(group.assertion)
    return failed


def _safe_commands_for_doc(doc_name: str | None, values: list[Any],
                           failed_by_case: dict[str, set[str]]) -> list[Any]:
    if not doc_name:
        return values
    failed = failed_by_case.get(doc_name, set())
    safe = []
    for value in values:
        run = _command_run(value)
        if run and f"command:{run}" in failed:
            continue
        safe.append(value)
    return safe


def _restore_assertions(generated_docs: list[dict], existing_docs: list[dict],
                        key: str, *,
                        failed_commands_by_case: dict[str, set[str]] | None = None) -> bool:
    """Preserve optional checks when fix-testcase regenerates YAML.

    The LLM can make deterministic assertions less brittle while accidentally
    dropping gated checks. Match by testcase name first, then by document order.
    """
    failed_commands_by_case = failed_commands_by_case or {}
    by_name, by_index = _values_by_name_and_index(existing_docs, key)

    changed = False
    for i, doc in enumerate(generated_docs):
        if not isinstance(doc, dict) or _expect_has_key(doc, key):
            continue

        values: list[Any] = []
        name = doc.get("name")
        name_str = name if isinstance(name, str) else None
        source_name = name_str
        if name_str:
            values = by_name.get(name_str, [])
        if not values and i < len(by_index):
            source_name, values = by_index[i]
        if key == "command":
            values = _safe_commands_for_doc(source_name, values, failed_commands_by_case)
        if not values:
            continue

        expect = doc.setdefault("expect", [])
        if not isinstance(expect, list):
            continue
        expect.extend({key: value} for value in values)
        changed = True
    return changed


def _format_validation_issues(issues) -> str:
    lines = []
    for issue in issues:
        line = f"[{issue.severity}] {issue.where}: {issue.message}"
        if issue.hint:
            line += f" (hint: {issue.hint})"
        lines.append(line)
    return "\n".join(lines)


def _failures_to_hint(failures_md: str, extra_hint: str | None,
                      existing_docs: list[dict] | None = None,
                      failed_commands_by_case: dict[str, set[str]] | None = None) -> str:
    """Wrap failure evidence in a hint that tells generate-testcase to
    actively correct broken assertion patterns."""
    existing_docs = existing_docs or []
    failed_commands_by_case = failed_commands_by_case or {}
    pieces = [
        "Regenerate the testcase because the failure evidence suggests the "
        "testcase assertions are too brittle or misaligned with the skill's "
        "actual correct behavior.",
        "",
        failures_md,
        "",
        "Repair rules:",
        "1. Replace brittle assertions with stable checks against real "
        "user-visible behavior.",
        "2. Do not assert internal SKILL.md placeholders or variable names.",
        "3. Keep `reads_file` only for files the scenario must definitely read.",
        "4. Prefer `output_contains` for content that may appear in final text, "
        "stdout, or generated files.",
    ]
    judge_hint = _format_existing_judges_for_hint(existing_docs)
    if judge_hint:
        pieces += [
            "",
            "The original testcase had semantic `judge:` assertions. Preserve "
            "them on the corresponding regenerated testcase documents unless "
            "you replace them with an equivalent positive semantic criterion:",
            judge_hint,
        ]
    command_hint = _format_existing_commands_for_hint(existing_docs, failed_commands_by_case)
    if command_hint:
        pieces += [
            "",
            "The original testcase had dynamic `command:` assertions that did "
            "not appear as failed commands in the trace. Preserve them on the "
            "corresponding regenerated testcase documents unless you replace "
            "them with an equivalent command check:",
            command_hint,
        ]
    if extra_hint:
        pieces += ["", f"Additional user hint: {extra_hint}"]
    return "\n".join(pieces)


def fix_testcase(target_testcase: Path, trace_path: Path,
                 adapter: CliAdapter, *,
                 case_filter: str | None = None,
                 coverage: str = "all", bias: str = "mixed",
                 extra_hint: str | None = None,
                 model: str | None = None,
                 timeout_s: int = 240,
                 workdir_base: str | Path | None = None,
                 apply: bool = False,
                 ) -> tuple[str, list[dict], RunResult, Path | None]:
    """Regenerate target_testcase based on failures from trace_path.

    Returns (yaml_text, parsed_docs, raw_result, backup_path):
      - yaml_text: the new testcase YAML text
      - parsed_docs: parsed documents (one per testcase)
      - raw_result: RunResult from the generate call
      - backup_path: where the original was backed up to, if apply=True
        and the file existed; None otherwise.

    apply=False (default) leaves target_testcase untouched -- caller can
    print the new YAML for review. apply=True backs up the original to
    `<name>.bak.<timestamp>.yaml` and writes the new version in place."""
    if not target_testcase.is_file():
        raise FileNotFoundError(f"testcase not found: {target_testcase}")

    existing = _load_testcase_docs(target_testcase)
    skill_dir = _extract_skill_path_from_docs(existing, target_testcase)
    if not skill_dir.is_dir():
        raise FileNotFoundError(
            f"testcase points at skill that doesn't exist: {skill_dir}")

    trace = _load_trace(trace_path)
    groups = _summarize_failures(trace, target_case_substr=case_filter)
    if not groups:
        raise RuntimeError("no failed runs found in trace -- nothing to fix")

    failures_md = _failures_to_markdown(groups)
    failed_commands_by_case = _failed_command_labels_by_case(groups)
    hint = _failures_to_hint(
        failures_md, extra_hint, existing, failed_commands_by_case)

    # Extract fixture from existing testcase so the regenerated one keeps it.
    fixture = None
    for doc in existing:
        if doc and "fixture" in doc:
            fixture = doc["fixture"]
            break

    yaml_text, docs, result = generate_testcase(
        skill_dir, adapter, fixture=fixture, model=model,
        timeout_s=timeout_s, workdir_base=workdir_base,
        coverage=coverage, bias=bias, hint=hint,
    )
    restored = False
    restored |= _restore_assertions(docs, existing, "judge")
    restored |= _restore_assertions(
        docs, existing, "command", failed_commands_by_case=failed_commands_by_case)
    if restored:
        issues = validate_docs(docs, source="regenerated testcase")
        if issues:
            raise ValueError(
                "regenerated testcase failed validation after restoring assertions:\n"
                + _format_validation_issues(issues)
            )
        yaml_text = _dump_generated_yaml(docs)

    backup_path: Path | None = None
    if apply:
        backup_path = _backup_path(target_testcase)
        backup_path.write_text(
            target_testcase.read_text(encoding="utf-8"), encoding="utf-8")
        target_testcase.write_text(yaml_text.rstrip() + "\n", encoding="utf-8")

    return yaml_text, docs, result, backup_path

