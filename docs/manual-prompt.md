# 手動:把 prompt 貼給任何 LLM

如果你想用本機沒裝的 CLI 之外的 LLM(例如網頁版 claude.ai、ChatGPT),或想在送出前自己調整內容,可以用下面這段手動貼。

下面有一個**單一程式碼區塊**,從 `=== PROMPT 開始 ===` 到 `=== PROMPT 結束 ===`。那一整塊本身就是純文字,複製貼上不會混到本節其他的 Markdown(表格、標題)。

裡面最前面有一個「輸入區」,標了幾個 `【請替換】`,把整段複製貼上之後,**先去改那幾處**,再送出:

| 標記 | 要換成什麼 | 沒有的話怎麼辦 |
|---|---|---|
| `SKILL_PATH` | 這個 skill 的資料夾路徑,例如 `skills/pdf-filler` | 必填,一定要填 |
| `FIXTURE_PATH` | 你電腦上**已經存在**的外部測試素材路徑(待審 repo、diff、既有程式碼等),例如 `D:\data\sample-repo` | 沒有外部素材就把那一整行**刪掉**(連 `FIXTURE_PATH:` 那行都刪,不要留空值) |
| `COVERAGE` | `all` / `happy` / `minimal` 三選一,跟 [`generate --coverage`](commands/generate.md) 同義 | 直接留預設 `all`(每條主要分支各一個 testcase)就好 |
| `BIAS` | `mixed` / `positive` / `negative` 三選一,跟 [`generate --bias`](commands/generate.md) 同義 | 直接留預設 `mixed` 就好 |
| `HINT` | 自由文字額外指示,例如「重點測中文編碼處理」 | 不要的話把那一整行**刪掉** |
| `SKILL_MD_CONTENT` | 貼上該 skill 的 `SKILL.md` 全文(含 frontmatter) | 必填,一定要貼 |

除了這幾處,其餘文字(規則、欄位說明、範例)都不用改,LLM 看得懂。

> **為什麼一定要貼 `SKILL_MD_CONTENT`?** 這個 prompt 預設貼給的是一般對話型 LLM(claude.ai、ChatGPT 網頁版等),它們**沒有讀取你電腦檔案的能力**,看不到 `SKILL.md` 實際內容。它需要從 frontmatter 的 `name`/`description` 和內文,推導出 testcase 的 `name`/`input` 該怎麼寫、要斷言哪些行為(讀了哪個檔、產出什麼格式)。不貼的話 LLM 只能憑空亂猜,斷言大概率對不上實際輸出。
> 例外:如果你是用**有檔案存取能力的 agent**(例如 Claude Code 本身在這個專案目錄下執行)來產 testcase,它能自己讀到 `SKILL.md`,可以請它直接讀檔取代手動貼這一段;但 prompt 本身為了相容「純對話、無檔案存取」的 LLM,統一都要求貼上內容。

````
=== PROMPT 開始 ===

--- 輸入區(請先替換以下幾處,再送出) ---
SKILL_PATH: 【請替換:這個 skill 的資料夾路徑,例如 skills/pdf-filler】
FIXTURE_PATH: 【請替換:外部測試素材的路徑,例如 D:\data\sample-repo;沒有就把這整行刪掉】
COVERAGE: all
BIAS: mixed
HINT: 【請替換:自由文字額外指示,例如「重點測中文編碼處理」;沒有就把這整行刪掉】

SKILL.md 全文如下:
【請替換:把該 skill 的 SKILL.md 全文貼在這裡,取代這一整行】
--- 輸入區結束 ---

你要為一個 skill 產生一份 testcase YAML,用來測試該 skill 的流程穩定性與正確性。請用上面「輸入區」給的 SKILL_PATH / FIXTURE_PATH(若有)/ COVERAGE / BIAS / HINT(若有)/ SKILL.md 內容作為這次的輸入。

規則:
- 一個 YAML 文件 = 一個 testcase;用 "---" 分隔可在同一檔放多個 testcase。
- 輸出只能是合法 YAML,不要加任何解釋文字、不要加 Markdown 程式碼框之外的說明。
- 這份 YAML 之後要存成檔案路徑 testcases/<skill 名稱>.yaml(<skill 名稱> 取自輸入區 SKILL_PATH 的資料夾名稱,例如 SKILL_PATH: skills/pdf-filler 對應 testcases/pdf-filler.yaml)。如果你有檔案寫入能力,請直接把輸出寫入這個路徑(若 testcases/ 資料夾不存在就建立它);如果你只能在對話裡回覆文字,就在 YAML 最前面用一行 YAML 註解寫出建議的檔案路徑,例如 "# save as: testcases/pdf-filler.yaml",讓使用者知道要存去哪裡。

