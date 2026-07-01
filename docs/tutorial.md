# 完整教學:測你自己的 skill

下面 8 步是完整的工作流程,從「skill 在電腦裡」走到「拿到一份穩定性報告」、再到「skill 出問題時讓工具幫你修」。每一步都告訴你:**要做什麼、要打什麼指令、看到什麼算 OK、下一步要做什麼**。

## 先看一個完整範例(用內建 `example-skill` 跑一輪)

讀抽象教學容易卡住,先用 repo 自帶的 `skills/example-skill`(一個「打招呼並寫進 out.txt」的迷你 skill)從頭到尾跑一次。每條指令、預期輸出、接下來該幹嘛全部攤開給你看。**整段大約 1 分鐘**。

```bash
# 本教學一律使用 `skill-test`；等價長寫法是 `python -m runner.cli ...`。

# 步驟 0:確認環境 OK
$ skill-test doctor
skill-auto-test doctor
  ✓  Python 3.10+                 3.11.5
  ✓  PyYAML                       installed
  ✓  claude                       /usr/local/bin/claude
  ...
ready! try:
  skill-test testcases/claude-example.yaml --runs 1

# 步驟 0b:看看 repo 有哪些 skill / testcase 可以用
$ skill-test list
Skills (3 in ./skills/):
  example-skill  [scripts/, references/]  testcases: claude-example.yaml, codex-example.yaml
  ...
Testcases (5 in ./testcases/):
  claude-example.yaml  → skills/example-skill  1 case(s)  runs: 5
  ...

# 步驟 1-3:這個 example 已經幫你做好了——skill 在 skills/example-skill/、
#          testcase 在 testcases/claude-example.yaml,你只要跑就好。

# 步驟 4:乾跑 1 次確認能動
$ skill-test testcases/claude-example.yaml --runs 1
===== START 14:05:18 | 1 case(s), 1 run(s), adapter=claude =====
[run 1/1] start  (adapter=claude)
[run 1/1] PASS in 27s
=== claude-greet-happy-path  (skills/example-skill, progressive) ===
  pass rate : 1/1  (100%)
  all checks stable
  flow      : stable (1 path)
===== END ... =====
# ↑ 看到這幾行(尤其是 PASS、all checks stable)就代表整套機器都正常

# 步驟 5:放大跑 5 次測穩定性
$ skill-test testcases/claude-example.yaml --runs 5
[run 1/5] PASS in 25s
[run 2/5] PASS in 28s
[run 3/5] PASS in 26s
[run 4/5] PASS in 30s
[run 5/5] PASS in 27s
=== claude-greet-happy-path  (skills/example-skill, progressive) ===
  pass rate : 5/5  (100%)
  all checks stable
  flow      : stable (1 path)
# ↑ 5/5 全綠 = 這個 skill 穩定

# 完成。約 2-3 分鐘從零到一份穩定性報告。
```

如果上面這條指令鏈跑不過,先去 [常見錯誤排查](troubleshooting.md);跑得過,就可以照下面的 8 步走自己的 skill。

---

## 8 步流程

教學裡的 `<my-skill>` 是個佔位符,代表「你正在測的 skill 名稱」——比方說你 skill 叫 `pdf-filler`,所有出現 `<my-skill>` 的地方都換成 `pdf-filler`,從頭到尾都用同一個名稱不要換。

| 佔位符 | 換成什麼(假設 skill 叫 `pdf-filler`) |
|---|---|
| `<my-skill>` | `pdf-filler` |
| skill 資料夾 | `skills/pdf-filler/`(`SKILL.md` 放裡面) |
| testcase 檔 | `testcases/pdf-filler.yaml` |
| 執行指令 | `skill-test testcases/pdf-filler.yaml ...` |

## 流程一圖總覽

```
  ┌─ 步驟 1 ─┐  ┌─ 步驟 2 ─┐  ┌─ 步驟 3 ─┐  ┌─ 步驟 4 ─┐
  │ 放 skill │→│ 決定 fix │→│ 產 test  │→│ 乾跑驗   │
  │ 進專案   │  │ ture 與否│  │ case YAML│  │ 證能跑   │
  └─────────┘  └─────────┘  └─────────┘  └────┬────┘
                                               │
                              全綠 → 步驟 5      │
                              有 FAIL → 步驟 7   │
                                          ┌────┴────┐
                                          ▼         ▼
                          ┌─ 步驟 5 ─┐  ┌─ 步驟 7 ─┐
                          │ 跑 N 次  │  │ 自動修 sk│
                          │ 測穩定性 │  │ ill 再回測│
                          └─────┬────┘  └─────┬────┘
                                ↓             │
                          ┌─ 步驟 6 ─┐         │
                          │ 讀報告→  │←────────┘
                          │ 處理結果 │
                          └─────┬────┘
                                ↓
                          ┌─ 步驟 8 ─┐
                          │ 換 codex │
                          │ / 接 CI  │
                          └─────────┘
```

