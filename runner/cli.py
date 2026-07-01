"""Entry point: load testcase YAML files, run them, print + save the report.

Usage:
    skill-test testcases/example.yaml
    skill-test testcases/*.yaml --adapter codex --out report.json
    skill-test tc.yaml --runs 30 --model gpt-5.4
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from .adapters import ADAPTERS
from .core.architect import resolve_architect_skill
from .core.check_skill import check_skill
from .core.diagnose import doctor as _doctor, list_local
from .core.init_project import init_project
from .core.validate import validate_file
from .core.fix_skill import fix_skill
from .core.fix_testcase import fix_testcase
from .core.generate import generate_fixture, generate_testcase
from .core.iterate import iterate, render_summary
from .core.judge import LlmJudge
from .core.new_skill import new_skill, skill_name_error
from .core.report import print_final_report, print_report, summarize, write_json, write_trace
from .core.runner import TestCase, run_testcase


DEFAULT_WORKDIR_BASE = ".work"

_BOOTSTRAP_JUDGE_GENERATION_HINT = (
    "Bootstrap is running the final smoke test with --judge. "
    "Generate every testcase with at least one `judge:` assertion in `expect` "
    "so the semantic judge is actually exercised during bootstrap. Keep "
    "deterministic assertions such as `exit_code` and `output_contains` too. "
    "Phrase each judge criterion as the positive behavior that should pass."
)

_BOOTSTRAP_EXEC_GENERATION_HINT = (
    "Bootstrap is running the final smoke test with --allow-exec. "
    "When the skill is expected to create runnable code, scripts, or other "
    "machine-checkable artifacts, include focused `command:` assertions in "
    "`expect` to verify them. Do not add command checks to pure review/report "
    "or mandatory-stop cases unless there is a deterministic command to run."
)


def _append_generation_hint(base: str | None, extra: str | None) -> str | None:
    if not extra:
        return base
    if not base:
        return extra
    return base.rstrip() + "\n" + extra


def _doc_has_nonempty_assertion(doc: dict, key: str) -> bool:
    expect = doc.get("expect")
    if not isinstance(expect, list):
        return False
    for item in expect:
        if not isinstance(item, dict) or key not in item:
            continue
        value = item[key]
        if key == "judge":
            return isinstance(value, str) and bool(value.strip())
        return True
    return False


def _docs_missing_assertion(docs: list[dict], key: str) -> list[str]:
    missing: list[str] = []
    for i, doc in enumerate(docs, 1):
        if not isinstance(doc, dict) or _doc_has_nonempty_assertion(doc, key):
            continue
        name = doc.get("name") if isinstance(doc, dict) else None
        missing.append(str(name or f"document {i}"))
    return missing


def load_cases(paths: list[str]) -> list[TestCase]:
    """Read one or more testcase YAML files and parse into TestCase objects.

    Raises a CLI-friendly RuntimeError (not a bare FileNotFoundError) when a
    path is missing or the YAML is malformed -- callers print and exit cleanly
    rather than dumping a traceback at the user."""
    cases: list[TestCase] = []
    for p in paths:
        path = Path(p)
        if not path.is_file():
            raise RuntimeError(
                f"testcase file not found: {p}\n"
                f"tip: see what's available with `skill-test list`"
            )
        try:
            text = path.read_text(encoding="utf-8")
            docs = list(yaml.safe_load_all(text))
        except yaml.YAMLError as e:
            raise RuntimeError(
                f"failed to parse {p} as YAML: {e}\n"
                f"tip: check syntax with `skill-test validate {p}`; "
                f"for raw YAML parser details, `python -c \"import yaml; "
                f"yaml.safe_load_all(open({p!r}).read())\"` will show the line"
            )
        for d in docs:
            if d:
                try:
                    cases.append(TestCase.from_dict(d))
                except KeyError as e:
                    raise RuntimeError(
                        f"{p} is missing required field {e}\n"
                        f"tip: every testcase needs `name`, `skill`, `input` "
                        f"-- see docs/assertions.md for the spec"
                    )
    return cases


def _validate_cases_for_run(paths: list[str]) -> bool:
    """Return True when testcase files have no static validation errors.

    Warnings are allowed for backwards compatibility with existing testcase
    files; errors stop before any adapter starts spending time or tokens.
    """
    ok = True
    for tc_path in paths:
        path = Path(tc_path)
        errors = [i for i in validate_file(path) if i.severity == "error"]
        if not errors:
            continue
        ok = False
        print(f"error: {path} failed validation ({len(errors)} error(s))",
              file=sys.stderr)
        for issue in errors:
            print(f"  [{issue.where}] {issue.message}", file=sys.stderr)
            if issue.hint:
                print(f"      hint: {issue.hint}", file=sys.stderr)
    if not ok:
        joined = " ".join(paths)
        print(f"tip: run `skill-test validate {joined}` for full details",
              file=sys.stderr)
    return ok


def _force_utf8_console() -> None:
    """Avoid UnicodeEncodeError when logging non-ASCII (e.g. Chinese agent
    messages) to a legacy cp950/cp1252 Windows console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - older/odd streams; _log has a fallback
            pass


def _print_case_banner(idx: int, total: int, name: str) -> None:
    """Visual divider between cases in a multi-case run. Lets the reader
    scan for case boundaries without parsing dense per-line prefixes.
    Plain ASCII so it survives any console / font on Windows."""
    label = f" case {idx}/{total}: {name} "
    bar = "=" * max(8, 60 - len(label))
    print(f"\n==={label}{bar}", file=sys.stderr, flush=True)


def cmd_generate(argv: list[str]) -> int:
    """`generate`: one LLM call to draft a testcase YAML from a skill's
    SKILL.md, saved to testcases/. Dev-time convenience -- review the output
    before trusting it; this is NOT part of the deterministic test path."""
    ap = argparse.ArgumentParser(
        prog="skill-test generate",
        description="Use an LLM to draft a testcase YAML from a skill's SKILL.md",
    )
    ap.add_argument("skill", help="skill folder path, e.g. skills/my-skill")
    ap.add_argument("--adapter", default="claude", choices=sorted(ADAPTERS),
                    help="which CLI drafts the YAML (default: claude)")
    ap.add_argument("--binary", default=None, help="override CLI binary path")
    ap.add_argument("--model", default=None, help="model for the drafting LLM")
    ap.add_argument("--fixture", default=None,
                    help="external fixture path to reference in the generated testcase")
    ap.add_argument("--coverage", default="all",
                    choices=("all", "happy", "minimal"),
                    help="how many testcases to produce -- all: one per major "
                         "branch in SKILL.md (default); happy: just one happy "
                         "path; minimal: 2-3 (1 happy + 1-2 edges)")
    ap.add_argument("--bias", default="mixed",
                    choices=("positive", "negative", "mixed"),
                    help="which scenarios to emphasize -- positive: only "
                         "happy-path inputs; negative: only inputs that should "
                         "trigger HALT/refuse; mixed (default): both")
    ap.add_argument("--hint", default=None,
                    help="extra free-form steering for the LLM (e.g. 'focus on "
                         "Chinese input', 'skip fixture-related cases'). "
                         "Overrides coverage/bias if they conflict.")
    ap.add_argument("--out", default=None,
                    help="output YAML path (default: testcases/<skill-name>.yaml)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite --out if it already exists")
    ap.add_argument("--workdir-base", default=DEFAULT_WORKDIR_BASE,
                    help="workdir base for generation "
                         f"(default: {DEFAULT_WORKDIR_BASE}; override if needed)")
    ap.add_argument("--timeout-s", type=_positive_int, default=180)
    args = ap.parse_args(argv)

    skill_dir = Path(args.skill)
    if not skill_dir.is_dir():
        print(f"skill folder not found: {skill_dir}", file=sys.stderr)
        return 2
    if not (skill_dir / "SKILL.md").exists():
        print(f"SKILL.md not found in {skill_dir}", file=sys.stderr)
        return 2
    if args.fixture and not Path(args.fixture).exists():
        print(f"fixture not found: {args.fixture}", file=sys.stderr)
        return 2

    out_path = Path(args.out) if args.out else Path("testcases") / f"{skill_dir.name}.yaml"
    if out_path.exists() and not args.force:
        print(f"{out_path} already exists (use --force to overwrite)", file=sys.stderr)
        return 2

    adapter_cls = ADAPTERS[args.adapter]
    adapter = adapter_cls(binary=args.binary) if args.binary else adapter_cls()

    print(f"generating testcase for {skill_dir} (adapter={args.adapter}, "
          f"coverage={args.coverage}, bias={args.bias})...",
          file=sys.stderr, flush=True)
    t0 = time.monotonic()
    try:
        yaml_text, docs, result = generate_testcase(
            skill_dir, adapter, fixture=args.fixture, model=args.model,
            timeout_s=args.timeout_s, workdir_base=args.workdir_base,
            coverage=args.coverage, bias=args.bias, hint=args.hint,
        )
    except Exception as e:  # noqa: BLE001 - surface any generation failure cleanly
        print(f"generation failed: {e}", file=sys.stderr)
        return 1
    elapsed = int(time.monotonic() - t0)

    tok_note = ""
    tok = result.tokens
    if isinstance(tok, dict):
        total = tok.get("total_tokens") or ((tok.get("input_tokens") or 0)
                                            + (tok.get("output_tokens") or 0))
        if total:
            tok_note = f", {total} tokens"
    print(f"done ({elapsed}s{tok_note})", file=sys.stderr, flush=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml_text.rstrip() + "\n", encoding="utf-8")
    print(f"wrote {out_path} ({len(docs)} testcase(s))")
    print(f"\n>>> 撱箄降?犖撌交炎?乩?????:\n"
          f"    skill-test {out_path.as_posix()} --adapter claude --runs 1",
          file=sys.stderr)
    return 0


