"""V16.6.2 regressions for official research history and period-safe UI data."""
from __future__ import annotations

from pathlib import Path
import tempfile

from market_data import ResearchDataClient

MONTHLY_HTML = """
<table>
<tr><th>公司代號</th><th>公司名稱</th><th>當月營收</th><th>上月營收</th><th>去年當月營收</th><th>上月比較增減(%)</th><th>去年同月增減(%)</th></tr>
<tr><td>6691</td><td>洋基工程</td><td>5287000</td><td>4763400</td><td>1685300</td><td>10.99</td><td>213.70</td></tr>
</table>
"""
row = ResearchDataClient._parse_monthly_revenue_html(MONTHLY_HTML, "6691", 2026, 8)
assert row is not None, row
assert row["month"] == "2026-08"
assert row["revenue_billion"] == 52.87, row
assert row["mom_pct"] == 10.99 and row["yoy_pct"] == 213.7, row

Q1_HTML = """
<table>
<tr><th>公司代號</th><th>公司名稱</th><th>營業收入</th><th>營業毛利（毛損）</th><th>基本每股盈餘（元）</th></tr>
<tr><td>6691</td><td>洋基工程</td><td>5000000</td><td>900000</td><td>8.00</td></tr>
</table>
"""
Q2_HTML = """
<table>
<tr><th>公司代號</th><th>公司名稱</th><th>營業收入</th><th>營業毛利（毛損）</th><th>基本每股盈餘（元）</th></tr>
<tr><td>6691</td><td>洋基工程</td><td>11000000</td><td>2000000</td><td>17.46</td></tr>
</table>
"""
q1 = ResearchDataClient._parse_quarterly_income_html(Q1_HTML, "6691", 2026, 1)
q2 = ResearchDataClient._parse_quarterly_income_html(Q2_HTML, "6691", 2026, 2)
assert q1 and q2
assert q2["ytd_eps"] == 17.46
# Revenue/gross margin can be de-cumulated, but EPS must remain official cumulative because weighted shares can change.
with tempfile.TemporaryDirectory() as td:
    c = ResearchDataClient(Path(td) / "r.sqlite", token="")
    c._official_financial_latest = lambda stock_id, ticker: {
        "available": True, "latest_ytd_eps": 17.46, "latest_period": "2026Q2",
        "gross_margin_latest_pct": 18.24, "history_limited": True,
        "source": "latest",
    }
    synthetic = {
        (2025, 2): {"quarter":"2025Q2", "ytd_eps": 3.0, "ytd_revenue": 200.0, "ytd_gross": 36.0},
        (2025, 3): {"quarter":"2025Q3", "ytd_eps": 5.0, "ytd_revenue": 350.0, "ytd_gross": 66.0},
        (2025, 4): {"quarter":"2025Q4", "ytd_eps": 8.0, "ytd_revenue": 550.0, "ytd_gross": 100.0},
        (2026, 1): q1,
        (2026, 2): q2,
    }
    c._official_quarter_cumulative = lambda stock_id, ticker, y, q: synthetic.get((y, q))
    hist = c._official_financial_history("6691", "6691.TW", periods=4)
    assert hist["eps_ytd_basis"] == "official_cumulative"
    assert len(hist["history_quarters"]) == 4, hist
    assert hist["history_quarters"][-1]["eps"] is None, hist
    assert hist["history_quarters"][-1]["eps_ytd_official"] == 17.46, hist
    assert hist["eps_4q_sum"] is None, hist
    assert round(hist["history_quarters"][-1]["gross_margin_pct"], 2) == 18.33, hist

T86 = {
    "fields": [
        "證券名稱", "投信買賣超股數", "證券代號", "三大法人買賣超股數",
        "自營商買賣超股數", "外陸資買賣超股數(不含外資自營商)",
    ],
    "data": [["洋基工程", "200,000", "6691", "1,100,000", "-100,000", "1,000,000"]],
}
inst = ResearchDataClient._parse_twse_t86_payload(T86, "6691", "2026-10-06")
assert inst == {
    "date": "2026-10-06", "foreign_net_lots": 1000.0, "trust_net_lots": 200.0,
    "dealer_net_lots": -100.0, "total_net_lots": 1100.0,
}, inst

TPEX = [{
    "SecuritiesCompanyCode ": "6691",
    "Date": "1151006",
    "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference": "1000000",
    "SecuritiesInvestmentTrustCompanies-Difference": "200000",
    "Dealers-Difference": "-100000",
    "TotalDifference": "1100000",
}]
inst2 = ResearchDataClient._parse_tpex_institutional_rows(TPEX, "6691")
assert inst2 and inst2["date"] == "2026-10-06" and inst2["total_net_lots"] == 1100.0, inst2

# Detail upgrades are display-only and prefer richer histories.
with tempfile.TemporaryDirectory() as td:
    c = ResearchDataClient(Path(td) / "r.sqlite", token="")
    c.stock_research = lambda ticker, include_branch=False: {
        "stock_id":"6691",
        "monthly_revenue":{"available":True,"rows":[{"month":"2026-08","revenue_billion":52.87}],"history_limited":True,"source":"latest"},
        "financials":{"available":True,"quarters":[],"latest_ytd_eps":17.46,"latest_period":"2026Q2","history_limited":True,"source":"latest"},
        "valuation":{"available":True,"pe":20.0,"source":"valuation"},
        "institutional_flow":{"available":False},
        "main_force_proxy":{"available":False},
        "source":"latest + valuation",
    }
    c._official_monthly_revenue_history = lambda *a, **k: {
        "available":True,"rows":[{"month":f"2026-{m:02d}","revenue_billion":40+m} for m in range(1,9)],
        "latest":{"month":"2026-08","revenue_billion":48},"coverage_count":8,"source":"hist_rev",
    }
    c._official_financial_history = lambda *a, **k: {
        "available":True,"history_quarters":[{"quarter":f"2025Q{q}","eps":q} for q in range(1,5)],
        "quarters":[{"quarter":f"2025Q{q}","eps":q} for q in range(1,5)],"source":"hist_fin",
    }
    c._official_institutional_detail = lambda *a, **k: {
        "available":True,"daily_rows":[{"date":"2026-10-06","total_net_lots":10}],
        "total_net_lots":10,"foreign_net_lots":5,"trust_net_lots":3,"dealer_net_lots":2,"source":"official_inst",
    }
    detail = c.stock_research_detail("6691.TW")
    assert len(detail["monthly_revenue"]["rows"]) == 8
    assert len(detail["financials"]["history_quarters"]) == 4
    assert detail["institutional_flow"]["available"] is True

print("Alpha Radar V16.6.2 official research data regressions: PASS")