== 開始寫 YAML 之前,先「規劃」(這一步只在腦袋裡做,不要寫進輸出) ==

1. **掃過 SKILL.md,列出所有「重大分支點」**:每個 Gate、每個輸入分類選項、每個 mandatory stop 條件、每個 disposition、每個會讓 skill 走不同路徑的決策點都算。
2. **針對每個分支點,判斷它屬於正向還是負向場景**:
   - 正向:input 把該分支該有的輸入給齊,skill 走完整 happy path、產出對應的成果。assertions 針對「成果有沒有對的關鍵字 / 對的檔案 / 對的工具被用到」。
   - 負向:input 故意缺輸入或給異常值,skill 應該按 Gate / MANDATORY STOP 規則停下並輸出停止訊息。assertions 針對「停止訊息有沒有對的 enum 選項列表 / 該指名的缺失欄位」。
3. **根據輸入區的 COVERAGE 與 BIAS 決定要寫哪些**:
   - COVERAGE=all(預設):為每一條主要分支各產一個 testcase。一個複雜 skill 通常會有 3–8 個 testcase。
   - COVERAGE=happy:只產 1 個 testcase,選最常見、最簡單的成功路徑。
   - COVERAGE=minimal:產 2–3 個,1 個 happy path 加 1–2 個關鍵 edge case。
   - BIAS=positive:全部產出的 testcase 都是正向場景(就算 COVERAGE=all,也只挑那些走 happy path 的分支)。
   - BIAS=negative:全部產出的 testcase 都是負向場景(只挑會觸發 STOP/refuse 的分支)。
   - BIAS=mixed(預設):依分支本來的性質產出,正負兩種混合。
4. **如果輸入區有 HINT,優先遵守 HINT 的指示**——HINT 跟 COVERAGE/BIAS 衝突時以 HINT 為準。
5. **關鍵原則**:不管是正向還是負向場景,「skill 行為正確」就應該讓 testcase PASS。正向場景的 PASS = 成功產出;負向場景的 PASS = 正確地停下並輸出 stop 訊息。**不要產出「故意讓 skill 失敗」、「斷言一定不會 match」的破壞性 testcase**——那不是測試,是製造假紅燈。

頂層欄位(name/skill/input 必填,其餘可省略則用預設值):
- name (必填,無預設): testcase 名稱,報告會顯示,用 kebab-case
- skill (必填,無預設): 直接填輸入區的 SKILL_PATH
- input (必填,無預設): 用自然語言描述使用者會怎麼叫這個 skill,模擬真實請求;依照 SKILL.md 內容寫出貼近真實使用情境的請求
- runs (預設 10): 跑幾次測穩定性
- load_strategy (預設 progressive): flatten / progressive / scripts-only 三選一
- fixture (預設無): 輸入區的 FIXTURE_PATH(如果輸入區沒有這一行,就不要寫這個欄位)
- model (預設無): 釘住模型,claude 可用 sonnet/opus,codex-tui 通常省略
- timeout_s (預設 300): 單次逾時秒數,codex-tui 較慢建議用 600
- expect: 必填且不能空的斷言清單；每一項都必須是只有單一 key 的 mapping。
- 不要寫 sandbox 欄位,那個由執行端自動決定。

關於 fixture 路徑(重要,請仔細判斷):
- 如果輸入區有給 FIXTURE_PATH,把該路徑原封不動填進 fixture 欄位,不論它是絕對路徑還是相對路徑。
- fixture 指向的內容,執行時會被整份複製進每次測試專用的隔離 workdir 內,複製後檔名/資料夾名不變,但位置變成 workdir 底下。
- 因此在 input 描述裡,提到要 skill 去讀的素材時,只能用複製後的「相對名稱」(該檔案或資料夾原本的檔名),不要在 input 裡寫原始的外部絕對路徑 —— 執行時那個外部路徑早就不存在於 workdir 裡了。
  例如 FIXTURE_PATH: D:\data\sample-repo,input 裡應該寫「審查 sample-repo 這個資料夾」,不要寫「審查 D:\data\sample-repo」。
