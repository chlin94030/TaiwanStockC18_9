# Alpha Radar V18.8｜三頁決策與可驗證模型

此版修復 V18.7 預設買點優先顯示造成「與 V18.6 換股很多」的錯覺。**短、中、長線原始 Alpha 排名固定**，買點只是同股票同週期的輔助資訊；買點觀察另行開啟、不混合主要策略名單。原核心分數程式不變。

- 主頁只保留：**今日決策／選股與買點／個股診斷**；國際快讀、科技供應鏈與日期健檢改放首頁下方展開，日期不再使用巨大指標字體。
- **V17/V18 權重算術已查核、但完整歷史真實樣本外勝率尚未驗證**。未取得 PIT 與歷史下市母體前，不改權重或宣稱績效提升。
- 指南：[`RELEASE_V18_8.md`](RELEASE_V18_8.md)；QA：[`VALIDATION_STATUS_V18_8.md`](VALIDATION_STATUS_V18_8.md)；快速測試：`python run_full_qa.py`。

---

# Alpha Radar V18.7｜買點導向與資料透明修正版

**從 V18.6 升級，保留 V18.5 的四頁介面、完整台股可投資母體及 V18.4 選股/DQ/MC/PIT 模型。** 本版補上費城半導體 ^SOX 單指數備援與缺值提示，擴充美股科技供應鏈觀察，以及六類台灣科技供應鏈觀察股票；首頁顯示台股細產業標籤、Alpha/DQ/MC 紅綠燈解釋與下次交易日買點狀態。下次交易日排序可優先呈現條件較接近的股票，但不改股票本身選股分數，也不把休市的日線參考訊號冒充 MIS 即時買進。

- [本版說明](RELEASE_V18_7.md)
- [本版離線驗證與限制](VALIDATION_STATUS_V18_7.md)
- `python run_full_qa.py` 驗證離線回歸；真實交易所行情與 Streamlit Cloud 仍需上線驗收。

---

# Alpha Radar V18.6 | High-performance stable update

**Based on V18.5, NOT Gemini app_2.py or simplified radar_service.py.**
All four core views, full liquid-market evaluation, PIT/DQ/MC, 10/40/120-session models, official EOD synchronization, and MIS-verified execution remain.

This release optimizes SQLite transaction cleanup/WAL bootstrapping, time-cutoff price queries, OHLC validation and historical analog vectorization. Frozen selection weights and Bayesian Beta(2,2) smoothing are unchanged. Old same-day valid snapshots remain usable; there is no forced costly multi-year refresh just to install this optimization.

- See `RELEASE_V18_6.md` for the feature audit, deployment and data limitations.
- Run `python run_full_qa.py` for offline regression; it includes new DB concurrency, historical date cutoff, ROC dates, Yahoo invalid bars, unchanged analog scoring and full-universe tests.

---

# Alpha Radar V18.5｜四個入口的台股決策介面

> **UI／行情資料完整度修正版。** V18.4 多因子選股引擎、V17 歷史相似案例與短／中／長線權重不變；本版修正使用者流程、日期來源可信度及快照更新驗證，不宣稱已完成樣本外績效驗證。

## 首次開啟先看哪裡？

| 入口 | 使用時機 | 看什麼 |
|---|---|---|
| **今日決策** | 開啟 App 第一眼 | 最新完整日線模型日、同步狀態、盤中動作或下次交易日條件、6 檔去重名單 |
| **選股與買點** | 想深入比較股票 | 按短／中／長線挑股票，每檔一張卡同時顯示標的分、買點分、資料 DQ／信心 MC，按需展開因子透視 |
| **市場與資料** | 想讀美股／重要新聞或確認來源日期 | 美股行情摘要、影響台股新聞、兩市場官方日期、模型日、母體、歷史與研究涵蓋率 |
| **個股診斷** | 有特定持股或想檢查買點 | 輸入股票代碼，區分考慮買入與已有持股；日期未核對時暫停當下買賣建議 |

### 舊版令人混淆的三份名單

- 「**合併精選**」不是第三種策略：只是跨週期去重，把同股票只顯示一張卡。
- 「**策略優選／完整日線**」是在最近完整交易日，評估哪檔股票值得關注；**Alpha 標的分**是相對投資研究分數，不是即時買點。
- 「**明日關注／下次交易日觀察**」是在已選股票中，額外考察下一交易時段的入場條件；**買點分數**是交易時機判斷，不代表隔日必漲。
- 「**因子透視**」不是另一批推薦股票：現在整合到「選股與買點」的「為什麼推薦這檔」內，只有想查證 DQ／MC、缺資料及分數由來時才展開。

### 資料同步怎麼判讀？