def cmd_generate_fixture(argv: list[str]) -> int:
    """`generate-fixture`: have an LLM actually WRITE an example input project
    (not just describe it) for a skill that needs external test material,
    saved to fixtures/. Dev-time convenience, same caveats as `generate`."""
    ap = argparse.ArgumentParser(
        prog="skill-test generate-fixture",
        description="Use an LLM to write an example input project (fixture) for a skill",
    )
    ap.add_argument("skill", help="skill folder path, e.g. skills/my-skill")
    ap.add_argument("--adapter", default="claude", choices=sorted(ADAPTERS),
                    help="which CLI writes the fixture (default: claude)")
    ap.add_argument("--binary", default=None, help="override CLI binary path")
    ap.add_argument("--model", default=None, help="model for the drafting LLM")
    ap.add_argument("--hint", default=None,
                    help="extra instruction for what the example should contain")
    ap.add_argument("--out", default=None,
                    help="output folder (default: fixtures/<skill-name>-sample)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite --out if it already exists and is non-empty")
    ap.add_argument("--workdir-base", default=DEFAULT_WORKDIR_BASE,
                    help="workdir base for fixture generation "
                         f"(default: {DEFAULT_WORKDIR_BASE}; override if needed)")
    ap.add_argument("--timeout-s", type=_positive_int, default=240)
    args = ap.parse_args(argv)

    skill_dir = Path(args.skill)
    if not skill_dir.is_dir():
        print(f"skill folder not found: {skill_dir}", file=sys.stderr)
        return 2
    if not (skill_dir / "SKILL.md").exists():
        print(f"SKILL.md not found in {skill_dir}", file=sys.stderr)
        return 2

    out_dir = Path(args.out) if args.out else Path("fixtures") / f"{skill_dir.name}-sample"
    if out_dir.exists() and any(out_dir.iterdir()) and not args.force:
        print(f"{out_dir} already exists and is non-empty (use --force to overwrite)",
              file=sys.stderr)
        return 2

    adapter_cls = ADAPTERS[args.adapter]
    adapter = adapter_cls(binary=args.binary) if args.binary else adapter_cls()

    print(f"generating example fixture for {skill_dir} (adapter={args.adapter})...",
          file=sys.stderr, flush=True)
    t0 = time.monotonic()
    try:
        rel_files, result = generate_fixture(
            skill_dir, adapter, out_dir, hint=args.hint, model=args.model,
            timeout_s=args.timeout_s, workdir_base=args.workdir_base,
            replace_existing=args.force,
        )
    except Exception as e:  # noqa: BLE001 - surface any generation failure cleanly
        print(f"fixture generation failed: {e}", file=sys.stderr)
        return 1
    elapsed = int(time.monotonic() - t0)

    tok_note = ""
    tok = result.tokens
    if isinstance(tok, dict):
        total = tok.get("total_tokens") or ((tok.get("input_tokens") or 0)
                                            + (tok.get("output_tokens") or 0))
        if total:
            tok_note = f", {total} tokens"
    print(f"done ({elapsed}s{tok_note})", file=sys.stderr, flush=True)

    print(f"wrote {out_dir}/ ({len(rel_files)} file(s)):")
    for rel in rel_files:
        print(f"  - {rel.as_posix()}")
    print(f"\n>>> ?乩?靘隞交??靘?Ｙ? testcase:\n"
          f"    skill-test generate {skill_dir.as_posix()} --fixture {out_dir.as_posix()}",
          file=sys.stderr)
    return 0


