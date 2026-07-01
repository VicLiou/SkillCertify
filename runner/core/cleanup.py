"""Best-effort cleanup helpers for per-run workdirs.

Windows agents can briefly keep files locked after the adapter reports
completion, and some sandboxed tools can leave restrictive ACLs on generated
workdirs. Cleanup should be aggressive enough to remove normal temp data, but
never hide a final failure from the user.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time
from pathlib import Path
from typing import Callable

LogFn = Callable[[str], None]


def _on_rm_error(func, path, exc_info) -> None:
    """Handle read-only files during shutil.rmtree on Windows/POSIX."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(path)
    except Exception:  # noqa: BLE001 - let the outer retry handle the real error
        pass


def _windows_account() -> str | None:
    user = os.environ.get("USERNAME")
    if not user:
        return None
    domain = os.environ.get("USERDOMAIN")
    if domain:
        return f"{domain}\\{user}"
    return user


def _repair_windows_permissions(path: Path) -> None:
    """Try to regain access to a temp tree before deleting it.

    This is intentionally best-effort. `icacls` may fail when a managed sandbox
    creates ACLs the current user cannot alter; in that case callers still get a
    visible cleanup warning instead of a silent leftover directory.
    """
    if os.name != "nt":
        return
    account = _windows_account()
    if not account:
        return
    try:
        subprocess.run(
            [
                "icacls",
                str(path),
                "/grant",
                f"{account}:(OI)(CI)F",
                "/T",
                "/C",
                "/Q",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        pass

def ensure_tree_accessible(path: str | Path, *, log_fn: LogFn | None = None) -> bool:
    """Best-effort repair before keeping a workdir for user inspection.

    Some sandboxed Windows agents leave temp trees owned by transient SIDs. If
    `--keep-failed` preserves such a tree without repairing ACLs, the user sees
    the path but cannot open it. This mirrors cleanup's permission repair but
    leaves the directory in place.
    """
    target = Path(path)
    if not target.exists():
        return True

    _repair_windows_permissions(target)
    try:
        if target.is_dir():
            # Touch the iterator so permission errors surface now, while the
            # runner can still warn with the kept path in context.
            next(target.iterdir(), None)
        else:
            target.stat()
    except BaseException as exc:  # noqa: BLE001 - diagnostic only
        if log_fn:
            log_fn(f"warning: kept workdir may not be readable: {target}: {exc}")
        return False
    return True

def remove_tree(path: str | Path, *, retries: int = 5, delay_s: float = 0.25,
                log_fn: LogFn | None = None) -> bool:
    """Remove a workdir tree, retrying transient locks and permission issues.

    Returns True when the directory is gone. Returns False after all retries and
    emits a warning through log_fn when provided.
    """
    target = Path(path)
    if not target.exists():
        return True

    last_error: BaseException | None = None
    for attempt in range(max(1, retries)):
        try:
            shutil.rmtree(target, onerror=_on_rm_error)
        except FileNotFoundError:
            return True
        except BaseException as exc:  # noqa: BLE001 - report after retries
            last_error = exc
        else:
            return True

        _repair_windows_permissions(target)
        if attempt < retries - 1:
            time.sleep(delay_s * (attempt + 1))

    if not target.exists():
        return True

    if log_fn:
        detail = f": {last_error}" if last_error else ""
        log_fn(f"warning: failed to remove workdir {target}{detail}")
    return False