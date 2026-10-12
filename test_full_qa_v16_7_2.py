from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
import types

import pandas as pd

import intraday_engine
import radar_service
from doctor_engine import buy_decision, holding_decision
from intraday_engine import FinMindRealtimeClient, RealtimeStatus, quick_rank_market
from recommendation_engine import evaluate_buy_opportunity, evaluate_next_session
from strategy_config import DOCTOR_VERSION

TZ = dt.timezone(dt.timedelta(hours=8))


def stock_fixture(price=100.0, ticker="9999.TW"):
    plan = {
        "reference_close": 100.0,
        "trigger": 100.5,
        "zone_low": 98.0,
        "zone_high": 101.0,
        "chase_limit": 103.0,
        "invalidation": 94.0,
        "breakout_level": 100.0,
        "atr14": 3.0,
        "ma5": 99.0,
        "ma20": 96.0,
    }
    forecast = {
        "estimate_available": True,
        "confidence_score": 75.0,
        "local_effective_n": 80,
        "strategy": {"median": 0.10, "mean": 0.11, "p10": -0.05, "smoothed_positive_rate": 0.64},
    }
    block = {
        "ranking_score": 82.0,
        "plan": plan,
        "entry_timing": {"score": 78.0, "action": "可分批", "chase_risk": "低"},
        "position_guidance": {"percent": 70},
        "forecast": forecast,
    }
    return {
        "ticker": ticker,
        "name": "測試股",
        "price": price,
        "price_date": "2026-10-06",
        "avg_volume_20d": 2_000_000.0,
        "setup": {"ma5": 99.0, "ma20": 96.0, "ma60": 90.0, "ma120": 82.0, "ma240": 75.0, "atr14": 3.0},
        "horizons": {"short": copy.deepcopy(block), "mid": copy.deepcopy(block), "long": copy.deepcopy(block)},
    }


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200
    def raise_for_status(self):
        return None
    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.headers = {}
    def get(self, *args, **kwargs):
        return FakeResponse(self.payload)


def test_mis_batch_requires_executable_requested_price():
    c = FinMindRealtimeClient(token="")
    c._mis_bootstrapped = True
    c.session = FakeSession({"rtcode":"0000","msgArray":[
        {"ch":"tse_2059.tw","c":"2059","y":"13100","z":"-","d":"20261007","t":"13:17:00"},
        {"ch":"tse_t00.tw","c":"t00","y":"27000","z":"27100","d":"20261007","t":"13:17:00"},
    ]})
    old_now = intraday_engine._taipei_now
    intraday_engine._taipei_now = lambda: dt.datetime(2026,10,7,13,17,tzinfo=TZ)
    try:
        df, status = c._mis_snapshots(["2059.TW"])
    finally:
        intraday_engine._taipei_now = old_now
    assert df.empty
    assert not status.available and not status.verified
    assert status.reason == "MIS_NO_EXECUTABLE_PRICE"
    assert status.requested_count == 1 and status.executable_count == 0


def test_mis_partial_batch_keeps_only_valid_stock_prices_plus_index():
    c = FinMindRealtimeClient(token="")
    c._mis_bootstrapped = True
    c.session = FakeSession({"rtcode":"0000","msgArray":[
        {"ch":"tse_2059.tw","c":"2059","y":"13100","z":"-","d":"20261007","t":"13:17:00"},
        {"ch":"tse_2330.tw","c":"2330","y":"1400","z":"1410","d":"20261007","t":"13:17:00"},
        {"ch":"tse_t00.tw","c":"t00","y":"27000","z":"27100","d":"20261007","t":"13:17:00"},
    ]})
    old_now = intraday_engine._taipei_now
    intraday_engine._taipei_now = lambda: dt.datetime(2026,10,7,13,17,tzinfo=TZ)
    try:
        df, status = c._mis_snapshots(["2059.TW","2330.TW"])
    finally:
        intraday_engine._taipei_now = old_now
    ids = set(df["stock_id"].astype(str))
    assert "2059" not in ids and "2330" in ids and "001" in ids
    assert status.available and status.verified
    assert status.requested_count == 2 and status.executable_count == 1
    assert status.reason == "MIS_PARTIAL_1_OF_2"


def test_buy_engine_live_call_cannot_fallback_to_completed_close():
    s = stock_fixture(100.0)
    d = evaluate_buy_opportunity(s, "short", live=True)
    assert d["state"] == "DATA"
    assert d["eligible_now"] is False
    assert d["price"] is None
    assert "live_price" in d["failed_gate_codes"]


def test_doctor_buy_intraday_missing_price_is_data_only():
    s = stock_fixture(100.0)
    d = buy_decision(s, "short", "INTRADAY")
    assert d["action_code"] == "DATA"
    assert d["eligible_now"] is False
    assert d["price"] == 100.0
    assert "非即時" in d["price_source"]


