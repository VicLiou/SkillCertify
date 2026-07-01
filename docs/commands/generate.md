# `generate` 子命令

讓 LLM 看你的 `SKILL.md`,自動產出一份 testcase YAML。

```bash
skill-test generate skills/pdf-filler
```

這會用指定的 adapter(預設 `claude`)對該 skill 的 `SKILL.md` 做**一次**呼叫,把回應寫進 `testcases/pdf-filler.yaml`(預設路徑是 `testcases/<skill 資料夾名>.yaml`)。

## 常用旗標

| 旗標 | 作用 |
|---|---|
| `--adapter` | 用哪個 CLI 來產生(預設 `claude`;這跟之後測試要用哪個 adapter 是分開的兩件事) |
| `--binary PATH` | 覆寫 `--adapter` 使用的 CLI 執行檔路徑 |
| `--model` | 釘住產生用的模型 |
| `--fixture` | 外部 fixture 路徑，會寫進產生的 YAML `fixture:` 欄位；testcase 的 `input` 應改用執行時複製後的位置 `./<fixture-name>` |
| `--coverage` | 覆蓋深度(見下表),預設 `all` |
| `--bias` | 場景偏好(見下表),預設 `mixed` |
| `--hint TEXT` | 自由文字額外指示(例如「重點測中文編碼處理」)。跟 `--coverage`/`--bias` 衝突時以 hint 為準 |
| `--out` | 輸出路徑(預設 `testcases/<skill-name>.yaml`) |
| `--force` | `--out` 的檔案已存在時,允許覆寫(預設不覆寫,避免蓋掉手調過的 testcase) |
| `--workdir-base DIR` | 生成用 workdir base；預設 `.work`，只有需要其他沙箱信任路徑時才覆寫 |
| `--timeout-s` | 這次生成呼叫的逾時秒數(預設 180) |

## `--coverage` 三個選項

LLM 會先在腦中掃過 `SKILL.md`、列出所有重大分支點(Gate、輸入分類、mandatory stop 條件…),再依這裡的設定決定要產幾個 testcase:

| 值 | 行為 | 適用情境 |
|---|---|---|
| `all`(預設) | 每條主要分支各一個 testcase(典型 3–8 個) | 想全面測試一個複雜 skill |
| `happy` | 只產 1 個最常見的成功路徑 | 想快速驗證 skill 至少能跑 |
| `minimal` | 2–3 個(1 happy + 1–2 關鍵 edge) | 中等規模,想兼顧速度與覆蓋率 |

## `--bias` 三個選項

| 值 | 行為 |
|---|---|
| `mixed`(預設) | 依分支本來的性質產:走 happy path 的分支產正向 testcase,觸發 STOP 的分支產負向 testcase |
| `positive` | 全部 testcase 都是合法輸入,測 skill 的成功路徑是否走得通(assertions 針對成果) |
| `negative` | 全部 testcase 都是缺輸入/邊界值,測 skill 是否正確 HALT/refuse(assertions 針對停止訊息) |

> **重要**:不管正向或負向,「skill 行為正確」都應該讓 testcase PASS。負向場景的 PASS = 它正確地停下並輸出 stop 訊息,不是「skill 失敗了所以 testcase 失敗」。生出來的 testcase 不會故意製造紅燈。

## 用例

```bash
# 全面覆蓋,混合正反場景(預設,適合初次)
skill-test generate skills/pdf-filler

# 只測 happy path,1 個 testcase 就好
skill-test generate skills/pdf-filler --coverage happy

# 全面測「skill 在缺輸入時會不會正確停下」
skill-test generate skills/pdf-filler --bias negative

# 指定重點,跳過某些主題
skill-test generate skills/pdf-filler \
    --hint "只測 PROJECT scope,跳過 diff 相關的 case"
```

## 注意事項

- **這是開發期工具,不是量測本身**:`generate` 只呼叫一次 LLM 把 `SKILL.md` 轉成 YAML,完全不會 stage skill、不會跑測試、不會碰 `--fixture` 指定路徑底下的內容(那個路徑只是被讀出字串、寫進 YAML 的 `fixture:` 欄位,原始檔案完全不動)。
- **產出後務必人工檢查一遍**:這一步本身是非確定的(同一個 skill 兩次 generate 可能產出不完全一樣的 YAML),所以是「草稿」而非「定案」,跑測試前建議先用 `--runs 1` 乾跑確認斷言合理。
- skill 資料夾裡必須有 `SKILL.md`,否則會報錯不產生檔案。
- 若 LLM 輸出格式不是合法 YAML(極少數情況),指令會直接報錯、**不會寫檔**,不會留半成品蓋掉舊檔。
- 有 `--fixture` 時，產生器會檢查 `input` 是否真的指向執行時複製後的 `./<fixture-name>`；若 LLM 誤寫成「current workspace」或原始 fixture 路徑，會在寫檔前自動修正成 runtime 路徑，其他 validation 問題仍會直接擋下。
- 若 testcase 使用 `judge:`,產生器會傾向把 `output_contains` 降成低脆弱度檢查,例如必要選項名、標題、檔名或狀態標籤；不要用整句中文/英文說明文當硬斷言,除非 SKILL.md 明確要求逐字輸出。

## 相關

- 不會產 YAML,但本機沒裝想用的 LLM → [手動把 prompt 貼給任何 LLM](../manual-prompt.md)
- 沒 fixture,想讓 LLM 生一份 → [`generate-fixture`](generate-fixture.md)
- 想一次跑完「fixture + testcase + 乾跑」 → [`bootstrap`](bootstrap.md)
- testcase 失敗、想重產 → [`fix-testcase`](fix-testcase.md)