def cmd_bootstrap(argv: list[str]) -> int:
    """`bootstrap`: chain generate-fixture -> generate -> run, for going from
    a bare skill folder to a first stability report in one command."""
    ap = argparse.ArgumentParser(
        prog="skill-test bootstrap",
        description="generate-fixture -> generate -> run, chained for one skill",
    )
    ap.add_argument("skill", help="skill folder path, e.g. skills/my-skill")
    ap.add_argument("--gen-adapter", default="claude", choices=sorted(ADAPTERS),
                    help="which CLI drafts the fixture/testcase (default: claude)")
    ap.add_argument("--gen-binary", default=None,
                    help="override CLI binary path for --gen-adapter")
    ap.add_argument("--adapter", default="claude", choices=sorted(ADAPTERS),
                    help="which CLI runs the final test (default: claude -- "
                         "fastest and least sandbox setup; use codex/codex-tui "
                         "explicitly when needed)")
    ap.add_argument("--binary", default=None,
                    help="override CLI binary path for --adapter")
    ap.add_argument("--model", default=None, help="model for fixture/testcase generation")
    ap.add_argument("--run-model", default=None,
                    help="model for the final smoke/stability test step")
    ap.add_argument("--no-fixture", action="store_true",
                    help="skip generate-fixture (skill doesn't need external material)")
    ap.add_argument("--hint", default=None,
                    help="extra instruction passed to BOTH generate-fixture and "
                         "generate (free-form steering text)")
    ap.add_argument("--coverage", default="all",
                    choices=("all", "happy", "minimal"),
                    help="testcase coverage (see `generate --help`)")
    ap.add_argument("--bias", default="mixed",
                    choices=("positive", "negative", "mixed"),
                    help="testcase scenario bias (see `generate --help`)")
    ap.add_argument("--fixture-out", default=None,
                    help="fixture output folder (default: fixtures/<skill-name>-sample)")
    ap.add_argument("--testcase-out", default=None,
                    help="testcase YAML output path (default: testcases/<skill-name>.yaml)")
    ap.add_argument("--runs", type=_positive_int, default=1,
                    help="runs for the final test step (default: 1, a smoke test)")
    ap.add_argument("--require-stable-flow", action="store_true",
                    help="fail the final test step when any case uses multiple tool flows; requires --runs >= 2")
    ap.add_argument("--allow-exec", action="store_true",
                    help="ask testcase generation to include suitable `command:` assertions and allow them to execute in the final test step")
    ap.add_argument("--judge", action="store_true",
                    help="ask testcase generation to include `judge:` assertions and enable LLM-as-judge in the final test step")
    ap.add_argument("--judge-adapter", default="claude", choices=sorted(ADAPTERS))
    ap.add_argument("--judge-model", default=None,
                    help="model for the final test step's judge")
    ap.add_argument("--judge-binary", default=None,
                    help="binary for the final test step's judge adapter")
    ap.add_argument("--workdir-base", default=DEFAULT_WORKDIR_BASE,
                    help="workdir base for generation and final test steps "
                         f"(default: {DEFAULT_WORKDIR_BASE})")
    # Flags forwarded to the final test step. The smoke run is the most
    # likely place for a first-time `bootstrap` user to hit a failure, so
    # we want to make inspecting that failure easy.
    ap.add_argument("--keep-failed", action="store_true",
                    help="leave the workdir of any FAILED run on disk so you "
                         "can inspect what the skill actually produced "
                         "(useful when the smoke run fails)")
    ap.add_argument("--trace", default=None,
                    help="write per-run detail JSON for the final test step "
                         "(same as `run`'s --trace); helpful for debugging")
    ap.add_argument("--debug", action="store_true",
                    help="extra-verbose logs in the final test step "
                         "(raw commands, agent messages, tool outputs)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite existing fixture/testcase outputs")
    ap.add_argument("--timeout-s", type=_positive_int, default=240,
                    help="timeout for each generation call (fixture/testcase)")
    args = ap.parse_args(argv)

    skill_dir = Path(args.skill)
    if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").exists():
        print(f"skill folder (with SKILL.md) not found: {skill_dir}", file=sys.stderr)
        return 2
    if args.require_stable_flow and args.runs < 2:
        print("error: --require-stable-flow needs --runs >= 2", file=sys.stderr)
        return 2

    gen_adapter_cls = ADAPTERS[args.gen_adapter]
    gen_adapter = (gen_adapter_cls(binary=args.gen_binary) if args.gen_binary
                   else gen_adapter_cls())

    fixture_path: str | None = None
    if not args.no_fixture:
        fixture_out = (Path(args.fixture_out) if args.fixture_out
                       else Path("fixtures") / f"{skill_dir.name}-sample")
        if fixture_out.exists() and any(fixture_out.iterdir()) and not args.force:
            print(f"{fixture_out} already exists and is non-empty (use --force)",
                  file=sys.stderr)
            return 2
        print("[1/3] generating example fixture...", file=sys.stderr, flush=True)
        t0 = time.monotonic()
        try:
            rel_files, _ = generate_fixture(
                skill_dir, gen_adapter, fixture_out, hint=args.hint,
                model=args.model, timeout_s=args.timeout_s,
                workdir_base=args.workdir_base,
                replace_existing=args.force,
            )
        except Exception as e:  # noqa: BLE001
            print(f"fixture generation failed: {e}", file=sys.stderr)
            return 1
        print(f"  wrote {fixture_out}/ ({len(rel_files)} file(s)) in "
              f"{int(time.monotonic() - t0)}s", file=sys.stderr, flush=True)
        fixture_path = str(fixture_out)
    else:
        print("[1/3] skipped (--no-fixture)", file=sys.stderr, flush=True)

    testcase_out = (Path(args.testcase_out) if args.testcase_out
                    else Path("testcases") / f"{skill_dir.name}.yaml")
    if testcase_out.exists() and not args.force:
        print(f"{testcase_out} already exists (use --force)", file=sys.stderr)
        return 2
    testcase_hint = args.hint
    testcase_hint = _append_generation_hint(
        testcase_hint, _BOOTSTRAP_EXEC_GENERATION_HINT if args.allow_exec else None)
    testcase_hint = _append_generation_hint(
        testcase_hint, _BOOTSTRAP_JUDGE_GENERATION_HINT if args.judge else None)
    print(f"[2/3] generating testcase (coverage={args.coverage}, "
          f"bias={args.bias})...",
          file=sys.stderr, flush=True)
    t0 = time.monotonic()
    try:
        yaml_text, docs, _ = generate_testcase(
            skill_dir, gen_adapter, fixture=fixture_path, model=args.model,
            timeout_s=args.timeout_s, workdir_base=args.workdir_base,
            coverage=args.coverage, bias=args.bias, hint=testcase_hint,
        )
    except Exception as e:  # noqa: BLE001
        print(f"testcase generation failed: {e}", file=sys.stderr)
        return 1
    if args.judge:
        missing_judge = _docs_missing_assertion(docs, "judge")
        if missing_judge:
            print(
                "testcase generation failed: --judge was requested but "
                "the generated testcase YAML lacks `judge:` assertions for: "
                + ", ".join(missing_judge),
                file=sys.stderr,
            )
            print(
                "tip: rerun with a stronger --hint or generate without --judge "
                "and add semantic assertions manually before running the smoke test",
                file=sys.stderr,
            )
            return 1
    testcase_out.parent.mkdir(parents=True, exist_ok=True)
    testcase_out.write_text(yaml_text.rstrip() + "\n", encoding="utf-8")
    print(f"  wrote {testcase_out} ({len(docs)} testcase(s)) in "
          f"{int(time.monotonic() - t0)}s", file=sys.stderr, flush=True)

    print(f"[3/3] running testcase (adapter={args.adapter}, runs={args.runs})...",
          file=sys.stderr, flush=True)
    cases = [TestCase.from_dict(d) for d in docs]
    test_adapter_cls = ADAPTERS[args.adapter]
    test_adapter = (test_adapter_cls(binary=args.binary) if args.binary
                    else test_adapter_cls())
    judge = None
    if args.judge:
        judge_cls = ADAPTERS[args.judge_adapter]
        judge_adapter = (judge_cls(binary=args.judge_binary) if args.judge_binary
                         else judge_cls())
        judge = LlmJudge(judge_adapter, model=args.judge_model,
                         workdir_base=args.workdir_base)

    all_pass = True
    n_cases = len(cases)
    reports = []
    for ci, tc in enumerate(cases):
        tc.runs = args.runs
        if args.run_model is not None:
            tc.options.model = args.run_model
        tc.options.debug = args.debug
        if n_cases > 1:
            _print_case_banner(ci + 1, n_cases, tc.name)
        rep = run_testcase(tc, test_adapter, judge=judge,
                           keep_failed_workdirs=args.keep_failed,
                           allow_exec=args.allow_exec,
                           workdir_base=args.workdir_base, verbose=True)
        print_report(rep)
        reports.append(rep)
        if rep.pass_rate < 1.0:
            all_pass = False

    # Consolidated FINAL REPORT for multi-case bootstrap runs.
    if n_cases > 1:
        print_final_report(reports)

    if args.require_stable_flow and _print_flow_instability_failures(reports):
        all_pass = False

    # Write --trace JSON if requested (after all cases, single file).
    if args.trace:
        write_trace(reports, Path(args.trace))
        print(f"wrote {args.trace}", file=sys.stderr)

    return 0 if all_pass else 1


