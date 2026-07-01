# 如何看 log 與報告

這篇講執行測試時終端機印出的 log 怎麼讀、`--out` / `--trace` 寫出來的 JSON 怎麼用。

## log 去哪、報告去哪(三者分開)

| 輸出 | 內容 | 去向 |
|---|---|---|
| 進度 / 事件 / 升權 log | 本節說明的東西 | **stderr** |
| `--out FILE` | 彙總報告(pass rate、latency…) | **JSON 檔** |
| `--trace FILE` | 逐次明細(tool_calls、final_message、各 check…) | **JSON 檔** |

**`--out` / `--trace` 都不含這些 log;log 只在 stderr。** 報告本體(那段 `=== ... pass rate ...`)印在 stdout。若要把 log 存檔,執行時導出 stderr:

```bash
skill-test ... 2> run.log
```

## 三種詳細度

| 模式 | 看到什麼 |
|---|---|
| `--quiet` | 只有 START/END + 每次 `PASS/FAIL` |
| 預設 | 下面的乾淨逐步視圖 |
| `--debug` | 全部:原始指令、agent 多行訊息、工具輸出、每一次升權核准動作 |

## 一次執行的 log 長相(預設模式)

**單一 case、多個 run**(沒有 banner):

```
===== START 14:05:18 | 1 case(s), 5 run(s), adapter=codex-tui =====
[run 1/5] start  (adapter=codex-tui)
[run 1/5]   launched with read-access profile 'skilltest_tui' (-p) and explicit workspace read config
[run 1/5]   turn started; streaming events...
[run 1/5]   cmd  Get-Content -LiteralPath .\.codex_tui_prompt.txt -Encoding UTF8
[run 1/5]   ESCALATION requested  Get-Content references/00-review-standards.md
[run 1/5]   ESCALATION requested  Get-Content references/01-design-functionality.md
[run 1/5]     -> approved by watcher [rollout] (+2 in batch)
[run 1/5]   apply_patch  ->  fizzbuzz.py, test_fizzbuzz.py
[run 1/5]   final  已產生 fizzbuzz.py 與 test_fizzbuzz.py
[run 1/5]   task_complete  (2 escalation(s) auto-approved)
[run 1/5] PASS in 53s
...
===== END 14:09:56 | elapsed 4m37s (277s) =====
```

**多個 case**(case 切換處印 banner,case 內維持 `[run i/N]` 短前綴,**全部跑完後印一塊 FINAL REPORT** 集中所有結果):

```
===== START 14:05:18 | 3 case(s), 3 run(s), adapter=codex-tui =====

=== case 1/3: my-skill-happy-path ============================
[run 1/1] start  (adapter=codex-tui)
[run 1/1]   ...
[run 1/1] PASS in 53s

  ↑ 這裡 run_testcase 結束時會即時印一段 per-case 摘要
    (pass rate / flow / flaky checks);讓你能中途判斷
    「進度看起來怎樣、要不要 ctrl-C 省 token」

=== case 2/3: my-skill-edge-case =============================
[run 1/1] PASS in 41s
=== case 3/3: my-skill-paraphrase ============================
[run 1/1] FAIL in 28s

===== FINAL REPORT =====
Cases: 3

Summary
   #  Result   Case                       Pass   Rate     Flows  Issues
  ----------------------------------------------------------------------
   1  PASS     my-skill-happy-path          5/5    100%    stable  ok
   2  PASS     my-skill-edge-case           5/5    100%    stable  ok
   3  FAIL     my-skill-paraphrase          0/5      0%   2 paths  1 check

Totals
  Runs passed : 10/15 (67%)
  Result      : FAIL

Failures
  3. my-skill-paraphrase  0/5 (0%)
     - regex=done
       failed 5/5
  Tip: rerun with --trace trace.json for full per-run details and untruncated assertion text.
===== END 14:09:56 | elapsed 4m37s (277s) =====
```

**FINAL REPORT 區塊**:per-case 摘要會跟著 log 走、容易被下一個 case 蓋掉,所以在 END 前統一再印一次集中所有結果。**只在多 case 時才印**(單 case 的話它本來就在 END 上面一行,沒必要重複)。

