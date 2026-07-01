# skill-auto-test

一套**確定性**的測試框架,用來量測 Anthropic 格式 skill 的**流程穩定性**(同一個 skill 跑 N 次,有沒有都過、走的是不是同一條路徑)。控制層是純 Python;被測的 AI CLI(Claude Code、OpenAI Codex 等)是可替換的黑盒後端,所以同一份 testcase 能跨後端執行。

```
你 → testcase YAML → runner.cli → 多個 adapter 之一 → AI CLI 跑 skill → 收回結果 → 報告
                  (一段純粹的測試規格)              (claude / codex / codex-tui)
```

## 它能做什麼

- **跑穩定性測試**:同一個 skill 跑 N 次,看 pass rate 跟流程穩不穩；需要 CI 嚴格檢查 flow 時可用 `--require-stable-flow`
- **產 testcase**:LLM 看你的 SKILL.md,自動寫一份 testcase YAML
- **產範例素材**:LLM 編一份範例輸入給審查/改檔類 skill 用
- **修 skill**:測試失敗時,呼叫 architect 自動修補 SKILL.md(只能加 Gotchas/警告/禁令,不能改流程判斷邏輯)
- **修 testcase**:當失敗是 testcase 寫錯時,重產一份對齊 skill 真實輸出的 testcase
- **品質健檢**:13 項 skill 品質檢查,不用 testcase 也能跑
- **建立新 skill**:從零產一個符合最佳實踐的 skill
- **自動迭代**:「測 → 修 → 再測」整個迴圈跑到全綠或工具放棄

## 對誰有用

- **寫了一個 Anthropic 格式 skill,想知道它穩不穩**(同樣的請求 10 次有幾次過、流程是否一致)。
- **想比較同一個 skill 在不同 CLI 上的表現**(例如 Claude 和 Codex)。
- **想把 skill 測試接進 CI**(用結束碼 0/非 0 判斷)。
- **想要工具幫你自動修錯**——失敗時不只告訴你哪裡爛,還能呼叫架構師 skill 幫你補 SKILL.md。

如果你還沒做過 skill,可以先看 [Anthropic Skills 介紹](https://docs.claude.com/en/docs/agents-and-tools/agent-skills) 再回來。

---

## 13 個進入點

```bash
skill-test [<子命令>] [旗標...]
# 等價長寫法: python -m runner.cli [<子命令>] [旗標...]
```

預設(沒給子命令)= 跑測試。

| 進入點 | 你想做什麼 | 詳細說明 |
|---|---|---|
| (預設) | 跑既有 testcase 測 skill 穩定性 | [docs/commands/run.md](docs/commands/run.md) |
| `init` | 在新資料夾建立一份 skill-testing 專案骨架 | (執行看看就會懂) |
| `doctor` | 一鍵環境檢查(Python / CLI / 範例齊不齊) | (執行看看就會懂) |
| `list` | 列出本專案有哪些 skill 跟 testcase | (執行看看就會懂) |
| `validate` | 靜態檢查 testcase YAML(找錯字、缺欄位等,免 LLM) | (執行看看就會懂) |
| `new-skill` | 從零建立一個新 skill | [docs/commands/new-skill.md](docs/commands/new-skill.md) |
| `check-skill` | skill 品質 13 項健檢(不改檔) | [docs/commands/check-skill.md](docs/commands/check-skill.md) |
| `generate-fixture` | 讓 LLM 編一份範例 input 素材 | [docs/commands/generate-fixture.md](docs/commands/generate-fixture.md) |
| `generate` | 讓 LLM 自動寫 testcase YAML | [docs/commands/generate.md](docs/commands/generate.md) |
| `bootstrap` | 串完「fixture → testcase → 乾跑」 | [docs/commands/bootstrap.md](docs/commands/bootstrap.md) |
| `fix-skill` | 用失敗 trace 修補 SKILL.md | [docs/commands/fix-skill.md](docs/commands/fix-skill.md) |
| `fix-testcase` | 用失敗 trace 重產 testcase | [docs/commands/fix-testcase.md](docs/commands/fix-testcase.md) |
| `iterate` | 自動「測 → 修 → 再測」直到全綠 | [docs/commands/iterate.md](docs/commands/iterate.md) |

典型開發週期:`init` → `doctor` → `new-skill` → `check-skill` → `generate` → `validate` → 跑測試 → 視情況 `fix-skill` 或 `fix-testcase` 或 `iterate` → 接 CI。

---

## 文件導覽

### 上手

1. [安裝(5 分鐘)](docs/install.md)
2. [快速上手:跑一個內建範例](docs/quickstart.md)
3. [完整教學:8 步從零測你自己的 skill](docs/tutorial.md)
4. [指令速查表](docs/cheatsheet.md)
5. [範例集:repo 附的 3 個可跑 skill](docs/examples.md)

### 卡住的時候

- [常見錯誤排查](docs/troubleshooting.md)
- [如何看 log 與 `--trace` 報告](docs/logs.md)

### 想懂內部設計

- [概念與架構(三階段、為什麼程式驅動、目錄結構)](docs/concepts.md)
- [Adapters(claude / codex / codex-tui)](docs/adapters.md)
- [斷言詳解(`expect:` 內容)](docs/assertions.md)
- [加另一個 CLI / 診斷工具](docs/extending.md)

### 沒裝 CLI 想手動跑

- [手動把 prompt 貼給任何 LLM](docs/manual-prompt.md)

---

## 30 秒上手

裝好 Python 3.10+ 和 `claude`(見 [安裝](docs/install.md))後:

```bash
pip install -e .                 # 多裝一個叫 `skill-test` 的短指令
skill-test doctor                # 一鍵確認環境 OK
skill-test testcases/claude-example.yaml --runs 1
```

看到 `PASS in ~30s` 就代表環境 OK 了。接著去 [完整教學](docs/tutorial.md) 開始測你自己的 skill。