def cmd_fix_skill(argv: list[str]) -> int:
    """`fix-skill`: hand a failing trace + target skill to the
    `interactive-skill-architect` skill, which proposes SKILL.md
    edits to close the gaps shown by the failures. Constraint: it may
    only ADD (warnings/gotchas/format reminders/tool bans/clarifications)
    -- never modify Hard Gates / Step order / ?曇?/?迫 conditions."""
    ap = argparse.ArgumentParser(
        prog="skill-test fix-skill",
        description="Invoke interactive-skill-architect to optimize a skill "
                    "based on test-failure evidence from --trace.",
    )
    ap.add_argument("skill", help="skill folder to fix, e.g. skills/cub-code-review")
    ap.add_argument("--trace", required=True,
                    help="trace.json from a previous failing test run")
    ap.add_argument("--adapter", default="claude", choices=sorted(ADAPTERS),
                    help="which CLI runs the architect (default: claude)")
    ap.add_argument("--binary", default=None, help="override CLI binary path")
    ap.add_argument("--model", default=None, help="model for the architect run")
    ap.add_argument("--scope", default="focused",
                    choices=("full", "focused", "style"),
                    help="architect's Phase O1 Step 3 scope: full (A=13-item "
                         "health check), focused (B=just the failures we "
                         "describe, default), style (C=style alignment only)")
    ap.add_argument("--case", default=None,
                    help="only feed failures whose case name contains this "
                         "substring (default: all failed cases)")
    ap.add_argument("--constraint", default=None,
                    help="extra free-text constraint passed to the architect "
                         "(stacks on top of the built-in 'no flow changes' rule)")
    ap.add_argument("--architect-skill", default=None,
                    help=f"path to interactive-skill-architect skill "
                         "(default: auto; local tools/skills first, then skill-auto-test bundled architect)")
    ap.add_argument("--apply", action="store_true",
                    help="copy the architect's modifications back to the "
                         "target skill (backs up touched files first). "
                         "Default: dry-run -- just print the diff.")
    ap.add_argument("--workdir-base", default=DEFAULT_WORKDIR_BASE,
                    help=f"workdir base for the architect run (default: {DEFAULT_WORKDIR_BASE})")
    ap.add_argument("--timeout-s", type=_positive_int, default=600,
                    help="timeout for the architect run (default: 600)")
    args = ap.parse_args(argv)

    target_dir = Path(args.skill)
    architect_dir = resolve_architect_skill(args.architect_skill)
    trace_path = Path(args.trace)
    if not trace_path.is_file():
        print(f"trace file not found: {trace_path}", file=sys.stderr)
        return 2

    adapter_cls = ADAPTERS[args.adapter]
    adapter = adapter_cls(binary=args.binary) if args.binary else adapter_cls()

    print(f"invoking architect to fix {target_dir} "
          f"(adapter={args.adapter}, scope={args.scope}, "
          f"mode={'apply' if args.apply else 'dry-run'})...",
          file=sys.stderr, flush=True)
    t0 = time.monotonic()
    try:
        changes, result, backup_dir = fix_skill(
            target_dir, architect_dir, adapter, trace_path,
            scope=args.scope, case_filter=args.case,
            extra_constraint=args.constraint, model=args.model,
            timeout_s=args.timeout_s, workdir_base=args.workdir_base,
            apply=args.apply,
        )
    except Exception as e:  # noqa: BLE001 - surface generation failure
        print(f"fix-skill failed: {e}", file=sys.stderr)
        return 1
    elapsed = int(time.monotonic() - t0)
    print(f"architect run done in {elapsed}s", file=sys.stderr, flush=True)

    # Surface architect's diagnosis (its final_message is its Phase O3 report).
    if result.final_message:
        print("\n=== architect's report (final_message) ===", file=sys.stderr)
        print(result.final_message, file=sys.stderr)
        print("=" * 50, file=sys.stderr)

    # Print the diff.
    print(f"\n=== proposed changes ({len(changes)} file(s)) ===")
    if not changes:
        print("(no files changed -- the architect didn't propose any edits)")
        return 0

    for ch in changes:
        print(f"\n--- {ch.kind}: {ch.relpath.as_posix()} ---")
        if ch.kind == "added":
            preview = ch.diff_text.splitlines()[:80]
            for line in preview:
                print(f"+ {line}")
            if len(ch.diff_text.splitlines()) > 80:
                print(f"... ({len(ch.diff_text.splitlines()) - 80} more lines)")
        elif ch.kind == "modified":
            print(ch.diff_text)

    if args.apply:
        print(f"\n>>> changes applied to {target_dir}", file=sys.stderr)
        if backup_dir:
            print(f">>> backup of original files: {backup_dir}", file=sys.stderr)
        print(f">>> 撱箄降:?? testcase 撽?\n"
              f"    skill-test testcases/{target_dir.name}.yaml "
              f"--adapter {args.adapter}", file=sys.stderr)
    else:
        print(f"\n>>> dry-run -- {target_dir} was not modified", file=sys.stderr)
        print(f">>> to apply these changes, re-run with --apply",
              file=sys.stderr)
    return 0


def cmd_check_skill(argv: list[str]) -> int:
    """`check-skill`: run interactive-skill-architect's 13-item quality
    health check on a skill and print the diagnosis. No testcase / trace
    needed -- this is a pure structural check ('is this skill well-written?').
    Does NOT modify the skill (no --apply path): the diagnosis covers
    judgement-call items the user should review themselves."""
    ap = argparse.ArgumentParser(
        prog="skill-test check-skill",
        description="Architect-driven 13-item quality check for a skill.",
    )
    ap.add_argument("skill", help="skill folder to check, e.g. skills/cub-code-review")
    ap.add_argument("--adapter", default="claude", choices=sorted(ADAPTERS))
    ap.add_argument("--binary", default=None, help="override CLI binary path")
    ap.add_argument("--model", default=None, help="model for the architect")
    ap.add_argument("--constraint", default=None,
                    help="extra free-text guidance to the architect")
    ap.add_argument("--architect-skill", default=None,
                    help=f"path to architect skill "
                         "(default: auto; local tools/skills first, then skill-auto-test bundled architect)")
    ap.add_argument("--workdir-base", default=DEFAULT_WORKDIR_BASE,
                    help=f"workdir base for the architect run (default: {DEFAULT_WORKDIR_BASE})")
    ap.add_argument("--out", default=None,
                    help="write the diagnosis to this file (default: stderr only)")
    ap.add_argument("--timeout-s", type=_positive_int, default=600)
    args = ap.parse_args(argv)

    target_dir = Path(args.skill)
    architect_dir = resolve_architect_skill(args.architect_skill)

    adapter_cls = ADAPTERS[args.adapter]
    adapter = adapter_cls(binary=args.binary) if args.binary else adapter_cls()

    print(f"running architect 13-item health check on {target_dir} "
          f"(adapter={args.adapter})...", file=sys.stderr, flush=True)
    t0 = time.monotonic()
    try:
        result = check_skill(target_dir, architect_dir, adapter,
                             extra_constraint=args.constraint,
                             model=args.model, timeout_s=args.timeout_s,
                             workdir_base=args.workdir_base)
    except Exception as e:  # noqa: BLE001
        print(f"check-skill failed: {e}", file=sys.stderr)
        return 1
    elapsed = int(time.monotonic() - t0)
    print(f"done in {elapsed}s", file=sys.stderr, flush=True)

    diagnosis = result.final_message or "(architect returned no diagnosis)"
    print("\n=== architect 13-item health-check report ===")
    print(diagnosis)
    print("=" * 50)

    if args.out:
        out_path = Path(args.out)
        if out_path.parent != Path("."):
            out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(diagnosis, encoding="utf-8")
        print(f"\nwrote {args.out}", file=sys.stderr)
    return 0


def cmd_fix_testcase(argv: list[str]) -> int:
    """`fix-testcase`: regenerate a testcase YAML to address quality issues
    exposed by a failing trace -- the 'skill is fine, testcase is wrong' path.
    Uses generate-testcase under the hood with a synthesized hint derived
    from the failure pattern."""
    ap = argparse.ArgumentParser(
        prog="skill-test fix-testcase",
        description="Regenerate a testcase YAML based on failure evidence.",
    )
    ap.add_argument("testcase", help="testcase YAML to fix, e.g. testcases/foo.yaml")
    ap.add_argument("--trace", required=True,
                    help="trace.json from a previous failing run")
    ap.add_argument("--adapter", default="claude", choices=sorted(ADAPTERS))
    ap.add_argument("--binary", default=None, help="override CLI binary path")
    ap.add_argument("--model", default=None)
    ap.add_argument("--case", default=None,
                    help="only feed failures whose case name contains this substring")
    ap.add_argument("--coverage", default="all",
                    choices=("all", "happy", "minimal"))
    ap.add_argument("--bias", default="mixed",
                    choices=("positive", "negative", "mixed"))
    ap.add_argument("--hint", default=None,
                    help="extra hint (stacks on top of the failure-derived hint)")
    ap.add_argument("--apply", action="store_true",
                    help="write the regenerated YAML back (backs up original "
                         "to <name>.bak.<timestamp>.yaml). Default: print only.")
    ap.add_argument("--workdir-base", default=DEFAULT_WORKDIR_BASE,
                    help="workdir base for generation "
                         f"(default: {DEFAULT_WORKDIR_BASE}; override if needed)")
    ap.add_argument("--timeout-s", type=_positive_int, default=240)
    args = ap.parse_args(argv)

    testcase_path = Path(args.testcase)
    trace_path = Path(args.trace)
    if not trace_path.is_file():
        print(f"trace file not found: {trace_path}", file=sys.stderr)
        return 2

    adapter_cls = ADAPTERS[args.adapter]
    adapter = adapter_cls(binary=args.binary) if args.binary else adapter_cls()

    print(f"regenerating {testcase_path} from failure evidence in {trace_path} "
          f"(adapter={args.adapter}, coverage={args.coverage}, bias={args.bias}, "
          f"mode={'apply' if args.apply else 'dry-run'})...",
          file=sys.stderr, flush=True)
    t0 = time.monotonic()
    try:
        yaml_text, docs, _, backup = fix_testcase(
            testcase_path, trace_path, adapter,
            case_filter=args.case, coverage=args.coverage, bias=args.bias,
            extra_hint=args.hint, model=args.model, timeout_s=args.timeout_s,
            workdir_base=args.workdir_base, apply=args.apply,
        )
    except Exception as e:  # noqa: BLE001
        print(f"fix-testcase failed: {e}", file=sys.stderr)
        return 1
    elapsed = int(time.monotonic() - t0)
    print(f"done in {elapsed}s ({len(docs)} testcase(s) generated)",
          file=sys.stderr, flush=True)

    print("\n=== regenerated testcase YAML ===")
    print(yaml_text)
    print("=" * 50)

    if args.apply:
        print(f"\n>>> wrote back to {testcase_path}", file=sys.stderr)
        if backup:
            print(f">>> original backed up to {backup}", file=sys.stderr)
    else:
        print(f"\n>>> dry-run -- {testcase_path} was not modified",
              file=sys.stderr)
        print(">>> to apply, re-run with --apply", file=sys.stderr)
    return 0


