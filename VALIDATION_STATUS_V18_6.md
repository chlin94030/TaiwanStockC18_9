# Alpha Radar V18.6 — Validation Status

## PASS — engineering/offline

- Existing V18.5/V18.4 selection, entry, market sync, PIT, DQ/MC, market journal, mobile four-view and provider parser tests (21 legacy scripts).
- New `test_v186_optimization.py` (7 tests): WAL and connection closing; transaction rollback; pre-LIMIT decision date cutoff; parallel readers and writers; ROC/Gregorian date validation; invalid Yahoo OHLC/negative volume rejection; historical analog golden 10/40/120-session scores; no candidate truncation and unchanged four-view UI.
- Comparison against untouched V18.5 historical analog implementation across three synthetic seeds: full returned output dictionaries identical for each horizon.
- Synthetic local benchmark: baseline 0.1231–0.1702 s per three-horizon call; optimized 0.0457–0.0650 s; measured 2.10x–3.60x speedup in these specific cases.
- Python syntax compilation and all existing app smoke paths.

## NOT VERIFIED — live providers, investment performance

- No live official TWSE/TPEx/MOPS/MIS or Streamlit Cloud networked end-to-end execution in this packaging environment.
- No claim that complete daily scan is 2x faster: API latency can dominate.
- No complete historical delisted-stock universe / point-in-time original filing archive for fully survivorship-bias-free performance replay.
- No claim of alpha, win-rate improvement or superiority over Gemini.

## Model invariants

- `strategy_config.ARCHITECTURE_VERSION` unchanged deliberately: ranking math and historical sample Beta(2,2) are not modified.
- Website release label is V18.6; selection still evaluates entire qualified liquid universe and preserves exchange-synchronized completed dates.
- No partial candles or unverified quotes can become executable intraday prices.