> **想一鍵跑完步驟 2–4?** 用 [`bootstrap`](commands/bootstrap.md) 一條指令搞定。下面還是把每步拆開,因為實務上很常單獨重跑某一步。

---

## 步驟 1 — 把 skill 放進專案資料夾

**做什麼**:把你要測的 skill 整個資料夾搬進 `skills/<my-skill>/`,確認裡面**至少**有一份 `SKILL.md`。

**結構長這樣就行**:

```
skills/<my-skill>/
├── SKILL.md          ← 一定要有,內含 frontmatter(name、description)和指示
├── scripts/          ← 可選,skill 用的腳本
├── references/       ← 可選,skill 用的參考文件
└── assets/           ← 可選,skill 用的模板/檔案
```

**看到什麼算 OK**:`ls skills/<my-skill>/` 看到 `SKILL.md` 在那裡。

**下一步**:步驟 2(決定要不要 fixture)。

---

## 步驟 2 — 決定要不要準備「fixture」(skill 要處理的素材)

**做什麼**:想一下你的 skill 需不需要「現成的素材」當輸入。

**怎麼判斷**:

| skill 在做什麼 | 要 fixture 嗎? | 為什麼 |
|---|---|---|
| 從零產生程式碼/檔案(例如 fizzbuzz 產生器) | **不用** | skill 自己無中生有,不需要素材 |
| 審查/分析既有東西(例如 code reviewer) | **要** | 沒給它 repo 或 diff,它沒東西可審 |
| 修改既有程式碼(例如重構工具) | **要** | 沒給它檔案,它沒得改 |

**如果不用 fixture**(像 fizzbuzz 那種):**這步跳過**,直接去步驟 3。

**如果需要 fixture**,你有兩種來源:

**A. 你手邊已經有真實素材**(推薦):記住絕對路徑,例如 `D:\data\sample-repo`,等下步驟 3 填進去。

**B. 沒有,想讓 LLM 編一份範例**:打這條指令(完整旗標見 [`generate-fixture`](commands/generate-fixture.md)):

```bash
skill-test generate-fixture skills/<my-skill> \
    --hint "你希望範例長什麼樣,例如:包含一份有語法錯誤的 Java 檔"
```

**看到什麼算 OK**:終端機印出 `wrote fixtures/<my-skill>-sample/` 以及產生了幾個檔案。

**下一步**:步驟 3(產生 testcase)。記得記住 fixture 的路徑(`fixtures/<my-skill>-sample/`)。

---

## 步驟 3 — 用 LLM 自動產生 testcase YAML

**做什麼**:讓 LLM 看你的 `SKILL.md`,推算出測試該怎麼寫,直接幫你生 testcase 檔。完整旗標見 [`generate`](commands/generate.md)。

**最簡單的指令**(沒 fixture):

```bash
skill-test generate skills/<my-skill>
```

**有 fixture 的話**:

```bash
skill-test generate skills/<my-skill> --fixture D:\data\sample-repo
```

預設行為:LLM 會掃 `SKILL.md` 找所有「重大分支點」(每個 Gate、每種輸入分類、每個停止條件…),為每條主要路徑各產一個 testcase,通常會生 **3 到 8 個**。

**想偏好特定方向**?加旗標:

```bash
# 「我只想先試試看能不能跑」→ 只產 1 個最簡單的成功路徑
skill-test generate skills/<my-skill> --coverage happy

# 「我要測 skill 在缺輸入時會不會正確停下」→ 全產負向場景
skill-test generate skills/<my-skill> --bias negative

# 「我有特殊重點想交代」→ 用自由文字
skill-test generate skills/<my-skill> \
    --hint "只測 PROJECT scope,跳過所有跟 diff 有關的 case"
```

**看到什麼算 OK**:終端機印出 `wrote testcases/<my-skill>.yaml (N testcase(s))`。

**下一步:打開 `testcases/<my-skill>.yaml` 用肉眼掃一遍**——LLM 不一定每次都對,你大概花 1 分鐘檢查:

