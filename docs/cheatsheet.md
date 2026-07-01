# 指令速查表

底下所有範例都使用 `skill-test ...`(`pip install -e .` 後生效)。等價長寫法是 `python -m runner.cli ...`，但日常使用建議記短指令。

```bash
# 安裝
pip install -e .                              # 多裝 `skill-test` 短指令
pip install -e ".[codex-tui]"                 # Windows + codex-tui 才要
pip install -e ".[all]"                       # 全部選用套件都裝

# 環境 / 探索
skill-test doctor                              # 一鍵環境檢查
skill-test list                                # 列本機 skill / testcase
skill-test --help                              # 看所有子命令

# 跑測試(最常用)
skill-test testcases/<name>.yaml --adapter claude --runs 5
skill-test testcases/<name>.yaml --adapter claude --runs 10 --allow-exec --judge

# 一次跑完所有 testcase(萬用字元)
skill-test testcases/*.yaml --adapter claude --runs 5

# 換 adapter
skill-test testcases/<name>.yaml --adapter codex      --runs 5 --workdir-base .work
skill-test testcases/<name>.yaml --adapter codex-tui  --runs 5 --workdir-base .work --allow-exec

# 產生 testcase / fixture / 一次串完
skill-test generate skills/<name>                     # 只產 testcase
skill-test generate skills/<name> --coverage happy    # 只產 1 個 happy path testcase
skill-test generate skills/<name> --bias negative     # 全部測 HALT/refuse 行為
skill-test generate skills/<name> --hint "重點測 X"   # 自由文字額外指示
skill-test generate-fixture skills/<name>             # 只產 fixture
skill-test bootstrap skills/<name>                    # 生 fixture → 生 testcase → 乾跑 1 次
skill-test fix-skill skills/<name> --trace tr.json    # 根據失敗 trace 修補 skill(dry-run)
skill-test fix-skill skills/<name> --trace tr.json --apply   # 同上,真的寫回去(自動備份)
skill-test check-skill skills/<name>                  # 跑 architect 13 項品質健檢
skill-test fix-testcase testcases/<n>.yaml --trace tr.json  # 重產 testcase(skill 沒問題,testcase 寫錯時用)
skill-test new-skill --name foo --description "..."   # 從零產一個新 skill
skill-test iterate testcases/<n>.yaml --skill skills/<name>  # 自動:測 → 修 → 再測,迭代到全綠

# 輸出
skill-test ... --out report.json   # 彙總 JSON
skill-test ... --trace trace.json  # 逐次明細 JSON
skill-test ... 2> run.log          # log 倒進檔案

# log 詳細度
skill-test ... --quiet             # 只剩 START/END + 每次結果
skill-test ... --debug             # 加倍詳細(原始指令、agent 訊息)
```
