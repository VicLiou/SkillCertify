# Adapters

| adapter | 後端 | 說明 |
|---|---|---|
| `claude`(預設) | `claude -p --output-format stream-json` | 完整可用(約 30s/run) |
| `codex` | `codex exec --json` | 需要能無人值守(核准)的 codex |
| `codex-appserver` | `codex app-server` JSON-RPC,自動核准 | 給非 GUI 綁定的 codex |
| `codex-tui` | 互動 TUI(pywinpty 驅動)+ 解析 rollout | 可在受管控的組織版 codex 上運作 |

codex/codex-tui 的 CLI workdir 預設在 `.work`；需要真的執行 `command:` 斷言時請加 `--allow-exec`，只有需要其他受信任路徑時才覆寫 `--workdir-base`。

## 升權處理(codex-tui)

codex-tui 會自動寫一個 `$CODEX_HOME/skilltest_tui.config.toml`(內含 `sandbox_permissions = ["disk-full-read-access"]`),並以 `-p skilltest_tui`、`-C <workdir>`、`-c sandbox_permissions=["disk-full-read-access"]` 啟動。這讓測試 workdir 與讀檔權限明確套用,降低一般讀檔觸發升權的機率。`--add-dir` 不會用在 codex-tui,因為某些受管控 Codex 權限模式會把它視為額外 writable root 並拒絕啟動。

若 skill 仍有需要升權的操作(例如寫/刪 workspace 外的檔),內建的「升權守望」會自動核准:

- **結構化偵測**:讀 rollout 的 `require_escalated` 欄位,看 rollout 裡有多少 escalated function_call 還沒對應 output → 那麼多個 → 那麼多次 Enter
- **畫面比對 fallback**:如果 rollout 還沒寫入但畫面已經跳對話框,用關鍵字偵測補上
- **stuck-detection 診斷**:pending 數連續 10 秒不下降會印一行 `N escalated call(s) still waiting for output; pressing Enter until cleared` 告訴你它正在努力清

`--trace` 會保留 codex-tui 的 `metadata`,包含 rollout 檔路徑、session id、stderr tail；逾時時也會記錄尚未完成的升權數,方便事後追查。詳細的 codex-tui 內部運作可以看 [診斷工具](extending.md) 提供的 `inspect_rollout.py` / `tui_probe.py`。
