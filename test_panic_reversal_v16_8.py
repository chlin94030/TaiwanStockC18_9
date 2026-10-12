from __future__ import annotations
import copy
import datetime as dt
import pandas as pd

from global_context import compute_global_context
from recommendation_engine import evaluate_buy_opportunity
from intraday_engine import quick_rank_market


def base_stock():
    forecast = {
        "estimate_available": True,
        "confidence_score": 80.0,
        "local_effective_n": 80,
        "strategy": {
            "median": 0.12,
            "mean": 0.13,
            "p10": -0.05,
            "smoothed_positive_rate": 0.62,
        },
    }
    plan = {
        "reference_close": 100.0,
        "breakout_level": 100.0,
        "trigger": 100.0,
        "zone_low": 98.0,
        "zone_high": 101.0,
        "chase_limit": 104.0,
        "atr14": 4.0,
        "ma5": 99.0,
        "invalidation": 90.0,
    }
    block = {
        "ranking_score": 80.0,
        "plan": plan,
        "entry_timing": {"score": 75.0, "action": "可小量試單", "chase_risk": "低"},
        "position_guidance": {"percent": 70},
        "forecast": forecast,
    }
    return {
        "ticker": "9999.TW",
        "name": "測試股",
        "price": 100.0,
        "price_date": "2026-10-07",
        "live_ranking_score": 47.0,
        "horizons": {"short": copy.deepcopy(block), "mid": copy.deepcopy(block), "long": copy.deepcopy(block)},
        "intraday": {
            "price": 96.0,
            "change_rate_pct": -4.0,
            "market_change_rate_pct": -5.0,
            "relative_market_pct_pt": 1.0,
            "rebound_from_low_pct": 2.0,
            "day_position": 0.55,
            "vwap_gap_pct": -0.1,
            "volume_ratio": 1.5,
            "spread_pct": 0.2,
            "limit_down": 90.0,
            "intraday_score": 48.0,
            "entry_timing": {
                "score": 50.0,
                "action": "暫不進場",
                "chase_risk": "中",
                "extension_atr": -1.0,
                "reasons": ["尚未回到突破區"],
            },
            "position_guidance": {"percent": 0},
        },
    }


def crash_context():
    return compute_global_context({
        "sox": -5.0,
        "ndx": -4.0,
        "tsm_adr": -6.0,
        "spx": -3.2,
        "vix": 32.0,
        "nq_fut": -1.8,
        "es_fut": -1.2,
        "tnx": 2.0,
        "dxy": 0.8,
    }, asof="2026-10-07", source="test")


def normal_context():
    return compute_global_context({
        "sox": 0.4,
        "ndx": 0.3,
        "tsm_adr": 0.5,
        "spx": 0.2,
        "vix": -4.0,
        "nq_fut": 0.2,
        "es_fut": 0.1,
        "tnx": -0.3,
        "dxy": 0.1,
    }, asof="2026-10-07", source="test")


def test_global_context():
    c = crash_context()
    assert c["available"] and c["panic_mode"] and c["shock_score"] >= 58, c
    n = normal_context()
    assert n["available"] and not n["panic_mode"] and n["shock_score"] < 40, n
    print("PASS global context distinguishes crash vs normal")


def test_panic_reversal_passes_only_after_recovery():
    stock = base_stock()
    d = evaluate_buy_opportunity(stock, "short", live=True, global_context=crash_context())
    assert d["eligible_now"] is True, d
    assert d["state"] == "PANIC_REVERSAL", d
    assert d["entry_path"] == "PANIC_REVERSAL", d
    assert 0 < d["position_percent"] <= 20, d
    assert d["panic_reversal"]["resilience_pct_pt"] >= 0.6, d

    falling = base_stock()
    falling["intraday"]["rebound_from_low_pct"] = 0.2
    falling["intraday"]["day_position"] = 0.15
    d2 = evaluate_buy_opportunity(falling, "short", live=True, global_context=crash_context())
    assert d2["eligible_now"] is False, d2
    assert "panic_rebound" in d2["failed_gate_codes"], d2
    print("PASS panic reversal requires rebound; falling knife rejected")


