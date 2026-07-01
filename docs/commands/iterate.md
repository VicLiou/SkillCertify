# `iterate` 子命令

把 `run → fix-skill → run → fix-skill → ...` 整個迴圈自動跑,直到 100% 通過或踩到安全限制才停。每輪的 trace 都會存下來,你事後可以查看每一輪 architect 改了什麼。

```bash
# 最簡:跑 testcases 並讓框架幫你修 skill
skill-test iterate testcases/cub-code-review.yaml \
    --skill skills/cub-code-review

# 進階:多輪 + 動態驗證 + judge,真的把 skill 測穩
skill-test iterate testcases/cub-code-review.yaml \
    --skill skills/cub-code-review \
    --max-rounds 5 --runs-per-round 3 --allow-exec --judge
```

## 常用旗標

| 旗標 | 作用 |
|---|---|
| `testcases`(位置參數) | 一或多個 testcase YAML |
| `--skill PATH`(必填) | 要修的 skill 資料夾 |
| `--adapter` | 跑 testcase 用哪個 CLI(預設 `claude`) |
| `--binary PATH` | 覆寫 testcase 執行時使用的 CLI 執行檔路徑 |
| `--model MODEL` | testcase 執行使用的模型 |
| `--gen-adapter` | 跑 architect 的 CLI(預設 `claude`) |
| `--gen-binary PATH` | 覆寫 `--gen-adapter` 使用的 CLI 執行檔路徑 |
| `--architect-model MODEL` | fix-skill architect 執行使用的模型 |
| `--max-rounds N` | 硬上限,預設 3(防止越改越糟無限循環) |
| `--no-improve-budget N` | 連續 N 輪 pass rate 沒上升就停,預設 1 |
| `--runs-per-round N` | 每輪每個 case 跑幾次,預設 1(快;穩定性測試調 3~5) |
| `--require-stable-flow` | 嚴格模式:pass rate 必須 100%，且每個 case 都只能有一條 tool flow 才算收斂；需要 `--runs-per-round >= 2` |
| `--allow-exec` / `--judge` | 同 [`run`](run.md) |
| `--judge-adapter` | judge 執行使用的 adapter |
| `--judge-model MODEL` / `--judge-binary PATH` | judge 執行的模型與執行檔覆寫 |
| `--workdir-base DIR` | run/fix/judge 各輪使用的 workdir base；預設 `.work`，需要時才覆寫 |
| `--trace-dir DIR` | 每輪 trace 存哪,預設 `.iterate-traces/` |
| `--fix-scope` | 每輪 [`fix-skill`](fix-skill.md) 用什麼 scope,預設 `focused` |
| `--architect-skill PATH` | fix-skill 各輪使用的 architect skill 路徑；未指定時會自動搜尋目前專案 `tools/skills/...`，再 fallback 到 skill-auto-test 內建 architect |
| `--architect-timeout-s N` | 每次 architect 修補執行的逾時秒數 |

## 執行前檢查

在進行任何模型呼叫前，`iterate` 會先檢查:

- testcase YAML 通過靜態驗證，規則同一般 run 指令。
- `--skill` 存在，且包含 `SKILL.md`。
- 解析後的 architect skill 存在，且包含 `SKILL.md`；未指定 `--architect-skill` 時會自動找本地 `tools/skills/...`，再找 skill-auto-test 內建路徑。
- 每份 testcase document 的 `skill:` 都指向和 `--skill` 相同的資料夾。
- 數字限制都是正整數。
- `--require-stable-flow` 只能搭配 `--runs-per-round >= 2`。

## 停止條件

1. **Converged**:pass rate 是 100%。若使用 `--require-stable-flow`，每個 case 也必須只有一條穩定 tool flow。只有這個條件會回傳 exit code `0`；通過率 100% 但 flow 不穩仍會回傳 `1`。
2. **達到 `--max-rounds`**:回傳 exit code `1`。
3. **沒有改善**:pass rate 沒上升；若使用 `--require-stable-flow`，flow 分歧減少也算有進展。回傳 exit code `1`。
4. **只有 skipped 造成失敗**:通常是缺 `--allow-exec` 或 `--judge`；`iterate` 會停止，不會把 runner 設定問題丟給 `fix-skill`。
5. **fix-skill 沒有產生變更**:`iterate` 會立刻停止，不再多花一輪重跑相同 skill。
6. **fix-skill 失敗**:`iterate` 會停止，並保留該輪 trace。

## 預期輸出

每輪結束都會印一行 `[case] X/Y (Z%)`,最後印整體 summary:

```
===== ITERATE summary =====
Ran 3 round(s):
  FAIL round 1: 6/10 runs passed (60%)  (fix-skill changed: SKILL.md)
  FAIL round 2: 9/10 runs passed (90%)  (fix-skill changed: SKILL.md, gotchas.md)
  PASS round 3: 10/10 runs passed (100%, stop=converged)

Final: ALL GREEN. skill is stable.
```

## 如果中途停了

- `.iterate-traces/round-N.json` 是每輪的 trace,找最後一輪失敗的細節
- `skills/<name>.bak.<timestamp>/` 是 architect 每次改 skill 之前的備份,可以還原到任何一輪之前的狀態

## `--skill` 必須和 testcase 的 `skill:` 對齊

`iterate` 會用 YAML 的 `skill:` 欄位執行每個 testcase，再把 `fix-skill` 套到 `--skill`。如果兩者指向不同資料夾，就會變成測 A 但改 B；現在會在第一次模型呼叫前直接拒絕。