- **已同步**：TWSE、TPEx 官方原始來源日與同日模型相符。仍需注意行情盤中更新與研究資料各自的公布時間。
- **安全回退**：兩個交易所可取得的最新完整日不同，全部股票回退至較早的共同日線；沒有混算兩個不同交易日，但並非最新，暫不顯示可執行買點。
- **來源未核對**：官方來源日期缺漏，不能用 Yahoo 或歷史快取日期冒充官方日期；保留研究名單但買點停用。
- **資料異常**：股票價格日不一致、零模型、母體不合理或快照儲存失敗時拒絕發布新結果，保留上一份已完成快照。

母體涵蓋率、歷史足夠率、可投資模型比例和公司／法人研究涵蓋率分別呈現。**DQ／MC 是個股證據評估，不是整個市場資料下載成功率。**

## 部署

上傳 ZIP 內 `alpha_radar_v18_5` 資料夾的所有內容至原 GitHub 專案根目錄。Streamlit 入口沿用 `app.py`、Secrets 不變，資料庫不必因 UI 更新刪除。首次更新會建立 V18.5 原始來源日期證據；老快照缺此紀錄時可能提示「來源未核對」，請按「背景更新日線模型」。完整說明見 `RELEASE_V18_5.md`。

**QA：** `python run_full_qa.py`。離線工程測試通過不等於實際 Streamlit Cloud、台股資料來源網路或長期超額報酬已驗證。

---

# 歷次技術說明（保留供查核）

# Alpha Radar V18.4｜資料可追溯、信心分級、多週期選股整合版

**正式部署及本次修正：[`RELEASE_V18_4.md`](RELEASE_V18_4.md)**；驗證範圍：[`VALIDATION_STATUS_V18_4.md`](VALIDATION_STATUS_V18_4.md)。

原始目標不變：從台股完整可交易母體，以 **10 / 40 / 120 交易日**分別做短中長線排名；已完成日K與盤中核對行情、歷史案例、股票吸引力與買點/部位風控各自獨立。V18.4 把原預計 V18.1～V18.4 的資料品質、PIT、信心、排名穩健度、全球風險和介面工作整合，但**不宣稱完成真實歷史市場樣本外績效驗證**。

## 最快安裝

1. 將**完整包**內 `alpha_radar_v18_4/` 目錄下所有檔案放到 GitHub 專案根目錄並覆蓋同名檔案（保留 `.github/workflows`）。
2. Streamlit 入口 `app.py`；原 `FINMIND_TOKEN` 若使用，仍由 Streamlit Secrets 設定，勿寫入程式。
3. 執行 `python run_full_qa.py`；部署後手動按一次「背景更新日線模型」，才能建立 V18.4 三分數與時間證據。舊版 Snapshot 僅作暫時閱讀，勿誤當新模型。
4. 不上傳 `data/*.sqlite`、金鑰、`__pycache__`；但若需要長期 PIT 歷史回放，必須將 PIT SQLite 保存於真正**持久化**儲存空間。單純 Streamlit Cloud 本地磁碟可能重啟遺失，系統目前不具備外部儲存保證。

---

## 舊版沿革（以下僅供追溯）
# Alpha Radar V18.0 — 多因子透明選股、分週期評分與國際市場觀察室

**最新部署請先閱讀 [`RELEASE_V18_0.md`](RELEASE_V18_0.md)。**

V18.0 以 V17.5 為底盤，新增 15 項可解釋因子（13 項正向選股證據＋2 項買點風險）、短中長線獨立權重、產業共振、缺失資料標示與一頁式手機因子透視。選股與買點分開，不把夜盤下跌當成公司品質降低。保留原行情日期一致性、背景更新、手機斷線續看、國際市場快讀、推薦總表。

## 版本來源

# Alpha Radar V17.5 — 全球市場觀察室 / 手機斷線續看 / 精簡推薦總表

**最新版本功能與安裝方法請先看 [`RELEASE_V17_5.md`](RELEASE_V17_5.md)。**

本版保留 V17.4 資料健檢與 V17.1 日期同步框架，額外加入「最近美股指數點位與日期」、「重要數據比較判讀」、「新聞 RSS 摘錄與條件式台股影響直接閱讀」、「伺服器端背景日線掃描與跨連線讀取進度」及「視覺合併去重推薦總表」。

原模型的短／中／長線選股規則、歷史案例推估及買點防線仍按原定義運作。所有離線測試並非真實市場績效、即時行情或 Streamlit Cloud 實際速度的驗證。

---

# Alpha Radar V17.1 — Synced Market / Fast Brief

V17.1 is a production-integrity release on top of V17.0. It fixes mixed TWSE/TPEx completed-date risk, makes universe coverage explicit, adds horizon-specific final investability/evidence guardrails, translates global-market abbreviations into plain Traditional Chinese, and gives the user a useful global/news/previous-model briefing while the expensive canonical update runs.

Key invariants remain unchanged: short/mid/long raw selection weights are frozen; global news/context never changes stock-quality ranking; intraday executable decisions still require verified prices; Panic Reversal remains a downstream entry route rather than a selection factor.

