from __future__ import annotations
from pathlib import Path
import tempfile

import radar_service as rs
from market_data import ResearchDataClient
from strategy_config import ARCHITECTURE_VERSION, CANONICAL_MODEL_POLICY, SELECTION_WEIGHTS


def _forecast(tech=70.0, empirical=70.0):
    return {
        "technical_factor_score": tech,
        "estimate_available": True,
        "empirical_quality_score": empirical,
        "confidence_score": 80.0,
        "local_effective_n": 80,
        "strategy": {"median": 0.08, "mean": 0.09, "p10": -0.04, "smoothed_positive_rate": 0.62},
    }


def _stock(research=None, ranking_research=None):
    return {
        "ticker": "9999.TW", "avg_volume_20d": 1_000_000,
        "research": research or {}, "ranking_research": ranking_research or {},
        "horizons": {h: {"forecast": _forecast()} for h in ("short","mid","long")},
    }


def test_canonical_evaluates_all_liquid():
    liquid=[{"ticker":f"{1000+i}.TW"} for i in range(120)]
    canonical=rs.RunSettings(canonical_mode=True,candidate_size=50)
    research=rs.RunSettings(canonical_mode=False,candidate_size=50)
    assert len(rs.model_candidates(liquid,canonical)) == 120
    assert len(rs.model_candidates(liquid,research)) == 50


def test_ranking_uses_same_evidence_scale():
    neutral_research={
        "monthly_revenue":{"available":True,"latest":{"yoy_pct":0,"mom_pct":0}},
        "financials":{"available":True,"latest_ytd_eps":1.0,"quarters":[]},
        "valuation":{"available":True,"pe":20},
        "institutional_flow":{"available":True,"sessions":1,"total_net_lots":0},
    }
    missing=_stock(ranking_research={})
    neutral=_stock(ranking_research=neutral_research)
    for h in ("short","mid","long"):
        rs._combined_ranking_score(missing,h,"full")
        rs._combined_ranking_score(neutral,h,"full")
        # Missing evidence may receive the bounded coverage penalty, but can no
        # longer be boosted by re-normalizing only technical/empirical weights.
        assert missing["horizons"][h]["score_components"]["evidence_coverage"] < 1.0
        assert missing["horizons"][h]["ranking_score"] <= neutral["horizons"][h]["ranking_score"] + 4.1


def test_display_detail_cannot_change_ranking():
    breadth={"monthly_revenue":{"available":True,"latest":{"yoy_pct":5,"mom_pct":1}},"financials":{"available":False},"valuation":{"available":False},"institutional_flow":{"available":False}}
    a=_stock(research={"monthly_revenue":{"available":True,"latest":{"yoy_pct":999,"mom_pct":999}}}, ranking_research=breadth)
    b=_stock(research={}, ranking_research=breadth)
    for h in ("short","mid","long"):
        rs._combined_ranking_score(a,h,"full"); rs._combined_ranking_score(b,h,"full")
        assert a["horizons"][h]["ranking_score"] == b["horizons"][h]["ranking_score"]


def test_official_breadth_snapshot_parses_once_for_otc():
    with tempfile.TemporaryDirectory() as td:
        c=ResearchDataClient(Path(td)/"research.sqlite", token="")
        def fake(key,url,ttl_hours=8):
            if key == "official:monthly_revenue:O":
                return [{"公司代號":"9999","資料年月":"11508","營業收入-當月營收":"500000","營業收入-上月比較增減(%)":"10","營業收入-去年同月增減(%)":"20"}]
            if key == "official:income:O:ci":
                return [{"公司代號":"9999","年度":"115","季別":"2","基本每股盈餘(元)":"5.2","營業收入":"1000","營業毛利(毛損)":"300"}]
            if key == "official:valuation:O":
                return [{"SecuritiesCompanyCode":"9999","PriceEarningRatio":"18","PriceBookRatio":"2.5","DividendYield":"2"}]
            if key == "official:tpex:3insti_daily":
                return [{"SecuritiesCompanyCode":"9999","Date":"1151008","Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference":"100000","SecuritiesInvestmentTrustCompanies-Difference":"20000","Dealers-Difference":"-10000","TotalDifference":"110000"}]
            return []
        c._public_json_snapshot=fake
        out=c.official_breadth_snapshot(["9999.TWO"],asof_date="2026-10-08")["9999.TWO"]
        assert out["breadth_comparable"] is True
        assert out["monthly_revenue"]["available"] is True
        assert out["financials"]["latest_ytd_eps"] == 5.2
        assert out["valuation"]["pe"] == 18.0
        assert out["institutional_flow"]["total_net_lots"] == 110.0


def test_canonical_ui_locked():
    src=Path(__file__).with_name("app.py").read_text(encoding="utf-8")
    assert 'selectbox("比較股票數"' not in src
    assert 'selectbox("歷史長度"' not in src
    assert 'canonical_mode=True' in src
    assert 'V18 標準模型' in src


def test_version_and_weights():
    assert ARCHITECTURE_VERSION.startswith("v18.")  # V17 core weights frozen, release version advanced
    assert CANONICAL_MODEL_POLICY["evaluate_all_liquid"] is True
    assert SELECTION_WEIGHTS == {
        "short":{"technical":0.52,"empirical":0.20,"fundamental":0.08,"flow":0.20},
        "mid":{"technical":0.42,"empirical":0.22,"fundamental":0.22,"flow":0.14},
        "long":{"technical":0.34,"empirical":0.22,"fundamental":0.34,"flow":0.10},
    }


if __name__ == "__main__":
    tests=[v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t(); print("PASS",t.__name__)
    print("ALL V17 GOAL-COMPLETE TESTS PASSED")
