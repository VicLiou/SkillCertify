# `fix-testcase` 子命令

跟 [`fix-skill`](fix-skill.md) 互補——當你看 trace 判斷「**這次是 testcase 寫太嚴,skill 沒問題**」時,用 `fix-testcase` 把失敗 pattern 當提示餵給 [`generate`](generate.md),重產一份**斷言對齊 skill 真實行為**的 testcase。

```bash
# dry-run 看新 testcase 長什麼樣:
skill-test fix-testcase testcases/cub-code-review.yaml \
    --trace cub-code-review-trace.json

# 確認 OK 就 --apply 寫回(自動備份原檔到 .bak.<時間戳記>.yaml)
skill-test fix-testcase testcases/cub-code-review.yaml \
    --trace cub-code-review-trace.json --apply
```

## 常用旗標

| 旗標 | 作用 |
|---|---|
| `--trace FILE`(必填) | 從這份 trace.json 讀失敗證據 |
| `--adapter` | 用哪個 CLI(預設 `claude`) |
| `--binary PATH` | 覆寫 `--adapter` 使用的 CLI 執行檔路徑 |
| `--model MODEL` | testcase 重產使用的模型 |
| `--case SUBSTR` | 只餵 case 名稱含這段字串的失敗 |
| `--coverage` / `--bias` | 同 [`generate`](generate.md),控制重產的 testcase 範圍與偏好 |
| `--hint TEXT` | 額外提示(疊加在自動產出的失敗修正 hint 之上) |
| `--apply` | 真的寫回(預設 dry-run) |
| `--workdir-base DIR` | 重產用 workdir base；預設 `.work`，需要時才覆寫 |
| `--timeout-s` | 預設 240 |

## 跟 fix-skill 的快速判斷

- **多個 case 的多種斷言一起失敗 + judge 都過** → [`fix-skill`](fix-skill.md)(skill 行為錯)
- **一個或少數斷言對不上實際輸出 + 模型語意是對的** → `fix-testcase`(斷言寫死了)
- **不確定** → 先 `fix-testcase` dry-run 看新 yaml 合理不,合理就用 fix-testcase,不合理就改試 fix-skill

`fix-testcase` 內部把失敗 pattern 轉成 hint 後呼叫 `generate`,所以結果風格跟 `generate` 一致。