def cmd_new_skill(argv: list[str]) -> int:
    """`new-skill`: scaffold a brand-new skill via the architect's create
    mode. Pre-seeds the architect's Q1-Q6 interview with CLI flags so it
    runs non-interactively and writes the new skill into skills/<name>/."""
    ap = argparse.ArgumentParser(
        prog="skill-test new-skill",
        description="Scaffold a new skill via interactive-skill-architect.",
    )
    ap.add_argument("--name", required=True,
                    help="new skill folder name (kebab-case), e.g. pdf-filler")
    ap.add_argument("--description", required=True,
                    help="one-paragraph description (goes into SKILL.md frontmatter)")
    ap.add_argument("--type", dest="skill_type", default=None,
                    help="skill type / pattern hint (e.g. 'code-review-and-scoped-repair')")
    ap.add_argument("--from", dest="blueprint", default=None,
                    help="path to an existing skill to use as blueprint "
                         "(triggers architect's A2 mode)")
    ap.add_argument("--hint", default=None,
                    help="extra free-text guidance")
    ap.add_argument("--out-root", default="skills",
                    help="parent folder for the new skill (default: skills/)")
    ap.add_argument("--adapter", default="claude", choices=sorted(ADAPTERS))
    ap.add_argument("--binary", default=None, help="override CLI binary path")
    ap.add_argument("--model", default=None)
    ap.add_argument("--architect-skill", default=None,
                    help=f"path to architect skill "
                         "(default: auto; local tools/skills first, then skill-auto-test bundled architect)")
    ap.add_argument("--workdir-base", default=DEFAULT_WORKDIR_BASE,
                    help=f"workdir base for the architect run (default: {DEFAULT_WORKDIR_BASE})")
    ap.add_argument("--timeout-s", type=_positive_int, default=900,
                    help="default 900 -- create mode is more work than fix")
    args = ap.parse_args(argv)

    name_error = skill_name_error(args.name)
    if name_error:
        print(f"error: {name_error}", file=sys.stderr)
        return 2

    out_root = Path(args.out_root)
    architect_dir = resolve_architect_skill(args.architect_skill)
    blueprint = Path(args.blueprint) if args.blueprint else None

    adapter_cls = ADAPTERS[args.adapter]
    adapter = adapter_cls(binary=args.binary) if args.binary else adapter_cls()

    print(f"scaffolding new skill '{args.name}' under {out_root}/ "
          f"(adapter={args.adapter}"
          + (f", blueprint={blueprint}" if blueprint else ", from scratch")
          + ")...",
          file=sys.stderr, flush=True)
    t0 = time.monotonic()
    try:
        created_dir, files, result = new_skill(
            args.name, args.description, out_root, architect_dir, adapter,
            skill_type=args.skill_type, blueprint_skill=blueprint,
            hint=args.hint, model=args.model, timeout_s=args.timeout_s,
            workdir_base=args.workdir_base,
        )
    except Exception as e:  # noqa: BLE001
        print(f"new-skill failed: {e}", file=sys.stderr)
        return 1
    elapsed = int(time.monotonic() - t0)
    print(f"done in {elapsed}s", file=sys.stderr, flush=True)

    print(f"\n=== created {created_dir}/ ({len(files)} file(s)) ===")
    for f in files:
        print(f"  + {f.as_posix()}")
    print()

    if result.final_message:
        print("=== architect's Phase 4 self-review ===")
        print(result.final_message)
        print("=" * 50)

    print(f"\n>>> next steps:", file=sys.stderr)
    print(f"    1. review {created_dir}/SKILL.md", file=sys.stderr)
    print(f"    2. skill-test check-skill {created_dir}",
          file=sys.stderr)
    print(f"    3. skill-test generate {created_dir}",
          file=sys.stderr)
    return 0


def _positive_int(value: str) -> int:
    try:
        n = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value!r}") from e
    if n < 1:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value!r}")
    return n


def _path_key(path: str | Path) -> str:
    try:
        return str(Path(path).resolve(strict=False)).casefold()
    except OSError:
        return str(Path(path).absolute()).casefold()


def _ensure_skill_dir(path: Path, label: str) -> str | None:
    if not path.is_dir():
        return f"{label} folder not found: {path}"
    if not (path / "SKILL.md").is_file():
        return f"{label} has no SKILL.md: {path}"
    return None


def _iterate_skill_mismatches(cases: list[TestCase], target_dir: Path) -> list[str]:
    target_key = _path_key(target_dir)
    out = []
    for tc in cases:
        if _path_key(tc.skill) != target_key:
            out.append(f"{tc.name}: testcase skill={tc.skill} but --skill={target_dir}")
    return out


