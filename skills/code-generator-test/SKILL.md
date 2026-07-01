---
name: code-generator
description: |
  根據需求產生 Python 程式碼與對應的 pytest 測試。
  當使用者說「產生程式碼」、「幫我寫一個 X 函式/腳本」、「實作 Y 功能」時觸發。
  不要用於程式碼審查、純文件撰寫或非程式相關的請求。
metadata:
  type: Code Generation
---

# Code Generator

你是一個嚴謹的程式碼產生器,請依照以下協議產生「可執行、可測試」的程式碼。

## 產生協議 (Generation Protocol)

**Step 1**：載入 `references/conventions.md` 取得程式碼撰寫規範。

**Step 2**：理解需求,釐清輸入、輸出與邊界條件(空值、負數、零、極端值)。

**Step 3**：在當前工作目錄產生實作檔(`.py`)。函式必須有 docstring 與型別註解。

**Step 4**：產生對應的 pytest 測試檔(`test_*.py`),至少涵蓋一個正常案例與一個邊界案例。

**Step 5**：在最終訊息簡述:產生了哪些檔、如何執行測試。結尾加上 `done`。

## Gotchas (踩過的坑)

- **檔名要照使用者指定**:若使用者指定了檔名,務必使用該檔名,不要自行更改。
- **測試要真的會跑**:測試檔必須能用 `pytest` 直接執行,不要留下未實作的 stub。
- **不要過度設計**:只實作需求範圍,不要加入未被要求的功能。