- `skill:` 欄位有指對嗎(`skills/<my-skill>`)?
- 有 fixture 的話,`fixture:` 路徑寫對了嗎?
- `expect:` 下面那串斷言看起來合理嗎(不要太鬆——「永遠會過」,也不要太嚴——「永遠不會過」)? 斷言完整語意見 [斷言詳解](assertions.md)。

確認沒問題就去步驟 4。

---

## 步驟 4 — 先「乾跑」1 次,確認 skill 至少能動

**做什麼**:用最便宜的方式(只跑 1 次、不開動態驗證、不叫 LLM 評分)確認 testcase 格式沒寫錯、skill 至少能順利跑完。

```bash
skill-test testcases/<my-skill>.yaml --adapter claude --runs 1
```

注意這裡**不**加 `--allow-exec`、**不**加 `--judge`——這兩個會花更多時間/錢,等步驟 5 才開。

**看到什麼算 OK**:

```
[run 1/1] PASS in 27s
pass rate : 1/1  (100%)
all checks stable
```

→ 進步驟 5。

**如果有 FAIL**:看終端機 `flaky/failed checks:` 那一段,它會列出**哪條斷言不過**。常見對應:

| 斷言失敗訊息 | 通常代表 |
|---|---|
| `file_exists=foo.txt: ...` | skill 沒產生 `foo.txt`,或它用了別的檔名 |
| `output_contains=[...]: missing: [...]` | 那些關鍵字沒出現在輸出/檔案內容 |
| `reads_file=[...]: missing: [...]` | skill 沒讀到那些檔(可能不需要讀,也可能 skill 行為跟預期不同) |

接下來判斷:

- **如果 1 條失敗** → 多半是 testcase 的斷言寫太字面,改 testcase。
- **如果好幾條同時失敗** → 多半是 skill 走了非預期路徑,跳到步驟 7 讓工具幫你修 skill。
- **想看完整細節** → 重跑時加 `--trace trace.json`,打開 JSON 看 `flow`(skill 每一步用了什麼工具、讀寫了哪些檔)和 `final_message`(skill 最後說了什麼)。trace 結構見 [如何看 log 與報告](logs.md)。

---

## 步驟 5 — 放大規模,測穩定性

**做什麼**:單次能過後,放大跑次數(預設 testcase 寫 `runs: 10`),並打開**動態驗證**(真的跑 skill 生的程式碼)和**語意驗證**(用 LLM 評斷品質)。

```bash
skill-test testcases/<my-skill>.yaml --adapter claude \
    --runs 10 --allow-exec --judge --trace trace.json
```

**每個旗標在做什麼**(完整列表見 [`run` 子命令](commands/run.md)):

| 旗標 | 為什麼開 |
|---|---|
| `--runs 10` | 跑 10 次,看 10 次的 pass rate 是不是 100%、flow 是不是每次都走同一條 |
| `--allow-exec` | 讓 testcase 裡的 `command:` 斷言**真的執行**(例如「跑 pytest 驗證 skill 生的程式碼能不能過」) |
| `--judge` | 讓 testcase 裡的 `judge:` 斷言**真的叫 LLM 評分**(這會花 token,但能抓「字串對得上但語意錯」的 case) |
| `--trace trace.json` | 把每一次的明細存成 JSON,事後可以細查哪一次怎麼不一樣 |

**看到什麼算 OK**:

```
pass rate : 10/10  (100%)
flow      : stable (1 path)
all checks stable
```

→ 進步驟 6 寫結論,或步驟 8 接 CI。

**看到部分失敗**(例如 `pass rate : 8/10`)→ 進步驟 6 解讀。

---

## 步驟 6 — 讀報告,判斷怎麼回事

**做什麼**:看終端機印的摘要,3 個欄位最重要:

```
=== <my-skill>-happy-path  (skills/<my-skill>, progressive) ===
  pass rate : 8/10  (80%)        ← ① 對的東西有做對嗎?
  flow      : 2 DISTINCT paths   ← ② 每次都走同一條路嗎?
  flaky/failed checks:           ← ③ 是哪條斷言在飄?
    - file_exists=report.md: failed 2/10
```

**每個欄位怎麼讀**:

- **`pass rate`**:過幾次 / 跑幾次。100% 才算穩定。
- **`flow`**:`stable (1 path)` = 每次都走同一條路;`N DISTINCT paths` = 走了 N 條不同的路,即使 pass rate 100% 也代表「行為有飄」,可能是噪音(無害),也可能是真的不穩(有害)。
- **`flaky/failed checks`**:列出**哪條斷言**過了幾次/沒過幾次。`failed 2/10` = 10 次裡有 2 次那條斷言過不去。

