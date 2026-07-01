"""Shared helpers for the Codex adapters (exec / app-server / tui)."""
from __future__ import annotations

import os
import shutil
import subprocess


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill the process and all its children. On Windows, taskkill /T is the only
    reliable way to take down a `cmd /c` -> codex -> node tree; proc.kill() alone
    leaves grandchildren running with the pipes still open."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        try:
            proc.kill()
        except OSError:
            pass


def resolve_launcher(binary: str) -> list[str] | None:
    """Resolve a CLI executable to a runnable command prefix. On Windows, npm
    installs codex as codex.cmd, which subprocess can't find ('codex' only
    auto-resolves to .exe) nor execute directly (.cmd needs `cmd /c`)."""
    resolved = shutil.which(binary)
    if resolved is None:
        return None
    if os.name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", resolved]
    return [resolved]