def cmd_iterate(argv: list[str]) -> int:
    """`iterate`: run testcases, auto-fix-skill on failure, re-run, repeat
    until converged or safety limits hit. Each round's trace is saved so
    the human can inspect what changed."""
    ap = argparse.ArgumentParser(
        prog="skill-test iterate",
        description="Run -> fix-skill -> run loop, until green or stalled.",
    )
    ap.add_argument("testcases", nargs="+", help="testcase YAML file(s)")
    ap.add_argument("--skill", required=True,
                    help="skill folder to auto-fix between rounds, "
                         "e.g. skills/cub-code-review")
    ap.add_argument("--adapter", default="claude", choices=sorted(ADAPTERS),
                    help="CLI that runs the testcases (default: claude)")
    ap.add_argument("--gen-adapter", default="claude", choices=sorted(ADAPTERS),
                    help="CLI that runs the architect for fix-skill (default: claude)")
    ap.add_argument("--gen-binary", default=None,
                    help="override CLI binary path for --gen-adapter")
    ap.add_argument("--binary", default=None,
                    help="override CLI binary path for --adapter")
    ap.add_argument("--model", default=None,
                    help="override model for testcase runs")
    ap.add_argument("--architect-model", default=None,
                    help="override model for fix-skill architect runs")
    ap.add_argument("--max-rounds", type=_positive_int, default=3,
                    help="hard cap on iterations (default: 3)")
    ap.add_argument("--no-improve-budget", type=_positive_int, default=1,
                    help="stop after this many flat rounds (default: 1)")
    ap.add_argument("--runs-per-round", type=_positive_int, default=1,
                    help="how many runs per testcase per round (default: 1, "
                         "smoke); raise to 3-5 to measure stability per round")
    ap.add_argument("--allow-exec", action="store_true")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--judge-adapter", default="claude", choices=sorted(ADAPTERS))
    ap.add_argument("--judge-model", default=None,
                    help="model for judge runs")
    ap.add_argument("--judge-binary", default=None,
                    help="binary for the judge adapter")
    ap.add_argument("--workdir-base", default=DEFAULT_WORKDIR_BASE,
                    help=f"workdir base for run/fix rounds (default: {DEFAULT_WORKDIR_BASE})")
    ap.add_argument("--trace-dir", default=".iterate-traces",
                    help="folder to save per-round traces (default: "
                         ".iterate-traces/round-N.json)")
    ap.add_argument("--fix-scope", default="focused",
                    choices=("full", "focused", "style"),
                    help="scope for fix-skill calls (default: focused)")
    ap.add_argument("--architect-skill", default=None,
                    help="path to architect skill "
                         "(default: auto; local tools/skills first, then skill-auto-test bundled architect)")
    ap.add_argument("--architect-timeout-s", type=_positive_int, default=600)
    ap.add_argument("--require-stable-flow", action="store_true",
                    help="require every case to use one stable tool flow before iterate converges")
    args = ap.parse_args(argv)

    target_dir = Path(args.skill)
    architect_dir = resolve_architect_skill(args.architect_skill)

    for label, skill_path in (("target skill", target_dir),
                              ("architect skill", architect_dir)):
        error = _ensure_skill_dir(skill_path, label)
        if error:
            print(f"error: {error}", file=sys.stderr)
            return 2

    if args.require_stable_flow and args.runs_per_round < 2:
        print("error: --require-stable-flow needs --runs-per-round >= 2",
              file=sys.stderr)
        return 2

    if not _validate_cases_for_run(args.testcases):
        return 2

    test_adapter = ADAPTERS[args.adapter](
        binary=args.binary) if args.binary else ADAPTERS[args.adapter]()
    gen_adapter = (ADAPTERS[args.gen_adapter](binary=args.gen_binary)
                   if args.gen_binary else ADAPTERS[args.gen_adapter]())
    judge = None
    if args.judge:
        judge_cls = ADAPTERS[args.judge_adapter]
        judge_adapter = (judge_cls(binary=args.judge_binary) if args.judge_binary
                         else judge_cls())
        judge = LlmJudge(judge_adapter, model=args.judge_model,
                         workdir_base=args.workdir_base)

    try:
        cases = load_cases(args.testcases)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not cases:
        print("no testcases found", file=sys.stderr)
        return 2
    mismatches = _iterate_skill_mismatches(cases, target_dir)
    if mismatches:
        print("error: iterate testcase skill mismatch", file=sys.stderr)
        for mismatch in mismatches:
            print(f"  {mismatch}", file=sys.stderr)
        print("tip: every testcase `skill:` must match --skill, because iterate runs the testcase and then edits --skill", file=sys.stderr)
        return 2
    if args.model is not None:
        for tc in cases:
            tc.options.model = args.model

    print(f"\n===== ITERATE start | skill={target_dir} | "
          f"testcases={len(cases)} | max_rounds={args.max_rounds} =====",
          file=sys.stderr, flush=True)

    def _log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    try:
        outcomes = iterate(
            cases, test_adapter, target_dir, architect_dir, gen_adapter,
            max_rounds=args.max_rounds,
            no_improve_budget=args.no_improve_budget,
            runs_per_round=args.runs_per_round,
            allow_exec=args.allow_exec, judge=judge,
            workdir_base=args.workdir_base,
            trace_dir=Path(args.trace_dir),
            fix_scope=args.fix_scope,
            architect_model=args.architect_model,
            architect_timeout_s=args.architect_timeout_s,
            require_stable_flow=args.require_stable_flow,
            log_fn=_log,
        )
    except Exception as e:  # noqa: BLE001
        print(f"iterate failed: {e}", file=sys.stderr)
        return 1

    print("\n===== ITERATE summary =====", file=sys.stderr)
    print(render_summary(outcomes))

    # Exit code: 0 only if iterate actually converged. A 100% pass rate with
    # --require-stable-flow can still be a failure when flows remain unstable.
    last = outcomes[-1] if outcomes else None
    ok = bool(last and last.stop_reason == "converged" and not last.unstable_cases)
    return 0 if ok else 1


def _doctor_try_command() -> str:
    _, testcases = list_local(Path("skills"), Path("testcases"))
    if testcases:
        return f"skill-test {testcases[0].path.as_posix()} --runs 1"
    return "skill-test list"


def cmd_doctor(argv: list[str]) -> int:
    """`doctor`: preflight environment check. Lists Python version, which
    CLIs are on PATH, whether sample content exists, and whether the
    architect skill is in place. Exit code 0 if all REQUIRED checks pass."""
    ap = argparse.ArgumentParser(
        prog="skill-test doctor",
        description="Check Python, CLIs, sample content, and architect skill.",
    )
    ap.parse_args(argv)

    rows, all_ok = _doctor()
    print("skill-auto-test doctor\n")
    for r in rows:
        mark = "OK" if r.passed else ("WARN" if not r.required else "FAIL")
        print(f"  {mark}  {r.name:<28s} {r.detail}")
    print()
    if all_ok:
        print("ready! try:")
        print(f"  {_doctor_try_command()}")
        return 0
    print("some REQUIRED checks failed -- fix them before running testcases.")
    return 1


def cmd_list(argv: list[str]) -> int:
    """`list`: scan ./skills and ./testcases and show what's available.
    Helps a fresh-clone user discover the sample content + bundled examples."""
    ap = argparse.ArgumentParser(
        prog="skill-test list",
        description="List skills and testcases in this project.",
    )
    ap.add_argument("--skills-root", default="skills")
    ap.add_argument("--testcases-root", default="testcases")
    args = ap.parse_args(argv)

    skills, testcases = list_local(
        Path(args.skills_root), Path(args.testcases_root))

    print(f"Skills ({len(skills)} in ./{args.skills_root}/):")
    if not skills:
        print("  (none)")
    for s in skills:
        extras = []
        if s.has_scripts:
            extras.append("scripts/")
        if s.has_references:
            extras.append("references/")
        if s.has_assets:
            extras.append("assets/")
        extras_s = f"  [{', '.join(extras)}]" if extras else ""
        tcs = f"  testcases: {', '.join(s.testcases)}" if s.testcases else "  (no testcase points at it)"
        print(f"  {s.name}{extras_s}{tcs}")
    print()

    print(f"Testcases ({len(testcases)} in ./{args.testcases_root}/):")
    if not testcases:
        print("  (none)")
    for tc in testcases:
        target = tc.skill or "(no skill: field!)"
        runs = f"  runs: {tc.runs}" if tc.runs is not None else ""
        cases = f"  {tc.n_cases} case(s)" if tc.n_cases else "  (empty)"
        print(f"  {tc.filename}  -> {target}{cases}{runs}")
    print()

    if skills and testcases:
        # Pick a sane first command to suggest.
        first_tc = testcases[0]
        print("Try one:")
        print(f"  skill-test {first_tc.path.as_posix()} --runs 1")
    return 0


def cmd_init(argv: list[str]) -> int:
    """`init`: scaffold a new skill-testing project layout in a target folder.
    Creates skills/, testcases/, fixtures/, .gitignore, README.md; optionally
    copies the architect skill (--with-architect) and a working example
    (--with-example) so the project is immediately runnable."""
    ap = argparse.ArgumentParser(
        prog="skill-test init",
        description="Scaffold a new skill-auto-test project.",
    )
    ap.add_argument("path", help="folder to create (must not exist or be empty, "
                                  "unless --force)")
    ap.add_argument("--with-architect", action="store_true",
                    help="also copy interactive-skill-architect into "
                         "tools/skills/ (needed for fix-skill / check-skill / "
                         "new-skill in the new project)")
    ap.add_argument("--with-example", action="store_true",
                    help="also copy this repo's example-skill + claude-example.yaml "
                         "as a starter the user can run immediately")
    ap.add_argument("--force", action="store_true",
                    help="proceed even if the target folder exists and is non-empty")
    args = ap.parse_args(argv)

    target = Path(args.path)
    try:
        actions = init_project(target, with_architect=args.with_architect,
                               with_example=args.with_example, force=args.force)
    except (FileExistsError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"initialized {target}/")
    for line in actions:
        print(f"  - {line}")
    print()
    print("next steps:")
    print(f"  cd {target}")
    print(f"  skill-test doctor")
    if args.with_example:
        print(f"  skill-test testcases/claude-example.yaml --runs 1")
    else:
        print(f"  skill-test new-skill --name my-skill --description \"...\"")
    return 0