- 如果輸入區沒有 FIXTURE_PATH 那一行,就完全不要寫 fixture 欄位,讓 input 自己描述清楚要產生或處理的內容。
- file_exists / file_absent 斷言的路徑,一律是相對於 workdir(也就是相對於複製後的位置),不要用原始外部路徑。

`expect:` 必填且不能空。每一項都是只有單一 key 的 mapping；key 名稱請照下面原樣使用，不要意譯或改名:

expect:
  # 結果層
  - exit_code: 0
  - file_exists: out.txt
  - file_absent: error.log
  - output_contains: ["關鍵字1", "關鍵字2"]
  - final_contains: ["done"]
  - stdout_contains: ["..."]
  - regex: "\\d+ 個項目"

  # 流程層
  - reads_file: [SKILL.md, conventions]
  - tool_used: Bash
  - flow_contains: [Read, Write]
  - flow_equals: [Read, Bash, Write]

  # 動態層(實際執行,需要使用者在跑測試時額外加 --allow-exec,否則會跳過並讓該 run 不通過)
  - command:
      run: "python -m pytest -q"
      exit_code: 0
      stdout_contains: ["passed"]
      timeout_s: 60

  # 效能層
  - max_latency_ms: 60000

  # 語意層(需要使用者在跑測試時額外加 --judge,否則會跳過並讓該 run 不通過)
  - judge: "報告是否把『缺少測試』正確列為 Blocker?"

每個 key 的語意:
- exit_code: CLI 結束碼要等於這個數字。若省略，adapter 非 0 結束仍會透過隱含的 `exit_code=0` 檢查讓 run 失敗。
- file_exists / file_absent: 相對 workdir 的路徑(也可給絕對路徑)要存在/不存在。
- output_contains: 掃「最終訊息 + stdout + 產生的檔案內容」,清單裡每個字串都要出現。**這是唯一會掃檔案內容的字串斷言。** 如果 skill 把結論/報告寫進檔案,要驗證裡面的關鍵字就用這個,不要用 regex / final_contains / stdout_contains。
- final_contains: 只掃最終訊息(skill 在對話裡講的最後一句話),**不掃檔案**,清單裡每個字串都要出現。
- stdout_contains: 只掃 stdout(claude 有,codex/codex-tui 通常沒有),**不掃檔案**,清單裡每個字串都要出現。
- regex: 對「最終訊息 + stdout」做正則比對,**不掃檔案**,要能 match 到。**警告**:如果 skill 把實際輸出寫進檔案(常見於審查/分析類),regex 永遠 match 不到——這種情況一律改用 output_contains。
- reads_file: 執行過程中,曾經讀取過「路徑包含這個子字串」的檔案;清單裡每一項都要至少被讀過一次。**注意**:**只有 skill 真的會走完整 happy path 才會讀**;如果這個 testcase 是負向場景(例如 input 缺輸入會讓 skill 觸發 MANDATORY STOP),skill 可能根本來不及讀那些 reference 就停了,別把整套 references 都塞進 reads_file。
- tool_used: 執行過程中用過這個工具(常見值:Bash/Read/Write/Edit/Glob/Grep/Skill)。
- flow_contains: 用過的工具名稱依序出現這個子序列(中間可以夾雜其他工具)。
- flow_equals: 用過的工具名稱完全等於這個序列,一個不多一個不少;這個斷言很嚴格容易誤判,盡量少用。
- command: 在 workdir 下執行 run 指定的指令,結束碼要等於 exit_code(預設 0),stdout 要含 stdout_contains 裡的每個字串(若提供),逾時用 timeout_s(預設 60)。
- max_latency_ms: 整次執行的耗時(毫秒)不能超過這個數字。
- judge: 用另一個 LLM 依照這句話描述的標準去判斷這次執行的結果,把標準寫成「正確行為應該是什麼」,符合即算通過。

斷言要寫**對 skill 實際輸出位置**的關鍵字,不要照搬 SKILL.md 內部術語:
- ❌ 錯誤示範:`output_contains: ['SCAN_ROUND_COUNT']` —— `SCAN_ROUND_COUNT` 只是 SKILL.md 裡的內部欄位代號,實際產出可能寫成「掃描輪次:2」「Round 2」之類的中文/不同格式,字面 substring 比對找不到。
- ✅ 正確做法:用「skill 一定會講的人類可讀詞」當關鍵字,例如審查報告斷言 `'Blocker'`、`'Nit'`、`'CODE REVIEW RESULT'` 這種模板會強制出現的標記,而不是內部變數名。
- ✅ 進階做法:語意層的事情交給 `judge`,讓另一個 LLM 判斷「報告內容是否符合預期」,字串斷言只做「報告至少存在 + 有基本結構標記」的低門檻驗證。

