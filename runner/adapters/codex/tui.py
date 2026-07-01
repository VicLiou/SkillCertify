"""Codex interactive-TUI adapter, driven via a PTY (Windows ConPTY / pywinpty).

Why: on org/cloud-managed codex, `codex exec` runs read-only (managed profile)
and auto-rejects write approvals, and `codex app-server` opens VS Code. But the
INTERACTIVE TUI runs with the managed profile's `workspace-write` mode, which
GRANTS WRITE to the launch cwd -- so it reads+writes the workspace WITHOUT any
approval prompt. We give the TUI a real TTY via pywinpty, hand it the prompt, and
read clean structured results from codex's on-disk session rollout JSONL.

The PTY is only a TTY host: we don't scrape the screen. Completion is detected by
tailing the rollout file for the `task_complete` event; all results come from the
rollout (function_call + custom_tool_call = tool calls, agent_message = final).

Requires: pip install pywinpty   (Windows)
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..base import RunOptions, RunResult
from .common import resolve_launcher

# rollout event_msg / response_item types we care about
_TOOL_ITEM_TYPES = {"function_call", "custom_tool_call"}


def _log(msg: str) -> None:
    line = f"  [codex-tui] {msg}"
    try:
        print(line, file=sys.stderr, flush=True)
    except UnicodeEncodeError:
        enc = sys.stderr.encoding or "ascii"
        print(line.encode(enc, "replace").decode(enc), file=sys.stderr, flush=True)


def _sessions_dir() -> Path:
    home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    return Path(home) / "sessions"


def _kill_pid_tree(pid: int) -> None:
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass


class CodexTuiAdapter:
    name = "codex-tui"

    def __init__(self, binary: str = "codex"):
        self.binary = binary
        # (rollout_size_bytes, labels) -- avoids re-parsing the whole rollout
        # every 0.3s polling tick. Reset at the start of each run().
        self._label_cache: tuple[int, list[str]] | None = None
        # Coordination between the approval watcher (background thread) and the
        # event-streamer (_await_complete): the watcher sees a new rollout
        # escalation and presses Enter BEFORE the event-streamer's slower 1s poll
        # has emitted the "ESCALATION requested" line for it -- without
        # coordination, "approved by watcher" appears in the log BEFORE the
        # request it confirmed (reverse causation, confusing). The queue holds
        # (escalation_count_target, message); the streamer drains entries whose
        # target <= how many escalations it has already emitted.
        self._pending_approvals: list[tuple[int, str]] = []
        self._pending_lock = threading.Lock()
        self._emitted_esc_count = [0]  # mutable for closure-style update

    def run(self, prompt: str, workdir: Path, opts: RunOptions) -> RunResult:
        self._label_cache = None  # fresh rollout per run
        self._pending_approvals = []
        self._emitted_esc_count[0] = 0
        try:
            from winpty import PtyProcess  # type: ignore
        except ImportError:
            return RunResult("", "", -1,
                             error="pywinpty not installed (pip install pywinpty)")

        launcher = resolve_launcher(self.binary)
        if launcher is None:
            return RunResult("", "", -1, error=(f"codex binary not found on PATH: {self.binary!r}. "
                                          f"tip: install codex CLI (https://developers.openai.com/codex/cli/), "
                                          f"or pass --binary <path-to-codex>. "
                                          f"Run `skill-test doctor` to verify."))

        # Multi-line prompts can't be passed reliably as a Windows argv arg, so
        # write the full prompt to a file and hand codex a single-line instruction
        # to read it. The file MUST live INSIDE the workspace (workdir): reading a
        # file outside the workspace triggers an escalation/approval the unattended
        # TUI can't answer. Named .codex_* so _produced_text excludes it from
        # output scanning.
        prompt_file = Path(workdir) / ".codex_tui_prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        instruction = (
            f"Read the UTF-8 file ./{prompt_file.name} in the current directory and "
            f"follow its instructions exactly. Do not ask for confirmation; just do "
            f"it. Produce the requested deliverable, then end your turn immediately. "
            f"Do NOT run any tests, builds, linters, or other verification commands "
            f"-- creating/editing the files (or writing the report) is enough."
        )

        sessions = _sessions_dir()
        before = set(glob.glob(str(sessions / "**" / "rollout-*.jsonl"), recursive=True))
        start = time.monotonic()

        # Launch the TUI with explicit workspace/read-access settings, so codex's
        # file reads (Get-Content/Get-ChildItem/Test-Path) don't get sandbox-denied
        # and escalate -- reads are the ONLY escalation source here. The approval
        # watcher stays as a safety net for anything still escalating.
        # We TYPE the instruction after launch (a positional prompt only pre-fills
        # the input box without submitting, so no turn runs).
        profile = self._ensure_read_profile()
        argv = self._build_launch_args(launcher, workdir, profile)
        try:
            pty = PtyProcess.spawn(argv, cwd=str(workdir), dimensions=(50, 200))
        except Exception as e:  # noqa: BLE001
            return RunResult("", "", -1, error=f"failed to spawn codex TUI: {e}")

        v = getattr(opts, "verbose", True)
        debug = getattr(opts, "debug", False)
        on_event = getattr(opts, "on_event", None)

        def emit(msg: str) -> None:
            if not v:
                return
            if on_event is not None:
                on_event(msg)
            else:
                _log(msg)  # standalone fallback (no runner attached)

        if profile:
            emit(f"launched with read-access profile '{profile}' (-p) and explicit workspace read config")
        screen: list[str] = []
        threading.Thread(target=self._drain, args=(pty, screen), daemon=True).start()

        # Auto-accept mid-task escalation/approval dialogs so the unattended run
        # never blocks. Detection is hybrid: structured (new require_escalated calls
        # in the rollout) + screen text matching. rollout_holder[0] is filled once
        # the rollout file is located.
        stop_evt = threading.Event()
        rollout_holder: list[Path | None] = [None]

        # Wrap everything from this point in try/finally so the PTY and the
        # approval-watcher thread are always cleaned up -- whether we hit a
        # normal completion, a timeout, an exception, or Ctrl+C from the user.
        # Without this, a Ctrl+C in the middle of _await_complete would leave
        # codex.cmd / node child processes alive after the Python exits.
        cleaned_up = False

        def _cleanup() -> None:
            nonlocal cleaned_up
            if cleaned_up:
                return
            cleaned_up = True
            stop_evt.set()
            self._shutdown(pty)

        try:
            emit("waiting for trust dialog / input prompt...")
            self._prepare(pty, screen, timeout=30)

            threading.Thread(target=self._approval_watcher,
                             args=(pty, screen, stop_evt, rollout_holder, emit),
                             daemon=True).start()

            # Retry submission: TUI may still be initializing on the first try, or
            # Enter may not register. Clear the input, type, pause, then Enter.
            # Swallow transient write errors (the first write can race the input box
            # becoming ready); success is decided by whether a rollout appears.
            rollout = None
            for _ in range(4):
                self._safe_write(pty, "\x15")   # Ctrl+U: clear the input line
                time.sleep(0.3)
                self._safe_write(pty, instruction)
                time.sleep(0.8)
                self._safe_write(pty, "\r")     # Enter: submit
                rollout = self._await_rollout(sessions, before, workdir, max_wait=20)
                if rollout is not None:
                    break
                emit("no turn started yet; re-typing the task...")

            if rollout is None:
                tail = self._strip_ansi("".join(screen))[-1200:]
                return RunResult("", "".join(screen), -1,
                                 error=f"no rollout file appeared. readable screen tail: {tail!r}",
                                 latency_ms=int((time.monotonic() - start) * 1000))

            rollout_holder[0] = rollout  # enable structured approval detection
            emit("turn started; streaming events...")
            completed = self._await_complete(rollout, opts.timeout_s, start, emit, debug,
                                              stats=opts.stats)

            res = self._parse_rollout(rollout)
            res.stderr = "".join(screen)
            res.metadata["stderr_tail"] = self._strip_ansi(res.stderr)[-2000:]
            res.latency_ms = int((time.monotonic() - start) * 1000)
            if not completed:
                res.metadata["pending_escalations_at_timeout"] = self._pending_escalation_count(rollout)
                res.error = res.error or f"timeout after {opts.timeout_s}s (no task_complete)"
                res.exit_code = -1
            return res
        finally:
            _cleanup()

    # --- PTY plumbing -------------------------------------------------------
    # name of the codex config profile that grants shell read access
    _READ_PROFILE = "skilltest_tui"

    @classmethod
    def _ensure_read_profile(cls) -> str | None:
        """Write $CODEX_HOME/<name>.config.toml granting disk-full-read-access, so
        codex's shell reads aren't sandbox-denied (the escalation source). Returns
        the profile name to pass via -p, or None if the file can't be written
        (then we fall back to the bare launch + approval watcher)."""
        home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
        try:
            home.mkdir(parents=True, exist_ok=True)
            (home / f"{cls._READ_PROFILE}.config.toml").write_text(
                "# skill-auto-test: grant shell read access so codex-tui does not\n"
                "# prompt for escalation on every file read.\n"
                'sandbox_permissions = ["disk-full-read-access"]\n',
                encoding="utf-8",
            )
            return cls._READ_PROFILE
        except OSError:
            return None

    _READ_ACCESS_CONFIG = 'sandbox_permissions=["disk-full-read-access"]'

    @classmethod
    def _build_launch_args(cls, launcher: list[str], workdir: Path,
                           profile: str | None) -> list[str]:
        """Build TUI argv with read access applied in two ways.

        Some managed Codex installs ignore or partially layer profile files.
        Passing the same read grant through `-c` as well makes the launch
        self-contained and matches the CLI help's documented override path.
        `-C` makes the intended workspace explicit instead of relying solely
        on the PTY cwd. Do not pass `--add-dir`: under managed/read-access
        profiles Codex treats it as an extra writable root and may refuse to
        start the TUI before any rollout is created.
        """
        argv = list(launcher)
        if profile:
            argv += ["-p", profile]
        argv += [
            "-C", str(workdir),
            "-c", cls._READ_ACCESS_CONFIG,
        ]
        return argv

    @staticmethod
    def _safe_write(pty, data: str) -> bool:
        """Write to the PTY, returning whether the write succeeded. Callers that
        retry on failure (the approval watcher) use this to detect a wedged PTY
        and surface a diagnostic, rather than silently spinning forever."""
        try:
            pty.write(data)
            return True
        except Exception:  # noqa: BLE001 - transient/closed
            return False

    def _approval_watcher(self, pty, screen: list[str], stop_evt,
                          rollout_holder: list, emit=None) -> None:
        """Auto-accept mid-task escalation/approval dialogs.

        Press-source-of-truth = `pending_escalation_count(rollout)`: codex
        records each escalation as a `function_call` and writes a matching
        `function_call_output` only AFTER the dialog has been approved and the
        command has run. So `function_calls - function_call_outputs` = number
        of dialogs still waiting for Enter. We press once per second (throttle)
        while that number is > 0.

        Why this matters: parallel batches (e.g. 7 reference reads issued at
        once) produce 7 stacked dialogs in codex's TUI, each needing its OWN
        Enter -- the old "press once per rollout-grew event" approach
        under-counted catastrophically (one press for a batch of 7), leaving 6
        dialogs stuck and the whole turn frozen until the harness timeout.

        Screen-marker fallback still runs when there's no pending rollout
        dialog -- catches odd cases where codex shows an approval dialog
        without first writing the escalation record."""
        markers = ("do you want to allow", "allow command", "allow this",
                   "approve this", "yes, proceed", "yes, allow", "grant write",
                   "grant read", "wants to run", "allow running", "requires approval",
                   "1. yes", "y/n")
        last = 0.0
        toggle = False
        seen_esc = 0           # for emit-tracking only (not press-tracking)
        write_fails = 0
        warned_wedged = False
        last_pending = 0
        last_pending_decrease = time.monotonic()
        stuck_announced = False

        while not stop_evt.is_set():
            rp = rollout_holder[0] if rollout_holder else None
            pending_dialogs = 0

            if rp is not None:
                labels = self._escalation_labels(rp)

                # Emit "approved by watcher (+N in batch)" for each new wave of
                # escalations, deferred via the shared queue so it lands AFTER
                # the streamer prints the ESCALATION requested lines.
                if len(labels) > seen_esc and emit is not None:
                    new_count = len(labels) - seen_esc
                    seen_esc = len(labels)
                    suffix = (f" (+{new_count} in batch)"
                              if new_count > 1 else "")
                    msg = f"  -> approved by watcher [rollout]{suffix}"
                    with self._pending_lock:
                        self._pending_approvals.append((seen_esc, msg))
                        while (self._pending_approvals
                               and self._pending_approvals[0][0]
                                   <= self._emitted_esc_count[0]):
                            _, m = self._pending_approvals.pop(0)
                            emit(m)
                elif len(labels) > seen_esc:
                    seen_esc = len(labels)

                # The actual press signal: how many escalated calls have NOT
                # yet had their function_call_output written back. Each one is
                # a stuck dialog (codex won't run an escalated call until the
                # dialog is approved, and won't write the output until the
                # call completes).
                pending_dialogs = self._pending_escalation_count(rp)

                # Track stuck-rollout state for diagnostics.
                # Reset the clock when (a) we're idle (pending == 0) -- so the
                # gap between batches doesn't accumulate into a false "stuck"
                # signal -- or (b) pending actually shrank, meaning the system
                # is making progress. The 10s countdown only ticks while
                # pending is continuously > 0 AND has not gone down.
                if pending_dialogs == 0 or pending_dialogs < last_pending:
                    last_pending_decrease = time.monotonic()
                    stuck_announced = False
                last_pending = pending_dialogs

                # Announce once when we conclude things are genuinely stuck
                # (pending > 0 stable for >10s) -- helps the user understand
                # why log shows so many presses in a row.
                if (pending_dialogs > 0 and not stuck_announced
                        and time.monotonic() - last_pending_decrease > 10
                        and emit is not None):
                    emit(f"  -> {pending_dialogs} escalated call(s) still "
                         f"waiting for output; pressing Enter until cleared")
                    stuck_announced = True

            # Press logic: throttle 1s/press. Prefer rollout-driven press;
            # fall back to screen markers only when rollout shows no pending.
            if time.monotonic() - last > 1.0:
                press_kind = None
                if pending_dialogs > 0:
                    press_kind = "pending"
                elif self._screen_has_marker(screen, markers):
                    press_kind = "screen"

                if press_kind is not None:
                    ok = self._safe_write(pty, "1\r" if toggle else "\r")
                    if ok:
                        write_fails = 0
                        if press_kind == "screen" and emit is not None:
                            emit("  -> approved by watcher [screen]")
                        toggle = not toggle
                        last = time.monotonic()
                    else:
                        write_fails += 1
                        # Surface a diagnostic ONCE after several consecutive
                        # write failures -- a PTY that won't accept Enter is
                        # almost always wedged (codex crashed or TTY closed);
                        # silent spinning makes the eventual timeout look
                        # mysterious.
                        if (write_fails >= 3 and not warned_wedged
                                and emit is not None):
                            emit("  -> warning: PTY write failed 3x; codex "
                                 "may be wedged (still polling until timeout)")
                            warned_wedged = True
            time.sleep(0.3)

    def _pending_escalation_count(self, rollout: Path) -> int:
        """Number of escalated `function_call` / `custom_tool_call` records
        in the rollout that don't yet have a matching `*_call_output`. Each
        such record is a dialog the codex TUI is showing and waiting for
        Enter -- this is the watcher's authoritative "should I keep pressing?"
        signal, immune to the "press once per batch" miscount that the older
        rollout-grew trigger had."""
        pending_ids: set = set()
        for rec in self._read_records(rollout):
            if rec.get("type") != "response_item":
                continue
            p = rec.get("payload", {})
            if not isinstance(p, dict):
                continue
            ptype = p.get("type")
            if ptype in _TOOL_ITEM_TYPES and self._call_escalated(p):
                cid = p.get("call_id")
                if cid:
                    pending_ids.add(cid)
            elif ptype in ("function_call_output", "custom_tool_call_output"):
                cid = p.get("call_id")
                if cid:
                    pending_ids.discard(cid)
        return len(pending_ids)

    @classmethod
    def _screen_has_marker(cls, screen: list[str], markers: tuple) -> bool:
        """Cheap last-1500-chars check; case-insensitive. Used as a fallback
        for approval dialogs that codex displays before writing them to the
        rollout (rare in practice)."""
        tail = cls._strip_ansi("".join(screen)).lower()[-1500:]
        return any(m in tail for m in markers)

    @staticmethod
    def _call_escalated(p: dict) -> bool:
        """Whether a response_item payload (shell function_call OR custom_tool_call
        like apply_patch) required sandbox escalation. function_call carries it
        inside its JSON `arguments`; check both that and the payload's top level so
        an escalated apply_patch (e.g. writing outside the workspace) isn't
        silently missed just because it isn't a plain shell call."""
        if p.get("sandbox_permissions") == "require_escalated":
            return True
        raw = p.get("arguments")
        if isinstance(raw, str):
            try:
                args = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                args = {}
            if args.get("sandbox_permissions") == "require_escalated":
                return True
        return False

    def _escalation_labels(self, rollout: Path) -> list[str]:
        """One-line description of each require_escalated tool call recorded
        in the rollout so far, in order. Used by the watcher to show what was
        just approved (not just how many). For shell calls = the command; for
        apply_patch = "apply_patch <files>".

        Cached by rollout file size: the watcher polls every 0.3s and the rollout
        is append-only, so size unchanged => labels unchanged. Avoids re-parsing
        the entire rollout JSONL on every tick (matters on long runs / lots of
        tool calls). Cache is cleared at the start of run()."""
        try:
            size = rollout.stat().st_size
        except OSError:
            size = -1
        cached = self._label_cache
        if cached is not None and cached[0] == size:
            return cached[1]
        out = self._compute_escalation_labels(rollout)
        self._label_cache = (size, out)
        return out

    def _compute_escalation_labels(self, rollout: Path) -> list[str]:
        out: list[str] = []
        for rec in self._read_records(rollout):
            if rec.get("type") != "response_item":
                continue
            p = rec.get("payload", {})
            if not (isinstance(p, dict) and p.get("type") in _TOOL_ITEM_TYPES
                    and self._call_escalated(p)):
                continue
            if p.get("name") == "apply_patch":
                files = self._patch_files(p.get("input", ""))
                out.append(f"apply_patch {', '.join(files)}" if files else "apply_patch")
                continue
            try:
                args = json.loads(p.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                args = {}
            cmd = args.get("command") or p.get("name") or "?"
            out.append(self._oneline(cmd, 80))
        return out

    def _count_escalations(self, rollout: Path) -> int:
        """Total require_escalated tool calls recorded in the rollout so far."""
        return len(self._escalation_labels(rollout))

    @staticmethod
    def _drain(pty, sink: list[str]) -> None:
        try:
            while True:
                data = pty.read()
                if not data:
                    break
                sink.append(data)
        except EOFError:
            pass
        except Exception:  # noqa: BLE001 - pty closed/forcibly killed
            pass

    @staticmethod
    def _prepare(pty, screen: list[str], timeout: float) -> bool:
        """Get the chat input ready before typing the task. On a fresh dir codex
        first shows a 'Do you trust the contents of this directory?' dialog; we
        accept it with Enter, then wait for the chat banner and let it settle."""
        deadline = time.monotonic() + timeout
        trusted = False
        banner = False
        while time.monotonic() < deadline:
            blob = "".join(screen)
            low = blob.lower()
            if not trusted and ("do you trust" in low or "trust the contents" in low
                                or "yes, continue" in low):
                try:
                    pty.write("\r")  # accept "1. Yes, continue" (default)
                except Exception:  # noqa: BLE001
                    pass
                trusted = True
                time.sleep(2.0)
                continue
            if "OpenAI Codex" in blob:
                banner = True
                break
            time.sleep(0.3)
        time.sleep(3.0)  # settle for model load + chat input readiness
        return banner

    @staticmethod
    def _shutdown(pty) -> None:
        try:
            pid = pty.pid
        except Exception:  # noqa: BLE001
            pid = None
        if pid and os.name == "nt":
            _kill_pid_tree(pid)
        try:
            pty.terminate(force=True)
        except Exception:  # noqa: BLE001
            pass
        if pid and os.name != "nt":
            _kill_pid_tree(pid)

    # --- rollout file discovery + tailing -----------------------------------
    @staticmethod
    def _strip_ansi(s: str) -> str:
        import re
        s = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s)  # CSI sequences
        s = re.sub(r"\x1b[()][AB012]", "", s)          # charset selects
        s = re.sub(r"\x1b[=>]", "", s)
        return "".join(ch for ch in s if ch >= " " or ch in "\r\n\t")

    def _await_rollout(self, sessions: Path, before: set, workdir: Path,
                       max_wait: float) -> Path | None:
        target = str(workdir)
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            for f in sorted(glob.glob(str(sessions / "**" / "rollout-*.jsonl"), recursive=True),
                            key=os.path.getmtime, reverse=True):
                if f in before:
                    continue
                cwd = self._rollout_cwd(f)
                if cwd and os.path.normcase(cwd) == os.path.normcase(target):
                    return Path(f)
            time.sleep(0.5)
        return None

    @staticmethod
    def _rollout_cwd(path: str) -> str | None:
        try:
            with open(path, encoding="utf-8") as fh:
                first = fh.readline()
            meta = json.loads(first)
            if meta.get("type") == "session_meta":
                return meta.get("payload", {}).get("cwd")
        except (OSError, json.JSONDecodeError):
            pass
        return None

    def _await_complete(self, rollout: Path, timeout_s: int, start: float,
                        emit=None, debug: bool = False, stats: dict | None = None) -> bool:
        deadline = start + timeout_s
        seen = 0
        last_agent = ""
        while time.monotonic() < deadline:
            recs = self._read_records(rollout)
            for rec in recs[seen:]:
                if rec.get("type") == "event_msg":
                    et = rec.get("payload", {}).get("type")
                    if et == "agent_message":
                        last_agent = rec["payload"].get("message", "") or last_agent
                    if et == "token_count" and stats is not None:
                        # Push live counters to opts.stats so the runner's
                        # heartbeat can show whether codex is still working
                        # (count climbing) vs wedged (count flat).
                        self._update_stats(rec.get("payload", {}), stats)
                    if et == "task_complete":
                        if emit:
                            # Drain any still-pending watcher approvals before
                            # the terminal summary, so the log finishes cleanly.
                            self._drain_pending_approvals(emit, force=True)
                            if last_agent:
                                emit(f"final  {self._oneline(last_agent, 90)}")
                            emit(f"task_complete  ({self._count_escalations(rollout)} "
                                 f"escalation(s) auto-approved)")
                        seen = len(recs)
                        return True
                if emit:
                    line = self._fmt_event(rec, debug)
                    if line:
                        emit(line)
                    # Track escalations as we emit them; drain any pending
                    # watcher approval whose batch is now fully on screen.
                    p = rec.get("payload", {}) if isinstance(rec.get("payload"), dict) else {}
                    if (rec.get("type") == "response_item"
                            and p.get("type") in _TOOL_ITEM_TYPES
                            and self._call_escalated(p)):
                        with self._pending_lock:
                            self._emitted_esc_count[0] += 1
                            self._drain_pending_approvals(emit)
            seen = len(recs)
            time.sleep(1.0)
        # Loop ended without task_complete (timeout); drain any leftovers so the
        # caller's "CRASH" line isn't preceded by silently-dropped approvals.
        if emit:
            self._drain_pending_approvals(emit, force=True)
        return False

    def _drain_pending_approvals(self, emit, force: bool = False) -> None:
        """Emit any queued approval messages whose escalation batch is now
        fully on screen (force=True drains everything, used at task_complete
        / timeout). Caller MAY or MAY NOT already hold _pending_lock -- this
        method re-enters it if not held by checking via try/finally pattern.
        Simpler: callers either hold or don't hold; here we just assume held
        when called from inside the event loop, and acquire when force=True."""
        if force:
            with self._pending_lock:
                while self._pending_approvals:
                    _, m = self._pending_approvals.pop(0)
                    emit(m)
            return
        # Called with lock held (from inside _await_complete's loop):
        while (self._pending_approvals
               and self._pending_approvals[0][0] <= self._emitted_esc_count[0]):
            _, m = self._pending_approvals.pop(0)
            emit(m)

    @staticmethod
    def _update_stats(payload: dict, stats: dict) -> None:
        """Pull cumulative counters out of a token_count event_msg and push them
        into the shared stats dict. Schema (codex 0.135+):
          payload.info.total_token_usage.{total_tokens, reasoning_output_tokens}"""
        info = payload.get("info")
        if not isinstance(info, dict):
            return
        ttu = info.get("total_token_usage")
        if not isinstance(ttu, dict):
            return
        for k_src, k_dst in (("total_tokens", "tokens_total"),
                             ("reasoning_output_tokens", "reasoning_tokens")):
            v = ttu.get(k_src)
            if isinstance(v, int):
                stats[k_dst] = v

    @staticmethod
    def _oneline(s: str, limit: int) -> str:
        s = " ".join(str(s).split())
        return s if len(s) <= limit else s[:limit] + " …"

    @classmethod
    def _fmt_event(cls, rec: dict, debug: bool = False) -> str | None:
        """One progress line for a rollout record. Default view shows tool calls
        (real commands) + escalations; agent chatter and tool outputs are debug-only
        (task_complete and the final message are handled in _await_complete)."""
        t = rec.get("type")
        p = rec.get("payload", {}) if isinstance(rec.get("payload"), dict) else {}
        if t == "response_item":
            pt = p.get("type")
            if pt == "function_call":
                try:
                    args = json.loads(p.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}
                cmd = cls._oneline(args.get("command", ""), 100)
                if args.get("sandbox_permissions") == "require_escalated":
                    return f"ESCALATION requested  {cmd}"
                return f"cmd  {cmd}"
            if pt == "custom_tool_call":
                if p.get("name") == "apply_patch":
                    files = cls._patch_files(p.get("input", ""))
                    return f"apply_patch  ->  {', '.join(files)}" if files else "apply_patch"
                return f"{p.get('name')} (custom tool)"
            if pt == "function_call_output" and debug:
                return "  output received"
        if t == "event_msg":
            et = p.get("type")
            if debug and et == "agent_message":
                return f"agent  {cls._oneline(p.get('message', ''), 120)}"
            if debug and et in ("task_started", "patch_apply_end"):
                return f"({et})"
        return None

    @staticmethod
    def _patch_files(patch: str) -> list[str]:
        """Pull the touched filenames out of an apply_patch body."""
        files = []
        for line in str(patch).splitlines():
            for marker in ("*** Add File: ", "*** Update File: ", "*** Delete File: "):
                if line.startswith(marker):
                    files.append(line[len(marker):].strip())
        return files

    @staticmethod
    def _read_records(path: Path) -> list[dict]:
        out = []
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass
        return out

    @staticmethod
    def _rollout_metadata(rollout: Path) -> dict:
        meta = {"rollout_path": str(rollout)}
        try:
            with open(rollout, encoding="utf-8") as fh:
                first = fh.readline().strip()
            rec = json.loads(first) if first else {}
        except (OSError, json.JSONDecodeError):
            return meta
        if rec.get("type") != "session_meta":
            return meta
        payload = rec.get("payload", {}) if isinstance(rec.get("payload"), dict) else {}
        if isinstance(payload.get("cwd"), str):
            meta["rollout_cwd"] = payload["cwd"]
        for key in ("id", "session_id", "sessionId"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                meta["session_id"] = value
                break
        return meta
    # --- rollout -> RunResult ----------------------------------------------
    def _parse_rollout(self, rollout: Path) -> RunResult:
        records = self._read_records(rollout)
        tool_calls: list[dict] = []
        final = ""
        tokens = None
        for r in records:
            t = r.get("type")
            p = r.get("payload", {}) if isinstance(r.get("payload"), dict) else {}
            if t == "response_item" and p.get("type") in _TOOL_ITEM_TYPES:
                tool_calls.append({
                    "name": p.get("name"),
                    "input": p.get("arguments", p.get("input")),
                    "escalated": self._call_escalated(p),
                    "ts": r.get("timestamp"),  # ISO time, for per-step timing in trace
                })
            elif t == "event_msg":
                et = p.get("type")
                if et == "agent_message":
                    final = p.get("message", "") or final
                elif et == "token_count":
                    tokens = {k: v for k, v in p.items() if k != "type"}
        return RunResult(
            stdout="", stderr="", exit_code=0, final_message=final,
            tool_calls=tool_calls, events=records, tokens=tokens,
            metadata=self._rollout_metadata(rollout), error=None,
        )
