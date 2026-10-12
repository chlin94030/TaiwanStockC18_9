from __future__ import annotations

import datetime
import copy
import pandas as pd

from intraday_engine import FinMindRealtimeClient, RealtimeStatus, rerank_snapshot
from doctor_engine import buy_decision, holding_decision
from strategy_config import DOCTOR_VERSION

TZ = datetime.timezone(datetime.timedelta(hours=8))


def _stock_fixture(price=13100.0):
    plan = {
        "reference_close": 13100.0,
        "trigger": 14271.0,
        "zone_low": 12838.0,
        "zone_high": 13231.0,
        "chase_limit": 13493.0,
        "invalidation": 11853.57,
        "breakout_level": 14271.0,
        "atr14": 650.0,
        "ma5": 12700.0,
        "ma20": 11900.0,
    }
    forecast = {
        "estimate_available": True,
        "confidence_score": 72.0,
        "local_effective_n": 80,
        "strategy": {"median": 0.06, "mean": 0.07, "p10": -0.08, "smoothed_positive_rate": 0.58},
    }
    block = {
        "ranking_score": 47.4,
        "plan": plan,
        "entry_timing": {"score": 65.0, "action": "等待突破確認", "chase_risk": "低"},
        "position_guidance": {"percent": 55},
        "forecast": forecast,
    }
    return {
        "ticker": "2059.TW",
        "name": "川湖",
        "price": price,
        "price_date": "2026-10-06",
        "avg_volume_20d": 700000.0,
        "setup": {"ma5": 12700.0, "ma20": 11900.0, "ma60": 10500.0, "ma120": 8500.0, "ma240": 6000.0, "atr14": 650.0},
        "horizons": {"short": copy.deepcopy(block), "mid": copy.deepcopy(block), "long": copy.deepcopy(block)},
    }


def test_mis_never_substitutes_previous_close_as_live_price():
    c = FinMindRealtimeClient(token="")
    raw = {"ch": "tse_2059.tw", "c": "2059", "y": "13100", "z": "-", "pz": "-", "b": "-", "a": "-", "d": "20261007", "t": "13:17:00"}
    row = c._mis_convert(raw)
    assert row is not None
    assert row["previous_close"] == 13100.0
    assert row["close"] is None, row


def test_verified_snapshot_prefers_official_mis_over_provider_close():
    c = FinMindRealtimeClient(token="dummy")
    mis = pd.DataFrame([{
        "stock_id": "2059", "date": "2026-10-07 13:17:00", "close": 11790.0,
        "open": 11790.0, "high": 11790.0, "low": 11790.0, "change_rate": -10.0,
        "total_volume": 169000.0, "source": "TWSE/TPEx MIS",
    }])
    fm = pd.DataFrame([{
        "stock_id": "2059", "date": "2026-10-07 13:17:00", "close": 13100.0,
        "open": 13100.0, "high": 13100.0, "low": 13100.0, "change_rate": 0.0,
    }])
    c._mis_snapshots = lambda tickers, batch_size=45: (mis, RealtimeStatus(True, quote_date="2026-10-07", source="TWSE/TPEx MIS", verified=True, verification="official_mis"))
    c._finmind_snapshots = lambda tickers, batch_size=45: (fm, RealtimeStatus(True, quote_date="2026-10-07", source="FinMind realtime snapshot"))
    df, status = c.verified_snapshots(["2059.TW"])
    assert status.verified is True
    assert float(df.iloc[0]["close"]) == 11790.0


def test_rerank_refuses_missing_live_price_instead_of_using_yesterday_close():
    s = _stock_fixture()
    snap = {"stocks": [s], "market": {"regime": "NEUTRAL"}, "snapshot_id": "x"}
    q = pd.DataFrame([{
        "stock_id": "2059", "date": "2026-10-07 13:17:00", "close": None,
        "open": 11790.0, "high": 11790.0, "low": 11790.0, "change_rate": -10.0,
        "total_volume": 169000.0, "source": "TWSE/TPEx MIS",
    }])
    out = rerank_snapshot(snap, "short", q, top_n=1, now=datetime.datetime(2026,10,7,13,17,tzinfo=TZ))
    assert out == []


def test_kawahu_limit_down_uses_11790_and_blocks_buy():
    s = _stock_fixture()
    q = pd.DataFrame([{
        "stock_id": "2059", "date": "2026-10-07 13:17:00", "close": 11790.0,
        "previous_close": 13100.0, "limit_down": 11790.0, "limit_up": 14410.0,
        "open": 11790.0, "high": 11790.0, "low": 11790.0, "change_rate": -10.0,
        "total_volume": 169000.0, "source": "TWSE/TPEx MIS",
    }])
    snap = {"stocks": [s], "market": {"regime": "NEUTRAL"}, "snapshot_id": "x"}
    out = rerank_snapshot(snap, "short", q, top_n=1, now=datetime.datetime(2026,10,7,13,17,tzinfo=TZ))
    assert len(out) == 1
    live = out[0]
    assert live["intraday"]["price"] == 11790.0
    d = buy_decision(live, "short", "INTRADAY")
    assert d["price"] == 11790.0
    assert d["action_code"] not in {"BUY", "SMALL"}
    assert "TWSE/TPEx MIS" in d["price_source"]


def test_kawahu_limit_down_breaches_model_stop_for_existing_holder():
    s = _stock_fixture()
    s["intraday"] = {"price": 11790.0, "quote_source": "TWSE/TPEx MIS"}
    d = holding_decision(s, "short", "INTRADAY", cost=13000.0, thesis_state="UNKNOWN")
    assert d["price"] == 11790.0
    assert d["action_code"] == "SELL"
    assert "TWSE/TPEx MIS" in d["price_source"]


if __name__ == "__main__":
    tests = [v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("ALL VERIFIED-PRICE TESTS PASSED |", DOCTOR_VERSION)
