from .base import CliAdapter, RunOptions, RunResult
from .claude import ClaudeAdapter
from .codex import CodexAdapter, CodexAppServerAdapter, CodexTuiAdapter

ADAPTERS = {
    "codex": CodexAdapter,
    "codex-appserver": CodexAppServerAdapter,
    "codex-tui": CodexTuiAdapter,
    "claude": ClaudeAdapter,
}

__all__ = [
    "CliAdapter", "RunOptions", "RunResult",
    "CodexAdapter", "CodexAppServerAdapter", "CodexTuiAdapter", "ClaudeAdapter",
    "ADAPTERS",
]