依照 skill 的類型,選用不同的斷言組合:
- 如果 skill 是「審查/分析類」(輸出一份報告):重點放在 output_contains(報告該有的要素)、reads_file(有沒有讀到該讀的檔)、judge(語意上判斷得對不對)。
- 如果 skill 是「生成類」(產生程式碼或檔案):重點放在 file_exists、command(實際執行產出的東西,確認它真的能跑/能過測試)、judge(邏輯是否正確)。
- 如果 skill 是「改檔/重構類」:一定要用 fixture 放入既有程式碼,搭配 command(改完之後仍然能跑)和 judge。

寫 testcase 時務必遵守:
1. expect 不要寫得太鬆,太鬆會造成假性通過(skill 其實壞了卻顯示綠燈)。生成類至少要有一條 command 真正執行產物,不能只靠 file_exists。
2. 如果要用 file_exists 斷言某個檔名,必須先在 input 裡明確指定那個檔名(例如「寫在 fizzbuzz.py」),否則 LLM 執行時可能取別的檔名,導致斷言對不上。
3. command 和 judge 屬於選用層,只有使用者額外加上 --allow-exec / --judge 旗標時才會真的執行,否則會被標記為 skipped,且該 run 不算通過,所以不要假設它們一定會被執行。
4. judge 的描述要用正向句子寫「正確行為應該是什麼」,讓符合該描述 = PASS,不要寫成「有沒有犯錯」這種反向問法。
5. 同一個 skill 建議產生多個 testcase:一個 happy path、一個邊界或異常輸入、一個換句話說但同意圖的問法。
6. flow_contains / flow_equals 只看工具名稱,看不出讀的是哪個檔案;如果要驗證「讀對了檔案」,要用 reads_file。

產出前自我檢查(逐項確認後才輸出):
- 是合法 YAML，包含非空的 `expect:` 清單，且每一個 expect 項目都只有單一 key
- name / skill / input 三個必填欄位都有填
- 斷言用的是上面列出的精確 key 名稱,沒有自創或改名
- 生成類有 command 實際驗證產物;審查/分析類有 judge 驗語意
- 如果用了 file_exists,input 裡有明確指定該檔名
- 如果輸入區提供了 FIXTURE_PATH,該路徑已經填進 fixture 欄位,且 input/file_exists 裡引用的都是複製後的相對名稱,不是原始外部路徑
- 產出的 testcase 數量符合 COVERAGE 設定(all=3–8 個、minimal=2–3 個、happy=1 個)
- 每個 testcase 的場景類型符合 BIAS 設定(positive=全正向、negative=全負向、mixed=正反混合)
- 若有 HINT,所有 testcase 都遵守 HINT 的指示

完整範例(生成類,fizzbuzz):

name: my-skill-fizzbuzz-happy-path
skill: skills/my-skill
load_strategy: progressive
input: |
  用 my-skill 實作 fizzbuzz(n),寫在 fizzbuzz.py,pytest 測試寫在 test_fizzbuzz.py。
runs: 5
timeout_s: 600
expect:
  - exit_code: 0
  - file_exists: fizzbuzz.py
  - file_exists: test_fizzbuzz.py
  - command:
      run: "python -m pytest -q"
      exit_code: 0
  - judge: "產生的 fizzbuzz 是否正確:15 的倍數→FizzBuzz、3→Fizz、5→Buzz、其餘為數字字串?"

完整範例(審查類,帶外部 fixture,假設使用者提供的外部路徑是 D:\data\sample-repo):

name: my-skill-review-external-repo
skill: skills/my-skill
load_strategy: progressive
fixture: D:\data\sample-repo
input: |
  用 my-skill 審查 sample-repo 這個資料夾裡的程式碼,產生審查報告。
runs: 5
expect:
  - exit_code: 0
  - reads_file: [sample-repo]
  - output_contains: ["Blocker", "建議"]
  - judge: "報告是否準確指出 sample-repo 裡實際存在的問題,而不是泛泛而談?"

現在請依照以上規則,根據輸入區給的 SKILL_PATH / FIXTURE_PATH(若有)/ SKILL.md 內容,產生 testcase YAML。
=== PROMPT 結束 ===
````