def test_no_panic_buy_below_structure_or_without_context():
    broken = base_stock()
    broken["intraday"]["price"] = 89.0
    d = evaluate_buy_opportunity(broken, "short", live=True, global_context=crash_context())
    assert d["eligible_now"] is False, d
    assert "panic_structure" in d["failed_gate_codes"], d

    quiet = base_stock()
    quiet["intraday"]["market_change_rate_pct"] = -0.4
    quiet["intraday"]["relative_market_pct_pt"] = 1.0
    d2 = evaluate_buy_opportunity(quiet, "short", live=True, global_context=normal_context())
    assert d2["state"] != "PANIC_REVERSAL", d2
    print("PASS no panic buy below stop or outside broad-shock context")


def test_global_shock_reduces_ordinary_position():
    stock = base_stock()
    stock["horizons"]["short"]["plan"]["invalidation"] = 94.0
    stock["intraday"].update({
        "price": 100.2,
        "change_rate_pct": 0.2,
        "market_change_rate_pct": -0.2,
        "relative_market_pct_pt": 0.4,
        "rebound_from_low_pct": 1.0,
        "day_position": 0.6,
        "entry_timing": {"score": 85.0, "action": "買點佳，可分批", "chase_risk": "低", "extension_atr": 0.05},
        "position_guidance": {"percent": 100},
        "intraday_score": 75.0,
    })
    stock["live_ranking_score"] = 82.0
    n = evaluate_buy_opportunity(stock, "short", live=True, global_context=normal_context())
    c = evaluate_buy_opportunity(stock, "short", live=True, global_context=crash_context())
    assert n["eligible_now"] is True, n
    assert c["eligible_now"] is True, c
    assert c["position_percent"] < n["position_percent"], (n, c)
    # Ranking/selection input itself is untouched.
    assert stock["horizons"]["short"]["ranking_score"] == 80.0
    print("PASS global shock changes risk budget, not stock selection score")


def test_panic_discovery_surfaces_resilient_decliner():
    snap = {
        "intraday_universe": [{"ticker":"9999.TW","name":"測試股","industry":"電子","avg_volume_20d":1_000_000,"pre_score":55}],
        "stocks": [],
    }
    now = dt.datetime(2026,10,8,10,0,tzinfo=dt.timezone(dt.timedelta(hours=8)))
    df = pd.DataFrame([
        {"stock_id":"001","close":19000,"open":19500,"high":19520,"low":18800,"average_price":19100,"change_rate":-5.0,"total_volume":1,"date":"2026-10-08 10:00:00"},
        {"stock_id":"9999","close":96.0,"open":94.0,"high":97.0,"low":93.5,"average_price":95.8,"change_rate":-4.0,"total_volume":900000,"buy_price":95.9,"sell_price":96.1,"buy_volume":100,"sell_volume":80,"date":"2026-10-08 10:00:00"},
    ])
    rows = quick_rank_market(snap, df, now=now, top_n=5, global_context=crash_context())
    assert rows and rows[0]["ticker"] == "9999.TW", rows
    assert rows[0]["panic_discovery_score"] is not None, rows[0]
    assert rows[0]["intraday"]["rebound_from_low_pct"] > 1.0, rows[0]
    print("PASS full-market discovery computes panic-resilience candidate")


if __name__ == "__main__":
    test_global_context()
    test_panic_reversal_passes_only_after_recovery()
    test_no_panic_buy_below_structure_or_without_context()
    test_global_shock_reduces_ordinary_position()
    test_panic_discovery_surfaces_resilient_decliner()
    print("ALL PANIC REVERSAL TESTS PASSED | V16.8")