## 每一行怎麼讀

- **`===== START ... =====`** / **`===== END ... =====`**:開始/結束時間戳、case/run 數、adapter;END 含**總耗時**。一定會印(不受 `--quiet` 影響)。
- **`=== case j/M: <name> =================`** —— **只在同時測多個 testcase 時**,case 切換處的橫向 banner,把不同 case 視覺切開。單一 case 不會印這行。
- **`[run i/N]`**:統一前綴,代表第 i 次(共 N 次)。同一個 case 內的所有子步驟都用這個前綴(case 訊息靠上面的 banner 告訴你,不再每行重複)。
- **`[run i/N] start`**:這一次開始。
- **`cmd  <指令>`**:CLI 實際下的一個指令(保留原文,方便你確認步驟)。
- **升權(成對出現,這是重點)**:
  - **`ESCALATION requested  <指令>`** —— CLI **要求升權**(該操作被沙箱擋下,需要核准)。批次升權時會連續印多行。
  - **`  -> approved by watcher [rollout] (+N in batch)`** —— **升權守望自動核准了**它們。`(+N in batch)` 代表這個 Enter 一次接住了上面 N 個 `ESCALATION requested`。`[rollout]` 表示用結構化偵測(讀 rollout 的 `require_escalated` 欄位);`[screen]` 表示用畫面文字比對接到的(無批次數字)。
  - `approved` 永遠出現在它涵蓋的那批 `ESCALATION requested` **之後**,讓你能從上往下對應「請求 → 核准」的因果順序。
- **`apply_patch  ->  <檔名清單>`**:codex 用內建的 patch 工具寫/改檔(列出動到哪些檔)。
- **`final  <一行>`**:這一次的最終訊息(收斂成一行;完整內容在 `--trace`)。
- **`task_complete (N escalation(s) auto-approved)`**:這一輪完成,並統計總共自動核准了幾次升權。
- **`[run i/N] PASS|FAIL|CRASH in 53s`**:這一次的結果與耗時。`CRASH` = adapter 層級失敗(例如逾時被砍)。未開 `--keep-failed` 時，CLI 會在這之後清掉該次 workdir；若清理失敗，會印 `warning: failed to remove workdir ...` 和 `kept workdir after cleanup failure: ...`。
- **心跳 `...still running (120s, no new events for 30s, +N tokens since last)`**:超過 25 秒沒有新事件就會印一次(之後大約每 30 秒重複)。`+N tokens since last` **只有 codex-tui 有**(它會把 codex 即時的 token 累計推給 runner)。意思依 adapter 不同:
  - 用 `codex-tui` 時,正常情況下每個工具呼叫都會即時印一行。看到心跳代表卡住了,看 `+N tokens` 判斷怎麼卡:
    - **`+0 tokens since last` 連續出現** → 連 token 都沒長,codex 那邊真的在等 API 回應(模型沒在動);
    - **`+M tokens since last` M 持續增加但沒新指令** → 模型在做很深的 reasoning,只是還沒切到 acting 階段——不一定是壞事,但要看你願不願意等。
  - 用 `claude` 或 `codex`(exec 模式)時,這兩個 adapter **本身不會逐步回報進度**,只會整個跑完才一次性回傳結果,所以中間全程只看得到心跳(且沒有 `+tokens` 標註)是正常現象,不代表卡住——真正要看的是有沒有在 `timeout_s` 內結束(超時會變成 `CRASH`)。

## 怎麼用 log 抓問題

- **卡在某個 `ESCALATION requested` 之後沒有 `-> approved`** → 升權守望沒接到那次升權(可能字樣/偵測漏掉);現在守望會自動偵測「rollout 中還有幾筆 escalated call 沒對應 output」並持續按 Enter,所以這個情境應該很罕見。
- **印到某一步就只剩心跳** → codex 卡在那一步(看上一行是哪個指令)。
- **完全沒有 `turn started`** → 任務沒送進 TUI(信任對話框或輸入框問題);改用 `--debug` 看更多。
- **要看每次升權的實際按鍵與原始指令** → 加 `--debug`。