See `DEBUG_REPORT_V17_1.md` and `VALIDATION_STATUS_V17_1.md` for the exact synchronization, coverage and QA contract.

---

# Alpha Radar V17.0 — Goal Complete / Full-Market Canonical

V17.0 is the first release whose production contract is explicitly aligned with the original goal: **at any query time, identify the best available Taiwan-stock candidates for short (10 sessions), mid (40 sessions), and long (120 sessions), then separately decide whether the current price is actionable.**

## Canonical production model

The normal UI no longer exposes candidate-count/history-length knobs that could silently change the answer. Production is fixed to:

- completed-day history: 5 years;
- universe: every liquid common stock with sufficient completed-day history;
- model family: full evidence;
- short/mid/long selection weights unchanged from the frozen core;
- global/US context affects execution risk and panic-reversal logic only, never stock-quality ranking.

## V17.0 changes

1. **Full liquid-universe baseline** — `candidate_size` no longer truncates production ranking. Every liquid stock with adequate history gets short/mid/long forecasts.
2. **Comparable breadth research** — official TWSE/TPEx market-wide revenue, latest financial, valuation and institutional evidence are loaded on one comparable scale for ranking. Rich historical research is loaded for leading names for display only and cannot change the published ranking.
3. **Missing evidence is neutral, not rewarded** — absent fundamental/flow evidence is neutral-imputed and receives a small bounded coverage penalty. Missing fields no longer inflate the remaining weights through re-normalization.
4. **Same-date core reuse** — if the completed-session date has not changed, the expensive historical/analog core is reused and only comparable research/ranking is refreshed.
5. **Full-market intraday discovery** — the one-minute scan quotes up to 2,500 liquid names and sends a bounded detail pool through official MIS price verification before BUY/SELL/HOLD execution scoring.
6. **Three entry routes remain separate** — Pullback, Continuation and Panic Reversal. Falling knives, structural breaks and near-limit-down situations remain blocked.
7. **Doctor consistency** — on-demand stock diagnosis uses the same comparable ranking evidence as the market selector; rich research is display detail, not an unfair ranking advantage.

## Time-of-day behavior

- **Pre-open:** latest completed-day canonical ranking + next-session watchlist + overnight global context.
- **Intraday:** completed-day ranking stays frozen; quotes refresh every 60 seconds; official MIS-verified execution prices drive buy/hold/sell decisions.
- **Post-close:** today's official EOD is used when complete; otherwise the last completed model remains authoritative and is clearly labeled pending.
- **Off-day:** latest completed session is reused; no fake daily candle is created.

## Core selection weights (unchanged)

| Horizon | Technical | Historical analog | Fundamental | Institutional flow |
|---|---:|---:|---:|---:|
| Short | 52% | 20% | 8% | 20% |
| Mid | 42% | 22% | 22% | 14% |
| Long | 34% | 22% | 34% | 10% |

Selection answers **what is worth owning**. Entry timing answers **whether this price is worth paying now**. Market/global regimes size risk; they do not rewrite selection quality.

## Verification

Run:

```bash
python run_full_qa.py
```

The bundled GitHub workflow `.github/workflows/qa_v17.yml` runs the same suite on push. A separate `validate_v17_2026_walkforward.py` performs leakage-controlled real-stock 2026 replay when external market data are reachable.

## Validation boundary

Offline engineering QA is complete. Real-market profitability is **not claimed** from the packaging container because external Taiwan market connectivity is unavailable there. The validator refuses to substitute synthetic data for real performance.

## Deployment

Upload the package source files to the repository; do **not** upload `__pycache__` or `.pyc` files. Existing price/research SQLite caches can be kept, but the first V17 update must build a new V17 dashboard snapshot because the model version changed.

## V17.3 — Mobile Recommendation Board

A new compact **推薦總表** board supports list filters (completed daily or next-session), horizon filters (short/mid/long), cross-horizon consensus (>=2 *distinct completed-day horizons*) and 3/5/8/15 picks per horizon. A Top 15 expandable summary and separately collapsed full comparison table reduce mobile scrolling. This release does not alter model weights or historical ranking logic.

See `DEBUG_REPORT_V17_3.md` and run `python test_v17_3_mobile_board.py`.

## V18.9 真實歷史驗效（V18.8 模型權重未更動）

使用 `historical_replay_v189.py` 對真實 OHLCV 的技術＋歷史案例核心進行多輪歷史 Top5 選股、次日開盤進場和 10/40/120 個交易日的報酬驗效。請先閱讀 `RELEASE_V18_9.md` 與 `VALIDATION_V18_9_GUIDE.md`。如無 `data/daily_prices.sqlite`，可在 GitHub Actions 手動執行 **V18.9 Historical Walk-forward (Real Prices)**。缺乏真實資料時嚴格回報 `NOT_RUN`，不產生虛假歷史績效。
