# 擴充與診斷

## 加另一個 CLI(新 adapter)

在 `runner/adapters/` 寫一個有 `run(prompt, workdir, opts) -> RunResult` 的類別,在 `runner/adapters/__init__.py:ADAPTERS` 註冊即可。其他都不用動 —— 你甚至能拿同一份 skill 在不同 CLI 之間做 A/B。

最小範例:

```python
# runner/adapters/myllm/adapter.py
from ..base import RunOptions, RunResult

class MyLlmAdapter:
    name = "myllm"
    def __init__(self, binary: str = "myllm"):
        self.binary = binary
    def run(self, prompt: str, workdir: Path, opts: RunOptions) -> RunResult:
        # 呼叫 myllm,解析輸出,組 RunResult 回傳
        ...
```

然後在 `runner/adapters/__init__.py`:

```python
from .myllm.adapter import MyLlmAdapter
ADAPTERS["myllm"] = MyLlmAdapter
```

跑測試時就能 `--adapter myllm`。

`CliAdapter` 介面與 `RunResult`/`RunOptions` 結構見 [`runner/adapters/base.py`](../runner/adapters/base.py)。

## 診斷工具

從專案根目錄執行(主要給 `codex-tui` 除錯用):

| 指令 | 做什麼 |
|---|---|
| `python tools/codex/inspect_rollout.py` | dump 最新 codex rollout 的事件類型 / turn_context |
| `python tools/codex/tui_probe.py` | 在 PTY 下探測 codex TUI(信任對話框、打字、rollout) |
| `python tools/codex/probe_appserver.py` | 探測 codex app-server 的 JSON-RPC 握手 |

這些不是日常會用的指令,只在 `codex-tui` 行為怪異時用來看「codex 那邊到底發生什麼事」。
