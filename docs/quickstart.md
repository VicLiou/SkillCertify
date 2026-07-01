# 快速上手:跑一個內建範例(5 分鐘)

這個 repo 自帶一個最小範例 `skills/example-skill`(一個叫使用者「打招呼並把訊息寫進 `out.txt`」的 skill),以及對應的 testcase `testcases/claude-example.yaml`。先用它確認整套東西能跑通。

## 1. 跑一次

```bash
skill-test testcases/claude-example.yaml --adapter claude --runs 1
```

(預設 testcase 裡寫了 `runs: 5`,但加 `--runs 1` 旗標可以覆寫成只跑 1 次,先求最快驗證能跑。)

## 2. 預期看到的輸出

大約 30 秒後,終端機會印出類似這樣的東西(行數會略有不同):

```
===== START 2026-06-20 14:05:18 | 1 case(s), 1 run(s), adapter=claude =====
[run 1/1] start  (adapter=claude)
[run 1/1] PASS in 27s

=== claude-greet-happy-path  (skills/example-skill, progressive) ===
  pass rate : 1/1  (100%)
  latency   : min 27341  p50 27341  max 27341 ms
  all checks stable
  flow      : stable (1 path)
===== END 2026-06-20 14:05:45 | elapsed 0m27s (27s) =====
```

關鍵看這幾行:

| 看到 | 代表 |
|---|---|
| `PASS in 27s` | 這一次跑過了 |
| `pass rate : 1/1 (100%)` | 這個 case 100% 通過 |
| `all checks stable` | 沒有任何斷言失敗 |
| `flow : stable (1 path)` | 流程穩定(就 1 條路徑) |

最終的結束碼會是 `0`(所有 case 都 100% 過時才 0,適合 CI)。如果你看到的就是這樣 — 恭喜,環境 OK,可以進下一節跑你自己的 skill。

## 3. 如果不是這樣怎麼辦

- **`claude binary not found`** → 你的 `claude` 沒裝好,或不在 PATH。先在另一個終端機跑 `claude --version` 確認。
- **跑了很久沒結束** → 可能 `claude -p` 那一端在等什麼。見 [常見錯誤排查](troubleshooting.md)。
- **PASS 但 `pass rate < 100%`** → 罕見,通常代表 skill 本身在這次跑出了非預期的輸出。可以加 `--trace trace.json` 然後打開 `trace.json` 看細節(細節格式見 [如何看 log 與報告](logs.md))。

接下來:[完整教學:從零測你自己的 skill](tutorial.md)。