**有失敗時的 SOP**:

1. 打開 `trace.json`,搜尋 `"passed": false`,找到那 2 次失敗的 run。
2. 看它的 `flow`(skill 那次跑了哪些工具)和 `final_message`(skill 最後說了什麼)。
3. 對照「成功的 run」的 `flow` 與 `final_message`,看差在哪。
4. 判斷該動哪邊:
   - **skill 真的有時候會走錯/沒產出檔案** → 跳到步驟 7,讓工具幫你修 skill
   - **斷言寫太字面**(例如要 `SCAN_ROUND_COUNT`,但 skill 寫成「掃描輪次:2」) → 用 [`fix-testcase`](commands/fix-testcase.md) 重產 testcase(或手動改 yaml)

---

## 步驟 7 — 失敗時,讓 `fix-skill` 自動幫你修 skill

**做什麼**:把上一步存的 `trace.json` 餵進去,工具會呼叫一個「skill 健檢專家」(`interactive-skill-architect`),它會分析失敗 pattern,幫你的 SKILL.md 加上必要的補充說明(警告、Gotchas、工具禁令…),然後給你看 diff。完整旗標見 [`fix-skill`](commands/fix-skill.md)。

**先**用 dry-run 看會改什麼(不會動到原 skill):

```bash
skill-test fix-skill skills/<my-skill> --trace trace.json
```

跑完終端機會印兩段:

1. **architect 的診斷報告**(它對失敗的分析)
2. **每個檔案的修改 diff**(具體加了什麼)

**看到什麼算 OK**:

- 診斷報告講的失敗原因對得上你看到的現象
- diff 看起來只是「加了一段 Gotcha」、「加了一條禁令」這種補充,**沒有刪掉任何既有規則、沒有改 Hard Gates / Step 順序**(框架有硬性禁令擋這種改動,但人眼再確認一次最保險)

**確認沒問題後,加 `--apply` 真的寫回**(會自動備份原檔到 `skills/<my-skill>.bak.<時間戳記>/`):

```bash
skill-test fix-skill skills/<my-skill> --trace trace.json --apply
```

**diff 看起來不對怎麼辦**:直接 `Ctrl+C` 或就**不要加 `--apply`**——dry-run 模式下你的原 skill 一個位元都沒被動到。

**寫回後**:**回到步驟 4 / 5 重新跑測試**,看是否真的修好了。如果還是有失敗,可以再跑一次 `fix-skill` 迭代,或人工檢查 SKILL.md 看缺什麼。或乾脆把整個迭代交給 [`iterate`](commands/iterate.md) 全自動跑。

> **fix-skill 不會碰流程邏輯**:它只能「**加**」(警告、Gotchas、新 Gate、釐清補充),不能「**改**」(既有 Gate 條件、Step 順序、放行/停止條件)。這是雙重保護的——一層是 architect skill 自己的閘門,另一層是框架在 prompt 內顯式禁令。

---

## 步驟 8 — 換 codex 跑、或接 CI(可選)

跑到這一步,skill 應該已經在 claude 上穩定了。可選的延伸:

**A. 也想看 skill 在 codex 上的表現**:換 `--adapter`(adapter 細節見 [Adapters](adapters.md)):

```bash
# 一般 codex(沒有 GUI 限制的環境)
skill-test testcases/<my-skill>.yaml --adapter codex \
    --runs 5 --allow-exec --judge --workdir-base ".work"

# 公司管控版 codex(會強制問核准、app-server 會開 GUI)
skill-test testcases/<my-skill>.yaml --adapter codex-tui \
    --runs 5 --allow-exec --judge --workdir-base ".work" --trace trace.json
```

注意事項:

- `codex` adapter 預設使用 `--workdir-base ".work"`；只有需要其他沙箱信任路徑時才覆寫。
- `codex-tui` 跑得慢(約 60s+/run),建議**先用 claude 把 testcase 除錯到綠**,再換 codex 驗
- `codex-tui` 只在 **Windows** 上能用(需要 `pywinpty`)

**B. 想接進 CI / 自動化**:`skill-test` 的結束碼:全部 case 100% 過 → `0`,任何 case 沒過 → `1`(可能是 crash 或斷言失敗)。直接寫進 CI script:

```bash
skill-test testcases/<my-skill>.yaml --adapter claude --runs 5 \
    --out report.json --trace trace.json
```

`report.json`(彙總)和 `trace.json`(明細)是純 JSON,可以收進 CI artifact 給人事後查。
