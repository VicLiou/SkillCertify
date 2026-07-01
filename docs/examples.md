# 範例(隨 repo 附的 3 個可跑的 skill)

這個 repo 自帶 3 個可立刻跑的 skill,**每個示範一種 skill 類型 + 一種 testcase 寫法**。直接拿來跑、或當你自己 skill 的模板都可以。

```bash
# 看一下全部
skill-test list
```

## 1. `example-skill` — 最小骨架

**位置**:`skills/example-skill/` + `testcases/claude-example.yaml`

**做什麼**:呼叫一個 Python 腳本產生問候訊息,寫進 `out.txt`,然後在最終訊息裡報告字數。

**示範什麼**:
- 最小可行的 skill 結構(SKILL.md + scripts/greet.py + references/style.md)
- `final_contains` 斷言驗證對話結尾關鍵字
- `file_exists` 斷言驗證有寫出檔
- `regex` 斷言驗證輸出含數字

**跑一次**:
```bash
skill-test testcases/claude-example.yaml --runs 1
```

**期望**:幾乎 100% pass。**這是你跑通整套機器的最快方式**。

---

## 2. `code-generator-test` — 生成類 skill 模板

**位置**:`skills/code-generator-test/` + `testcases/code-generator.yaml`

**做什麼**:給定需求,產出 Python 實作 + pytest 測試,可以實際跑通。

**示範什麼**:
- 「生成類」skill 的標準結構(載入 conventions → 實作 → 寫測試 → 報告)
- 用 `command: python -m pytest` 動態驗證(模型產出的程式碼**真的能跑**)
- 多個 testcase 涵蓋不同情境(happy path、邊界案例)

**跑一次**:
```bash
skill-test testcases/code-generator.yaml --runs 1 --allow-exec
#                                                 ^^^^^^^^^^^^
#                                  必要,讓 command: 斷言真的跑 pytest
```

**期望**:看到 `[run 1/1] PASS in ~40s`,中間會有實際跑 pytest 的階段。

---

## 3. `google-code-reviewer-test` — 審查類 skill 模板(含 fixture)

**位置**:`skills/google-code-reviewer-test/` + `testcases/google-code-reviewer.yaml`

**做什麼**:依 Google Code Review Guidelines 審查一段程式碼,產出結構化報告。

**示範什麼**:
- 「審查/分析類」skill 結構(載入規則 → 分類審查 → 模板化輸出)
- 多份 `references/` 拆分章節 + `assets/` 放輸出模板
- `judge:` 語意層斷言(用 LLM 判斷報告品質)
- `output_contains` 斷言驗證報告該有的關鍵字
- `reads_file` 斷言驗證 skill 真的讀了該讀的檔

**跑一次**:
```bash
skill-test testcases/google-code-reviewer.yaml --runs 1 --judge
#                                                       ^^^^^^^
#                                       要開 judge 才會真的呼叫 LLM 評分
```

注意這個 skill 內部有自帶測試素材的描述(它的 testcase 直接給一段示範程式碼當 `input`),所以**不需要 `fixture:`**;如果你的審查類 skill 要審「使用者提供的 repo」,看 [`generate-fixture`](commands/generate-fixture.md) 怎麼做。

---

## 把這些當模板

| 你要做的 skill 類型 | 拿哪個當模板 |
|---|---|
| 從零產出程式碼/檔案 | `code-generator-test` |
| 審查/分析既有素材並產出報告 | `google-code-reviewer-test` |
| 只是想玩看看框架怎麼用 | `example-skill` |

複製 + 改 `SKILL.md`,然後跑:

```bash
cp -r skills/code-generator-test skills/my-new-skill
# 編輯 skills/my-new-skill/SKILL.md
skill-test check-skill skills/my-new-skill     # 先看品質
skill-test generate skills/my-new-skill        # LLM 自動寫 testcase
skill-test testcases/my-new-skill.yaml --runs 5
```

或從零開始用 [`new-skill`](commands/new-skill.md) 互動建立。