def test_doctor_hold_intraday_missing_price_is_data_not_stale_hold_or_sell():
    s = stock_fixture(90.0)
    d = holding_decision(s, "short", "INTRADAY", cost=100.0)
    assert d["action_code"] == "DATA"
    assert d["price"] is None
    assert d["reference_price"] == 90.0
    assert d["model_stop"] == 94.0
    assert any("禁止用昨收替代" in x for x in d["reasons"])


def test_completed_day_next_session_explicitly_carries_reference_price():
    s = stock_fixture(100.0)
    d = evaluate_next_session(s, "short")
    assert d["price"] == 100.0


def test_quick_market_discovery_skips_rows_without_latest_trade():
    snap = {
        "intraday_universe": [
            {"ticker":"2059.TW","name":"川湖","avg_volume_20d":100000,"pre_score":90},
            {"ticker":"2330.TW","name":"台積電","avg_volume_20d":100000,"pre_score":50},
        ]
    }
    q = pd.DataFrame([
        {"stock_id":"001","close":27100,"change_rate":1.0,"date":"2026-10-07 13:17:00"},
        {"stock_id":"2059","close":None,"previous_close":13100,"change_rate":0.0,"date":"2026-10-07 13:17:00"},
        {"stock_id":"2330","close":1410,"previous_close":1400,"change_rate":0.7,"date":"2026-10-07 13:17:00","open":1400,"high":1420,"low":1390,"total_volume":100000},
    ])
    out = quick_rank_market(snap, q, now=dt.datetime(2026,10,7,13,17,tzinfo=TZ), top_n=10)
    assert [x["ticker"] for x in out] == ["2330.TW"]


def test_diagnose_with_live_clears_batch_verified_when_specific_ticker_has_no_price():
    s = stock_fixture(100.0, ticker="2059.TW")
    original_diagnose = radar_service.diagnose
    original_client = intraday_engine.FinMindRealtimeClient
    original_phase = radar_service.session_reference_policy
    class FakeClient:
        def verified_snapshots(self, tickers):
            df = pd.DataFrame([{"stock_id":"2059","close":None,"date":"2026-10-07 13:17:00","source":"TWSE/TPEx MIS"}])
            return df, RealtimeStatus(True, source="TWSE/TPEx MIS", verified=True, verification="official_mis", quote_date="2026-10-07")
    radar_service.diagnose = lambda code, snap, data_dir: {"snapshot_id":"x","stock":copy.deepcopy(s),"analysis_date":"2026-10-06","source":"test"}
    intraday_engine.FinMindRealtimeClient = FakeClient
    radar_service.session_reference_policy = lambda now=None: {"phase":"INTRADAY","label":"盤中","today":"2026-10-07"}
    try:
        dr = radar_service.diagnose_with_live("2059", {"market":{"regime":"NEUTRAL"}}, Path("/tmp"), horizon="short", now=dt.datetime(2026,10,7,13,17,tzinfo=TZ))
    finally:
        radar_service.diagnose = original_diagnose
        intraday_engine.FinMindRealtimeClient = original_client
        radar_service.session_reference_policy = original_phase
    assert dr["live_status"]["verified"] is False
    assert dr["live_status"]["available"] is False
    assert dr["live_status"]["reason"] == "NO_EXECUTABLE_PRICE_FOR_TICKER"
    assert "intraday" not in dr["stock"]


def test_ui_has_no_live_price_fallback_to_completed_close_and_invalidates_old_doctor_state():
    src = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
    assert 'price = live_info.get("price") or stock.get("price")' not in src
    assert 'price = finite(live_info.get("price"))' in src
    assert 'alpha_doctor_version' in src and 'DOCTOR_VERSION' in src
    assert '不會用昨收代替目前價格做續抱／減碼／賣出判斷' in src


def test_selection_core_remains_frozen():
    from strategy_config import SELECTION_WEIGHTS, ARCHITECTURE_VERSION
    assert ARCHITECTURE_VERSION.startswith("v18.")  # V17 core weights frozen, release version advanced
    assert SELECTION_WEIGHTS == {
        "short": {"technical": 0.52, "empirical": 0.20, "fundamental": 0.08, "flow": 0.20},
        "mid": {"technical": 0.42, "empirical": 0.22, "fundamental": 0.22, "flow": 0.14},
        "long": {"technical": 0.34, "empirical": 0.22, "fundamental": 0.34, "flow": 0.10},
    }


if __name__ == "__main__":
    tests = [v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("ALL FULL-QA TESTS PASSED |", DOCTOR_VERSION)
