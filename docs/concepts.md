# 概念與架構

## 三階段模型(與後端無關)

```
① SETUP   把 skill(+ 可選的 fixture 輸入)stage 進隔離 workdir
② RUN     skill(LLM)執行,產出 artifacts
③ VERIFY  靜態檢查 + 動態指令執行 + 語意 LLM-judge
```

審查類 skill(產出報告)和生成類 skill(產出要能跑/過測試的程式碼)都適用。

## 為什麼是程式驅動,而非 LLM 驅動

測試基礎設施本身必須是確定的。如果量尺會飄,你就分不清變異來自 skill 還是來自量尺。這裡的 LLM 只出現在兩處:**被測對象**(它的變異就是我們要的資料)和可選的 **judge**(一個我們努力維持穩定的量測儀器)。runner/控制層是純 Python。

## 目錄結構

```
runner/
├── cli.py                         入口(與後端無關)
├── core/                          與後端無關的引擎
│   ├── runner.py                  跑 N 次;進度 log / 心跳
│   ├── skill_loader.py            stage skill + fixture;三種注入策略
│   ├── assertions.py              靜態檢查 + 動態 command
│   ├── report.py                  pass rate / flaky / 流程分歧 / latency
│   ├── judge.py                   LLM-as-judge(與後端無關;用 --judge-adapter 選)
│   ├── generate.py                generate / generate-fixture 用的核心函式
│   ├── architect.py               呼叫 interactive-skill-architect 的共用基礎(stage + diff + backup)
│   ├── fix_skill.py               fix-skill 用的核心函式(呼叫 architect 修補 skill)
│   ├── check_skill.py             check-skill 用的核心函式(呼叫 architect 做 13 項健檢)
│   ├── fix_testcase.py            fix-testcase 用的核心函式(用失敗 trace 重產 testcase)
│   ├── new_skill.py               new-skill 用的核心函式(呼叫 architect 從零產 skill)
│   └── iterate.py                 iterate 用的核心函式(自動測 → 修 → 再測)
└── adapters/
    ├── base.py                    CliAdapter 介面 + RunResult/RunOptions
    ├── claude/adapter.py          ClaudeAdapter(claude -p, stream-json)
    └── codex/
        ├── common.py              resolve_launcher / kill_process_tree(共用)
        ├── exec.py                CodexAdapter(codex exec)
        ├── appserver.py           CodexAppServerAdapter(app-server JSON-RPC)
        └── tui.py                 CodexTuiAdapter(互動 TUI via pywinpty)
tools/
├── codex/                         診斷:inspect_rollout / probe_appserver / tui_probe
└── skills/
    └── interactive-skill-architect/   `fix-skill` 用來修補目標 skill 的工具型 skill
testcases/*.yaml                   input + expectations + runs(範本見 _TEMPLATE.yaml)
skills/                            被測的 skill 資料夾
fixtures/                          (可選)範例素材;`generate-fixture` 寫到這裡
docs/                              本文件樹的所有 md 檔
```

> **`tools/skills/` vs `skills/` 的區別**:`skills/` 放**被測對象**(你正在開發、想驗證流程穩定性的 skill);`tools/skills/` 放**框架自己用的工具型 skill**(像 `interactive-skill-architect`,負責優化 skill 的 skill)。兩者語意完全不同,請不要把工具型 skill 放錯地方。

## Skill 注入策略(`load_strategy`)

testcase YAML 裡的 `load_strategy` 欄位控制 skill 怎麼被「灌」進模型 prompt:

- `flatten` —— 整份 `SKILL.md` 塞進 prompt(資訊上限)
- `progressive` —— 只給 metadata + 檔案清單,讓模型自己讀 references(預設,最貼近真實)
- `scripts-only` —— 只列出 scripts/(只測流程編排)

---

延伸閱讀:[Adapters](adapters.md)、[斷言詳解](assertions.md)、[加新 CLI / 診斷工具](extending.md)
