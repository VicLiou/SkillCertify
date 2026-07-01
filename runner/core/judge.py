"""LLM-as-judge for semantic expectations (`judge:` checks).

Off by default: it costs tokens and has its own variance. To keep the judge as
stable as possible:
  - fixed rubric prompt
  - structured single-line JSON verdict (easy, deterministic to parse)
  - the output-under-test is embedded in the prompt; the judge uses NO tools
  - phrase criteria so that PASS == the desired behavior

The judge is itself a CliAdapter run with no skill staged -- just a plain LLM call.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from ..adapters import CliAdapter, RunOptions
from .cleanup import remove_tree

_MAX_CHARS = 8000  # truncate the evaluated output to keep prompts bounded

_PROMPT = """You are a strict, deterministic evaluator of an AI skill's output.
Decide whether the OUTPUT satisfies the CRITERION.

Reply with ONLY a single-line JSON object and nothing else:
{{"verdict": "PASS", "reason": "<one short sentence>"}}
or
{{"verdict": "FAIL", "reason": "<one short sentence>"}}

Judge strictly against the CRITERION only. Do not use any tools. Be consistent.

=== CRITERION ===
{criterion}

=== OUTPUT TO EVALUATE ===
{output}
"""


class LlmJudge:
    def __init__(self, adapter: CliAdapter, model: str | None = None,
                 timeout_s: int = 120,
                 workdir_base: str | Path | None = None):
        self.adapter = adapter
        self.opts = RunOptions(model=model, timeout_s=timeout_s)
        self.workdir_base = workdir_base

    def __call__(self, criterion: str, output_text: str) -> tuple[bool, str]:
        prompt = _PROMPT.format(criterion=criterion, output=output_text[:_MAX_CHARS])
        if self.workdir_base:
            Path(self.workdir_base).mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(
            prefix="judge_",
            dir=str(self.workdir_base) if self.workdir_base else None,
        ))
        try:
            res = self.adapter.run(prompt, workdir, self.opts)
        finally:
            remove_tree(workdir)
        if res.crashed:
            return False, f"judge error: {res.error}"
        return self._parse(res.final_message)

    @staticmethod
    def _parse(text: str) -> tuple[bool, str]:
        m = re.search(r'\{[^{}]*"verdict"[^{}]*\}', text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                verdict = str(obj.get("verdict", "")).upper()
                reason = str(obj.get("reason", ""))
                return verdict == "PASS", f"{verdict}: {reason}"
            except json.JSONDecodeError:
                pass
        up = text.upper()
        if "PASS" in up and "FAIL" not in up:
            return True, "PASS (unparsed)"
        if "FAIL" in up:
            return False, "FAIL (unparsed)"
        return False, f"unparseable verdict: {text[:120]!r}"