def cmd_validate(argv: list[str]) -> int:
    """`validate`: fast static check on testcase YAML files. Catches syntax
    errors, missing required fields, unknown assertion keys (with did-you-mean
    suggestions), and skill: / fixture: paths that don't exist. Doesn't call
    any LLM, runs in <1s per file. Exit code 0 if all files clean, 1 if any
    have errors."""
    ap = argparse.ArgumentParser(
        prog="skill-test validate",
        description="Static validation for testcase YAML files (no LLM call).",
    )
    ap.add_argument("testcases", nargs="+",
                    help="testcase YAML file(s) to validate")
    ap.add_argument("--strict", action="store_true",
                    help="treat warnings as errors (CI-friendly)")
    args = ap.parse_args(argv)

    total_errors = 0
    total_warnings = 0
    for tc_path in args.testcases:
        path = Path(tc_path)
        issues = validate_file(path)
        # Header per file.
        if not issues:
            print(f"OK {path}")
            continue
        n_err = sum(1 for i in issues if i.severity == "error")
        n_warn = sum(1 for i in issues if i.severity == "warning")
        total_errors += n_err
        total_warnings += n_warn
        marker = "FAIL" if n_err else ("WARN" if n_warn else "OK")
        print(f"{marker} {path}  ({n_err} error(s), {n_warn} warning(s))")
        for issue in issues:
            sev = "error" if issue.severity == "error" else "warn "
            print(f"    [{sev}] {issue.where}: {issue.message}")
            if issue.hint:
                print(f"            hint: {issue.hint}")

    # Summary.
    n_files = len(args.testcases)
    if total_errors == 0 and total_warnings == 0:
        print(f"\nall {n_files} file(s) clean")
        return 0
    print(f"\n{n_files} file(s) checked: {total_errors} error(s), "
          f"{total_warnings} warning(s)")
    if total_errors > 0:
        return 1
    if args.strict and total_warnings > 0:
        return 1
    return 0


def _cmd_arg(value: str | Path) -> str:
    s = str(value)
    if not s:
        return '""'
    if any(ch.isspace() for ch in s) or '"' in s:
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _testcase_args(paths: list[str]) -> str:
    return " ".join(_cmd_arg(Path(p).as_posix()) for p in paths)


def _run_option_flags(args, *, runs: int | None = None,
                      include_trace: str | None = None,
                      keep_failed: bool = False) -> str:
    flags: list[str] = ["--adapter", args.adapter]
    if args.binary:
        flags += ["--binary", str(args.binary)]
    if args.model:
        flags += ["--model", str(args.model)]
    if runs is not None:
        flags += ["--runs", str(runs)]
    if args.judge:
        flags.append("--judge")
        if args.judge_adapter != "claude":
            flags += ["--judge-adapter", args.judge_adapter]
        if args.judge_model:
            flags += ["--judge-model", str(args.judge_model)]
        if args.judge_binary:
            flags += ["--judge-binary", str(args.judge_binary)]
    if args.allow_exec:
        flags.append("--allow-exec")
    if args.workdir_base != DEFAULT_WORKDIR_BASE:
        flags += ["--workdir-base", str(args.workdir_base)]
    if getattr(args, "require_stable_flow", False):
        flags.append("--require-stable-flow")
    if include_trace:
        flags += ["--trace", include_trace]
    if keep_failed:
        flags.append("--keep-failed")
    return " ".join(_cmd_arg(f) for f in flags)


def _unique_case_skills(reports) -> list[str]:
    out: list[str] = []
    for rep in reports:
        skill = rep.case.skill
        if skill not in out:
            out.append(skill)
    return out


def _effective_runs(tc: TestCase, runs_override: int | None) -> int:
    return runs_override if runs_override is not None else tc.runs


def _reject_require_stable_flow_with_single_run(
        cases: list[TestCase], runs_override: int | None, *, option_name: str) -> bool:
    too_small = [(tc.name, _effective_runs(tc, runs_override)) for tc in cases
                 if _effective_runs(tc, runs_override) < 2]
    if not too_small:
        return False

    print("error: --require-stable-flow needs at least 2 runs per case",
          file=sys.stderr)
    if runs_override is None:
        for name, runs in too_small[:5]:
            print(f"  {name}: runs={runs}", file=sys.stderr)
        if len(too_small) > 5:
            print(f"  ... and {len(too_small) - 5} more", file=sys.stderr)
        print(f"tip: pass `{option_name} 2` or set `runs: 2`+ in the testcase YAML",
              file=sys.stderr)
    else:
        print(f"tip: pass `{option_name} 2` or higher", file=sys.stderr)
    return True


def _unstable_flow_cases(reports) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for rep in reports:
        s = summarize(rep)
        if s["distinct_flows"] > 1:
            out.append((rep.case.name, s["distinct_flows"]))
    return out


def _print_flow_instability_failures(reports) -> bool:
    unstable = _unstable_flow_cases(reports)
    if not unstable:
        return False
    print("\nflow instability: --require-stable-flow requires one stable tool flow per case",
          file=sys.stderr)
    for name, distinct_flows in unstable:
        print(f"  - {name}: {distinct_flows} distinct flows", file=sys.stderr)
    return True


def _print_run_next_steps(args, reports, all_pass: bool) -> None:
    testcase_args = _testcase_args(args.testcases)
    print("\n>>> next steps:", file=sys.stderr)
    if all_pass:
        stress_runs = 10 if args.runs is None or args.runs < 10 else args.runs
        stress_flags = _run_option_flags(
            args, runs=stress_runs, include_trace="trace.json")
        print(f"    skill-test {testcase_args} {stress_flags}", file=sys.stderr)
        print(f"    skill-test validate {testcase_args} --strict", file=sys.stderr)
        if not args.out:
            report_flags = _run_option_flags(
                args, runs=args.runs, include_trace="trace.json")
            print(f"    skill-test {testcase_args} {report_flags} --out report.json", file=sys.stderr)
        return

    if not args.trace:
        debug_runs = 1 if args.runs is None or args.runs > 1 else args.runs
        debug_flags = _run_option_flags(
            args, runs=debug_runs, include_trace="trace.json", keep_failed=True)
        print(f"    skill-test {testcase_args} {debug_flags}", file=sys.stderr)
        print("    # then use trace.json with fix-skill or fix-testcase", file=sys.stderr)
        return

    trace_arg = _cmd_arg(args.trace)
    skills = _unique_case_skills(reports)
    skill_arg = _cmd_arg(skills[0]) if len(skills) == 1 else "<skill-folder>"
    print(f"    skill-test fix-skill {skill_arg} --trace {trace_arg} --scope focused", file=sys.stderr)
    if len(args.testcases) == 1:
        tc_arg = _cmd_arg(Path(args.testcases[0]).as_posix())
    else:
        tc_arg = "<failing-testcase.yaml>"
    print(f"    skill-test fix-testcase {tc_arg} --trace {trace_arg} --apply", file=sys.stderr)
    rerun_flags = _run_option_flags(args, runs=1)
    print(f"    skill-test {testcase_args} {rerun_flags}", file=sys.stderr)