---

## `--trace` 檔格式

`--trace` 輸出一個 JSON 陣列,每個元素是「一次 run」的完整明細。工具細節有**三層**,由淺到深:

| 欄位 | 內容 |
|---|---|
| `tool_sequence` | 只有工具名稱清單(最快掃) |
| `flow` | **解碼後的逐步視圖** —— 每步 `{step, tool, command, escalated, files, at_ms}`,直接看到指令、有沒有升權、apply_patch 動到哪些檔、距開始幾毫秒 |
| `tool_calls` | 原始紀錄(完整忠實,供查證) |
| `token_timeline` | **(只 codex-tui 有)** token 累計快照,每筆 `{at_ms, total_tokens, reasoning_tokens}`;沒變動的快照會被去重,只留「總數真的有變」的時刻。可以一眼看出模型在哪段時間活著、哪段時間裝死 |
| `metadata` | adapter 診斷資料；`codex-tui` 會寫 `rollout_path`、`session_id`、`rollout_cwd`、`stderr_tail`,逾時時也會寫 `pending_escalations_at_timeout` |

其他欄位:`run`(1-based) / `run_label` / `adapter` / `model` / `load_strategy` / `started_at` / `passed` / `exit_code` / `error` / `latency_ms` / `workdir`(該 run 的隔離目錄；搭配 `--keep-failed` 可檢查失敗產物) / `escalations`(這次自動核准幾次升權) / `tokens_total`(跨 adapter 可比的單一數字) / `tokens`(原始) / `metadata`(adapter 診斷) / `final_message` / `checks`(每條斷言結果)。

```jsonc
{
  "run": 1, "run_label": "1/5", "adapter": "codex-tui", "load_strategy": "progressive",
  "started_at": "2026-06-20T14:05:18", "passed": true, "latency_ms": 53295, "workdir": ".work/skilltest_abc123",
  "escalations": 4, "tokens_total": 149014,
  "tool_sequence": ["shell_command", "shell_command", "apply_patch"],
  "flow": [
    {"step": 1, "tool": "shell_command", "command": "Get-Content ...SKILL.md", "escalated": false, "at_ms": 0},
    {"step": 2, "tool": "shell_command", "command": "Get-Content ...SKILL.md", "escalated": true,  "at_ms": 2500},
    {"step": 3, "tool": "apply_patch", "files": ["fizzbuzz.py", "test_fizzbuzz.py"], "at_ms": 16900}
  ],
  "token_timeline": [
    {"at_ms":     0, "total_tokens":  1000, "reasoning_tokens":    0},
    {"at_ms": 15000, "total_tokens": 25000, "reasoning_tokens":  200},
    {"at_ms": 65000, "total_tokens": 92892, "reasoning_tokens": 1098}
    // 之後沒有任何 token 累積變化 → 模型那邊真的沒在動,卡的不是測試框架
  ],
  "final_message": "...", "tool_calls": [ /* 原始 */ ], "checks": [ ... ]
}
```

> **怎麼用 `token_timeline` 抓「為什麼卡」**:跑 timeout 的 run 看這個欄位:如果最後一筆的 `at_ms` 遠小於 `latency_ms`(例如 65000 vs 600000),代表「token 累計在 65 秒就停了」、剩下 535 秒模型根本沒輸出——通常是 codex 在等永遠不會回來的 API 回應,不是模型在深度思考。如果最後一筆 `at_ms` 接近 `latency_ms`,代表模型一直有在累積 token,只是慢——可以試著拉高 `timeout_s`。

## 怎麼讀印出來的報告

```
pass rate : 4/5 (80%)        ← ① 過不過(結果對嗎)
flow      : 2 DISTINCT paths ← ② 流程穩嗎(是噪音還是真分歧)
flaky/failed checks: ...     ← ③ 哪條斷言在飄 -> 去 --trace 查那筆
```

不是所有流程分歧都是壞事(例如 Skill-tool-vs-直接-Read 是無害的機制噪音);只對 skill 真正的協議下斷言。
