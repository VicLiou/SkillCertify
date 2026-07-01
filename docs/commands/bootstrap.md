# `bootstrap` 子命令

如果想一次跑完「生 fixture → 生 testcase → 跑測試」三步,不用三個指令分開打:

```bash
skill-test bootstrap skills/pdf-filler
```

預設流程:[`generate-fixture`](generate-fixture.md) → [`generate`](generate.md)(自動帶上剛生成的 fixture)→ run(預設 `--runs 1` 乾跑一次)。任何一步失敗就停,不會硬撐到下一步。

## 常用旗標

| 旗標 | 作用 |
|---|---|
| `--gen-adapter` | 生 fixture/testcase 用哪個 CLI(預設 `claude`) |
| `--gen-binary PATH` | 覆寫 `--gen-adapter` 使用的 CLI 執行檔路徑 |
| `--model MODEL` | fixture/testcase 生成用模型 |
| `--adapter` | **跑測試**用哪個 CLI(預設 `claude`;跟生成用的 adapter 是分開的兩件事;要在受管控環境跑 codex 的話顯式給 `--adapter codex-tui` (`--workdir-base` defaults to `.work`)) |
| `--binary PATH` | 覆寫最後測試 adapter 使用的 CLI 執行檔路徑 |
| `--run-model MODEL` | 最後 smoke/stability 測試步驟使用的模型 |
| `--no-fixture` | 跳過 `generate-fixture`(這個 skill 不需要外部素材時用) |
| `--hint TEXT` | 自由文字指示,**同時**傳給 `generate-fixture` 和 `generate`(兩邊都會看到) |
| `--coverage` / `--bias` | 傳給 `generate` 的覆蓋深度與場景偏好(語意同 [`generate`](generate.md)) |
| `--fixture-out` / `--testcase-out` | 兩個產出檔的路徑(預設同 `generate-fixture`/`generate`) |
| `--runs` | 最後測試跑幾次(預設 1,先求「能跑」;要測穩定性再調大重跑) |
| `--require-stable-flow` | 最後測試步驟啟用嚴格 flow 穩定性；需要 `--runs >= 2`,否則會直接拒絕執行 |
| `--allow-exec` / `--judge` / `--judge-adapter` | `--allow-exec` 會要求 `generate` 在適合場景產生 `command:` 斷言，並允許最後測試步驟執行；`--judge` 會要求 `generate` 產生 `judge:` 斷言，並在最後測試步驟啟用 judge；`--judge-adapter` 只傳給最後測試步驟 |
| `--judge-model MODEL` / `--judge-binary PATH` | 最後測試步驟的 judge 模型與執行檔覆寫；不影響 testcase 生成模型 |
| `--workdir-base DIR` | 生成、最後測試與 judge 步驟的 workdir base；預設 `.work` |
| `--keep-failed` | 失敗的 run 留下 workdir 不刪,**方便事後翻** skill 實際產出了什麼。bootstrap 是首次嘗試,smoke 跑掛時這個尤其有用 |
| `--trace FILE` | 把最後測試步驟的逐次明細寫成 JSON,失敗時可以打開細查 `flow` / `final_message` |
| `--debug` | 最後測試步驟開 verbose 模式(原始指令、agent 訊息、工具輸出全印) |
| `--force` | 允許覆寫已存在的 fixture/testcase 輸出 |
| `--timeout-s` | 每次生成呼叫(fixture/testcase)各自的逾時秒數 |

`--no-fixture` 適合純生成類 skill(像 fizzbuzz 產生器那種,不需要外部輸入素材);審查/改檔類 skill 通常不要加這個旗標,讓它照預設先生一份範例素材。

bootstrap 帶 fixture 生成 testcase 時，會要求並檢查 `input` 使用執行時複製後的位置 `./<fixture-name>`。若 LLM 誤寫成「current workspace」或原始 fixture 路徑，會在寫入 testcase 前自動修正；其他 validation 問題仍會停止流程。

若要用 bootstrap 直接量測流程穩定性,請搭配 `--runs 2` 以上與 `--require-stable-flow`；否則預設 `--runs 1` 只是 smoke run,不可能判斷 flow 是否穩定。

## 失敗時的 debug 套路

bootstrap 失敗最常見的是第三步(smoke 測試)沒通過。建議組合:

```bash
skill-test bootstrap skills/<my-skill> \
    --keep-failed --trace bootstrap-trace.json
```

跑完如果有 FAIL:
1. `cat bootstrap-trace.json | python -m json.tool` 找 `"passed": false` 那筆,看 `flow` 跟 `final_message`
2. 終端機印出的 `[run i/N] kept failed workdir: ...` 會列出保留目錄；直接進那個資料夾看 skill 實際產出了什麼檔案（例如 `ls`、`cat out.txt`）。

兩個合在一起通常 1 分鐘內就能定位是 testcase 太嚴還是 skill 走錯路徑。