def cmd_run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="skill-test", description="Skill flow stability tester")
    ap.add_argument("testcases", nargs="+", help="testcase YAML file(s)")
    ap.add_argument("--adapter", default="claude", choices=sorted(ADAPTERS),
                    help="default: claude. Use codex/codex-tui explicitly; "
                         "workdirs default to .work.")
    ap.add_argument("--binary", default=None, help="override CLI binary path")
    ap.add_argument("--runs", type=_positive_int, default=None, help="override runs per case")
    ap.add_argument("--require-stable-flow", action="store_true",
                    help="fail when any case uses multiple tool flows; needs at least 2 runs per case")
    ap.add_argument("--model", default=None, help="override model for all cases")
    ap.add_argument("--out", default=None, help="write aggregate JSON report to this path")
    ap.add_argument("--trace", default=None,
                    help="write per-run detail (tool sequence, final message) to this path")
    ap.add_argument("--keep-failed", action="store_true",
                    help="leave workdirs of failed runs on disk for inspection")
    ap.add_argument("--judge", action="store_true",
                    help="enable LLM-as-judge for `judge:` checks (costs tokens)")
    ap.add_argument("--judge-adapter", default="claude", choices=sorted(ADAPTERS),
                    help="which backend runs the judge (default: claude)")
    ap.add_argument("--judge-model", default=None,
                    help="model for the judge (default: the adapter's default)")
    ap.add_argument("--judge-binary", default=None,
                    help="binary for the judge adapter (default: on PATH)")
    ap.add_argument("--allow-exec", action="store_true",
                    help="allow `command:` checks to execute (runs model-produced code!)")
    ap.add_argument("--workdir-base", default=DEFAULT_WORKDIR_BASE,
                    help="create per-run workdirs under this dir "
                         f"(default: {DEFAULT_WORKDIR_BASE}; use a sandbox-trusted path)")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress per-run progress logs (default: show progress)")
    ap.add_argument("--debug", action="store_true",
                    help="extra-verbose logs: raw commands, agent messages, tool outputs")
    args = ap.parse_args(argv)

    if not _validate_cases_for_run(args.testcases):
        return 2

    try:
        cases = load_cases(args.testcases)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not cases:
        print("no testcases found", file=sys.stderr)
        return 2
    if args.require_stable_flow and _reject_require_stable_flow_with_single_run(
            cases, args.runs, option_name="--runs"):
        return 2

    adapter_cls = ADAPTERS[args.adapter]
    adapter = adapter_cls(binary=args.binary) if args.binary else adapter_cls()

    judge = None
    if args.judge:
        # the judge runs on its own adapter (default claude) with no skill staged;
        # it just reasons over the output embedded in the prompt.
        judge_cls = ADAPTERS[args.judge_adapter]
        judge_adapter = (judge_cls(binary=args.judge_binary) if args.judge_binary
                         else judge_cls())
        judge = LlmJudge(judge_adapter, model=args.judge_model,
                         workdir_base=args.workdir_base)

    total_runs = sum((args.runs if args.runs is not None else tc.runs) for tc in cases)
    started_at = datetime.now()
    t_start = time.monotonic()
    print(f"\n===== START {started_at:%Y-%m-%d %H:%M:%S} | "
          f"{len(cases)} case(s), {total_runs} run(s), adapter={args.adapter} =====",
          file=sys.stderr, flush=True)

    reports = []
    all_pass = True
    n_cases = len(cases)
    for ci, tc in enumerate(cases):
        if args.runs is not None:
            tc.runs = args.runs
        if args.model is not None:
            tc.options.model = args.model
        tc.options.verbose = not args.quiet
        tc.options.debug = args.debug
        if n_cases > 1:
            _print_case_banner(ci + 1, n_cases, tc.name)
        rep = run_testcase(tc, adapter, judge=judge,
                           keep_failed_workdirs=args.keep_failed,
                           allow_exec=args.allow_exec,
                           workdir_base=args.workdir_base,
                           verbose=not args.quiet)
        print_report(rep)
        reports.append(rep)
        if rep.pass_rate < 1.0:
            all_pass = False

    # Consolidated FINAL REPORT block: re-prints every case's summary in
    # one place so the user doesn't have to scroll back through per-case
    # logs. Only meaningful when there's >1 case -- a single case's summary
    # is already right above the END marker, no duplication needed.
    if n_cases > 1:
        print_final_report(reports)

    if args.require_stable_flow and _print_flow_instability_failures(reports):
        all_pass = False

    if args.out:
        write_json(reports, Path(args.out))
        print(f"\nwrote {args.out}")
    if args.trace:
        write_trace(reports, Path(args.trace))
        print(f"wrote {args.trace}")

    ended_at = datetime.now()
    elapsed = int(time.monotonic() - t_start)
    mins, secs = divmod(elapsed, 60)
    print(f"===== END {ended_at:%Y-%m-%d %H:%M:%S} | "
          f"elapsed {mins}m{secs:02d}s ({elapsed}s) =====", file=sys.stderr, flush=True)
    _print_run_next_steps(args, reports, all_pass)

    return 0 if all_pass else 1


_SUBCOMMANDS = {
    "generate": cmd_generate,
    "generate-fixture": cmd_generate_fixture,
    "bootstrap": cmd_bootstrap,
    "fix-skill": cmd_fix_skill,
    "check-skill": cmd_check_skill,
    "fix-testcase": cmd_fix_testcase,
    "new-skill": cmd_new_skill,
    "iterate": cmd_iterate,
    "doctor": cmd_doctor,
    "list": cmd_list,
    "validate": cmd_validate,
    "init": cmd_init,
}

# One-line summary shown in top-level --help, keyed by subcommand name.
# Default ("(default)") is the bare "run testcases" path with no subcommand.
_SUBCOMMAND_SUMMARIES = {
    "(default)":      "run testcases - measure skill stability",
    "init":           "scaffold a new skill-auto-test project layout",
    "doctor":         "preflight: check Python / CLIs / sample skills are ready",
    "list":           "list available skills + testcases in this project",
    "validate":       "static check on testcase YAML files (no LLM call)",
    "new-skill":      "scaffold a brand-new skill via interactive-skill-architect",
    "check-skill":    "13-item quality health-check on a skill (no edits)",
    "generate-fixture": "let LLM write a sample input project for a skill",
    "generate":       "let LLM produce a testcase YAML from a skill's SKILL.md",
    "bootstrap":      "chain generate-fixture -> generate -> smoke run",
    "fix-skill":      "patch SKILL.md based on failure trace (architect-driven)",
    "fix-testcase":   "regenerate testcase YAML based on failure trace",
    "iterate":        "auto loop: run -> fix-skill -> run, until green or stuck",
}


def _print_top_help() -> None:
    """Top-level help shown when the user runs `... --help`, `... -h`,
    `... help`, or invokes with no args at all. Lists every subcommand so
    new users can discover what's available without reading the README."""
    print("skill-auto-test - deterministic stability testing for Anthropic-format skills")
    print()
    print("Usage:")
    print("  skill-test [subcommand] [options]")
    print("  skill-test testcases/<name>.yaml [options]   # default: run testcases")
    print()
    print("Subcommands:")
    # Render with stable column widths. Width 18 fits "generate-fixture".
    for name, summary in _SUBCOMMAND_SUMMARIES.items():
        print(f"  {name:<18s} {summary}")
    print()
    print("Run any subcommand with `--help` for its full flag list:")
    print("  skill-test iterate --help")
    print()
    print("Docs:")
    print("  Quick start    docs/quickstart.md")
    print("  Tutorial       docs/tutorial.md")
    print("  Cheatsheet     docs/cheatsheet.md")
    print("  Per-command    docs/commands/<name>.md")


def _dispatch(argv: list[str]) -> int:
    """Route argv to the right subcommand (or top-level help)."""
    # Top-level help / no args -> show subcommand catalogue.
    if not argv or argv[0] in ("-h", "--help", "help"):
        _print_top_help()
        return 0

    if argv[0] in _SUBCOMMANDS:
        return _SUBCOMMANDS[argv[0]](argv[1:])

    # Unknown subcommand vs positional testcase path: distinguish by checking
    # if it looks like a path. If the first arg starts with `-` or isn't a
    # known subcommand and not a file path, surface a clearer error than
    # argparse's generic "no testcases" later.
    first = argv[0]
    if not first.startswith("-") and "/" not in first and "\\" not in first \
            and not first.endswith(".yaml") and not Path(first).exists():
        print(f"unknown subcommand: {first!r}", file=sys.stderr)
        print(f"tip: see available subcommands with `skill-test --help`",
              file=sys.stderr)
        return 2

    return cmd_run(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Wraps `_dispatch` with a friendly Ctrl+C handler so
    interrupting mid-run exits cleanly (no traceback dump) with the
    POSIX-conventional exit code 130 for SIGINT."""
    _force_utf8_console()
    argv = sys.argv[1:] if argv is None else argv
    try:
        return _dispatch(argv)
    except KeyboardInterrupt:
        print("\n^C interrupted by user. exiting.", file=sys.stderr, flush=True)
        # 130 = 128 + SIGINT(2); standard POSIX convention so CI / shells
        # can distinguish "user aborted" from "assertion failed" (1) or
        # "argument error" (2).
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

