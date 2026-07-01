# 常見錯誤排查

| 看到的訊息 | 通常代表 | 怎麼修 |
|---|---|---|
| `claude binary not found` / `codex binary not found` | 對應的 CLI 沒裝,或不在 PATH | 另開終端機跑 `claude --version` / `codex --version` 確認;或用 `--binary "C:\path\to\claude.cmd"` 指路徑 |
| `pywinpty not installed` | 你用了 `codex-tui` 但 Windows 上沒裝 pywinpty | `pip install pywinpty` |
| `no rollout file appeared` | codex-tui 啟動了但 codex 沒開始跑這回合 | 通常是任務沒送進去(信任對話框、輸入框問題);加 `--debug` 看畫面 tail |
| `timeout after 600s (no task_complete)` | codex 跑超時沒結束 | 看 trace 的 `flow` 最後一步在做什麼;skill 可能要求 codex 跑很久的指令、或卡在等對話框 |
| log 卡在某個 `ESCALATION requested` 後沒有對應 `-> approved by watcher` | 升權守望沒接到那次升權 | 偶發;若常態出現,加 `--debug` 看是否畫面用了非預期字樣。注:升權守望現在會以「rollout 中還有幾筆 escalated call 沒收到 output」作為「該不該再按 Enter」的依據,連續批次的升權應該都會被清掉 |
| `7 escalated call(s) still waiting for output; pressing Enter until cleared` | 升權守望偵測到 rollout 已經 10 秒沒新事件、但仍有 escalated function_call 沒收到對應 output | 正常診斷訊息——守望正在持續按 Enter 試圖清掉卡住的對話框佇列;不需要動作。如果按了 N 秒之後 token 仍然沒長(看心跳的 `+0 tokens`),才是 codex 真的掛了 |
| 看到很久只有 `...still running` 沒別的 | 看 adapter:`claude` / `codex` 不會逐步回報,全程心跳是正常的;`codex-tui` 全程心跳代表卡住了 | 前者等 `timeout_s`;後者看上一行卡在哪步 |
| `reads_file` 一直 fail 但模型明明讀了 | 已修復(早期版本只認 Claude 的 `Read` 工具,不認 codex 的 shell `Get-Content`/`cat`) | 確認你用的是最新版本 |
| `UnicodeEncodeError` / 中文變問號 | Windows 終端機編碼問題(已強制 utf-8,殘留時可能還會看到) | 一般不影響跑測試,只是 log 字壞掉;用 `2> run.log` 把 log 倒進檔案就會是正常 utf-8 |
| `LLM output contained no YAML documents`(`generate` 子命令) | LLM 沒生出合法 YAML | 重跑一次;或用[手動方式自己貼 prompt](manual-prompt.md) 拿輸出,看是哪邊出錯 |

更多 log / 心跳的意義 → [如何看 log 與報告](logs.md)。
