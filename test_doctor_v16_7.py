from __future__ import annotations

from pathlib import Path
import copy

from doctor_engine import buy_decision, holding_decision
from strategy_config import DOCTOR_VERSION


def stock_fixture(price=100.0):
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
        "strategy": {
            "median": 0.10,
            "mean": 0.11,
            "p10": -0.05,
            "smoothed_positive_rate": 0.64,
        },
    }
    block = {
        "ranking_score": 82.0,
        "plan": plan,
        "entry_timing": {"score": 78.0, "action": "可分批", "chase_risk": "低"},
        "position_guidance": {"percent": 70},
        "forecast": forecast,
    }
    return {
        "ticker": "9999.TW",
        "name": "測試股",
        "price": price,
        "price_date": "2026-10-06",
        "setup": {"ma5": 99.0, "ma20": 96.0, "ma60": 90.0, "ma120": 82.0, "ma240": 75.0, "atr14": 3.0},
        "horizons": {"short": copy.deepcopy(block), "mid": copy.deepcopy(block), "long": copy.deepcopy(block)},
    }


def test_completed_day_buy_is_not_claimed_as_instant_fill():
    s = stock_fixture()
    d = buy_decision(s, "short", "POSTCLOSE")
    assert d["mode"] == "COMPLETED_DAY"
    assert d["eligible_now"] is False
    assert d["action_code"] in {"READY", "WAIT"}


def test_intraday_missing_quote_never_claims_buy():
    s = stock_fixture()
    d = buy_decision(s, "short", "INTRADAY")
    assert d["action_code"] == "DATA"
    assert d["eligible_now"] is False


def test_holding_is_separate_from_new_entry_timing():
    s = stock_fixture(price=106.0)
    # A deliberately poor *new entry* should not force an existing good holding out.
    s["horizons"]["short"]["entry_timing"] = {"score": 20.0, "action": "不追", "chase_risk": "高"}
    d = holding_decision(s, "short", "POSTCLOSE", cost=90.0, thesis_state="INTACT")
    assert d["action_code"] in {"HOLD", "HOLD_STRONG", "PROTECT"}
    assert d["pnl_pct"] is not None and d["pnl_pct"] > 0


def test_breached_model_stop_is_sell():
    s = stock_fixture(price=92.0)
    d = holding_decision(s, "short", "POSTCLOSE", cost=100.0, thesis_state="UNKNOWN")
    assert d["action_code"] == "SELL"
    assert any("模型結構防守價" in x for x in d["hard_exit_reasons"])


def test_user_stop_and_broken_thesis_are_hard_overrides():
    s = stock_fixture(price=101.0)
    d1 = holding_decision(s, "mid", "POSTCLOSE", cost=95.0, original_stop=102.0, thesis_state="INTACT")
    assert d1["action_code"] == "SELL"
    d2 = holding_decision(s, "mid", "POSTCLOSE", cost=95.0, thesis_state="BROKEN")
    assert d2["action_code"] == "SELL"


def test_profit_protection_only_after_profit_activation():
    s = stock_fixture(price=112.0)
    d = holding_decision(s, "short", "POSTCLOSE", cost=95.0, thesis_state="INTACT")
    assert d["profit_protection"] is not None
    assert d["profit_protection"] < d["price"]


def test_doctor_ui_uses_blank_optional_fields_not_three_zero_inputs():
    src = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
    assert '持股成本（元／股，建議填寫）' in src
    assert '你原本的停損／結構失效價（選填）' in src
    assert '你原本的獲利保護價（選填）' in src
    assert 'st.number_input("成本"' not in src
    assert 'value="", placeholder="例如 980' in src


def test_direct_diagnosis_has_completed_day_cutoff_guard():
    src = Path(__file__).with_name("radar_service.py").read_text(encoding="utf-8")
    assert 'snapshot_cutoff = str((snap or {}).get("price_date")' in src
    assert 'trial = trial[trial.index <= pd.Timestamp(cutoff)]' in src


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("ALL DOCTOR TESTS PASSED |", DOCTOR_VERSION)
