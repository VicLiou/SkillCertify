# `check-skill` 子命令

跟 [`fix-skill`](fix-skill.md) 一樣呼叫 architect,但**只做診斷不做修改**。架構師會跑完整 13 項品質檢查(描述清晰度、reference 深度、Gotchas 鮮度、模板合規…),產出一份報告告訴你 skill 的弱點。

沒有 trace 需要、沒有 `--apply` 路徑(很多項是判斷題,該人類自己決定要不要動)。

```bash
skill-test check-skill skills/cub-code-review

# 把報告也存成檔:
skill-test check-skill skills/cub-code-review --out report.md
```

## 常用旗標

| 旗標 | 作用 |
|---|---|
| `--adapter` | 用哪個 CLI 跑 architect(預設 `claude`) |
| `--binary PATH` | 覆寫 `--adapter` 使用的 CLI 執行檔路徑 |
| `--model MODEL` | architect 健檢使用的模型 |
| `--out FILE` | 把診斷報告寫成檔案(預設只印到 stderr) |
| `--constraint TEXT` | 額外指示給 architect |
| `--architect-skill PATH` | 換用其他 architect skill |
| `--workdir-base DIR` | architect 健檢用 workdir base；預設 `.work`，需要時才覆寫 |
| `--timeout-s` | 預設 600,健檢通常較長 |

## 什麼時候用

- 寫完 skill 還沒做測試之前先跑一次,看有沒有結構性問題
- 定期(每月/每季)複查既有 skill,看品質有沒有退步
- 不知道為什麼測試一直 flaky,先用 check-skill 看 architect 的判斷,可能會點出 SKILL.md 本身就有歧義

跟 fix-skill 的差別:**check 是定期保養、fix 是失敗時急救**。
