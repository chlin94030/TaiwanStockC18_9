"""Regression test for on-demand revenue / EPS / institutional detail rendering."""
from __future__ import annotations
import importlib
import sys
import types

class _Ctx:
    def __enter__(self): return self
    def __exit__(self, *args): return False

calls = {"toggle_keys": [], "dataframes": 0, "plots": 0, "warnings": 0}
st = types.ModuleType("streamlit")
st.session_state = {}
st.secrets = {}
st.sidebar = _Ctx()

def _decorator(*args, **kwargs):
    if args and callable(args[0]) and len(args) == 1 and not kwargs:
        return args[0]
    return lambda fn: fn
st.cache_data = _decorator
st.fragment = _decorator
st.expander = lambda *a, **k: _Ctx()
st.markdown = lambda *a, **k: None
st.caption = lambda *a, **k: None
st.info = lambda *a, **k: None
st.error = lambda *a, **k: None
st.success = lambda *a, **k: None

def warning(*a, **k): calls["warnings"] += 1
st.warning = warning

def dataframe(*a, **k): calls["dataframes"] += 1
st.dataframe = dataframe

def plotly_chart(*a, **k): calls["plots"] += 1
st.plotly_chart = plotly_chart

def toggle(*a, value=False, key=None, **k):
    calls["toggle_keys"].append(key)
    return True
st.toggle = toggle
sys.modules["streamlit"] = st
app = importlib.import_module("app")

fresh = {
    "monthly_revenue": {
        "available": True,
        "latest": {"month":"2026-08","revenue_billion":123.4,"mom_pct":2.1,"yoy_pct":11.2},
        "rows": [
            {"month":"2026-07","revenue_billion":120.0,"mom_pct":1.0,"yoy_pct":10.0},
            {"month":"2026-08","revenue_billion":123.4,"mom_pct":2.1,"yoy_pct":11.2},
        ],
    },
    "financials": {
        "available": True,
        "quarters": [
            {"quarter":"2025Q4","eps":2.0,"eps_ytd_sum_unadjusted":8.0,"gross_margin_pct":35.0},
            {"quarter":"2026Q1","eps":2.2,"eps_ytd_sum_unadjusted":2.2,"gross_margin_pct":36.0},
        ],
        "history_quarters": [
            {"quarter":"2025Q4","eps":2.0,"eps_ytd_sum_unadjusted":8.0,"gross_margin_pct":35.0},
            {"quarter":"2026Q1","eps":2.2,"eps_ytd_sum_unadjusted":2.2,"gross_margin_pct":36.0},
        ],
        "eps_4q_sum": 8.4,
        "gross_margin_latest_pct": 36.0,
    },
    "valuation": {"available": True, "pe": 20.0, "pbr": 3.0, "dividend_yield_pct": 2.0, "date":"2026-10-05"},
    "institutional_flow": {
        "available": True,
        "foreign_net_lots": 1000,
        "trust_net_lots": 200,
        "dealer_net_lots": -100,
        "total_net_lots": 1100,
        "daily_rows": [
            {"date":"2026-10-02","foreign_net_lots":100,"trust_net_lots":20,"dealer_net_lots":-10,"total_net_lots":110},
            {"date":"2026-10-05","foreign_net_lots":120,"trust_net_lots":30,"dealer_net_lots":-5,"total_net_lots":145},
        ],
    },
    "main_force_proxy": {"available": False},
    "source": "synthetic",
}
app.service.research_on_demand = lambda ticker, data_dir, include_branch=False: fresh
stock = {"ticker":"2330.TW","name":"台積電","price_date":"2026-10-05","research":{}}
app._render_research(stock, "short_1_snapA")
app._render_research(stock, "mid_1_snapA")
assert calls["dataframes"] >= 6, calls
assert calls["plots"] >= 4, calls
assert len(calls["toggle_keys"]) == 2 and len(set(calls["toggle_keys"])) == 2, calls

# Provider failure must degrade to a message, not crash the whole app.
st.session_state["alpha_research_detail_cache"] = {}
app.service.research_on_demand = lambda ticker, data_dir, include_branch=False: {
    "error":"provider unavailable",
    "monthly_revenue":{"available":False,"rows":[]},
    "financials":{"available":False,"quarters":[]},
    "valuation":{"available":False},
    "institutional_flow":{"available":False},
    "main_force_proxy":{"available":False},
    "source":"research unavailable",
}
app._render_research({"ticker":"9999.TW","price_date":"2026-10-05","research":{}}, "live_1_snapB")
assert calls["warnings"] >= 2, calls
print("Alpha Radar V16.6.1 research detail regression: PASS")
