# 預設指令(跑測試)

直接打 `skill-test testcases/...yaml [旗標]` 就是「跑測試」,沒有「`run`」這個子命令字。它讀 testcase YAML、執行 skill、評估斷言、印報告。

```bash
skill-test testcases/<name>.yaml --adapter claude --runs 5
```

## 所有旗標

| 旗標 | 作用 |
|---|---|
| `--adapter` | `claude`(預設)/ `codex` / `codex-appserver` / `codex-tui` 之一 |
| `--binary PATH` | 指定 CLI 執行檔路徑(預設靠 PATH 找) |
| `--runs N` | 覆寫每個 case 的次數；必須是正整數 |
| `--require-stable-flow` | 嚴格模式:任何 case 出現多條 tool flow 都算失敗；每個 case 至少要跑 2 次 |
| `--model M` | 覆寫所有 case 的模型 |
| `--judge` | 啟用 LLM-judge 跑 `judge:` 斷言(會花 token) |
| `--judge-adapter` | judge 用哪個後端(預設 `claude`) |
| `--judge-model` / `--judge-binary` | judge 用的模型/執行檔 |
| `--allow-exec` | 允許 `command:` 斷言真的執行(會跑模型產生的程式碼!) |
| `--workdir-base DIR` | 每次 run 的 workdir base；預設 `.work`，需要時才覆寫 |
| `--out FILE` | 寫彙總 JSON 報告；父資料夾會自動建立 |
| `--trace FILE` | 寫逐次明細 JSON；父資料夾會自動建立 |
| `--keep-failed` | 保留失敗 run 的 workdir，並印出 `kept failed workdir: ...` 供檢查 |
| `--quiet` | 關掉逐步進度 log(仍會印 START/END 與每次結果) |
| `--debug` | 加倍詳細:原始指令、agent 訊息、工具輸出 |

未開 `--keep-failed` 時，run 結束後會清掉該次 workdir。若 Windows 權限或檔案鎖導致清理失敗，CLI 會印出 warning 並列出保留下來的路徑。

`skill-test` 結束碼:全部 case 100% 通過時 `0`,任何 case 沒過 `1`(適合 CI)。若使用 `--require-stable-flow`,即使 pass rate 是 100%,只要 flow 不穩也會回傳 `1`。

## 閱讀報告

單一 case 仍會立即印出該 case 的摘要:

```text
pass rate : 4/5 (80%)        <- 結果穩定度
flow      : 2 DISTINCT paths <- 工具流程穩定度
flaky/failed checks: ...     <- 失敗或飄動的斷言；細節看 --trace
```

多個 case 一起跑時，最後的區塊會分成:

```text
Summary  -> 每個 case 的精簡表格
Totals   -> 彙總 pass rate 與最終結果
Failures -> 只列失敗或 crash 的 case，過長斷言會截短
```

不是每個 flow 分歧都是 bug。當流程穩定性是 skill contract 的一部分時，加 `--require-stable-flow` 讓 CI 將 flow 分歧視為失敗，並用 `--trace` 追查。

## 跑完後的下一步

執行 `skill-test testcases/<name>.yaml ...` 後，CLI 會依結果提示下一步可用指令:

- 全部通過:提高 `--runs`、加上 `--trace trace.json`，或用 `validate --strict` 檢查 testcase。
- 失敗且沒有 trace:先加 `--trace trace.json --keep-failed` 重跑。
- 已有 trace 但仍失敗:使用 `fix-skill`、`fix-testcase`，再重跑該 testcase。

更多 log 細節 -> [logs](../logs.md)
