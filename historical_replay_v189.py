"""V18.9 real OHLCV historical walk-forward replay (PRICE-ONLY core).

No data is fabricated. If no accessible real price cache is provided, outputs
NOT_RUN and never writes fabricated Top-5 or success metrics.

The full V18.8 alpha model additionally needs point-in-time corporate / chip
records. This tool intentionally uses the unchanged production technical and
historical-analog engines, not present-day fundamentals in past cutoffs.

Run:
 python historical_replay_v189.py --db data/daily_prices.sqlite --out data/replay_v189 \
   --start 2023-01-01 --end 2025-12-31 --repeats 3 --per-repeat 4 --max-stocks 0

See VALIDATION_V18_9_GUIDE.md for scope and limitations.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import sqlite3
from typing import Any

import numpy as np
import pandas as pd

from radar_service import RunSettings, empirical_evidence_score
from return_first_model import estimate_all_horizons
from strategy_config import SELECTION_WEIGHTS, CANONICAL_MODEL_POLICY, ARCHITECTURE_VERSION

HORIZONS = {"short": 10, "mid": 40, "long": 120}
HORIZON_TURNOVER = dict(CANONICAL_MODEL_POLICY["horizon_min_avg_turnover"])
FIELDS = ["Open", "High", "Low", "Close", "Volume"]


@dataclass(frozen=True)
class ReplayConfig:
    db: Path
    out: Path
    start: str = "2023-01-01"
    end: str = "2025-12-31"
    repeats: int = 3
    per_repeat: int = 4
    seed: int = 20261012
    max_stocks: int = 0
    min_history: int = 380
    min_pool: int = 50
    top_n: int = 5


def connect_ro(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"真實股價資料庫不存在: {path}")
    # Protect the user's deployed SQLite: historical validation never mutates it.
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=30)
    if "daily_prices" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        conn.close()
        raise ValueError("資料庫沒有 daily_prices 表；請提供 Alpha Radar 歷史行情快取")
    return conn


def available_tickers(conn: sqlite3.Connection) -> list[str]:
    return sorted(str(x[0]) for x in conn.execute("SELECT DISTINCT ticker FROM daily_prices"))


def get_ohlcv(conn: sqlite3.Connection, ticker: str) -> pd.DataFrame:
    q = "SELECT date, open, high, low, close, volume FROM daily_prices WHERE ticker=? ORDER BY date"
    raw = pd.read_sql_query(q, conn, params=(ticker,))
    if raw.empty:
        return pd.DataFrame(columns=FIELDS)
    raw.columns = ["Date", *FIELDS]
    raw["Date"] = pd.to_datetime(raw["Date"], errors="coerce")
    for c in FIELDS:
        raw[c] = pd.to_numeric(raw[c], errors="coerce")
    raw = raw.dropna(subset=["Date", *FIELDS]).drop_duplicates("Date", keep="last")
    good = (raw[["Open", "High", "Low", "Close"]] > 0).all(axis=1) & (raw["Volume"] >= 0)
    good &= (raw["High"] >= raw[["Open", "Close", "Low"]].max(axis=1)) & (raw["Low"] <= raw[["Open", "Close", "High"]].min(axis=1))
    return raw.loc[good].set_index("Date")[FIELDS].sort_index()


def random_historical_dates(benchmark: pd.DataFrame, cfg: ReplayConfig) -> list[dict[str, Any]]:
    idx = benchmark.index
    earliest = max(cfg.min_history, 241)
    latest = len(idx) - max(HORIZONS.values()) - 1
    if latest <= earliest:
        raise ValueError("大盤歷史區間不足以完成 120 個交易日追蹤")
    start = pd.Timestamp(cfg.start)
    end = pd.Timestamp(cfg.end)
    eligible = [pd.Timestamp(x) for i, x in enumerate(idx) if earliest <= i <= latest and start <= x <= end]
    if len(eligible) < cfg.per_repeat * 5:
        raise ValueError(f"歷史可選交易日僅 {len(eligible)} 天，不足以跨市場階段抽樣")
    n = int(cfg.per_repeat)
    rng = random.Random(cfg.seed)
    used: set[str] = set()
    slots = np.array_split(np.arange(len(eligible)), n)
    result: list[dict[str, Any]] = []
    for repeat in range(1, int(cfg.repeats) + 1):
        one = []
        for b in slots:
            candidates = [eligible[int(i)] for i in b if eligible[int(i)].date().isoformat() not in used]
            if not candidates:
                candidates = [eligible[int(i)] for i in b]
            choice = rng.choice(candidates)
            one.append(choice)
            used.add(choice.date().isoformat())
        for date in sorted(one):
            result.append({"repeat": repeat, "cutoff_date": date.date().isoformat()})
    return result


def _production_core_share(h: str) -> float:
    w = SELECTION_WEIGHTS[h]
    return float(w["technical"] / (w["technical"] + w["empirical"]))


def future_stats(stock: pd.DataFrame, benchmark: pd.DataFrame, cutoff: str, days: int, cost: float) -> dict:
    """Evaluate next benchmark-session open to session-N close.

    If any of those trading sessions is missing in the stock series, never
    quietly skip forward to next available stock row, which would understate
    suspension/delisting risk and overstate tradability.
    """
    result = {"exit_status": "NO_OUTCOME", "entry_date": "", "exit_date": "", "entry_open": None,
              "exit_close": None, "net_return": None, "benchmark_return": None,
              "excess_return": None, "mae": None, "mfe": None}
    idx = benchmark.index
    pos = idx.get_indexer([pd.Timestamp(cutoff)])[0]
    if pos < 0 or pos + days >= len(idx):
        result["exit_status"] = "BENCHMARK_INCOMPLETE"
        return result
    path_dates = idx[pos + 1:pos + 1 + days]
    result["entry_date"] = str(path_dates[0].date())
    result["exit_date"] = str(path_dates[-1].date())
    if not path_dates.isin(stock.index).all():
        result["exit_status"] = "MISSING_SESSION_OR_SUSPENDED"
        return result
    path = stock.loc[path_dates]
    enter = float(path.iloc[0]["Open"])
    leave = float(path.iloc[-1]["Close"])
    b_in = float(benchmark.loc[path_dates[0], "Open"])
    b_out = float(benchmark.loc[path_dates[-1], "Close"])
    if not all(np.isfinite(x) and x > 0 for x in (enter, leave, b_in, b_out)):
        result["exit_status"] = "INVALID_PRICE"
        return result
    result.update({
        "exit_status": "VALUED_PRICE_ONLY", "entry_open": round(enter, 4), "exit_close": round(leave, 4),
        "net_return": leave / enter - 1.0 - cost,
        "benchmark_return": b_out / b_in - 1.0,
        "excess_return": leave / enter - 1.0 - cost - (b_out / b_in - 1.0),
        "mae": float(path["Low"].min() / enter - 1),
        "mfe": float(path["High"].max() / enter - 1),
    })
    # Very large adjacent changes in back-adjusted / unadjusted price histories
    # deserve manual corporate-action verification, not automatic "profits".
    daily = path["Close"].pct_change().abs().dropna()
    if (daily > 0.45).any():
        result["exit_status"] = "CORPORATE_ACTION_REVIEW"
        for k in ("net_return", "excess_return", "mae", "mfe"):
            result[k] = None
    return result


def _is_common_stock(ticker: str) -> bool:
    code = str(ticker).split(".")[0]
    return len(code) == 4 and code.isdigit() and not code.startswith("0") and str(ticker).endswith((".TW", ".TWO"))


def _asfloat(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def apply_confidence_free_rank_tech_emp(technical: float, empirical: float, share: float) -> float:
    """V18.8 production price-only _combined_ranking_score share-equivalent."""
    return round(float(np.clip(share * technical + (1 - share) * empirical, 0, 100)), 1)


def _performance(picks: pd.DataFrame, key: str = "ranking_score") -> dict:
    valued = picks[picks["exit_status"] == "VALUED_PRICE_ONLY"].copy()
    net = pd.to_numeric(valued.get("net_return", pd.Series(dtype=float)), errors="coerce").dropna()
    excess = pd.to_numeric(valued.get("excess_return", pd.Series(dtype=float)), errors="coerce").dropna()
    if net.empty:
        return {"signal_count": int(len(picks)), "valued_count": 0, "mean_net_return": None,
                "positive_rate": None, "mean_excess_return": None, "beat_index_rate": None,
                "p10_return": None, "mean_mae": None}
    return {
        "signal_count": int(len(picks)), "valued_count": int(len(valued)),
        "mean_net_return": float(net.mean()), "median_net_return": float(net.median()),
        "positive_rate": float((net > 0).mean()),
        "mean_excess_return": float(excess.mean()), "beat_index_rate": float((excess > 0).mean()),
        "p10_return": float(net.quantile(0.10)), "mean_mae": float(valued["mae"].mean()),
        "mean_mfe": float(valued["mfe"].mean()),
    }


def render_dashboard_html(rows: pd.DataFrame, path: Path) -> None:
    """Local, self-contained report; separate panels clarify three horizons."""
    try:
        from plotly.subplots import make_subplots
        import plotly.graph_objects as go
        names={"short":"短線 10 日","mid":"中線 40 日","long":"長線 120 日"}
        fig=make_subplots(rows=3,cols=1,shared_xaxes=False,vertical_spacing=0.16,
                          subplot_titles=[names[h] for h in HORIZONS])
        for row_num,h in enumerate(HORIZONS,1):
            d=rows[rows['horizon']==h].sort_values('cutoff_date')
            if d.empty:continue
            y=d['mean_net_return'].astype(float)*100
            b=(d['mean_net_return'].astype(float)-d['mean_excess_return'].astype(float))*100
            fig.add_trace(go.Bar(x=d['cutoff_date'], y=y, marker_color='#2563eb',name='策略含成本淨報酬',
                                 showlegend=row_num==1,hovertemplate='%{x}: %{y:.2f}%<extra></extra>'),row=row_num,col=1)
            fig.add_trace(go.Bar(x=d['cutoff_date'], y=b, marker_color='#94a3b8',name='同期加權指數',
                                 showlegend=row_num==1,hovertemplate='%{x}: %{y:.2f}%<extra></extra>'),row=row_num,col=1)
            fig.update_yaxes(title_text='報酬 %',zeroline=True,row=row_num,col=1)
        fig.update_layout(barmode='group',title='歷史隨機日期回放｜真實行情僅價格核心',
                          template='plotly_white',height=1080,paper_bgcolor='#f8fafc',plot_bgcolor='#ffffff',
                          legend=dict(orientation='h',y=1.09),margin=dict(l=60,r=24,t=110,b=60),
                          font=dict(family='Arial, Noto Sans CJK TC, sans-serif',size=13))
        fig.write_html(str(path),include_plotlyjs=True,full_html=True)
    except Exception as exc:
        path.with_suffix('.error.txt').write_text(
            f'Optional visualization failed: {type(exc).__name__}: {exc}', encoding='utf-8')


def audit_weight_calibration(scores: pd.DataFrame, bm: pd.DataFrame, conn: sqlite3.Connection,
                             cfg: ReplayConfig, cost: float) -> dict:
    """Predeclared train / chronological, embargoed holdout review; NEVER auto-tune.

    In-sample parameter exploration is not validation. The holdout is only a
    single pass/fail check and is not used for selecting a candidate grid point.
    Unresolved corporate actions / incomplete trade paths block proposals.
    """
    unique_dates = sorted(scores["cutoff_date"].unique())
    if len(unique_dates) < 8:
        return {"status": "INSUFFICIENT_DATES", "dates": len(unique_dates), "applied_to_model": False}
    holdout_cut = unique_dates[int(round(len(unique_dates) * .67))]
    bidx = bm.index
    pos_holdout = bidx.get_indexer([pd.Timestamp(holdout_cut)])[0]
    by_stock: dict[str, pd.DataFrame] = {}
    memo = {}

    def outcome(t: str, d: str, days: int):
        key=(t,d,days)
        if key not in memo:
            if t not in by_stock:
                by_stock[t]=get_ohlcv(conn,t)
            memo[key]=future_stats(by_stock[t], bm, d, days, cost)
        return memo[key]

    result = {"status": "RESEARCH_ONLY", "applied_to_model": False,
              "holdout_start": str(holdout_cut), "horizons": {}}
    for h, days in HORIZONS.items():
        g = scores[scores["horizon"]==h].copy()
        # Strict embargo: a training date's entire forward outcome must mature
        # BEFORE the first holdout selection can be made.
        train = [d for d in unique_dates if bidx.get_indexer([pd.Timestamp(d)])[0]+days < pos_holdout]
        holdout = [d for d in unique_dates if d >= holdout_cut]
        drec = {"train_dates":train,"holdout_dates":holdout,"horizon":h,
                "change_applied":False,"recommendation":"KEEP_PRODUCTION_WEIGHTS"}
        if len(train)<4 or len(holdout)<3:
            drec["reason"]="PURGED_SPLIT_INSUFFICIENT"
            result["horizons"][h]=drec
            continue
        base=_production_core_share(h)
        grid=sorted(set(round(float(np.clip(base+delta,0.1,0.9)),5)
                        for delta in (-0.10,-0.05,0,0.05,0.10)))

        def simulate(share, dates):
            picks=[]
            for date in dates:
                v = g[g["cutoff_date"]==date].copy()
                v["candidate_score"]=share*v["score_tech"]+(1-share)*v["score_empirical"]
                top=v.sort_values(["candidate_score","ticker"], ascending=[False,True]).head(cfg.top_n)
                for r in top.itertuples(index=False):
                    path=outcome(r.ticker,date,days)
                    picks.append({"date":date,"excess":path.get("excess_return"),
                                  "return":path.get("net_return"),"status":path["exit_status"]})
            df=pd.DataFrame(picks)
            valid=df[df["status"]=="VALUED_PRICE_ONLY"]
            if valid.empty:
                return {"dates":len(dates),"valued":0,"signals":len(df),"mean_excess":None}
            dated=valid.groupby('date')['excess'].mean()
            return {"dates":len(dates),"valued":len(valid),"signals":len(df),
                    "coverage":len(valid)/len(df),"mean_excess":float(valid['excess'].mean()),
                    "mean_net":float(valid['return'].mean()),
                    "p10_net":float(valid['return'].quantile(.1)),
                    "date_alpha":{str(d):float(a) for d,a in dated.items()}}

        train_results={str(v):simulate(v,train) for v in grid}
        adequate=[(v,train_results[str(v)]) for v in grid if train_results[str(v)].get('coverage',0)>=0.98]
        if not adequate:
            drec.update({"reason":"TRAIN_MISSING_OUTCOMES","grid_train":train_results})
            result["horizons"][h]=drec
            continue
        base_res=simulate(base,train)
        best_share,best_train=max(adequate,key=lambda x:x[1]['mean_excess'])
        candidate_test=simulate(best_share,holdout)
        baseline_test=simulate(base,holdout)
        drec.update({"production_core_share":round(base,5),
                     "best_training_share":best_share,"train_grid":train_results,
                     "holdout_baseline":baseline_test,"holdout_candidate":candidate_test,
                     "reason":"HOLDOUT_NOT_IMPROVED_OR_NOT_INDEPENDENT"})
        if abs(best_share-base)<1e-5:
            drec['reason']='TRAIN_OPTIMUM_EQUALS_BASELINE'
        elif min(candidate_test.get('coverage',0),baseline_test.get('coverage',0))<0.98:
            drec['reason']='HOLDOUT_MISSING_OUTCOMES'
        elif candidate_test['mean_excess']<=baseline_test['mean_excess']+.005:
            drec['reason']='HOLDOUT_EXCESS_LIFT_BELOW_0.5PP'
        elif candidate_test['p10_net']<baseline_test['p10_net']-.01:
            drec['reason']='HOLDOUT_DOWNSIDE_WORSE_THAN_1PP'
        else:
            delta={d:candidate_test['date_alpha'].get(d,float('nan'))-baseline_test['date_alpha'].get(d,float('nan'))
                   for d in holdout}
            wins=sum(math.isfinite(x) and x>0 for x in delta.values())
            if wins >= math.ceil(len(holdout)*.75):
                # This is a research candidate, not statistically proven.
                # Re-using the same holdout after implementation would be leakage.
                drec['reason']='CANDIDATE_FOR_NEW_EXTERNAL_HOLDOUT_ONLY'
                w=dict(SELECTION_WEIGHTS[h]);total=w['technical']+w['empirical']
                drec['UNAPPLIED_proposed_weights']={**w,'technical':round(total*best_share,5),
                                                      'empirical':round(total*(1-best_share),5)}
        result['horizons'][h]=drec
    return result


def _insufficient_report(cfg: ReplayConfig, message: str) -> dict:
    return {
        "status": "NOT_RUN", "cause": message,
        "real_history_validation": False, "production_weights_modified": False,
        "note": "需要真實台股日線快取及^TWII；不得以合成行情替代實際歷史選股績效。",
        "expected_source": str(cfg.db),
    }


def run(cfg: ReplayConfig) -> dict:
    cfg.out.mkdir(parents=True, exist_ok=True)
    conn = connect_ro(cfg.db)
    try:
        tickers = available_tickers(conn)
        if "^TWII" not in tickers:
            raise ValueError("缺少 ^TWII 大盤日線，禁止以其他股票充當加權指數")
        bm = get_ohlcv(conn, "^TWII")
        if len(bm) < cfg.min_history + 120:
            raise ValueError(f"加權指數僅 {len(bm)} 筆，歷史深度不足")
        picks = random_historical_dates(bm, cfg)
        all_tickers = [t for t in tickers if _is_common_stock(t)]
        if len(all_tickers) < cfg.min_pool:
            raise ValueError(f"可用普通股僅 {len(all_tickers)} 檔，不足預設至少 {cfg.min_pool} 檔")
        # When deliberately sampling (faster smoke research), restrict the
        # pool with a deterministic lottery. Never label it "full-market".
        if cfg.max_stocks > 0 and len(all_tickers) > cfg.max_stocks:
            all_tickers = sorted(random.Random(cfg.seed).sample(all_tickers, cfg.max_stocks))
        settings = RunSettings(model_family="price_only", canonical_mode=True)
        # No producer/network calls; real OHLCV is read strictly from the cache.
        cost = float(settings.commission * 2 + settings.sell_tax + settings.slippage * 2)
        raw_rows = []
        fail_counts: Counter = Counter()
        ordered = sorted(set(x["cutoff_date"] for x in picks))
        bench_hist = {date: bm.loc[:pd.Timestamp(date)] for date in ordered}
        market_ret = {
            date: float(b["Close"].iloc[-1] / b["Close"].iloc[-21] - 1) if len(b) >= 21 else 0.0
            for date, b in bench_hist.items()
        }
        for index, ticker in enumerate(all_tickers, start=1):
            stock = get_ohlcv(conn, ticker)
            if stock.empty:
                fail_counts["no_history"] += 1
                continue
            for date in ordered:
                cut = pd.Timestamp(date)
                hist = stock.loc[:cut]
                if len(hist) < cfg.min_history or hist.index[-1] != cut:
                    fail_counts["history_short_or_stale"] += 1
                    continue
                last = float(hist["Close"].iloc[-1]); turnover = float((hist["Close"].tail(20) * hist["Volume"].tail(20)).mean())
                if (hist["Close"].tail(125).pct_change().abs() > 0.45).any():
                    fail_counts["corporate_action_near_signal"] += 1
                    continue
                if last < float(settings.min_price) or turnover < float(settings.min_avg_turnover):
                    fail_counts["liquidity"] += 1
                    continue
                try:
                    estimates = estimate_all_horizons(hist, settings,
                        twii_ret_20d=market_ret[date], benchmark_df=bench_hist[date])
                except Exception:
                    fail_counts["model_error"] += 1
                    continue
                for h, days in HORIZONS.items():
                    if turnover < float(HORIZON_TURNOVER[h]):
                        continue
                    forecast = estimates[h]
                    if not forecast.get("estimate_available"):
                        fail_counts[f"{h}_insufficient_analog"] += 1
                        continue
                    tscore = _asfloat(forecast.get("technical_factor_score"))
                    empirical = _asfloat(empirical_evidence_score(forecast))
                    if tscore is None or empirical is None:
                        continue
                    share = _production_core_share(h)
                    raw_rows.append({
                        "cutoff_date": date, "ticker": ticker, "horizon": h,
                        "score_tech": tscore, "score_empirical": empirical,
                        "ranking_score": apply_confidence_free_rank_tech_emp(tscore, empirical, share),
                        "signal_close": last, "turnover_20d": turnover,
                        "forecast_confidence": _asfloat(forecast.get("confidence_score")),
                        "analog_cases": forecast.get("local_effective_n"),
                    })
            if index % 100 == 0:
                print(f"[historical-replay] completed {index}/{len(all_tickers)} tickers", flush=True)

        scores = pd.DataFrame(raw_rows)
        if scores.empty:
            raise ValueError("所有日期均無可供選股的有效歷史評分，無法產出真實清單")
        pool_counts = scores.groupby(["cutoff_date", "horizon"]).size().to_dict()
        missing_pools = [(d, h, pool_counts.get((d, h), 0)) for d in ordered for h in HORIZONS
                         if pool_counts.get((d, h), 0) < cfg.min_pool]
        if missing_pools:
            raise ValueError(f"歷史日評分股票數不足 {cfg.min_pool} 檔，不能宣稱有效市場比較：{missing_pools[:5]}")
        picks_df = scores.sort_values(["cutoff_date", "horizon", "ranking_score", "ticker"],
            ascending=[True, True, False, True]).groupby(["cutoff_date", "horizon"], group_keys=False).head(cfg.top_n).copy()
        picks_df["rank"] = picks_df.groupby(["cutoff_date", "horizon"]).cumcount() + 1
        repeat_map = {x["cutoff_date"]: x["repeat"] for x in picks}
        picks_df["repeat"] = picks_df["cutoff_date"].map(repeat_map)
        # Computing future prices only now makes the time boundary auditable:
        # selection ranks were frozen before future returns were accessed.
        stock_dfs = {t: get_ohlcv(conn, t) for t in picks_df["ticker"].unique()}
        outcomes = [future_stats(stock_dfs[r.ticker], bm, r.cutoff_date, HORIZONS[r.horizon], cost)
                    for r in picks_df.itertuples(index=False)]
        picks_df = pd.concat([picks_df.reset_index(drop=True), pd.DataFrame(outcomes)], axis=1)
        picks_df["net_return_pct"] = picks_df["net_return"] * 100
        picks_df["excess_return_pct"] = picks_df["excess_return"] * 100
        picks_df["mae_pct"] = picks_df["mae"] * 100
        picks_df.to_csv(cfg.out / "historical_top5_real.csv", index=False, encoding="utf-8-sig")
        # Random-stock comparator is fixed without seeing future returns. Each
        # group draws five from the exact SAME past-eligible pool.
        random_rows = []
        for (date, h), d in scores.groupby(["cutoff_date", "horizon"], sort=True):
            available = sorted(d["ticker"].tolist())
            selected = random.Random(f"{cfg.seed}-{date}-{h}").sample(available, cfg.top_n)
            for rank, ticker in enumerate(selected, 1):
                if ticker not in stock_dfs:
                    stock_dfs[ticker] = get_ohlcv(conn, ticker)
                result_path = future_stats(stock_dfs[ticker], bm, date, HORIZONS[h], cost)
                random_rows.append({"cutoff_date": date, "horizon": h, "rank": rank,
                                    "ticker": ticker, **result_path})
        random_df = pd.DataFrame(random_rows)
        random_df.to_csv(cfg.out / "random_pool_baseline.csv", index=False, encoding="utf-8-sig")
        # All scored stocks are saved as a separate research-only artifact;
        # historical rankings can be re-audited without repeating provider calls.
        scores.to_csv(cfg.out / "all_scores_price_only.csv", index=False, encoding="utf-8-sig")
        groups = []
        for (date, horizon), df in picks_df.groupby(["cutoff_date", "horizon"], sort=True):
            m = _performance(df)
            groups.append({"cutoff_date": date, "repeat": int(df.iloc[0]["repeat"]), "horizon": horizon,
                           "pool_n": int(pool_counts.get((date, horizon), 0)), **m})
        per_date = pd.DataFrame(groups)
        per_date.to_csv(cfg.out / "cutoff_performance.csv", index=False, encoding="utf-8-sig")
        render_dashboard_html(per_date, cfg.out / "validation_dashboard.html")
        summary = []
        for horizon in HORIZONS:
            h_df = picks_df[picks_df["horizon"] == horizon]
            m = _performance(h_df)
            summary.append({"horizon": horizon, "distinct_dates": h_df["cutoff_date"].nunique(), **m})
        pd.DataFrame(summary).to_csv(cfg.out / "horizon_summary.csv", index=False, encoding="utf-8-sig")
        tuning = audit_weight_calibration(scores, bm, conn, cfg, cost)
        (cfg.out / "weight_research_only.json").write_text(json.dumps(tuning, ensure_ascii=False, indent=2), encoding="utf-8")
        coverage_ratios = [x["valued_count"] / max(1, x["signal_count"]) for x in summary]
        random_summary = {h: _performance(random_df[random_df["horizon"] == h]) for h in HORIZONS}
        acquisition = {}
        metadata_path = cfg.out / 'data_acquisition.json'
        if metadata_path.exists():
            try:
                acquisition = json.loads(metadata_path.read_text(encoding='utf-8'))
            except Exception:
                acquisition = {'source_metadata_unreadable': True}
        report = {
            "status": "COMPLETED_LIMITED_PRICE_ONLY" if min(coverage_ratios) >= 0.95 else "COMPLETED_INCOMPLETE_OUTCOMES",
            "execution_at_utc": datetime.now(timezone.utc).isoformat(),
            "architecture_version": ARCHITECTURE_VERSION,
            "repeats": cfg.repeats, "points_per_repeat": cfg.per_repeat,
            "cutoffs": picks, "unique_dates": len(ordered),
            "stock_universe_count": len(all_tickers),
            "universe_type": "CACHE_AVAILABLE_COMMON_STOCKS; not archived historical full membership",
            "stock_pool_mode": "ALL_AVAILABLE_CACHE_SERIES_NOT_HISTORICAL_FULL_MARKET" if cfg.max_stocks == 0 else f"sampled_from_cached_symbols_max_{cfg.max_stocks}",
            "data_acquisition_scope": acquisition.get('scope','UNKNOWN_CACHE_MEMBERSHIP'),
            "data_acquisition_source": acquisition.get('source','UNKNOWN_CACHE_SOURCE'),
            "actual_scored_pool_range": {h: [int(min(v for (d, hh), v in pool_counts.items() if hh==h)),
                                                    int(max(v for (d, hh), v in pool_counts.items() if hh==h))] for h in HORIZONS},
            "ranking_scope": "V18.8 unchanged price-only technical + historical analog core",
            "NOT_VALIDATED": ["完整 V18 DQ/MC 多因子樣本外回測", "歷史年度上市母體與已下市股",
                              "歷史財報與法人公告 PIT 版本", "當日收盤價格可交易保證",
                              "除權息/拆併股對所有來源的完整驗證", "不同隨機切點的報酬相互獨立"],
            "weights_modified": False, "weight_research": tuning,
            "outcome_coverage_by_horizon": {x['horizon']: round(x['valued_count'] / max(1,x['signal_count']),4) for x in summary},
            "roundtrip_cost_rate": cost,
            "summary": summary, "random_stock_baseline": random_summary,
            "excluded_rows": dict(fail_counts),
        }
        (cfg.out / "replay_status.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_markdown(cfg.out, report, picks_df)
        return report
    finally:
        conn.close()


def _pct(value):
    return "—" if value is None or not np.isfinite(value) else f"{value * 100:.2f}%"


def _write_markdown(out_dir: Path, report: dict, picks_df: pd.DataFrame) -> None:
    lines = [
        "# Alpha Radar V18.9｜真實行情限定範圍驗效", "",
        "**注意：本報告是已保存真實股價的 PRICE-ONLY 歷史選股核心，不是 V18.8 全因子績效認證。**", "",
        f"- 歷史行情母體：{report['stock_universe_count']} 檔；{report['stock_pool_mode']}",
        f"- 隨機測試：{report['repeats']} 輪 × 每輪 {report['points_per_repeat']} 日，共 {report['unique_dates']} 個不同歷史日期", "- 模擬成本：買賣手續費 + 證交稅 + 雙向滑價（依部署設定）", "",
        "## 短／中／長線驗效總表", "",
        "| 週期 | 日期數 | 已可評值/推薦數 | 平均淨報酬 | 勝率 | 平均超額報酬 | 打敗大盤率 |", "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in report["summary"]:
        lines.append(f"| {r['horizon']} | {r['distinct_dates']} | {r['valued_count']}/{r['signal_count']} | "
                     f"{_pct(r.get('mean_net_return'))} | {_pct(r.get('positive_rate'))} | "
                     f"{_pct(r.get('mean_excess_return'))} | {_pct(r.get('beat_index_rate'))} |")
    lines += ["", "## 同日期同母體的隨機五檔比較（僅供參考，非獨立統計）", "",
              "| 週期 | 原策略平均淨報酬 | 隨機五檔平均淨報酬 | 原策略平均超額報酬 | 隨機五檔平均超額報酬 |",
              "|---|---:|---:|---:|---:|"]
    for r in report["summary"]:
        base = report["random_stock_baseline"][r["horizon"]]
        lines.append(f"| {r['horizon']} | {_pct(r.get('mean_net_return'))} | "
                     f"{_pct(base.get('mean_net_return'))} | {_pct(r.get('mean_excess_return'))} | "
                     f"{_pct(base.get('mean_excess_return'))} |")
    lines += ["", "## 每日 Top-5 清單（收盤訊號，隔日開盤進場）", ""]
    for (date, h), d in picks_df.groupby(["cutoff_date", "horizon"], sort=True):
        lines.extend([f"### {date} · {h}", "", "| 名次 | 代號 | 分數 | 次開盤 | 驗效日 | 含成本報酬 | 狀態 |", "|---|---|---:|---:|---|---:|---|"])
        for r in d.itertuples(index=False):
            lines.append(f"| {int(r.rank)} | {r.ticker} | {r.ranking_score:.1f} | "
                         f"{r.entry_open if r.entry_open is not None else '—'} | {r.exit_date} | "
                         f"{_pct(r.net_return)} | {r.exit_status} |")
        lines.append("")
    lines += ["## 不能擴大解讀的限制", ""]
    lines += [f"- {x}" for x in report["NOT_VALIDATED"]]
    (out_dir / "walkforward_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="V18.9 僅讀取真實快取的歷史短中長線重複驗效")
    p.add_argument("--db", type=Path, default=Path("data/daily_prices.sqlite"))
    p.add_argument("--out", type=Path, default=Path("data/historical_replay_v189"))
    p.add_argument("--start", default="2023-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--per-repeat", type=int, default=4)
    p.add_argument("--seed", type=int, default=20261012)
    p.add_argument("--max-stocks", type=int, default=0, help="0=所有快取普通股；>0=實驗抽樣，報告會標示限制")
    p.add_argument("--min-pool", type=int, default=50)
    p.add_argument("--min-history", type=int, default=380)
    p.add_argument("--top-n", type=int, default=5)
    a = p.parse_args(argv)
    cfg = ReplayConfig(**vars(a))
    cfg.out.mkdir(parents=True, exist_ok=True)
    try:
        report = run(cfg)
        print(json.dumps({"status": report["status"], "out": str(cfg.out),
                          "distinct_cutoffs": report["unique_dates"], "stocks": report["stock_universe_count"]},
                         ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        data = _insufficient_report(cfg, msg)
        (cfg.out / "replay_status.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (cfg.out / "VALIDATION_NOT_RUN.md").write_text(
            "# V18.9 實際行情驗效：未能執行\n\n" + msg +
            "\n\n目前沒有可讀取的真實歷史日線與加權指數資料，故不產生 Top-5 或投資績效。\n", encoding="utf-8")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
