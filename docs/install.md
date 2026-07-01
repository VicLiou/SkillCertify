# 安裝(5 分鐘)

## 必要條件

- **Python 3.10+**(用了 `dataclass(slots)` 之外的新語法,例如 `X | None`)
- **至少一個 AI CLI 已裝好、能在終端機直接呼叫**:
  - [Claude Code](https://docs.claude.com/en/docs/claude-code/quickstart) — 指令叫 `claude`,執行 `claude --version` 應該有輸出。
  - [OpenAI Codex CLI](https://developers.openai.com/codex/cli/) — 指令叫 `codex`,執行 `codex --version` 應該有輸出。
  - **建議第一次至少裝 Claude**:它最快、設定最少,適合先驗證測試框架本身能跑。Codex 是進階情境(尤其是受公司管控的環境)才需要,後面教學會講。

## 安裝步驟

```bash
git clone <this-repo>      # 或解壓你拿到的壓縮檔
cd skill-auto-test
pip install -e .           # 推薦:會多裝一個叫 `skill-test` 的短指令
```

如果需要直接從原始碼執行，也可以用等價長寫法 `python -m runner.cli ...`；但安裝後建議一律使用 `skill-test ...`。

### 選用套件

下面這兩個**只有特定情境才需要**:

- `pip install -e ".[pytest]"` — 若你的 testcase 裡會用 `command: python -m pytest ...` 去跑模型產生的測試。
- `pip install -e ".[codex-tui]"` — **只有 Windows + `codex-tui` adapter** 才需要(會多裝 `pywinpty`)。Mac/Linux 或不用 codex-tui 就跳過。
- `pip install -e ".[all]"` — 兩個都裝。

## 驗證安裝

執行專案內建的 doctor 指令,**它會自動把所有預檢項目跑一次**:

```bash
skill-test doctor
```

等價長寫法是 `python -m runner.cli doctor`；本份文件以下一律用短指令 `skill-test`。

預期輸出:

```
skill-auto-test doctor

  ✓  Python 3.10+                 3.11.5
  ✓  PyYAML                       installed
  ✓  claude                       /usr/local/bin/claude
  ⚠  codex                        missing — install: ...   ← optional
  ⚠  pywinpty (optional)          missing — ...            ← optional
  ✓  pytest (optional)            installed
  ✓  architect skill              <auto-resolved path>
  ✓  sample skills + testcases    3 skill(s), 5 testcase(s)

ready! try:
  skill-test testcases/claude-example.yaml --runs 1
```

至少 Python、PyYAML、以及 **claude / codex 至少一個**要 ✓ 才能跑測試;codex / pywinpty / pytest 是選用的,⚠ 不算錯誤。

## 接下來

- [快速上手:跑一個內建範例](quickstart.md)
- [完整教學:從零測你自己的 skill](tutorial.md)
