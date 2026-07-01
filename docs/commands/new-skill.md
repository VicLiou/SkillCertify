# `new-skill` 子命令

呼叫 architect 的「建立模式」(Phase 0 → A)幫你從零產一個符合最佳實踐的 skill。CLI 旗標把架構師要問的 Q1~Q6 答案先預填進 prompt,所以是**無人值守**模式不會卡在中間等使用者回答。

```bash
# 最簡:給 name + description
skill-test new-skill --name pdf-filler \
    --description "讀使用者提供的 PDF 表單範本,根據對話填入欄位產出新檔"

# 進階:用既有 skill 當藍本衍生(architect 的 A2 模式)
skill-test new-skill --name pdf-filler \
    --description "..." \
    --from skills/example-skill
```

## 常用旗標

| 旗標 | 作用 |
|---|---|
| `--name`(必填) | 新 skill 資料夾名,kebab-case |
| `--description`(必填) | 一段話描述,會塞進 SKILL.md frontmatter |
| `--type TEXT` | (選用)pattern 提示,例如 `code-review-and-scoped-repair` |
| `--from PATH` | (選用)用既有 skill 當藍本,觸發 architect 的 A2 模式 |
| `--hint TEXT` | (選用)額外指示 |
| `--out-root` | 新 skill 寫到哪個 parent 資料夾下(預設 `skills/`) |
| `--adapter` / `--architect-skill` / `--workdir-base` | 同其他指令；`--workdir-base` 預設 `.work` |
| `--binary PATH` | 覆寫 `--adapter` 使用的 CLI 執行檔路徑 |
| `--model MODEL` | architect 建立流程使用的模型 |
| `--timeout-s` | 預設 900(create 比 fix 慢) |

## 跑完後

寫完會印 architect 的 Phase 4 自審表(8 項自評,**0 FAIL 才算交付**),最後給三個下一步建議:

```
>>> next steps:
    1. review skills/pdf-filler/SKILL.md
    2. skill-test check-skill skills/pdf-filler
    3. skill-test generate skills/pdf-filler
```

照著做就會自然走進測試流程。
