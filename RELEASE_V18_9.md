# Alpha Radar V18.9 歷史重複驗效套件

> **發行性質：驗證工具更新；正式選股模型、V18.8 核心權重、Streamlit 三頁介面均未改動。**

## 為何沒有直接發布新的權重？

V18.8 完整程式包不含真實多年 `daily_prices.sqlite`，本次本機環境亦無法連至 TWSE／TPEx／Yahoo／FinMind。故不能在缺乏真實價格的情況下宣布某些 Top5 股票、勝率或建議增減權重。已實際執行一次真實資料重播，結果為 `NOT_RUN`，完整診斷另附。

## 新增功能

1. `historical_replay_v189.py`：針對真實快取、以固定隨機種子進行 3 輪、每輪 4 個歷史日期的短中長線 Top5；每一檔採次交易日開盤價進場、10／40／120 個交易日持有至收盤；計算扣成本淨報酬、相對加權指數表現、勝率、MAE/MFE，儲存逐筆候選與排名。
2. `download_replay_data_v189.py`：在有外部網路的環境下載真實股價與大盤資料，不混入虛構資料。
3. `.github/workflows/historical_replay_v189.yml`：GitHub Actions 可手動啟動、下載真實歷史行情並將結果以 artifact 保存；下載失敗明確標示。
4. `test_v189_historical_replay.py`：時間切分、交易日對齊、停止交易缺價、禁止用未來價格參與選股、固定排名一致性及全流程回歸。
5. 權重研究：歷史日期做嚴格先訓練、再保留 10／40／120 日 embargo 後進入未接觸保留樣本。只建議在新一輪外部樣本再次驗證的權重草案，**絕不修改 `strategy_config.py`**。

## 歷史驗證公平性與邊界

- 正式 V18.8 是完整財報／籌碼／技術／產業多因子模型；此驗效工具若只有行情 SQLite，僅能驗證「**技術＋歷史相似案例價格核心**」。要驗證完整多因子，還需能對應過去決策時點的營收、財報、法人及集保資料檔。
- 以**目前仍有歷史股價檔的股票**作回測，仍有倖存者偏差，並非完整歷史上市母體。若抽樣 250 檔，Top5 僅是「抽樣母體中的 Top5」，不能宣稱全市場 Top5。
- 資料庫價格可能含供應商事後復權，尚不能完全認定是歷史當日公告版本。若在單日發現極端價格跳動，會略過訊號或要求公司行為核查。
- 每次隨機測試可能共享部分未來報酬區間，因此不能把每檔、每次選股全部當作獨立統計樣本。權重調整要通過額外未接觸時間區間與實際前瞻追蹤。
- 如果交易路徑有缺日、停牌或公司行為無法還原，**保留當時 Top5 原始推薦**並標示無法核算；不會事後改挑其他漲得較好的股票填補。
- 處於漲跌停、量太小等日線無法驗證成交可行性的情況，仍需更詳細委託簿資料。

## 快速執行

本地端擁有真實價格資料庫時，在專案根目錄執行：

```bash
python historical_replay_v189.py --db data/daily_prices.sqlite --out data/historical_replay_v189 --start 2023-01-01 --end 2025-12-31 --repeats 3 --per-repeat 4 --max-stocks 0
```

結果：`historical_top5_real.csv`（每次短中長線 Top5 與報酬）、`cutoff_performance.csv`、`horizon_summary.csv`、`all_scores_price_only.csv`、`weight_research_only.json`、`walkforward_report.md`、`replay_status.json`。

沒有上述資料庫時，GitHub 右上方切換到 **Actions → V18.9 Historical Walk-forward (Real Prices) → Run workflow**，在 GitHub Actions 雲端拉取真實行情後執行並提供可下載的驗效 artifact。第一次建議 `stock_count=250` 確認資料可取，成功後再用 `0` 擴至「目前在市全部普通股」（仍有歷史下市樣本缺漏）。若網路／供應商限流，工作流程會標示失敗而不是產出虛假績效。

另包含與當時合格候選母體的**固定隨機五檔基準**比較（`random_pool_baseline.csv`）；此基準僅用於提高分析透明度，不代表統計上已證明超額報酬。歷史資料若由 GitHub Actions 以 `stock_count=250` 取得，報告會標示 `RANDOM_CURRENT_SURVIVORS_SAMPLE`，不得誤稱全市場回測。

報告並附 `validation_dashboard.html`，將各次真實歷史日期的 10／40／120 日報酬與大盤分三區比較，方便在手機或電腦閱讀。若未取得真實價格資料，不會產生假圖表。
