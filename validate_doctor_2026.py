"""Point-in-time validation for the V16.7 single-stock buy diagnosis.

Scope
-----
* Real TWSE/TPEx daily OHLCV when network access is available.
* At each 2026 cutoff, the model sees only bars <= cutoff.
* The doctor is evaluated in POSTCLOSE mode, so a READY decision is assumed to
  enter at the *next trading day's open* (not the cutoff close).
* Future 10/40/120-session returns are then measured from that next open.

This validates the completed-day/next-session buy path.  It does not claim to
validate intraday fills because one-minute historical quote/order-book data are
not part of the daily OHLCV archive.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from market_data import DailyPriceStore, fetch_twse_universe
from radar_service import RunSettings, _evaluate_one
from doctor_engine import buy_decision
from validate_2026_walkforward import choose_real_sample, shared_2026_cutoffs
from strategy_config import ARCHITECTURE_VERSION

HORIZONS = {"short": 10, "mid": 40, "long": 120}


def _pos_at_or_before(df: pd.DataFrame, date: pd.Timestamp) -> int | None:
    if df is None or df.empty:
        return None
    p = int(df.index.searchsorted(pd.Timestamp(date), side="right") - 1)
    return p if 0 <= p < len(df) else None


def _market_regime(benchmark_hist: pd.DataFrame) -> str:
    if benchmark_hist is None or len(benchmark_hist) < 60:
        return "UNKNOWN"
    close = pd.to_numeric(benchmark_hist["Close"], errors="coerce").dropna()
    if len(close) < 60:
        return "UNKNOWN"
    p = float(close.iloc[-1])
    ma20 = float(close.tail(20).mean())
    ma60 = float(close.tail(60).mean())
    if p >= ma20 and ma20 >= ma60:
        return "BULL"
    if p < ma20 and p >= ma60:
        return "NEUTRAL"
    return "BEAR"


def _forward_from_next_open(df: pd.DataFrame, cutoff: pd.Timestamp, days: int, cost_rate: float) -> dict | None:
    pos = _pos_at_or_before(df, cutoff)
    if pos is None or pos + 1 >= len(df):
        return None
    entry_pos = pos + 1
    exit_pos = entry_pos + days - 1
    if exit_pos >= len(df):
        return None
    entry = float(df["Open"].iloc[entry_pos])
    exit_close = float(df["Close"].iloc[exit_pos])
    path = df.iloc[entry_pos:exit_pos + 1]
    if not np.isfinite(entry) or entry <= 0 or not np.isfinite(exit_close):
        return None
    low = float(pd.to_numeric(path["Low"], errors="coerce").min())
    high = float(pd.to_numeric(path["High"], errors="coerce").max())
    return {
        "entry_date": str(df.index[entry_pos].date()),
        "entry_open": entry,
        "exit_date": str(df.index[exit_pos].date()),
        "net_return": exit_close / entry - 1.0 - cost_rate,
        "mae": low / entry - 1.0 if np.isfinite(low) else None,
        "mfe": high / entry - 1.0 if np.isfinite(high) else None,
    }


def _summary(g: pd.DataFrame) -> dict:
    if g.empty:
        return {"observations": 0}
    ready = g[g["doctor_action"] == "READY"].copy()
    def med(x):
        return None if len(x) == 0 else float(np.nanmedian(x))
    def mean(x):
        return None if len(x) == 0 else float(np.nanmean(x))
    out = {
        "observations": int(len(g)),
        "cutoff_dates": int(g["cutoff_date"].nunique()),
        "stocks": int(g["ticker"].nunique()),
        "ready_count": int(len(ready)),
        "ready_rate": float(len(ready) / len(g)) if len(g) else None,
        "all_median_return": med(g["net_return"].dropna()),
        "ready_median_return": med(ready["net_return"].dropna()),
        "ready_mean_return": mean(ready["net_return"].dropna()),
        "ready_positive_rate": float((ready["net_return"] > 0).mean()) if len(ready) else None,
        "ready_median_mae": med(ready["mae"].dropna()),
        "ready_median_mfe": med(ready["mfe"].dropna()),
    }
    # Same-date lift prevents bull-market dates from dominating the comparison.
    lifts = []
    for _, d in g.groupby("cutoff_date"):
        r = d[d["doctor_action"] == "READY"]
        if len(r):
            lifts.append(float(r["net_return"].median() - d["net_return"].median()))
    out["mean_same_date_median_lift"] = None if not lifts else float(np.mean(lifts))
    return out


def run(out_dir: Path, stocks: int = 60, period: str = "8y", year: int = 2026, points: int = 12, seed: int = 1337) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    store = DailyPriceStore(out_dir / "doctor_validation_prices.sqlite")
    settings = RunSettings(history_period=period, model_family="price_only")
    universe = fetch_twse_universe()
    if len(universe) < stocks:
        raise RuntimeError(f"股票母體僅取得 {len(universe)} 檔，無法執行 {stocks} 檔真實驗效")

    store.batch_fetch_and_update(["^TWII"], period=period)
    benchmark = store.get_prices("^TWII")
    if benchmark is None or len(benchmark) < 400:
        raise RuntimeError("TAIEX 歷史資料不足")
    sample = choose_real_sample(store, universe, stocks, period, seed)
    if len(sample) < stocks:
        raise RuntimeError(f"具足夠歷史資料標的只有 {len(sample)} 檔")
    cutoffs = shared_2026_cutoffs(benchmark, year, points, longest_days=max(HORIZONS.values()))
    if len(cutoffs) < 10:
        raise RuntimeError(f"2026 可用共同 cutoff 只有 {len(cutoffs)} 個，未達至少 10 個")

    # Approximate round-trip cost using the production settings.
    cost_rate = settings.commission * 2 + settings.sell_tax + settings.slippage * 2
    meta = universe.set_index("ticker").to_dict("index")
    rows: list[dict] = []
    for cutoff in cutoffs:
        bench_hist = benchmark[benchmark.index <= cutoff].copy()
        if len(bench_hist) < 320:
            continue
        market_ret20 = float(bench_hist["Close"].iloc[-1] / bench_hist["Close"].iloc[-21] - 1.0) if len(bench_hist) >= 21 else 0.0
        regime = _market_regime(bench_hist)
        market_day = float(bench_hist["Close"].iloc[-1] / bench_hist["Close"].iloc[-2] - 1.0) * 100.0 if len(bench_hist) >= 2 else 0.0
        for ticker in sample:
            full = store.get_prices(ticker)
            hist = full[full.index <= cutoff].copy()
            if len(hist) < 320:
                continue
            m = meta.get(ticker, {})
            stock = _evaluate_one(
                ticker,
                str(m.get("name") or ticker),
                str(m.get("industry") or ""),
                hist,
                settings,
                market_ret20,
                {},
                ranking_research={},
                market_regime=regime,
                market_day_change=market_day,
            )
            for h, days in HORIZONS.items():
                fwd = _forward_from_next_open(full, cutoff, days, cost_rate)
                if not fwd:
                    continue
                decision = buy_decision(stock, h, "POSTCLOSE")
                rows.append({
                    "cutoff_date": str(cutoff.date()),
                    "ticker": ticker,
                    "name": str(m.get("name") or ticker),
                    "horizon": h,
                    "doctor_action": decision.get("action_code"),
                    "headline": decision.get("headline"),
                    "doctor_score": decision.get("score"),
                    "selection_score": decision.get("selection_score"),
                    "entry_score": decision.get("entry_score"),
                    "expected_return": decision.get("expected_return"),
                    "forecast_confidence": decision.get("forecast_confidence"),
                    "sample_n": decision.get("sample_n"),
                    **fwd,
                })

    obs = pd.DataFrame(rows)
    if obs.empty:
        raise RuntimeError("沒有產生 doctor walk-forward 觀察值")
    obs.to_csv(out_dir / "doctor_walkforward_observations.csv", index=False, encoding="utf-8-sig")
    report = {
        "architecture_version": ARCHITECTURE_VERSION,
        "scope": "completed-day single-stock doctor buy decision; next-session open execution",
        "year": year,
        "requested_stocks": stocks,
        "validated_stocks": int(obs["ticker"].nunique()),
        "cutoff_dates": sorted(obs["cutoff_date"].unique().tolist()),
        "lookahead_control": "Each cutoff uses only bars on/before cutoff; trade entry is next session open; future returns are read only after the decision is frozen.",
        "intraday_note": "Does not validate intraday continuation/pullback fills because minute/order-book history is not included in the daily archive.",
        "metrics": {h: _summary(obs[obs["horizon"] == h]) for h in HORIZONS},
    }
    (out_dir / "doctor_validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Alpha Radar V16.7 — Doctor 2026 point-in-time validation",
        "",
        f"- Architecture: `{ARCHITECTURE_VERSION}`",
        f"- Real stocks: `{report['validated_stocks']}` / `{stocks}`",
        f"- Historical cutoffs: `{len(report['cutoff_dates'])}`",
        "- Decision: POSTCLOSE doctor; READY entries execute at the next session open.",
        "- No look-ahead: decision inputs are truncated at each historical cutoff.",
        "- Intraday minute/order-book execution is outside this validator's scope.",
        "",
        "| Horizon | Obs | READY | READY rate | READY median | All median | Same-date lift | Positive rate | Median MAE | Median MFE |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    def pct(v): return "—" if v is None else f"{v*100:.2f}%"
    for h in HORIZONS:
        m = report["metrics"][h]
        lines.append(
            f"| {h} | {m.get('observations',0)} | {m.get('ready_count',0)} | {pct(m.get('ready_rate'))} | "
            f"{pct(m.get('ready_median_return'))} | {pct(m.get('all_median_return'))} | {pct(m.get('mean_same_date_median_lift'))} | "
            f"{pct(m.get('ready_positive_rate'))} | {pct(m.get('ready_median_mae'))} | {pct(m.get('ready_median_mfe'))} |"
        )
    (out_dir / "doctor_validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/doctor_validation_2026_v17")
    ap.add_argument("--stocks", type=int, default=60)
    ap.add_argument("--period", default="8y")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--points", type=int, default=12)
    ap.add_argument("--seed", type=int, default=1337)
    args = ap.parse_args()
    try:
        report = run(Path(args.out), args.stocks, args.period, args.year, args.points, args.seed)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except Exception as exc:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        failure = {
            "status": "NOT_RUN",
            "reason": f"{type(exc).__name__}: {exc}",
            "note": "No synthetic data was substituted for real-market performance validation.",
        }
        (out / "doctor_validation_not_run.json").write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(failure, ensure_ascii=False, indent=2), file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
