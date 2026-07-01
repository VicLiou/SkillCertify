# 斷言詳解(testcase 裡的 `expect:`)

testcase YAML 的 `expect:` 必填且不能空；它是一個 list，每一項都必須是只有單一 key 的 mapping。除非該 testcase 明確預期 CLI 回傳非 0，否則建議明寫 `exit_code: 0`。可用的 key 完整列表:

| key | 層級 | 檢查什麼 |
|---|---|---|
| `exit_code` | 結果 | CLI 結束碼 |
| `file_exists` / `file_absent` | 結果 | 檔案有 / 沒有產生 |
| `output_contains` | 結果 | 子字串出現在 最終訊息 + stdout + 產生的檔案 |
| `final_contains` / `stdout_contains` | 結果 | 子字串出現在該通道 |
| `regex` | 結果 | 對輸出做正則 |
| `reads_file` | 流程 | 有讀過某檔(用路徑子字串) |
| `tool_used` | 流程 | 用過某工具 |
| `flow_contains` / `flow_equals` | 流程 | 工具序列(子序列 / 完全一致) |
| `max_latency_ms` | 效能 | 單次 latency 上限 |
| `command` | 動態 | 跑一個 shell 指令,斷言 exit/stdout(需 `--allow-exec`,否則 skipped 且 run 不通過) |
| `judge` | 語意 | LLM 依 criterion 判斷輸出(需 `--judge`,否則 skipped 且 run 不通過) |

`output_contains` 和 `judge` 會掃產生的檔案,但會排除 staged skill、fixture、以及 `.codex*` 暫存檔(那些是輸入,不是輸出)。

## 各斷言精確語意

- `exit_code`: CLI 結束碼要等於這個數字。若省略，adapter 非 0 結束仍會透過隱含的 `exit_code=0` 檢查讓 run 失敗；如果非 0 是預期行為，請明寫對應數字。
- `file_exists` / `file_absent`:相對 workdir 的路徑(也可給絕對路徑)要存在/不存在。
- `output_contains`:掃「最終訊息 + stdout + 產生的檔案內容」,清單裡每個字串都要出現。**這是唯一會掃檔案內容的字串斷言。** 如果 skill 把結論/報告寫進檔案,要驗證裡面的關鍵字就用這個,不要用 regex / final_contains / stdout_contains。
- `final_contains`:只掃最終訊息(skill 在對話裡講的最後一句話),**不掃檔案**。
- `stdout_contains`:只掃 stdout(claude 有,codex/codex-tui 通常沒有),**不掃檔案**。
- `regex`:對「最終訊息 + stdout」做正則比對,**不掃檔案**。**警告**:如果 skill 把實際輸出寫進檔案(常見於審查/分析類),regex 永遠 match 不到——這種情況一律改用 output_contains。
- `reads_file`:執行過程中,曾經讀取過「路徑包含這個子字串」的檔案。**注意**:**只有 skill 真的會走完整 happy path 才會讀**;如果這個 testcase 是負向場景(例如 input 缺輸入會讓 skill 觸發 MANDATORY STOP),skill 可能根本來不及讀那些 reference 就停了,別把整套 references 都塞進 reads_file。
- `tool_used`:執行過程中用過這個工具(常見值:Bash/Read/Write/Edit/Glob/Grep/Skill)。
- `flow_contains`:用過的工具名稱依序出現這個子序列(中間可以夾雜其他工具)。
- `flow_equals`:用過的工具名稱完全等於這個序列;**這個斷言很嚴格容易誤判,盡量少用**。
- `command`:在 workdir 下執行 run 指定的指令,結束碼要等於 exit_code(預設 0),stdout 要含 stdout_contains 裡的每個字串(若提供),逾時用 timeout_s(預設 60)。
- `max_latency_ms`:整次執行的耗時(毫秒)不能超過這個數字。
- `judge`:用另一個 LLM 依照這句話描述的標準去判斷,把標準寫成「正確行為應該是什麼」,符合即算通過。

## 寫斷言的常見錯誤

斷言要寫**對 skill 實際輸出位置**的關鍵字,不要照搬 SKILL.md 內部術語:

- ❌ 錯誤:`output_contains: ['SCAN_ROUND_COUNT']` —— 這是 SKILL.md 內部欄位代號,實際產出可能寫成「掃描輪次:2」,字面 substring 比對找不到。
- ✅ 正解:用「模板會強制出現的人類可讀詞」,例如 `'Blocker'`、`'Nit'`、`'CODE REVIEW RESULT'`。
- ✅ 進階:語意層交給 `judge`,字串斷言只做「報告至少存在 + 有基本結構標記」的低門檻驗證。

依 skill 類型挑斷言組合:

- **審查/分析類**(產報告)→ `output_contains`、`reads_file`、`judge`
- **生成類**(產程式碼)→ `file_exists`、`command`(實際執行產物)、`judge`
- **改檔/重構類**(改既有程式碼)→ `fixture`、`command`、`judge`
