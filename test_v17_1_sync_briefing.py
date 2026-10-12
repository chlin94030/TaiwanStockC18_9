from __future__ import annotations

import datetime as dt
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import radar_service as rs
from market_briefing import human_global_summary, _headline_bias

TZ = dt.timezone(dt.timedelta(hours=8))


def frame_until(end: str, seed: int = 1, rows: int = 360) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=rows)
    ret = rng.normal(0.0005, 0.015, rows)
    close = 80 * np.exp(np.cumsum(ret))
    op = close * (1 + rng.normal(0, 0.003, rows))
    hi = np.maximum(op, close) * (1 + rng.uniform(0.001, 0.015, rows))
    lo = np.minimum(op, close) * (1 - rng.uniform(0.001, 0.015, rows))
    vol = rng.integers(120_000, 600_000, rows).astype(float)
    return pd.DataFrame({"Open": op, "High": hi, "Low": lo, "Close": close, "Volume": vol}, index=idx)


def _run_with_official_sequence(sequence):
    twse_tickers = [f"{2300+i}.TW" for i in range(4)]
    tpex_tickers = [f"{6500+i}.TWO" for i in range(4)]
    tickers = twse_tickers + tpex_tickers
    frames = {t: frame_until("2026-10-08", i + 10) for i, t in enumerate(tickers)}
    frames["^TWII"] = frame_until("2026-10-08", 99)
    calls = []

    class FakeStore:
        def __init__(self, _path):
            self.frames = frames
        def batch_fetch_and_update(self, requested, period="5y", progress=None, **kwargs):
            return {"downloaded_tickers": 0, "requested": 0, "mode": "incremental", "covered_ratio": 1.0, "last_full_refresh": "2026-10-01", "errors": []}
        def overlay_official_eod(self, target_date=None, progress=None):
            calls.append(target_date)
            idx = min(len(calls)-1, len(sequence)-1)
            obj = dict(sequence[idx])
            obj.setdefault("errors", [])
            return obj
        def iter_prices(self, requested, limit=None, end_date=None):
            for t in requested:
                df = self.frames.get(t, pd.DataFrame()).copy()
                if end_date and not df.empty:
                    df = df[df.index <= pd.Timestamp(end_date)]
                yield t, df
        def get_prices(self, ticker, limit=None, end_date=None):
            df = self.frames.get(ticker, pd.DataFrame()).copy()
            if end_date and not df.empty:
                df = df[df.index <= pd.Timestamp(end_date)]
            if limit and not df.empty:
                df = df.tail(int(limit))
            return df

    universe = pd.DataFrame({"ticker": tickers, "name": [f"測試{i}" for i in range(8)], "industry": ["電子"] * 8})
    universe.attrs["twse_count"] = 4
    universe.attrs["tpex_count"] = 4

    originals = {
        "DailyPriceStore": rs.DailyPriceStore,
        "fetch_twse_universe": rs.fetch_twse_universe,
        "_load_breadth_research": rs._load_breadth_research,
        "_enrich_tickers": rs._enrich_tickers,
    }
    try:
        rs.DailyPriceStore = FakeStore
        rs.fetch_twse_universe = lambda: universe.copy()
        rs._load_breadth_research = lambda tickers, data_dir, asof_date=None: {
            t: {
                "breadth_comparable": True,
                "monthly_revenue": {"available": True, "latest": {"yoy_pct": 8, "mom_pct": 2}},
                "financials": {"available": True, "latest_ytd_eps": 2.0},
                "valuation": {"available": True, "pe": 18},
                "institutional_flow": {"available": True, "total_net_lots": 50, "sessions": 1},
            } for t in tickers
        }
        rs._enrich_tickers = lambda *args, **kwargs: None
        with tempfile.TemporaryDirectory() as td:
            now = dt.datetime(2026, 10, 9, 8, 30, tzinfo=TZ)
            snap = rs.run_scan(Path(td), rs.RunSettings(), now=now)
        return snap, calls
    finally:
        for k, v in originals.items():
            setattr(rs, k, v)


