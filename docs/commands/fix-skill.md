# `fix-skill` 子命令

跑完測試發現有 testcase fail,你想知道:**這個 skill 該怎麼改才能擋住下次同樣的失敗**?`fix-skill` 把 trace.json 餵給 `interactive-skill-architect`(一個專門做 skill 健檢/優化的工具型 skill；預設會先找目前專案的 `tools/skills/interactive-skill-architect/`，找不到就使用 skill-auto-test 內建的那份),讓它在隔離 workdir 裡幫你動 skill,並把建議 diff 回來給你 review。

```bash
# dry-run:印 diff 但不改原 skill
skill-test fix-skill skills/cub-code-review \
    --trace cub-code-review-trace.json

# --apply:確認 diff 沒問題後,真的覆寫原 skill(自動先備份)
skill-test fix-skill skills/cub-code-review \
    --trace cub-code-review-trace.json --apply
```

預設 `--scope focused`,只修「跟這次失敗證據有關」的部分,不做整套 13 項健檢(健檢用 `--scope full`,或乾脆改用 [`check-skill`](check-skill.md))。

## 常用旗標

| 旗標 | 作用 |
|---|---|
| `--trace FILE`(必填) | 從這份 trace.json 讀失敗證據(framework 會自動把失敗按 assertion 分群) |
| `--adapter` | 用哪個 CLI 跑 architect(預設 `claude`) |
| `--binary PATH` | 覆寫 `--adapter` 使用的 CLI 執行檔路徑 |
| `--model MODEL` | architect 執行使用的模型 |
| `--scope {full,focused,style}` | 對應 architect 的 Phase O1 範圍選擇——`full`=13 項健檢、`focused`=只修這次失敗(預設)、`style`=只做風格對齊 |
| `--case SUBSTR` | 只餵失敗 case 名稱含這段字串的(預設全部) |
| `--constraint TEXT` | 額外限制傳給 architect(疊加在「不能改 Hard Gates / Step / 放行停止條件」之上) |
| `--apply` | 把 architect 的修改寫回原 skill(預設 dry-run) |
| `--architect-skill PATH` | 換用其他 architect skill；未指定時會自動搜尋目前專案 `tools/skills/...`，再 fallback 到 skill-auto-test 內建 architect |
| `--workdir-base DIR` | architect 執行用 workdir base；預設 `.work`，需要時才覆寫 |
| `--timeout-s` | architect 跑的逾時(預設 600,因為健檢通常較長) |

## 設計上的硬限制

`fix-skill` 在傳給 architect 的 prompt 內顯式禁止:

- 修改既有 Hard Gates 的條件 / 放行條件 / 停止條件
- 改變 Step 的執行順序
- 刪除任何既有規則
- 改變既有 disposition 選項清單 / input contract 的「必須」要求

允許的修改只能是「**追加性**」的——加 Gotchas / 加全新的 Gate / 加格式提醒 / 加工具禁令 / 加釐清說明。這個限制疊加在 architect 本身的閘門規則之上,**雙重保護**「動到流程判斷」這種高風險改動。

**dry-run 是預設**:`fix-skill` 不加 `--apply` 永遠不會動到原 skill,只是把 architect 在隔離 workdir 內做的修改 diff 出來給你看。確認 diff 看起來合理才加 `--apply`(會自動先把原檔備份到 `skills/<name>.bak.<timestamp>/`)。

## 跟其他指令的選擇

- **多個 case 的多種斷言一起失敗 + judge 都過** → `fix-skill`(skill 行為錯)
- **一個或少數斷言對不上實際輸出 + 模型語意是對的** → [`fix-testcase`](fix-testcase.md)(斷言寫死了)
- **想全自動「測 → 修 → 再測」直到全綠** → [`iterate`](iterate.md)
- **沒 trace,純粹想知道 skill 寫得好不好** → [`check-skill`](check-skill.md)
