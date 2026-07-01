# `generate-fixture` 子命令

有些 skill(審查類、改檔類)的 testcase 需要一個 `fixture`——一份「待處理的素材」(待審 repo、既有程式碼、要填的範本)。如果你手邊還沒有真實素材,可以讓 LLM 直接生一個範例出來:

```bash
skill-test generate-fixture skills/pdf-filler --hint "包含一份有缺失欄位的 PDF 表單範本"
```

跟 [`generate`](generate.md) 不一樣的地方:`generate` 只取 LLM 的**文字回應**,但這裡要的是**真的被寫出來的檔案**——所以這個指令會把 skill 複製進一個暫存資料夾當參考,真的執行 adapter 讓它用 Bash/Write 工具把範例專案的檔案寫出來,跑完再把產生的檔案複製到 `--out`(預設 `fixtures/<skill-name>-sample/`)。

## 常用旗標

| 旗標 | 作用 |
|---|---|
| `--adapter` | 用哪個 CLI 來生成(預設 `claude`) |
| `--binary PATH` | 覆寫 `--adapter` 使用的 CLI 執行檔路徑 |
| `--model` | 釘住生成用的模型 |
| `--hint` | 額外告訴 LLM 這次範例要包含什麼情境/邊界案例 |
| `--out` | 輸出資料夾(預設 `fixtures/<skill-name>-sample`) |
| `--force` | `--out` 資料夾已存在且非空時,允許覆寫 |
| `--workdir-base DIR` | fixture 生成用 workdir base；預設 `.work`，需要時才覆寫 |
| `--timeout-s` | 逾時秒數(預設 240,寫檔案通常比純文字生成慢) |

## 注意事項

- 這個範例是 LLM 照 `SKILL.md` 描述**想像**出來的典型案例,不是真實資料;手邊有真實素材時優先用真實的,這個指令適合「還沒有素材、想先讓流程跑起來」的階段。
- 跑完會在終端機列出寫了哪些檔案,並提示下一步指令(接 `generate ... --fixture <out_dir>`)。
- 模型如果什麼檔案都沒寫,會直接報錯,不會留空資料夾。

## 相關

- 想一次跑完「fixture + testcase + 乾跑」 → [`bootstrap`](bootstrap.md)