def test_retry_repairs_twse_one_day_lag():
    seq = [
        {"twse_date":"2026-10-07", "tpex_date":"2026-10-08", "index_date":"2026-10-08"},
        {"twse_date":"2026-10-08", "tpex_date":"2026-10-08", "index_date":"2026-10-08"},
    ]
    snap, calls = _run_with_official_sequence(seq)
    assert calls[0] == "2026-10-08", calls
    assert len(calls) >= 2, calls
    assert snap["session"]["common_complete_date"] == "2026-10-08", snap["session"]
    assert snap["session"]["twse_complete_date"] == "2026-10-08"
    assert snap["session"]["tpex_complete_date"] == "2026-10-08"
    assert {x["price_date"] for x in snap["stocks"]} == {"2026-10-08"}


def test_failed_repair_rolls_everything_to_common_date():
    seq = [
        {"twse_date":"2026-10-07", "tpex_date":"2026-10-08", "index_date":"2026-10-08"},
        {"twse_date":"2026-10-07", "tpex_date":"2026-10-08", "index_date":"2026-10-08"},
    ]
    snap, _ = _run_with_official_sequence(seq)
    assert snap["session"]["common_complete_date"] == "2026-10-07", snap["session"]
    assert {x["price_date"] for x in snap["stocks"]} == {"2026-10-07"}
    off = snap["coverage"]["official_eod"]
    assert off["model_date_aligned"] is False


def test_horizon_guardrails_are_not_one_size_fits_all():
    stock = {
        "avg_turnover_20d": 6_000_000,
        "ranking_evidence": {"financials": True, "revenue": True, "valuation": True},
        "horizons": {
            h: {"forecast": {"estimate_available": True}, "score_components": {"evidence_coverage": 0.90}, "ranking_score": 90}
            for h in ("short", "mid", "long")
        },
    }
    assert rs._selection_quality(stock, "short")[0] is False  # short needs more liquidity
    assert rs._selection_quality(stock, "mid")[0] is True
    assert rs._selection_quality(stock, "long")[0] is True
    stock["ranking_evidence"]["financials"] = False
    assert rs._selection_quality(stock, "long")[0] is False


def test_global_summary_is_plain_language():
    g = {
        "available": True, "shock_score": 60, "mode": "PANIC",
        "returns_pct": {"sox": -3.8, "ndx": -1.7, "tsm_adr": -3.5, "vix": 5.2, "nq_fut": -1.6},
        "components": {
            "sox": {"risk": 95}, "ndx": {"risk": 49}, "tsm_adr": {"risk": 78}, "vix": {"risk": 21}, "nq_fut": {"risk": 64},
        },
    }
    s = human_global_summary(g)
    names = [x["name"] for x in s["factors"]]
    assert "美國半導體" in names
    assert "那斯達克100" in names
    assert "台積電ADR" in names
    assert "恐慌" in s["headline"] or "壓力" in s["headline"]
    assert _headline_bias("Fed rate cut lifts semiconductor stocks") == "可能偏多"
    assert _headline_bias("Chip stocks plunge on new export controls") == "可能偏空"


def test_app_has_waiting_reading_panel_and_clear_coverage_labels():
    src = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
    assert "更新等待閱讀區" in src
    assert "普通股母體" in src
    assert "可投資模型" in src
    assert "同步完整基準" in src
    assert '_gf("SOX"' not in src


if __name__ == "__main__":
    tests = [
        test_retry_repairs_twse_one_day_lag,
        test_failed_repair_rolls_everything_to_common_date,
        test_horizon_guardrails_are_not_one_size_fits_all,
        test_global_summary_is_plain_language,
        test_app_has_waiting_reading_panel_and_clear_coverage_labels,
    ]
    for t in tests:
        t(); print("PASS", t.__name__)
    print("ALL V17.1 SYNC/BRIEFING TESTS PASSED")
