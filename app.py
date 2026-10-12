"""Alpha Radar V18.8 - fixed canonical selection, clear optional timing, compact market notes."""
from __future__ import annotations

from pathlib import Path
from v174_integrity import assess_snapshot
import gc
import html
import os
import time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import radar_service as service
from industry_profile import fine_industry
from intraday_engine import FinMindRealtimeClient, candidate_tickers, full_market_tickers, dynamic_candidate_pool, filter_realtime_df, market_is_open, rerank_snapshot
from market_data import DailyPriceStore, _taipei_timestamp, provider_runtime_status
from policy_engine import HORIZONS
from presentation import investment_view
from trading_calendar import session_reference_policy, INTRADAY, POSTCLOSE
from operational_tools import ScanBusyError, safe_error_text, scan_lock
from recommendation_engine import rank_intraday_buys, summarize_buy_gate_failures, rank_next_session, rank_next_session_live
from strategy_config import LIVE_SCAN, DOCTOR_VERSION, CANONICAL_MODEL_POLICY
from doctor_engine import buy_decision, holding_decision
from global_context import fetch_global_context
from market_briefing import human_global_summary, previous_model_digest, market_tape_insights
from briefing_jobs import ensure_brief, load_brief
from scan_job_manager import start_scan, status as scan_status
import v18_ui
from v188_board import canonical_shortlist, optional_entry_watchlist
from v187_experience import (signal_light, prioritize_next_session, readiness_counts,
                             US_WATCH_GROUPS, taiwan_watch_snapshot, TW_WATCH_GROUPS)

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("ALPHA_RADAR_DATA_DIR", str(ROOT / "data")))

VIEWS = ["今日決策", "選股與買點", "個股診斷"]
H_LABEL = {"short": "短線 · 約2週", "mid": "中線 · 約2個月", "long": "長線 · 約6個月"}
REGIME = {"BULL": "大盤偏多", "NEUTRAL": "大盤整理", "BEAR": "大盤偏弱", "UNKNOWN": "大盤待確認"}
FAMILY_LABELS = {
    "完整資料": "full",
    "價格＋公司資料": "business_confirmed",
    "價格＋法人": "flow_confirmed",
    "只看價格": "price_only",
}
HOLD_LABELS = {
    "DATA_UNVERIFIED": "價格尚未核對。",
    "ORIGINAL_STRUCTURE_INVALIDATED": "已跌破原防守價，需優先重新評估。",
    "ORIGINAL_THESIS_INVALIDATED": "原買進理由已不成立，需重新評估。",
    "PROTECTION_TRIGGER_REVIEW_EXECUTION": "已碰到獲利保護價，請依原紀律處理。",
    "ORIGINAL_THESIS_UNKNOWN_MANUAL_REVIEW": "缺少原始防守條件，無法自動判斷。",
    "ORIGINAL_RULES_NOT_BREACHED_NOT_A_RETURN_GUARANTEE": "原防守條件尚未被破壞。",
}

def _bootstrap_secrets():
    """Expose Streamlit secrets to the data layer without hard-coding tokens."""
    for key in ("FINMIND_TOKEN",):
        if os.getenv(key):
            continue
        try:
            value = st.secrets.get(key)
        except Exception:
            value = None
        if value not in (None, ""):
            os.environ[key] = str(value)


@st.cache_data(ttl=int(LIVE_SCAN.get("cache_ttl_seconds", 52)), show_spinner=False)
def _cached_realtime_snapshot(snapshot_id: str, tickers: tuple[str, ...]):
    """Reuse the same complete quote batch while users switch views.

    The one-minute full-market quote batch is reused while the user switches
    views, preventing duplicate provider calls inside the same refresh window.
    """
    client = FinMindRealtimeClient()
    return client.snapshots(list(tickers))


@st.cache_data(ttl=int(LIVE_SCAN.get("cache_ttl_seconds", 52)), show_spinner=False)
def _cached_verified_execution_snapshot(snapshot_id: str, tickers: tuple[str, ...]):
    """Official-verified quote batch for prices that can trigger a trade decision."""
    client = FinMindRealtimeClient()
    return client.verified_snapshots(list(tickers))


@st.cache_data(ttl=35, show_spinner=False)
def _cached_global_context():
    """Instant, non-blocking global risk input, not a new network fetch.

    A disconnected mobile session must not trigger a 20-second Yahoo request.
    The separate briefing worker refreshes this risk cache while the page is
    responsive. If the cached evidence is old, return an unavailable context
    rather than applying unverified panic/reversal logic.
    """
    cached = (load_brief(DATA_DIR).get("global") or {})
    if cached.get("available") and not cached.get("stale"):
        return cached
    ensure_brief(DATA_DIR)
    return {"available": False, "stale": True, "mode": "UNKNOWN", "shock_score": None,
            "position_multiplier": 1.0, "panic_mode": False, "returns_pct": {},
            "reason": "WAIT_FOR_ASYNC_GLOBAL_CACHE"}


CSS = r"""
<style>
:root{
  --paper:#f3f0e7; --surface:#fffdfa; --ink:#0e1116; --muted:#656a74;
  --line:#d1cec4; --blue:#1248c7; --red:#d93025; --green:#0b7d5c;
  --amber:#9a4d00; --soft:#ece8de;
}
.stApp{background:var(--paper);color:var(--ink);font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
header[data-testid="stHeader"]{background:rgba(245,244,239,.96);backdrop-filter:blur(8px);}
.block-container{max-width:1120px;padding-top:4.75rem;padding-bottom:4rem;}
@supports (padding-top:env(safe-area-inset-top)){
 .block-container{padding-top:calc(4.75rem + env(safe-area-inset-top));}
}
#MainMenu,footer{visibility:hidden;}
.mast{display:grid;grid-template-columns:1fr auto;align-items:end;border-top:5px solid var(--ink);border-bottom:1px solid var(--ink);padding:13px 1px 11px;margin-bottom:10px;}
.brand{font-size:1.62rem;font-weight:950;letter-spacing:-.055em;line-height:1}.brand span{color:var(--blue)}
.version{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.76rem;color:var(--muted);letter-spacing:.06em;}
.marketbar{display:flex;gap:13px;flex-wrap:wrap;align-items:center;padding:7px 0 10px;border-bottom:1px solid var(--line);font-size:.88rem;color:var(--muted);}
.marketbar b{color:var(--ink)}
.section-kicker{font-size:.72rem;font-weight:900;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:18px 0 5px;}
.section-title{font-size:1.32rem;font-weight:950;letter-spacing:-.025em;margin:0 0 9px;}
.rankrow{display:grid;grid-template-columns:44px minmax(0,1fr) auto;gap:10px;align-items:center;background:var(--surface);border-top:1px solid var(--ink);border-bottom:1px solid var(--line);padding:13px 10px 12px;margin-top:9px;}
.rankno{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:1.28rem;font-weight:900;color:#a3a39d;align-self:start;padding-top:2px;}
.stockname{font-size:1.28rem;font-weight:950;line-height:1.05;letter-spacing:-.025em}.stockcode{font-size:.79rem;font-weight:750;color:var(--muted);margin-left:5px;}
.industry{font-size:.82rem;color:#4d5159;margin-top:4px;line-height:1.15;}.tagrow{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}.tag{display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;border:1px solid var(--line);background:#f7f6f1;font-size:.73rem;font-weight:850;color:#434851}.tag.blue{color:#0b57d0;border-color:#c9daf8;background:#f2f7ff}.tag.red{color:#8a3b11;border-color:#ead2c6;background:#fff6f1}
.quote{text-align:right;min-width:92px}.price{font-size:1.30rem;font-weight:950;line-height:1}.change{font-size:.87rem;font-weight:900;margin-top:5px}.up{color:var(--red)}.down{color:var(--green)}.flat{color:var(--muted)}
.signal{display:grid;grid-template-columns:minmax(130px,.34fr) 1fr;gap:12px;background:var(--surface);padding:9px 10px 11px;border-bottom:1px solid var(--line);}
.signal-title{font-size:1.02rem;font-weight:950;color:var(--blue);line-height:1.3}.signal-copy{font-size:.91rem;line-height:1.45;color:#3f434a}.risk{color:#8a3b11}
.entryline{display:flex;flex-wrap:wrap;gap:7px;align-items:center;background:#fffefb;padding:8px 10px 5px;font-size:.80rem}.entrychip{display:inline-flex;align-items:center;padding:3px 8px;border:1px solid var(--line);border-radius:999px;background:#f7f6f1;font-weight:850;color:#3f434a}.entrychip b{color:var(--ink);margin-left:4px}.entryaction{font-weight:950;color:#8a3b11}.entryok{color:var(--green)}.overnight{color:#8a3b11;font-weight:850}.entrywhy{background:#fffefb;border-bottom:1px solid var(--line);padding:0 10px 8px;font-size:.76rem;line-height:1.35;color:var(--muted)}.scoreline{display:flex;flex-wrap:wrap;gap:6px;background:#fffefb;padding:6px 10px 8px;border-bottom:1px solid var(--line)}.scorechip{font-size:.72rem;color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:2px 7px}.scorechip b{color:var(--ink)}
.quick{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-top:0;background:var(--surface);}
.q{padding:9px 10px;border-right:1px solid var(--line);min-width:0}.q:last-child{border-right:0}.qk{font-size:.72rem;color:var(--muted);font-weight:800}.qv{font-size:.94rem;font-weight:900;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.liveflag{display:inline-flex;align-items:center;gap:6px;font-size:.78rem;font-weight:900;color:var(--green);letter-spacing:.04em}.dot{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 0 3px rgba(7,135,93,.12)}
.offflag{font-size:.78rem;font-weight:850;color:var(--muted)}
.datagrid{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);background:var(--surface);margin:8px 0 12px}.datum{padding:10px;border-right:1px solid var(--line);border-bottom:1px solid var(--line)}.datum:nth-child(4n){border-right:0}.dk{font-size:.74rem;color:var(--muted);font-weight:800}.dv{font-size:1rem;font-weight:950;margin-top:3px}.ds{font-size:.73rem;color:var(--muted);margin-top:2px;line-height:1.3}.flowbar{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);border:1px solid var(--line);margin:6px 0 10px}.flowbar>div{background:var(--surface);padding:8px 9px}.flowbar small{display:block;color:var(--muted);font-weight:800}.flowbar b{display:block;margin-top:2px;font-size:.95rem}.overview{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:6px 0 14px}.ovcol{border:1px solid var(--line);background:var(--surface)}.ovhead{padding:10px;border-bottom:1px solid var(--line);font-weight:950}.ovitem{display:grid;grid-template-columns:32px 1fr;gap:8px;padding:10px;border-bottom:1px solid var(--line)}.ovitem:last-child{border-bottom:0}.ovrank{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#9a9b95;font-weight:900}.ovname{font-weight:900}.ovmeta{font-size:.8rem;color:var(--muted);margin-top:2px}.consensus{border:1px solid var(--line);background:var(--surface);padding:10px 11px;margin:10px 0 6px}.consensus b{font-size:1rem}.sectorbar{display:flex;gap:7px;overflow-x:auto;padding:4px 0 10px;margin-bottom:4px;scrollbar-width:none}.sectorbar::-webkit-scrollbar{display:none}.sectorpill{flex:0 0 auto;border:1px solid var(--line);background:var(--surface);padding:7px 10px;border-radius:999px;font-size:.78rem;font-weight:850;color:#3f434a}.sectorpill b{color:var(--ink);margin-left:4px}.sectorpill.hot{border-color:#bfd3f7;background:#f1f6ff;color:#0b57d0}.sectorpill.weak{background:#faf4f0;color:#8a3b11}.consensus-note{font-size:.79rem;color:var(--muted);margin-top:4px}
.plan{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;background:var(--line);border:1px solid var(--line);margin-bottom:8px}.plan>div{background:var(--surface);padding:9px}.plan small{display:block;color:var(--muted);font-weight:800}.plan b{display:block;margin-top:3px;font-size:.98rem}
.chartchips{display:flex;gap:12px;align-items:center;font-size:.76rem;color:var(--muted);margin:4px 0 1px;flex-wrap:wrap}.chipline{display:inline-block;width:18px;height:3px;vertical-align:middle;margin-right:5px}.ma5{background:#111318}.ma20{background:#0b57d0}.ma60{background:#f08c00}.ma120{background:#7b61ff}.ma240{background:#2f9e44}.bb{background:#d9480f}.ind-caption{font-size:.78rem;color:var(--muted);margin:2px 0 6px}
.mini-note{font-size:.78rem;color:var(--muted);line-height:1.4}
[data-testid="stExpander"]{border:0!important;border-bottom:1px solid var(--line)!important;border-radius:0!important;background:transparent!important;}[data-testid="stExpander"] summary{padding-left:0!important;padding-right:0!important;font-weight:850!important;}
div[data-baseweb="tab-list"]{gap:4px}button[data-baseweb="tab"]{font-weight:850}.stRadio [role="radiogroup"]{gap:.35rem!important;align-items:center}.stRadio label{background:var(--surface);border:1px solid var(--line);padding:.16rem .58rem;border-radius:999px}.stRadio label p{color:var(--ink)!important;font-weight:850!important}button[data-baseweb="tab"]{color:var(--ink)!important}
.stButton>button{border-radius:5px!important;font-weight:850!important;min-height:42px}.stButton>button[kind="primary"]{background:var(--ink)!important;border-color:var(--ink)!important;}
hr{border-color:var(--line)!important}
@media(max-width:720px){
 .stApp{font-size:17.5px}.block-container{padding:4.35rem .72rem 3rem}.mast{padding:13px 0 10px;margin-top:.15rem}.brand{font-size:1.52rem}.version{font-size:.68rem}
 .marketbar{font-size:.84rem;gap:8px 11px}.rankrow{grid-template-columns:36px minmax(0,1fr) auto;padding:12px 7px 11px;gap:7px}.rankno{font-size:1.08rem}.stockname{font-size:1.30rem}.stockcode{font-size:.78rem}.industry{font-size:.84rem}.quote{min-width:78px}.price{font-size:1.25rem}.change{font-size:.88rem}
 .signal{grid-template-columns:1fr;gap:3px;padding:9px 7px 10px}.signal-title{font-size:1.05rem}.signal-copy{font-size:.96rem;line-height:1.42}
 .quick{grid-template-columns:repeat(2,1fr)}.q:nth-child(2){border-right:0}.q:nth-child(-n+2){border-bottom:1px solid var(--line)}.qk{font-size:.78rem}.qv{font-size:1rem}
 .datagrid{grid-template-columns:repeat(2,1fr)}.datum:nth-child(odd){border-right:1px solid var(--line)}.datum:nth-child(even){border-right:0}.dk{font-size:.80rem}.dv{font-size:1.05rem}.ds{font-size:.77rem}
 .plan{grid-template-columns:repeat(2,1fr)}.overview{grid-template-columns:1fr}.flowbar{grid-template-columns:repeat(2,1fr)}.section-title{font-size:1.25rem}
 .stRadio [role="radiogroup"]{gap:.22rem!important}.stRadio label{font-size:.92rem!important;padding:.14rem .45rem!important}
 @supports (padding-top:env(safe-area-inset-top)){.block-container{padding-top:calc(4.35rem + env(safe-area-inset-top));}}
}

.decision{display:grid;grid-template-columns:repeat(6,1fr);gap:1px;background:var(--line);border:1px solid var(--line);border-top:0;margin-bottom:0}.decision>div{background:#fffefb;padding:8px 9px}.decision small{display:block;color:var(--muted);font-size:.69rem;font-weight:800}.decision b{display:block;margin-top:2px;font-size:.91rem}.decision-action{grid-column:1/-1;background:#f7f6f1!important;font-weight:900;color:#3f434a}.decision-reasons{grid-column:1/-1;background:#fbfaf6!important;color:var(--muted);font-size:.78rem}.gatebar{display:flex;gap:7px;flex-wrap:wrap;margin:7px 0 9px}.gatechip{border:1px solid var(--line);background:#fffefb;padding:5px 7px;font-size:.76rem;color:var(--muted)}.decision-action.buy{color:var(--green)}.decision-action.wait{color:#8a3b11}@media(max-width:720px){.decision{grid-template-columns:repeat(2,1fr)}.decision small{font-size:.72rem}.decision b{font-size:.94rem}}.phasebar{border:1px solid var(--line);background:var(--surface);padding:10px 11px;margin:8px 0 12px}.phasebar b{font-weight:950}.phasebar .mini-note{margin-top:3px}.buyempty{border:1px solid var(--line);background:#fffefb;padding:12px;margin:8px 0;color:#4d5159;font-size:.9rem}.buyempty b{color:var(--ink)}

/* V16.7 doctor readability: custom light paper must override browser/Streamlit dark label text. */
[data-testid="stWidgetLabel"] p,
.stTextInput label p,.stSelectbox label p,.stNumberInput label p,.stCheckbox label p,
.stRadio>label p,.stTextInput label,.stSelectbox label,.stNumberInput label,.stCheckbox label{color:var(--ink)!important;font-weight:800!important}
.doctor-hero{border:1px solid var(--line);border-top:4px solid var(--ink);background:var(--surface);padding:14px 15px;margin:10px 0 8px}
.doctor-hero.buy{border-top-color:var(--green)}.doctor-hero.wait{border-top-color:var(--amber)}.doctor-hero.sell{border-top-color:var(--red)}.doctor-hero.hold{border-top-color:var(--blue)}
.doctor-kicker{font-size:.72rem;font-weight:900;letter-spacing:.10em;color:var(--muted);text-transform:uppercase}.doctor-title{font-size:1.35rem;font-weight:950;letter-spacing:-.02em;margin-top:3px}.doctor-sub{font-size:.84rem;color:var(--muted);margin-top:5px;line-height:1.4}
.doctor-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);border:1px solid var(--line);margin:8px 0 10px}.doctor-grid>div{background:var(--surface);padding:10px}.doctor-grid small{display:block;color:var(--muted);font-size:.72rem;font-weight:800}.doctor-grid b{display:block;margin-top:3px;font-size:1rem}.doctor-reasons{border-left:3px solid var(--line);padding:6px 10px;margin:8px 0 12px;color:#3f434a;font-size:.88rem;line-height:1.55}
.doctor-plan{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);border:1px solid var(--line);margin:8px 0 12px}.doctor-plan>div{background:var(--surface);padding:9px}.doctor-plan small{display:block;color:var(--muted);font-size:.72rem;font-weight:800}.doctor-plan b{display:block;margin-top:3px}
@media(max-width:720px){.doctor-grid{grid-template-columns:repeat(2,1fr)}.doctor-plan{grid-template-columns:repeat(2,1fr)}.doctor-title{font-size:1.25rem}}

.briefbox{border:1px solid var(--line);border-top:4px solid var(--ink);background:var(--surface);padding:12px 13px;margin:10px 0 12px;box-shadow:0 2px 10px rgba(17,19,24,.04)}.briefbox h4{margin:0 0 6px;font-size:1rem}.briefline{font-size:.86rem;line-height:1.5;color:#3f434a}.briefchips{display:flex;gap:6px;flex-wrap:wrap;margin:7px 0}.briefchip{border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.76rem;font-weight:850;background:#f7f6f1}.briefchip.pos{border-color:#f0c2bc;background:#fff5f3;color:#9a241b}.briefchip.neg{border-color:#bee0d3;background:#f1fbf7;color:#0b7d5c}.briefdetail{font-size:.8rem;line-height:1.5;color:#4d5159;margin-top:8px}.newsitem{padding:8px 0;border-top:1px solid var(--line);font-size:.84rem;line-height:1.45}.newsmeta{font-size:.72rem;color:var(--muted);margin-top:2px}.syncwarn{border-left:4px solid var(--amber);padding:8px 10px;background:#fff8ef;margin:7px 0 10px;font-size:.83rem;line-height:1.45}.boardnote{font-size:.8rem;color:var(--muted);margin:4px 0 8px}.tabletitle{font-weight:900;margin:8px 0 4px}
/* V17.5 editorial visual system: deliberate hierarchy, restrained color, legible mobile cards. */
:root{
  --paper:#f4f6fb;--surface:#ffffff;--ink:#15213b;--muted:#56647c;
  --line:#dce3ed;--blue:#3556c7;--red:#c3364f;--green:#137669;
  --amber:#a05d1e;--soft:#ebeff8;--violet:#7657be;
  --shadow:0 6px 26px rgba(17,34,66,.045);
}
.stApp{background:radial-gradient(circle at 96% 3%,rgba(154,173,232,.20),transparent 26rem),linear-gradient(160deg,#f4f6fb 0%,#f6f8fc 53%,#fdfbf8 100%);color:var(--ink);font-family:Inter,"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif}
header[data-testid="stHeader"]{background:rgba(246,248,253,.91);backdrop-filter:blur(18px)}
.block-container{max-width:1170px;padding-top:4.25rem}
.mast{background:linear-gradient(112deg,#182647 0%,#213968 66%,#4b4382 100%);border:none;border-radius:17px;box-shadow:0 18px 38px rgba(23,41,85,.16);padding:22px 24px;margin:1px 0 15px;gap:14px;position:relative;overflow:hidden}
.mast:before{content:"";position:absolute;right:21%;top:-58px;width:270px;height:175px;border:1px solid rgba(255,255,255,.12);transform:rotate(-15deg);border-radius:100%}
.mast .brand{color:#fff;font-size:1.78rem;font-weight:900;letter-spacing:-.06em;z-index:1}.mast .brand span{color:#9bcff9}
.mast .version{color:#e4e7ff;font-weight:800;letter-spacing:.04em;z-index:1}
.phasebar{border:1px solid #d9e4f4;border-left:5px solid #6f78d4;border-radius:12px;background:linear-gradient(90deg,#f2f5ff,#fbfcff);padding:11px 14px;box-shadow:0 2px 10px rgba(42,62,103,.035)}
.marketbar{background:#fff;padding:12px 14px;border:1px solid var(--line);border-radius:12px;gap:9px 12px;box-shadow:var(--shadow)}
.marketbar>span{border-radius:7px;background:#f2f5fb;padding:4px 7px;color:#3e4d6b;line-height:1.4}.marketbar>span:first-child{background:#dce8ff;color:#1e438f}
.section-kicker{color:#4c63a2;font-size:.71rem;letter-spacing:.115em;margin-top:20px}
.section-title{color:#15264e;font-size:1.43rem;margin-bottom:11px;font-weight:950}
.stButton>button{border-radius:11px!important;font-weight:850!important;min-height:46px;box-shadow:0 3px 8px rgba(26,52,93,.07)}
.stButton>button[kind="primary"]{background:linear-gradient(110deg,#274ba8,#5441a3)!important;border-color:#314caa!important;color:white!important;box-shadow:0 6px 16px rgba(45,70,148,.2)}
.stButton>button:hover{filter:brightness(1.055)}
.stRadio label{background:#fff!important;border:1px solid #dbe3f2!important;border-radius:9px!important;padding:.20rem .62rem!important}
.stRadio [role="radiogroup"]{gap:7px!important}
.stRadio [role="radiogroup"] label:has(input:checked){background:#e9eeff!important;border-color:#536bd0!important;box-shadow:0 0 0 1px rgba(71,93,191,.15)}
.stRadio [role="radiogroup"] label:has(input:checked) p{color:#29479f!important}
.rankrow{border:1px solid var(--line);border-left:5px solid #536bc9;border-radius:12px 12px 0 0;box-shadow:var(--shadow);margin-top:12px;padding:12px 14px}
.signal{border-left:1px solid var(--line);border-right:1px solid var(--line);background:#f9fbff}.signal-title{color:#3156af}
.quick,.datagrid,.flowbar{border-color:var(--line);border-radius:0 0 11px 11px;overflow:hidden}
.overview{gap:12px}.ovcol{border:1px solid var(--line);border-radius:13px;overflow:hidden;box-shadow:var(--shadow)}
.ovhead{background:#edf2ff;color:#2c4488}.ovcol:nth-child(2) .ovhead{background:#e7f4f1;color:#0d7065}.ovcol:nth-child(3) .ovhead{background:#f0eaff;color:#69459f}
.tag{background:#f3f6fc}.tag.blue{background:#ebf1ff}
.briefbox{border:1px solid var(--line);border-top:4px solid #5873c0;border-radius:12px;box-shadow:var(--shadow)}
/* Market journal: numerical tape. Red rises / green falls (Taiwan convention). */
.reading-intro{background:linear-gradient(125deg,#162c52,#253968 72%,#2f5471);border-radius:17px;padding:24px 25px;color:#fff;margin:9px 0 18px;box-shadow:0 14px 32px rgba(18,38,79,.13)}
.reading-kicker{font-size:.74rem;letter-spacing:.11em;font-weight:850;color:#bcd6ff}.reading-kicker span{color:#ffc493;margin:0 4px}
.reading-intro h2{font-size:1.75rem;letter-spacing:-.03em;margin:6px 0 7px;font-weight:950;color:#fff}.reading-intro p{margin:0;color:#dce8fb;font-size:.89rem;line-height:1.6}
.tape-group-title{font-size:.91rem;font-weight:900;color:#253965;margin:16px 0 9px;padding-left:10px;border-left:3px solid #536ac0}
.tape-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.tape-tile{min-width:0;background:#fff;padding:14px 14px 12px;border:1px solid #dee6f1;border-radius:12px;box-shadow:var(--shadow);position:relative;overflow:hidden}
.tape-tile:before{content:"";display:block;position:absolute;top:0;left:0;width:4px;height:100%;background:#9ca8bc}
.tape-tile.positive:before{background:#ce4763}.tape-tile.negative:before{background:#199386}.tape-tile.muted{opacity:.73}
.tape-name{font-size:.83rem;font-weight:850;color:#4d5c76;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tape-value{font-size:1.52rem;font-variant-numeric:tabular-nums;font-weight:950;color:#14264b;letter-spacing:-.04em;margin-top:6px;line-height:1.12}
.tape-change{font-size:.81rem;font-weight:800;margin-top:6px;color:#596780;font-variant-numeric:tabular-nums}
.tape-change b{margin-left:6px;font-size:.92rem}.tape-tile.positive .tape-change b{color:#b82943}.tape-tile.negative .tape-change b{color:#087968}
.tape-note{font-size:.72rem;margin-top:8px;color:#637189}
.market-takeaway{background:#f1f4ff;border:1px solid #dce5fb;border-left:5px solid #5269ba;border-radius:12px;padding:13px 16px;margin:7px 0 12px}
.market-takeaway span{color:#3853a5;display:block;font-size:.73rem;font-weight:900;margin-bottom:3px}.market-takeaway strong{color:#243a67;font-size:.90rem;line-height:1.6}
.insight-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.insight-card{background:#fff;border:1px solid #dce5f1;border-radius:12px;padding:14px 15px;box-shadow:var(--shadow);border-top:3px solid #6480cf}
.insight-card.positive{border-top-color:#c83550}.insight-card.caution{border-top-color:#d5973a}.insight-card.neutral{border-top-color:#5685b4}
.insight-card b{color:#1e3564;font-size:.93rem}.insight-card p{margin:7px 0 0;font-size:.83rem;line-height:1.65;color:#455775}
.news-story{border:1px solid var(--line);border-left:5px solid #6378b9;background:#fff;border-radius:12px;padding:13px 16px;margin:9px 0;box-shadow:var(--shadow)}
.news-story.positive{border-left-color:#c43d56}.news-story.negative{border-left-color:#248578}
.news-story header{display:flex;align-items:center;gap:7px;font-size:.72rem;font-weight:850;color:#567090}
.news-index{display:inline-block;background:#e9edf7;border-radius:6px;padding:3px 7px;color:#3153a3}
.news-topic{background:#edf2fd;border-radius:6px;padding:3px 7px;color:#3054a5}.news-tone{background:#f7f1eb;border-radius:6px;padding:3px 7px;color:#865219}
.news-story h4{margin:9px 0 5px;color:#162b50;font-size:1.01rem;line-height:1.45;font-weight:940}
.news-excerpt{font-size:.82rem;line-height:1.6;color:#57657b;margin:5px 0 8px}
.news-impact{font-size:.84rem;line-height:1.6;color:#294467;background:#f2f6fc;border-radius:8px;padding:9px 10px;margin:8px 0}
.news-story footer{font-size:.73rem;color:#68768c;margin-top:6px}.news-story footer a{float:right;color:#3559ba;text-decoration:none;font-weight:850}
.prevmodel{border:1px solid var(--line);border-radius:10px;padding:12px 15px;background:#fff}.prevmodel small{color:var(--muted)}.prevline{display:flex;gap:8px;border-top:1px solid var(--line);padding:9px 0;font-size:.84rem}.prevline b{flex:0 0 100px;color:#3751a2}.prevline span{color:#33415b}
.market-mini-strip{display:flex;flex-wrap:wrap;gap:7px;margin:7px 0 12px}.market-mini-strip span{background:#fff;border:1px solid #dfe7f3;border-radius:8px;padding:7px 10px;font-size:.83rem;box-shadow:var(--shadow)}.market-mini-strip b{color:#405475;margin-right:5px}.market-mini-strip em{font-style:normal;font-weight:950;font-variant-numeric:tabular-nums}.market-mini-strip em.positive{color:#b82943}.market-mini-strip em.negative{color:#07836f}
/* Unified recommendations without visually duplicate lists. */
.explainer-pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:9px 0 13px}
.explainer-pair>div{background:linear-gradient(125deg,#f1f4ff,#fff);padding:14px 15px;border:1px solid #dbe3f6;border-radius:12px}
.explainer-pair>div+div{background:linear-gradient(125deg,#ecf7f3,#fff);border-color:#d6ebe5}
.explainer-pair .ex-icon{display:inline-flex;border-radius:5px;background:#dce5ff;color:#3453a3;font-size:.70rem;font-weight:900;padding:3px 6px;margin-right:7px}.explainer-pair .ex-icon.alt{background:#d4eee7;color:#13766a}
.explainer-pair b{font-size:.93rem;color:#20375f}.explainer-pair p{font-size:.83rem;line-height:1.6;color:#506078;margin:7px 0 0}.explainer-pair strong{color:#233962}
.board-headline{display:flex;justify-content:space-between;align-items:center;background:#e9eefc;color:#233f82;border-radius:10px;padding:10px 13px;margin:13px 0 10px;gap:10px;font-size:.83rem}.board-headline span{color:#51648e}
.pick-mini-list{display:flex;flex-direction:column;gap:8px}
.pick-mini{display:grid;grid-template-columns:minmax(175px,1.15fr) minmax(165px,1fr) minmax(100px,.45fr);gap:8px 14px;background:#fff;border:1px solid #dce4f0;border-left:5px solid #5265c2;border-radius:12px;padding:13px 15px 10px;align-items:center;box-shadow:var(--shadow)}
.pick-mini.next{border-left-color:#19887e}.pick-mini.core{border-left-color:#536cc7}
.pick-name{color:#182e57;font-weight:950;font-size:1.04rem;line-height:1.2}.pick-name small{font-size:.75rem;color:#7b879b;font-weight:800;margin-left:4px}
.pick-tags{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:7px;color:#586888;font-size:.73rem;font-weight:800}
.pick-label{background:#e9efff;color:#3151a6;border-radius:6px;padding:2px 6px}.pick-mini.next .pick-label{background:#ddf4ed;color:#13766b}
.pick-mini-center b{display:block;font-size:.88rem;color:#1a456b}.pick-mini-center small{font-size:.73rem;color:#566783}.pick-mini-price{text-align:right}.pick-mini-price>b{font-size:1.15rem;font-variant-numeric:tabular-nums;color:#213563}.pick-mini-price small{display:block;font-size:.69rem;color:#6b7b95}
.pick-mini-extra{grid-column:1/-1;border-top:1px dashed #e1e7f0;padding-top:7px;color:#5e6c86;font-size:.75rem}.pick-mini-extra b{color:#243961;font-weight:850}.pick-mini-extra em{float:right;font-style:normal;color:#60739b}
@media(max-width:720px){
 .block-container{padding-top:3.95rem}.mast{padding:17px 16px;border-radius:13px}.mast .brand{font-size:1.55rem}.mast .version{font-size:.68rem}
 .marketbar{gap:6px;padding:9px}.marketbar>span{font-size:.77rem;padding:4px 6px}
 .section-title{font-size:1.35rem}.reading-intro{padding:19px 17px}.reading-intro h2{font-size:1.44rem}
 .tape-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.tape-tile{padding:11px 10px 9px}.tape-name{font-size:.77rem}.tape-value{font-size:1.23rem}.tape-change{font-size:.73rem}.tape-change b{margin-left:1px;font-size:.83rem}.tape-note{font-size:.67rem}
 .insight-grid,.explainer-pair{grid-template-columns:1fr}.insight-card{padding:12px}.news-story{padding:12px}.news-story h4{font-size:.99rem}.news-impact{font-size:.82rem}
 .pick-mini{grid-template-columns:minmax(0,1fr) auto;gap:7px;padding:11px 12px}.pick-mini-main{grid-column:1}.pick-mini-center{grid-column:1;grid-row:2}.pick-mini-price{grid-column:2;grid-row:1/3}.pick-mini-price>b{font-size:1.11rem}.pick-mini-extra{grid-column:1/-1;line-height:1.6}.pick-mini-extra em{float:none;display:block;font-size:.70rem;margin-top:2px}
 .board-headline{flex-wrap:wrap;gap:3px}.board-headline span{font-size:.75rem}.stRadio label{padding:.15rem .45rem!important}
}
@media(max-width:390px){.tape-value{font-size:1.11rem}.tape-change{font-size:.7rem}.mast .version{font-size:.60rem}}

</style>
"""


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def finite(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else None
    except Exception:
        return None


def nfmt(v, d=2):
    x = finite(v)
    if x is None:
        return "—"
    value = f"{x:,.{d}f}"
    # A zero-decimal integer must NEVER have significant trailing zeroes removed
    # (40/80/100 were previously displayed as 4/8/1 in model score cards).
    return value if d <= 0 else value.rstrip("0").rstrip(".")


def pp(v, d=1):
    x = finite(v)
    return "—" if x is None else f"{x:+.{d}f}%"


def decpct(v, d=1):
    x = finite(v)
    return "—" if x is None else f"{x*100:+.{d}f}%"


def lots(v):
    x = finite(v)
    return "—" if x is None else f"{x:+,.0f}張"


def _optional_float(v):
    text = str(v or "").strip().replace(",", "")
    if not text:
        return None
    try:
        x = float(text)
        return x if np.isfinite(x) and x > 0 else None
    except Exception:
        return None


def _research(stock: dict):
    r = stock.get("research") or {}
    return (
        r.get("monthly_revenue") or {},
        r.get("financials") or {},
        r.get("valuation") or {},
        r.get("institutional_flow") or {},
        r.get("main_force_proxy") or {},
    )


def _quick_values(stock: dict):
    rev, fin, val, inst, _ = _research(stock)
    latest = rev.get("latest") or {}
    eps4 = fin.get("eps_4q_sum")
    if eps4 is not None:
        eps_label, eps_value, eps_note = "近4季EPS", nfmt(eps4, 2), "單季加總"
    elif fin.get("latest_ytd_eps") is not None:
        eps_label, eps_value, eps_note = "最新累計EPS", nfmt(fin.get("latest_ytd_eps"), 2), fin.get("latest_period") or "官方季報"
    else:
        eps_label, eps_value, eps_note = "EPS", "—", "資料未取得"
    sector = stock.get("sector_strength") or {}
    theme = stock.get("theme_strength") or {}
    mainline = finite(((stock.get("horizons") or {}).get("mid",{}).get("score_components") or {}).get("mainline"))
    if mainline is None:
        mainline = 0.56*(finite(sector.get("score")) or 50.0)+0.44*(finite(theme.get("score")) or 50.0)
    theme_name = str(theme.get("name") or stock.get("theme") or sector.get("status") or "主線")
    return [
        ("月營收", f"{nfmt(latest.get('revenue_billion'),1)}億", f"YoY {pp(latest.get('yoy_pct'))}"),
        (eps_label, eps_value, eps_note),
        ("產業鏈主線", nfmt(mainline, 0), theme_name),
        ("法人淨超", lots(inst.get("total_net_lots")) if inst.get("available") else "—", str(inst.get("scope_label") or "合計")),
    ]


def _quick_html(stock: dict) -> str:
    cells = []
    for k, v, s in _quick_values(stock):
        cells.append(f"<div class='q'><div class='qk'>{esc(k)}</div><div class='qv'>{esc(v)}</div><div class='ds'>{esc(s)}</div></div>")
    return "<div class='quick'>" + "".join(cells) + "</div>"


def _merge_research(base: dict | None, fresh: dict | None) -> dict:
    """Prefer usable on-demand sections without discarding an existing good snapshot."""
    base = dict(base or {})
    fresh = dict(fresh or {})
    merged = dict(base)
    for section in ["monthly_revenue", "financials", "valuation", "institutional_flow", "main_force_proxy"]:
        old = base.get(section) or {}
        cur = fresh.get(section) or {}
        usable = bool(
            cur.get("available")
            or cur.get("rows")
            or cur.get("quarters")
            or cur.get("history_quarters")
            or cur.get("daily_rows")
            or cur.get("latest")
            or cur.get("latest_ytd_eps") is not None
        )
        if usable or not old:
            merged[section] = cur
    sources = []
    for src in [base.get("source"), fresh.get("source")]:
        if src and str(src) not in sources:
            sources.append(str(src))
    if sources:
        merged["source"] = " + ".join(sources)
    if fresh.get("fetched_at"):
        merged["fetched_at"] = fresh.get("fetched_at")
    if fresh.get("error"):
        merged["detail_error"] = fresh.get("error")
    return merged


def _research_detail_for_ticker(stock: dict, force: bool = False) -> tuple[dict, str | None]:
    """Load detail lazily and keep it in Streamlit session state for 15 minutes."""
    ticker = str(stock.get("ticker") or "").strip()
    embedded = stock.get("research") or {}
    if not ticker:
        return embedded, "股票代碼缺失"
    cache = st.session_state.setdefault("alpha_research_detail_cache", {})
    now_ts = time.time()
    item = cache.get(ticker) if isinstance(cache, dict) else None
    item_ttl = 60 if isinstance(item, dict) and item.get("error") else 900
    if (
        not force
        and isinstance(item, dict)
        and isinstance(item.get("data"), dict)
        and now_ts - float(item.get("ts") or 0.0) < item_ttl
    ):
        return _merge_research(embedded, item.get("data")), item.get("error")
    try:
        fresh = service.research_on_demand(ticker, DATA_DIR, include_branch=False)
        err = str(fresh.get("error")) if isinstance(fresh, dict) and fresh.get("error") else None
        cache[ticker] = {"ts": now_ts, "data": fresh if isinstance(fresh, dict) else {}, "error": err}
        return _merge_research(embedded, fresh), err
    except Exception as exc:
        err = safe_error_text(exc, mask_tokens=(os.getenv("FINMIND_TOKEN", ""),))
        cache[ticker] = {"ts": now_ts, "data": {}, "error": err}
        return embedded, err


def _render_fundamental_details(stock: dict, key: str):
    """Render research detail with explicit coverage and categorical period axes."""
    rev, fin, _, inst, _ = _research(stock)
    rendered = 0

    st.markdown("#### 月營收")
    rows = [dict(r) for r in (rev.get("rows") or []) if isinstance(r, dict)]
    rows = sorted(rows, key=lambda x: str(x.get("month") or ""))[-24:]
    if rows:
        rdf = pd.DataFrame(rows).copy()
        keep = [c for c in ["month", "revenue_billion", "mom_pct", "yoy_pct"] if c in rdf.columns]
        if keep:
            rdf = rdf[keep].rename(columns={
                "month": "月份", "revenue_billion": "營收(億元)", "mom_pct": "MoM(%)", "yoy_pct": "YoY(%)"
            })
            st.dataframe(rdf.tail(12), hide_index=True, use_container_width=True)
            rendered += 1
        chart_df = pd.DataFrame(rows)
        if {"month", "revenue_billion"}.issubset(chart_df.columns):
            chart_df["revenue_billion"] = pd.to_numeric(chart_df["revenue_billion"], errors="coerce")
            chart_df = chart_df.dropna(subset=["revenue_billion"]).drop_duplicates("month", keep="last")
            # Plotly interprets YYYY-MM as dates unless explicitly categorical.  With
            # a single point this used to create microsecond ticks around Aug 1.
            if len(chart_df) >= 2:
                try:
                    fig = go.Figure()
                    fig.add_bar(x=chart_df["month"].astype(str), y=chart_df["revenue_billion"], name="月營收")
                    fig.update_layout(
                        height=250, margin=dict(l=5, r=5, t=8, b=5), showlegend=False,
                        paper_bgcolor="#fffefb", plot_bgcolor="#fffefb", xaxis_title="", yaxis_title="億元",
                    )
                    fig.update_xaxes(type="category", tickmode="auto", nticks=min(6, len(chart_df)), fixedrange=True)
                    fig.update_yaxes(gridcolor="#ecebe5", fixedrange=True)
                    st.plotly_chart(fig, use_container_width=True, key=f"{key}_rev", config={"displayModeBar": False})
                except Exception:
                    st.caption("營收趨勢圖暫時無法繪製，但上方數值表仍可使用。")
            else:
                st.caption("目前只有最新 1 個月資料，因此不繪製容易誤判的單點趨勢圖。")
    else:
        latest = rev.get("latest") or {}
        if latest:
            st.dataframe(pd.DataFrame([{
                "月份": latest.get("month"), "營收(億元)": latest.get("revenue_billion"),
                "MoM(%)": latest.get("mom_pct"), "YoY(%)": latest.get("yoy_pct"),
            }]), hide_index=True, use_container_width=True)
            rendered += 1
            st.caption("目前資料源僅提供最新月份；取得歷史資料後會自動補上趨勢。")
        else:
            st.caption("未取得月營收明細。")
    if rev.get("coverage_label"):
        st.caption(f"營收涵蓋：{rev.get('coverage_label')}")
    if rev.get("source"):
        st.caption("營收來源：" + str(rev.get("source")))

    st.markdown("#### EPS / 毛利率")
    qhist = [dict(q) for q in (fin.get("history_quarters") or fin.get("quarters") or []) if isinstance(q, dict)]
    qhist = sorted(qhist, key=lambda x: str(x.get("quarter") or ""))[-8:]
    if qhist:
        qdf = pd.DataFrame(qhist).copy()
        official_ytd = fin.get("eps_ytd_basis") == "official_cumulative"
        ytd_title = "官方累積EPS" if official_ytd else "同年度累積EPS*"
        wanted = ["quarter", "eps_ytd_sum_unadjusted", "gross_margin_pct"] if official_ytd else ["quarter", "eps", "eps_ytd_sum_unadjusted", "gross_margin_pct"]
        cols = [c for c in wanted if c in qdf.columns]
        if cols:
            show = qdf[cols].rename(columns={
                "quarter": "季別", "eps": "單季EPS", "eps_ytd_sum_unadjusted": ytd_title, "gross_margin_pct": "單季毛利率(%)"
            })
            st.dataframe(show, hide_index=True, use_container_width=True)
            rendered += 1
        chart_col = "eps_ytd_sum_unadjusted" if official_ytd else "eps"
        if {"quarter", chart_col}.issubset(qdf.columns):
            chart_df = qdf[["quarter", chart_col]].copy()
            chart_df[chart_col] = pd.to_numeric(chart_df[chart_col], errors="coerce")
            chart_df = chart_df.dropna(subset=[chart_col]).drop_duplicates("quarter", keep="last")
            if len(chart_df) >= 2:
                try:
                    chart_name = "官方累積 EPS" if official_ytd else "單季 EPS"
                    fig = go.Figure()
                    fig.add_bar(x=chart_df["quarter"].astype(str), y=chart_df[chart_col], name=chart_name)
                    fig.update_layout(
                        height=240, margin=dict(l=5, r=5, t=8, b=5), showlegend=False,
                        paper_bgcolor="#fffefb", plot_bgcolor="#fffefb", xaxis_title="", yaxis_title=chart_name,
                    )
                    fig.update_xaxes(type="category", fixedrange=True)
                    fig.update_yaxes(gridcolor="#ecebe5", fixedrange=True)
                    st.plotly_chart(fig, use_container_width=True, key=f"{key}_eps", config={"displayModeBar": False})
                except Exception:
                    st.caption("EPS 趨勢圖暫時無法繪製，但上方數值表仍可使用。")
            else:
                st.caption("目前只有 1 個可比較 EPS 期間，因此不繪製單點趨勢圖。")
        if official_ytd:
            st.caption("官方 Q2/Q3/Q4 EPS 是年初至當季的累計 EPS。因加權平均股數可能改變，本系統不以相減方式冒充單季 EPS；單季毛利率則可由營收／毛利累計額差分還原。")
        else:
            st.caption("* FinMind EPS 為單季值；跨季直接加總遇配股／分割時可能與公司財報累計 EPS 不一致。")
    elif fin.get("latest_ytd_eps") is not None:
        st.dataframe(pd.DataFrame([{
            "期間": fin.get("latest_period") or "最新季報",
            "官方累積EPS": fin.get("latest_ytd_eps"),
            "累計毛利率(%)": fin.get("gross_margin_latest_pct"),
        }]), hide_index=True, use_container_width=True)
        rendered += 1
        st.caption("目前只有最新累計季報；沒有足夠相鄰季度時，不把累計 EPS 誤畫成單季趨勢。")
    else:
        st.caption("未取得 EPS / 季財報明細。")
    if fin.get("coverage_label"):
        st.caption(f"財報涵蓋：{fin.get('coverage_label')}")
    if fin.get("source"):
        st.caption("財報來源：" + str(fin.get("source")))

    st.markdown("#### 三大法人")
    daily = [dict(r) for r in (inst.get("daily_rows") or []) if isinstance(r, dict)]
    daily = sorted(daily, key=lambda x: str(x.get("date") or ""))[-22:]
    if daily:
        ddf = pd.DataFrame(daily).copy()
        cols = [c for c in ["date", "foreign_net_lots", "trust_net_lots", "dealer_net_lots", "total_net_lots"] if c in ddf.columns]
        if cols:
            ddf = ddf[cols].rename(columns={
                "date": "日期", "foreign_net_lots": "外資(張)", "trust_net_lots": "投信(張)",
                "dealer_net_lots": "自營商(張)", "total_net_lots": "合計(張)",
            })
            st.dataframe(ddf, hide_index=True, use_container_width=True)
            rendered += 1
    elif inst.get("available"):
        scope = str(inst.get("scope_label") or "目前可取得期間")
        st.dataframe(pd.DataFrame([
            ["外資", inst.get("foreign_net_lots")],
            ["投信", inst.get("trust_net_lots")],
            ["自營商", inst.get("dealer_net_lots")],
            ["合計", inst.get("total_net_lots")],
        ], columns=["類別", f"{scope}淨買賣超(張)"]), hide_index=True, use_container_width=True)
        rendered += 1
    else:
        reason = str(inst.get("reason") or "")
        st.caption("未取得三大法人明細。" + (f"（{reason}）" if reason else ""))
    if inst.get("scope_label"):
        st.caption("法人涵蓋：" + str(inst.get("scope_label")))
    if inst.get("source"):
        st.caption("法人來源：" + str(inst.get("source")))
    if inst.get("summary"):
        st.caption(str(inst.get("summary")))

    if rendered == 0:
        st.warning("本次沒有取得可顯示的營收、EPS 或法人明細。請稍後再展開；這不影響短／中／長線既有排名。")

def _render_research(stock: dict, widget_scope: str):
    ticker_for_cache = str(stock.get("ticker") or "").strip()
    safe_scope = "".join(ch if ch.isalnum() else "_" for ch in f"{widget_scope}_{ticker_for_cache or 'x'}")[-140:]
    toggle_key = f"fund_toggle_{safe_scope}"
    detail_err = None
    detail_research = None

    # Streamlit updates widget state before rerunning the script.  If the detail
    # toggle is already ON, load/merge the detail *before* drawing the summary
    # cards.  V16.6.1 fetched only at the bottom of the card, so the top法人 tiles
    # could remain as dashes on the very same run that showed detailed tables.
    if bool(st.session_state.get(toggle_key, False)):
        detail_research, detail_err = _research_detail_for_ticker(stock)
        stock = dict(stock)
        stock["research"] = detail_research
    else:
        detail_cache = st.session_state.get("alpha_research_detail_cache", {})
        cached = detail_cache.get(ticker_for_cache) if isinstance(detail_cache, dict) else None
        if isinstance(cached, dict) and isinstance(cached.get("data"), dict):
            stock = dict(stock)
            stock["research"] = _merge_research(stock.get("research") or {}, cached.get("data"))
    rev, fin, val, inst, main = _research(stock)
    latest = rev.get("latest") or {}
    qs = fin.get("quarters") or []
    gm = fin.get("gross_margin_latest_pct")
    eps_name = "近4季EPS" if fin.get("eps_4q_sum") is not None else "最新累計EPS" if fin.get("latest_ytd_eps") is not None else "EPS"
    eps_value = fin.get("eps_4q_sum") if fin.get("eps_4q_sum") is not None else fin.get("latest_ytd_eps")
    eps_note = "單季加總" if fin.get("eps_4q_sum") is not None else (fin.get("latest_period") or "官方季報") if fin.get("latest_ytd_eps") is not None else "資料未取得"
    gm_name = "最新累計毛利率" if fin.get("latest_ytd_eps") is not None and fin.get("eps_4q_sum") is None else "最新毛利率"
    items = [
        ("最新月營收", f"{nfmt(latest.get('revenue_billion'))} 億", latest.get("month") or "—"),
        ("月增", pp(latest.get("mom_pct")), "MoM"), ("年增", pp(latest.get("yoy_pct")), "YoY"),
        (eps_name, nfmt(eps_value), eps_note), (gm_name, pp(gm, 1) if gm is not None else "—", ""),
        ("本益比", f"{nfmt(val.get('pe'),1)} 倍" if val.get("pe") is not None else "—", val.get("date") or ""),
        ("股價淨值比", nfmt(val.get("pbr"),1), "PBR"), ("殖利率", pp(val.get("dividend_yield_pct"),1), "近值"),
        ("近4季平均毛利", pp(fin.get("gross_margin_4q_avg_pct"),1) if fin.get("gross_margin_4q_avg_pct") is not None else "—", ""),
        ("資料時間", stock.get("price_date") or "—", "股價基準"),
    ]
    html_cells="".join(f"<div class='datum'><div class='dk'>{esc(k)}</div><div class='dv'>{esc(v)}</div><div class='ds'>{esc(note)}</div></div>" for k,v,note in items)
    st.markdown(f"<div class='datagrid'>{html_cells}</div>", unsafe_allow_html=True)
    st.markdown(f"""<div class='flowbar'>
      <div><small>外資</small><b>{esc(lots(inst.get('foreign_net_lots')) if inst.get('available') else '—')}</b></div>
      <div><small>投信</small><b>{esc(lots(inst.get('trust_net_lots')) if inst.get('available') else '—')}</b></div>
      <div><small>自營商</small><b>{esc(lots(inst.get('dealer_net_lots')) if inst.get('available') else '—')}</b></div>
      <div><small>三大法人合計</small><b>{esc(lots(inst.get('total_net_lots')) if inst.get('available') else '—')}</b></div>
    </div>""", unsafe_allow_html=True)
    if inst.get("available") and inst.get("scope_label"):
        st.caption("法人數字範圍：" + str(inst.get("scope_label")))
    if qs:
        try:
            if fin.get("eps_ytd_basis") == "official_cumulative":
                st.dataframe(pd.DataFrame([{
                    "季度": q.get("quarter"), "官方累積EPS": q.get("eps_ytd_sum_unadjusted"),
                    "單季毛利率(%)": q.get("gross_margin_pct")
                } for q in qs[-4:]]), hide_index=True, use_container_width=True)
                st.caption("官方 Q2/Q3/Q4 EPS 為累計值；不以相減方式推估單季 EPS。")
            else:
                st.dataframe(pd.DataFrame([{
                    "季度": q.get("quarter"), "單季EPS": q.get("eps"),
                    "同年度累積EPS*": q.get("eps_ytd_sum_unadjusted"), "毛利率(%)": q.get("gross_margin_pct")
                } for q in qs[-4:]]), hide_index=True, use_container_width=True)
                st.caption("* FinMind EPS 為單季值；遇配股/分割時跨季加總可能與公司重編累計值不同。")
        except Exception:
            st.caption("季財報摘要暫時無法表格化；可展開明細重新取得。")
    source=str((stock.get("research") or {}).get("source") or "")
    if source: st.caption("公司資料來源："+source)
    if rev.get("history_limited") or fin.get("history_limited"):
        st.caption("部分公司資料目前為官方最新期備援；展開明細會再嘗試回補官方歷史資料。")
    if inst.get("summary"): st.caption(str(inst.get("summary")))

    if st.toggle("顯示營收 / EPS / 法人明細", value=False, key=toggle_key):
        if detail_research is None:
            detail_research, detail_err = _research_detail_for_ticker(stock)
        detail_stock = dict(stock)
        detail_stock["research"] = detail_research
        if detail_err:
            st.warning("公司／法人明細重新取得時有部分資料源未回應；已顯示可取得的資料。")
        _render_fundamental_details(detail_stock, f"fund_{safe_scope}")


def _chart_dataframe(chart: dict) -> pd.DataFrame:
    if not chart or "ohlcv" not in chart:
        return pd.DataFrame()
    df = pd.DataFrame(chart["ohlcv"], columns=["Open", "High", "Low", "Close", "Volume"])
    df["Date"] = pd.to_datetime(chart.get("dates", []), errors="coerce")
    df = df.dropna(subset=["Date"]).reset_index(drop=True)
    if df.empty:
        return df
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    c = df["Close"]
    for n in [5, 20, 60, 120, 240]:
        df[f"MA{n}"] = c.rolling(n).mean()
    m20 = c.rolling(20).mean()
    s20 = c.rolling(20).std()
    df["BBU"] = m20 + 2 * s20
    df["BBL"] = m20 - 2 * s20

    low9 = df["Low"].rolling(9).min()
    high9 = df["High"].rolling(9).max()
    k = (c - low9) / (high9 - low9).replace(0, np.nan) * 100
    df["KD_K"] = k.ewm(alpha=1/3, adjust=False).mean()
    df["KD_D"] = df["KD_K"].ewm(alpha=1/3, adjust=False).mean()

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_SIGNAL"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_HIST"] = df["MACD"] - df["MACD_SIGNAL"]
    return df


def render_chart(chart: dict, plan: dict | None, key: str):
    raw = _chart_dataframe(chart)
    if raw.empty:
        st.caption("K線資料未取得。")
        return
    window = st.radio("區間", ["1個月", "3個月", "6個月", "1年"], index=2, horizontal=True, label_visibility="collapsed", key=f"{key}_range")
    n = {"1個月": 24, "3個月": 66, "6個月": 132, "1年": 252}[window]
    base = raw.tail(n).copy()
    opts = list(base["Date"])
    if len(opts) >= 2:
        left, right = st.select_slider(
            "放大區間", options=opts, value=(opts[0], opts[-1]),
            format_func=lambda d: pd.Timestamp(d).strftime("%m/%d"),
            key=f"{key}_zoom_{window}", help="拖曳左右端點可鎖定想看的交易日期；不使用手勢平移，避免手機上整張圖跑掉。"
        )
        df = base[(base["Date"] >= pd.Timestamp(left)) & (base["Date"] <= pd.Timestamp(right))].copy()
    else:
        df = base.copy()
    x = df["Date"].dt.strftime("%Y-%m-%d").tolist()
    if df.empty:
        st.caption("所選區間沒有交易資料。")
        return

    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.54,0.16,0.15,0.15], vertical_spacing=0.025)
    fig.add_trace(go.Candlestick(
        x=x, open=df.Open, high=df.High, low=df.Low, close=df.Close,
        increasing_line_color="#d92d20", increasing_fillcolor="#d92d20",
        decreasing_line_color="#07875d", decreasing_fillcolor="#07875d",
        name="價格", showlegend=False, whiskerwidth=.72,
    ), row=1,col=1)
    fig.add_trace(go.Scatter(x=x,y=df.BBU,mode="lines",line=dict(color="rgba(217,72,15,.55)",width=1.1,dash="dot"),hovertemplate="布林上緣 %{y:.2f}<extra></extra>"),row=1,col=1)
    fig.add_trace(go.Scatter(x=x,y=df.BBL,mode="lines",line=dict(color="rgba(217,72,15,.55)",width=1.1,dash="dot"),fill='tonexty',fillcolor='rgba(217,72,15,.045)',hovertemplate="布林下緣 %{y:.2f}<extra></extra>"),row=1,col=1)
    ma_specs=[("MA5","#111318",1.35),("MA20","#0b57d0",1.8),("MA60","#f08c00",1.65),("MA120","#7b61ff",1.35),("MA240","#2f9e44",1.35)]
    for col,color,width in ma_specs:
        fig.add_trace(go.Scatter(x=x,y=df[col],mode="lines",line=dict(color=color,width=width),hovertemplate=f"{col.replace('MA','')}日 %{{y:.2f}}<extra></extra>"),row=1,col=1)
    colors=np.where(df.Close>=df.Open,"rgba(217,45,32,.62)","rgba(7,135,93,.62)")
    fig.add_trace(go.Bar(x=x,y=df.Volume,marker_color=colors,showlegend=False,hovertemplate="量 %{y:,.0f}<extra></extra>"),row=2,col=1)
    fig.add_trace(go.Scatter(x=x,y=df.KD_K,mode='lines',line=dict(color='#0b57d0',width=1.6),hovertemplate='K %{y:.1f}<extra></extra>'),row=3,col=1)
    fig.add_trace(go.Scatter(x=x,y=df.KD_D,mode='lines',line=dict(color='#f08c00',width=1.6),hovertemplate='D %{y:.1f}<extra></extra>'),row=3,col=1)
    fig.add_hline(y=80,line_color='rgba(120,120,120,.40)',line_width=1,line_dash='dot',row=3,col=1); fig.add_hline(y=20,line_color='rgba(120,120,120,.40)',line_width=1,line_dash='dot',row=3,col=1)
    macd_colors=np.where(df.MACD_HIST>=0,'rgba(217,45,32,.55)','rgba(7,135,93,.55)')
    fig.add_trace(go.Bar(x=x,y=df.MACD_HIST,marker_color=macd_colors,showlegend=False,hovertemplate='MACD柱 %{y:.3f}<extra></extra>'),row=4,col=1)
    fig.add_trace(go.Scatter(x=x,y=df.MACD,mode='lines',line=dict(color='#111318',width=1.4),hovertemplate='MACD %{y:.3f}<extra></extra>'),row=4,col=1)
    fig.add_trace(go.Scatter(x=x,y=df.MACD_SIGNAL,mode='lines',line=dict(color='#7b61ff',width=1.4),hovertemplate='訊號 %{y:.3f}<extra></extra>'),row=4,col=1)
    if plan:
        stop=finite(plan.get("invalidation")); trigger=finite(plan.get("trigger"))
        if trigger is not None: fig.add_hline(y=trigger,line_color="#8a5a00",line_width=1.1,line_dash="dot",row=1,col=1)
        if stop is not None: fig.add_hline(y=stop,line_color="#9b1c1c",line_width=1.1,line_dash="dash",row=1,col=1)
    vals=pd.concat([df["High"],df["Low"],df["MA20"],df["MA60"],df["MA120"],df["MA240"],df["BBU"],df["BBL"]]).dropna()
    if not vals.empty:
        lo,hi=float(vals.min()),float(vals.max()); pad=max((hi-lo)*.14,max(hi,1)*.010)
        fig.update_yaxes(range=[max(0,lo-pad),hi+pad],row=1,col=1)
    # Category x-axis removes weekend/holiday gaps, keeping candles almost touching.
    if x:
        ids=sorted(set(np.linspace(0,len(x)-1,min(4,len(x)),dtype=int).tolist()))
        tickvals=[x[i] for i in ids]; ticktext=[pd.to_datetime(x[i]).strftime("%m/%d") for i in ids]
        for r in [1,2,3,4]:
            fig.update_xaxes(type="category",tickmode="array",tickvals=tickvals,ticktext=ticktext,fixedrange=True,row=r,col=1)
    fig.update_layout(height=575,margin=dict(l=3,r=5,t=6,b=2),paper_bgcolor="#fffefb",plot_bgcolor="#fffefb",xaxis_rangeslider_visible=False,dragmode=False,hovermode="x unified",showlegend=False,font=dict(size=11,color="#4d5159"),bargap=.04)
    fig.update_yaxes(gridcolor="#ecebe5",zeroline=False,fixedrange=True,tickfont=dict(size=10))
    st.markdown("<div class='chartchips'><span>K線（日）</span><span><i class='chipline ma5'></i>週線5</span><span><i class='chipline ma20'></i>月線20</span><span><i class='chipline ma60'></i>季線60</span><span><i class='chipline ma120'></i>半年120</span><span><i class='chipline ma240'></i>年線240</span><span><i class='chipline bb'></i>布林</span></div><div class='ind-caption'>下方依序：量能、KD、MACD；交易日採等距顯示，週末不留大空隙。</div>",unsafe_allow_html=True)
    st.plotly_chart(fig,use_container_width=True,key=key,config={"displayModeBar":False,"scrollZoom":False,"displaylogo":False,"staticPlot":False})


def _history_line(stock: dict, h: str) -> str:
    f = stock.get("horizons", {}).get(h, {}).get("forecast", {}) or {}
    s = f.get("strategy") or {}
    if not f.get("estimate_available"):
        return "歷史案例不足"
    med = decpct(s.get("median"), 1)
    rate = finite(s.get("smoothed_positive_rate"))
    n = int(finite(f.get("local_effective_n")) or 0)
    return f"相似案例 {n} 次 · 中位數 {med} · 偏正向 {rate*100:.0f}%" if rate is not None else f"相似案例 {n} 次 · 中位數 {med}"


def _row(stock: dict, h: str, snap: dict, rank: int, live: bool = False):
    name = stock.get("name") or stock.get("ticker")
    ticker = stock.get("ticker") or ""
    fine = stock.get("fine_industry") or fine_industry(ticker, name, stock.get("industry", ""))
    role_label = ((stock.get("role") or {}).get("label") or "").strip()
    stage_label = ((stock.get("stage") or {}).get("label") or "").strip()
    tags = [f"<span class='tag'>{esc(fine)}</span>"] if fine else []
    if role_label:
        tags.append(f"<span class='tag blue'>{esc(role_label)}</span>")
    if stage_label:
        tags.append(f"<span class='tag red'>{esc(stage_label)}</span>")
    tag_html = "<div class='tagrow'>" + "".join(tags) + "</div>" if tags else ""

    block = stock.get("horizons", {}).get(h, {}) or {}
    if live:
        live_info = stock.get("intraday") or {}
        price = finite(live_info.get("price"))
        change = finite(live_info.get("change_rate_pct"))
        reasons = live_info.get("reasons") or []
        title = live_info.get("state") or "即時資料不足"
        risk = ""
        timing = live_info.get("entry_timing") or {}
        position = live_info.get("position_guidance") or block.get("position_guidance") or {}
        selection_score = finite(stock.get("live_ranking_score"))
        history = _history_line(stock, h)
    else:
        view = investment_view(stock, h, snap.get("market") or {})
        price = stock.get("price")
        change = None
        reasons = view.get("reasons") or []
        title = view.get("title") or "觀察"
        risk = view.get("risk") or ""
        timing = block.get("entry_timing") or {}
        position = block.get("position_guidance") or {}
        selection_score = finite(block.get("ranking_score"))
        history = _history_line(stock, h)

    cls = "flat"
    if change is not None:
        cls = "up" if change > 0 else "down" if change < 0 else "flat"
    change_text = pp(change, 2) if change is not None else esc(H_LABEL[h])
    reason_text = " · ".join(str(x) for x in reasons[:2])
    risk_text = f" · <span class='risk'>{esc(risk)}</span>" if risk else ""

    timing_html = ""
    entry_score = finite(timing.get("score"))
    if entry_score is not None:
        chase = str(timing.get("chase_risk") or "待確認")
        action = str(timing.get("action") or "等確認")
        action_cls = "entryok" if entry_score >= 70 and "不追" not in action and "暫不" not in action else "entryaction"
        idea_text = nfmt(selection_score, 1) if selection_score is not None else "—"
        overnight = "<span class='overnight'>隔夜風險↑</span>" if timing.get("overnight_risk") else ""
        timing_reasons = [str(x) for x in (timing.get("reasons") or [])[:2]]
        why_html = f"<div class='entrywhy'>{esc(' · '.join(timing_reasons))}</div>" if timing_reasons else ""
        pos_pct = position.get("percent")
        pos_chip = f"<span class='entrychip'>部位上限 <b>{int(pos_pct)}%</b></span>" if pos_pct is not None else ""
        timing_html = (
            "<div class='entryline'>"
            f"<span class='entrychip'>標的分數 <b>{esc(idea_text)}</b></span>"
            f"<span class='entrychip'>進場分數 <b>{entry_score:.0f}</b></span>"
            f"<span class='entrychip'>追價風險 <b>{esc(chase)}</b></span>"
            f"{pos_chip}<span class='{action_cls}'>{esc(action)}</span>{overnight}</div>{why_html}"
        )

    comp = block.get("score_components") or {}
    score_parts = []
    vp = stock.get("v18_profile") or {}
    rank18 = block.get("rank_v18") or {}
    if rank18:
        score_parts.append(f"<span class='scorechip' style='color:#6244a7;background:#f2eeff'>V18因子 <b>{nfmt(rank18.get('score_v18'),1)}</b></span>")
        score_parts.append(f"<span class='scorechip'>因子證據 <b>{int(vp.get('evidence_n') or 0)}/13</b></span>")
        score_parts.append(f"<span class='scorechip'>階段 <b>{esc(vp.get('stage') or '待確認')}</b></span>")
    for label, key in (("技術", "technical"), ("歷史", "empirical"), ("基本", "fundamental"), ("法人", "flow")):
        v = finite(comp.get(key))
        score_parts.append(f"<span class='scorechip'>{label} <b>{'—' if v is None else f'{v:.0f}'}</b></span>")
    cov = finite(comp.get("evidence_coverage"))
    if cov is not None:
        score_parts.append(f"<span class='scorechip'>證據完整度 <b>{cov*100:.0f}%</b></span>")
    component_html = "<div class='scoreline'>" + "".join(score_parts) + "</div>" if score_parts else ""

    # Dedicated decision layer: buy-now ranking during the session, or next-session
    # planning outside the session.  This is intentionally separate from the
    # underlying stock-selection score.
    decision = (stock.get("buy_opportunity") or stock.get("next_session") or {}) if live else (stock.get("next_session") or {})
    decision_html = ""
    if decision:
        zone = decision.get("buy_zone") or {}
        low = finite(zone.get("low")); high = finite(zone.get("high"))
        target = finite(decision.get("target_price"))
        exp_ret = finite(decision.get("expected_return"))
        rr = finite(decision.get("reward_risk"))
        confidence = finite(decision.get("forecast_confidence"))
        sample_n = int(finite(decision.get("sample_n")) or 0)
        dscore = finite(decision.get("score"))
        zone_text = f"{nfmt(low)}–{nfmt(high)}" if low is not None and high is not None else "—"
        target_text = nfmt(target) if target is not None else "—"
        exp_text = f"{exp_ret*100:+.1f}%" if exp_ret is not None else "—"
        rr_text = f"{rr:.2f}" if rr is not None else "—"
        conf_text = f"{confidence:.0f}/100 · n={sample_n}" if confidence is not None else f"n={sample_n}" if sample_n else "—"
        score_text = f"{dscore:.0f}" if dscore is not None else "—"
        action_text = str(decision.get("action") or decision.get("stance") or "")
        cls_dec = "buy" if bool(decision.get("eligible_now")) else "wait"
        decision_is_buy = bool(stock.get("buy_opportunity"))
        decision_label = "買入優先" if decision_is_buy else "明日關注"
        path = str(decision.get("entry_path") or "")
        path_label = {"PULLBACK":"拉回承接", "CONTINUATION":"強勢續攻", "PANIC_REVERSAL":"恐慌錯殺反轉", "WAIT_PULLBACK":"等拉回", "WAIT_BREAKOUT":"等突破"}.get(path, "")
        blockers = [str(x.get("label") or "") for x in (decision.get("failed_gates") or []) if isinstance(x, dict) and x.get("label")]
        blocker_html = f"<div class='decision-reasons'>未通過：{esc(' · '.join(blockers[:3]))}</div>" if blockers and not decision.get("eligible_now") else ""
        decision_html = (
            "<div class='decision'>"
            f"<div><small>{decision_label}分數</small><b>{esc(score_text)}</b></div>"
            f"<div><small>進場路徑</small><b>{esc(path_label or '—')}</b></div>"
            f"<div><small>建議買入區</small><b>{esc(zone_text)}</b></div>"
            f"<div><small>模型中位目標</small><b>{esc(target_text)}</b></div>"
            f"<div><small>保守剩餘報酬</small><b>{esc(exp_text)}</b></div>"
            f"<div><small>報酬/風險</small><b>{esc(rr_text)}</b></div>"
            f"<div><small>案例信心</small><b>{esc(conf_text)}</b></div>"
            f"<div class='decision-action {cls_dec}'>{esc(action_text)} · 目標與報酬為歷史模型估計，非保證值</div>"
            f"{blocker_html}"
            "</div>"
        )

    st.markdown(f"""
    <div class="rankrow">
      <div class="rankno">{rank:02d}</div>
      <div><div><span class="stockname">{esc(name)}</span><span class="stockcode">{esc(ticker)}</span></div>{tag_html}</div>
      <div class="quote"><div class="price">{nfmt(price)}</div><div class="change {cls}">{esc(change_text)}</div></div>
    </div>
    <div class="signal"><div class="signal-title">{esc(title)}</div><div class="signal-copy">{esc(reason_text)}{risk_text}<div class="mini-note">{esc(history)}</div></div></div>
    {timing_html}
    {component_html}
    {decision_html}
    {_quick_html(stock)}
    """, unsafe_allow_html=True)

    label = f"{name}｜數據與走勢"
    with st.expander(label, expanded=False):
        _render_research(stock, f"{'live' if live else h}_{rank}_{snap.get('snapshot_id','x')}")
        block = stock.get("horizons", {}).get(h, {}) or {}
        plan = block.get("plan") or {}
        if plan:
            pos = position or block.get("position_guidance") or {}
            pos_label = pos.get("label") or "依風險調整"
            st.markdown(f"""<div class='plan'>
              <div><small>較理想區間</small><b>{nfmt(plan.get('zone_low'))}–{nfmt(plan.get('zone_high'))}</b></div>
              <div><small>轉強確認</small><b>{nfmt(plan.get('trigger'))}</b></div>
              <div><small>不追高超過</small><b>{nfmt(plan.get('chase_limit'))}</b></div>
              <div><small>跌破重看</small><b>{nfmt(plan.get('invalidation'))}</b></div>
              <div><small>目前部位節奏</small><b>{esc(pos_label)}</b></div>
            </div>""", unsafe_allow_html=True)
        if st.toggle("顯示 K 線與技術指標", value=False, key=f"chart_toggle_{'live' if live else h}_{ticker}_{snap.get('snapshot_id','x')}"):
            chart = None
            try:
                chart = service.chart_on_demand(snap, ticker, DATA_DIR, allow_fetch=False)
            except Exception:
                pass
            render_chart(chart, plan, f"chart_{'live' if live else h}_{ticker}_{snap.get('snapshot_id','x')}")


def _render_daily_list(snap: dict | None, h: str, n: int = 5):
    if not snap:
        st.info("請先更新市場資料。")
        return
    picks = service.select_market_best(snap, h, n=n)
    if not picks:
        st.info("目前沒有可排序標的。")
        return
    for i, stock in enumerate(picks, 1):
        _row(stock, h, snap, i, live=False)



def _quote_html(row: dict, *, key: str) -> str:
    """Responsive tape tile with explicit quote date and no fake 0%/price."""
    labels = {
        "dji": "道瓊工業", "spx": "標普500", "nasdaq": "那斯達克綜合", "ndx": "那斯達克100",
        "sox": "費城半導體", "rut": "羅素2000", "tsm_adr": "台積電ADR",
        "nvda": "輝達 NVIDIA", "amd": "超微 AMD", "avgo":"博通", "mrvl":"邁威爾", "qcom":"高通",
        "mu":"美光記憶體", "asml":"艾司摩爾", "amat":"應用材料",
        "lrcx":"科林研發", "klac":"科磊", "dell":"戴爾",
        "vrt":"維諦", "anet":"Arista網通", "smci":"美超微", "vix": "VIX恐慌指數",
        "nq_fut": "那指100期貨", "es_fut": "標普500期貨",
        "tnx": "10年債殖利率指標", "dxy": "美元指數",
    }
    price = finite(row.get("close"))
    diff = finite(row.get("change"))
    pct = finite(row.get("pct"))
    tag = str(row.get("date") or "日期未明")
    if pct is None or price is None:
        return f"<div class='tape-tile muted'><div class='tape-name'>{esc(labels.get(key,key))}</div><div class='tape-value'>待取得</div><div class='tape-note'>—</div></div>"
    change = f"{diff:+,.2f}" if diff is not None else "—"
    cls = "positive" if pct > 0 else "negative" if pct < 0 else "neutral"
    return (
        f"<div class='tape-tile {cls}'><div class='tape-name'>{esc(labels.get(key,key))}</div>"
        f"<div class='tape-value'>{price:,.2f}</div>"
        f"<div class='tape-change'>{change} <b>{pct:+.2f}%</b></div>"
        f"<div class='tape-note'>交易日 {esc(tag)}" +
        (" · 個別補抓" if "補抓" in str(row.get('source') or '') or "重試" in str(row.get('source') or '') else "") +
        "</div></div>"
    )


def _render_plain_global_context(gctx: dict | None):
    g = gctx or {}
    summary = human_global_summary(g)
    tape = {str(r.get("key")): r for r in (g.get("market_tape") or []) if isinstance(r, dict)}
    st.markdown("<div class='section-kicker'>WALL STREET CLOSE</div><div class='section-title'>美股最近一個完整交易日</div>", unsafe_allow_html=True)
    if not tape:
        st.warning("美股指數點位尚未取得。這次不以漲跌幅推算收盤點數；可先閱讀上次快照或新聞，行情由背景更新補齊。")
        st.markdown("<div class='tape-group-title'>主要美股指數</div><div class='tape-grid'>"+
                    ''.join(_quote_html({'key':k},key=k) for k in ['dji','spx','nasdaq','ndx','sox','rut'])+
                    '</div>',unsafe_allow_html=True)
        factors = summary.get("factors") or []
        if factors:
            st.markdown("<div class='briefchips'>" + "".join(
                f"<span class='briefchip'>{esc(x.get('name'))} {float(x.get('value_pct')):+.2f}%（僅漲跌幅）</span>"
                for x in factors if x.get("value_pct") is not None
            ) + "</div>", unsafe_allow_html=True)
    else:
        cash = ["dji", "spx", "nasdaq", "ndx", "sox", "rut"]
        macro = ["vix", "tnx", "dxy", "nq_fut", "es_fut"]
        # Indispensable index tiles stay visible even on partial provider failure.
        st.markdown("<div class='tape-group-title'>主要美股指數｜含費城半導體</div><div class='tape-grid'>" +
                    "".join(_quote_html(tape.get(k, {"key":k}), key=k) for k in cash) + "</div>", unsafe_allow_html=True)
        if 'sox' not in tape:
            st.caption('費城半導體 (^SOX) 本次來源缺值；已安排單一指數備援補抓。無法核對時顯示「待取得」，不會以 ETF 價格代替費半指數。')
        first = ['tsm_adr','nvda','amd','avgo','mu','asml']
        tech_all = [key for group in US_WATCH_GROUPS.values() for key,_ in group]
        n_available = sum(k in tape for k in tech_all)
        st.markdown("<div class='tape-group-title'>美股科技供應鏈｜台股連動觀察</div>"+
                    "<div class='tape-grid'>"+''.join(_quote_html(tape.get(k, {'key':k}), key=k) for k in first)+"</div>",unsafe_allow_html=True)
        st.caption(f'已取得 {n_available}/{len(tech_all)} 檔美股科技鏈觀察行情。這是相關公司觀測名單，不代表費半指數正式成分股。')
        # Native <details> keeps mobile panels expandable without a new
        # Streamlit radio/sidebar state or any additional data requests.
        detail_parts=["<details class='watch-details'><summary>查看全部科技供應鏈｜晶片、設備、伺服器與網通</summary>"]
        for group,members in US_WATCH_GROUPS.items():
            detail_parts.append(f"<div class='tape-group-title'>{esc(group)}</div>"+
                                "<div class='tape-grid'>"+''.join(_quote_html(tape.get(k,{'key':k}),key=k) for k,_ in members)+"</div>")
        detail_parts.append('</details>')
        st.markdown(''.join(detail_parts),unsafe_allow_html=True)
        st.markdown("<div class='tape-group-title'>風險、利率及期貨參考</div><div class='tape-grid'>"+
                    "".join(_quote_html(tape.get(k,{'key':k}),key=k) for k in macro)+"</div>",unsafe_allow_html=True)
        latest = str(g.get("asof") or "個別商品交易日見卡片")
        stamp = str(g.get("fetched_at") or "")[:19].replace("T", " ")
        stale = " · 已使用前次快取，可能延遲" if g.get("stale") else ""
        st.caption(f"資料：Yahoo Finance／yfinance，可能延遲｜美股主要收盤參考日 {latest}｜取得時間 {stamp or '未標示'}{stale}。美股與台灣的紅漲綠跌配色一致；期貨與現貨交易時段不同，不能當作同一天收盤。")

    st.markdown("<div class='section-kicker'>WHAT THE NUMBERS MEAN</div><div class='section-title'>重要數據解讀</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='market-takeaway'><span>國際風險觀察</span><strong>{esc(summary.get('headline') or '')}</strong></div>", unsafe_allow_html=True)
    insights = market_tape_insights(g)
    if insights:
        st.markdown("<div class='insight-grid'>" + "".join(
            f"<article class='insight-card {esc(x.get('tone'))}'><b>{esc(x.get('title'))}</b><p>{esc(x.get('detail'))}</p></article>"
            for x in insights
        ) + "</div>", unsafe_allow_html=True)
    else:
        st.caption("仍待取得足夠的指數點位才能解讀科技股與大盤的相對強弱；不會以缺漏資料填補零變動。")
    st.caption("上述國際資料僅協助判斷市場風險與進場條件，不改台股短中長線的標的品質排名。")


def _render_taiwan_supply_chain(snap: dict | None):
    """Local official-completed-day watch; no extra network and no invented quotes."""
    st.markdown("<div class='section-kicker'>TAIWAN SUPPLY CHAIN</div>"
                "<div class='section-title'>台灣科技供應鏈代表股｜六大環節</div>",unsafe_allow_html=True)
    if not snap:
        st.caption('背景日線完成後可查看台灣上市櫃代表股；不會使用過期或不明來源的報價補數字。')
        return
    watch = taiwan_watch_snapshot(snap)
    total=sum(len(v) for v in watch.values())
    st.caption(f'預設觀察清單收錄 {sum(map(len,TW_WATCH_GROUPS.values()))} 個重要產業代號，'
               f'本次符合完整日線模型者 {total} 檔；已排除不在當前可投資母體的股票。這是產業觀察，不是自動買入推薦。')
    with st.expander('展開台灣科技鏈｜分類、代表股與完成日線價格',expanded=False):
        for group,rows in watch.items():
            st.markdown(f'**{group}** · {len(rows)} 檔')
            if rows:
                st.dataframe(pd.DataFrame([{'代號':r['ticker'].split('.')[0],
                    '股票':r['name'], '細產業定位':r['category'],
                    '日線參考價':r['price'], '模型日':r['date'],
                    '短線研究分':round(r['score_short'],1) if r['score_short'] is not None else None}
                    for r in rows]),hide_index=True,use_container_width=True)
            else:
                st.caption('本次沒有同日、通過流動性條件的代表股。')


def _render_score_guide():
    """Visible at the START of the shortlist instead of buried under factors."""
    st.markdown("<div class='light-guide'><b>分數與燈號怎麼看？</b>　"
                "<span>🟢 Alpha ≥75：標的值得研究</span>"
                "<span>🟢 DQ ≥75：資料較充分</span>"
                "<span>🟢 MC ≥75：證據較穩健</span>"
                "<small>55–74（Alpha為60–74）黃燈；低於門檻紅燈；無資料白燈。"
                "<strong>三燈都綠，也不等於現在能買；買點另看盤中官方核價。</strong></small></div>",
                unsafe_allow_html=True)


def _mini_traffic(value: object, kind: str, title: str) -> str:
    state=signal_light(value,kind)
    val='—' if state['score'] is None else f"{state['score']:.0f}"
    return (f"<span class='traffic traffic-{state['color']}' aria-label='{esc(title)} {esc(val)} {esc(state['label'])}'>"
            f"{state['icon']} <b>{esc(title)} {val}</b><small>{esc(state['label'])}</small></span>")


@st.cache_data(ttl=180,show_spinner=False)
def _cached_readiness_picks(snapshot_id: str,horizon: str,top_n: int,_snap: dict,
                            global_stamp: str='',_global_context: dict | None=None) -> list[dict]:
    """Stable completed-day shortlist shared across phone reruns/switches."""
    return prioritize_next_session(_snap,horizon=horizon,n=top_n,global_context=_global_context)


def _render_waiting_briefing(brief: dict | None):
    brief = brief or {}
    now = _taipei_timestamp()
    fetching = bool(brief.get("refreshing"))
    state = "背景整理中｜先顯示最近快取" if fetching else "市場資料即時整理｜無需等待完整選股更新"
    st.markdown(
        f"<div class='reading-intro'><div class='reading-kicker'>更新等待閱讀區 <span>•</span> MARKET JOURNAL</div>"
        f"<h2>全球市場觀察室</h2><p>{esc(state)}。各來源附交易日，未取得的行情不會自行補數字。</p></div>",
        unsafe_allow_html=True,
    )
    _render_plain_global_context(brief.get("global") or {})
    news = brief.get("news") or {}
    items = news.get("items") or []
    st.markdown("<div class='section-kicker'>TAIWAN IMPACT WATCH</div><div class='section-title'>可能影響台股的最新重要消息</div>", unsafe_allow_html=True)
    if items:
        st.caption("直接閱讀事件標題、RSS提供的摘要與可能影響；不需離開畫面。判讀是依標題／公開摘要作條件推論，並非已讀取或核實新聞全文。新聞以近 96 小時為限。")
        for index, row in enumerate(items[:12]):
            headline = str(row.get("title") or "")
            summary = str(row.get("summary") or "").strip()
            if headline and summary and headline in summary:
                summary = summary.replace(headline, "").strip(" ·-:：")
            impact = str(row.get("impact") or "").strip()
            bias = str(row.get("bias") or "待判讀")
            tint = "positive" if bias == "可能偏多" else "negative" if bias == "可能偏空" else "neutral"
            link = str(row.get("link") or "")
            url = f"<a href='{esc(link)}' target='_blank' rel='noopener noreferrer'>閱讀原文 ↗</a>" if link.startswith(("https://", "http://")) else ""
            intro = f"<p class='news-excerpt'><b>公開摘要：</b>{esc(summary)}</p>" if summary else "<p class='news-excerpt'><b>內容說明：</b>來源只提供標題，以下為條件式市場觀察，非完整新聞內文。</p>"
            st.markdown(
                f"<article class='news-story {tint}'><header><span class='news-index'>{index+1:02d}</span>"
                f"<span class='news-topic'>{esc(row.get('topic') or '市場')}</span><span class='news-tone'>{esc(bias)}</span></header>"
                f"<h4>{esc(headline)}</h4>{intro}<p class='news-impact'><b>台股關注：</b>{esc(impact)}</p>"
                f"<footer>{esc(row.get('source') or '來源未註明')} · {esc(row.get('published') or '日期未標示')}{url}</footer></article>",
                unsafe_allow_html=True,
            )
    else:
        st.info("暫無符合最近 96 小時且附有發布時間的新聞，或來源暫時不可用；不會把過期消息當成今日新聞。")
    prev = brief.get("previous") or {}
    if prev.get("date"):
        with st.expander("上次完整模型摘要（更新期間可先參考）", expanded=False):
            rows = []
            for h in ("short", "mid", "long"):
                rows.append(f"<div class='prevline'><b>{esc(H_LABEL[h])}</b><span>{esc('、'.join(prev.get(h) or []) or '—')}</span></div>")
            st.markdown(f"<div class='prevmodel'><small>基準日 {esc(prev['date'])}</small>{''.join(rows)}</div>", unsafe_allow_html=True)


def _market_header(snap: dict | None):
    """One compact provenance badge; detailed audit lives with the market tape."""
    phase = session_reference_policy(_taipei_timestamp())
    if not snap:
        st.markdown(
            f"<div class='marketbar'><span><b>{esc(phase.get('label'))}</b></span><span>尚未建立完整日線模型</span></div>",
            unsafe_allow_html=True,
        )
        return
    report = assess_snapshot(snap)
    model_day = report['dates'].get('統一模型日') or '未核對'
    m = snap.get('market') or {}
    cov = snap.get('coverage') or {}
    mother, model = int(cov.get('requested') or 0), int(snap.get('full_universe_model_n') or snap.get('candidate_n') or 0)
    stamp = str(snap.get('generated_at') or '').replace('T',' ')[:16]
    cls = {'ALIGNED':'health-green','ROLLBACK':'health-amber','UNVERIFIED':'health-amber',
           'INVALID':'health-red','NO_DATA':'health-amber'}.get(report['sync_state'],'health-amber')
    parts = [f"<span><b>{esc(phase.get('label'))} · 日線模型日 {esc(model_day)}</b></span>",
             f"<span class='{cls}'>{esc(report['sync_label'])}</span>",
             f"<span>{esc(REGIME.get(m.get('regime'),'大盤待確認'))}</span>"]
    if mother:
        parts.append(f"<span>普通股母體 {mother:,} · 可投資模型 {model:,}</span>")
    if stamp:
        parts.append(f"<span>更新於 {esc(stamp)}</span>")
    st.markdown("<div class='marketbar marketbar-compact'>"+"".join(parts)+"</div>",unsafe_allow_html=True)
    if report['status'] == 'BLOCK':
        st.error('資料未通過來源日期核對；僅顯示已保存研究資料，暫停買賣判定。可由今日決策下方查詢。')


def _render_data_health(snap: dict | None, *, compact: bool = False):
    """Stable, read-only diagnostic: never fetches prices nor changes rankings."""
    report = assess_snapshot(snap)
    if compact:
        state=report['sync_state']
        color={'ALIGNED':'#1b805a','ROLLBACK':'#a77330','UNVERIFIED':'#a77330','INVALID':'#a83e3b'}.get(state,'#687586')
        dates=report.get('dates') or {}
        st.markdown("<div class='data-compact'>" +
            f"<b style='color:{color}'>{esc(report.get('sync_label') or '資料待確認')}</b>" +
            "<div class='dates'>" + ''.join(f"<span>{esc(label)} <b>{esc(dates.get(key) or '待核對')}</b></span>" for key,label in
                [('上市來源','上市'),('上櫃來源','上櫃'),('統一模型日','模型')]) + "</div></div>",unsafe_allow_html=True)
        metrics=report.get('metrics') or {}
        st.caption('普通股母體 '+str(metrics.get('普通股母體') or '—')+' 檔｜入模 '+str(metrics.get('可投資模型') or '—')+
                   ' 檔；資料日期核對不等於財報與所有因子都完整。')
        if state != 'ALIGNED':
            st.warning('官方來源未完全核對，不提供即時買進判定。')
        with st.expander('資料來源、回補及排除原因',expanded=False):
            for issue in (report.get('issues') or [])[:8]:st.caption(str(issue))
            cov=(snap or {}).get('coverage') or {}
            for name,key in [('歷史不足','history_insufficient'),('流動性門檻','below_turnover_excluded'),('交易日落後','stale_excluded')]:
                if cov.get(key):st.caption(f'{name}：{int(cov[key]):,} 檔')
            off=cov.get('official_eod') or {}
            st.caption('官方來源：'+str(off.get('twse_source') or '待核')+'／'+str(off.get('tpex_source') or '待核'))
        return
    st.markdown("<div class='section-kicker'>DATA HEALTH</div><div class='section-title'>資料完整度與同步日期</div>", unsafe_allow_html=True)
    state = report['sync_state']
    if state == 'ALIGNED' and report['status'] == 'OK':
        st.success('來源與模型日已同步。這是快照的一致性檢查，並非即時行情或財報完整性的保證。')
    elif state == 'ROLLBACK':
        st.warning('上市與上櫃來源日期不同，模型已統一回退到較早的共同日線；這是保守措施，不是跨日混算。稍後可再背景更新。')
    elif state == 'UNVERIFIED':
        st.warning('部分官方來源日期未取得，僅能查看前次日線；不能宣稱兩市場已同步。請檢查來源並重新更新。')
    elif state == 'INVALID':
        st.error('同步檢查未通過。停止將目前快照視為可直接操作的最新訊號；背景更新會保留上一份有效快照。')
    else:
        st.info(report['sync_label'])
    st.caption('只有兩市場官方來源日期均核對、且與模型日一致，才稱為「同步完整基準」；回退日或舊快照不會冒充最新。')
    dates = report.get('dates') or {}
    if dates:
        cols = st.columns(3)
        for col, k in zip(cols, ('上市來源','上櫃來源','統一模型日')):
            col.metric(k, dates.get(k,'未驗證'))
    metrics=report.get('metrics') or {}
    rates=report.get('rates') or {}
    if metrics:
        cols = st.columns(3)
        main = [('普通股母體', metrics.get('普通股母體') or 0),
                ('歷史足夠', metrics.get('歷史有效') or 0),
                ('進入模型', metrics.get('可投資模型') or 0)]
        for col, (label, n) in zip(cols, main):
            col.metric(label, f'{n:,} 檔')
        history = rates.get('history_pct'); invest = rates.get('investable_pct')
        st.caption('涵蓋率｜' +
                   f"歷史足夠 {history:.1f}%" if history is not None else '涵蓋率｜歷史資料待確認')
        if invest is not None:
            st.caption(f'可投資模型占普通股母體 {invest:.1f}%（其餘可能因歷史不足、流動性、價格或資料日期而排除；不等於失敗率）')
        b = rates.get('research_pct')
        st.caption('公司／法人可比資料：' + (f"{metrics.get('公司／法人可比資料',0):,} 檔／已入模 {metrics.get('可投資模型',0):,} 檔（{b:.1f}%）" if b is not None else '待確認') + '；DQ／MC 是每檔個股的證據品質，不是全市場行情下載率。')
    st.caption('快照完成時間：'+(report.get('generated_at') or '無紀錄')+'｜基準日不是更新時間；休市期間沿用最近完整交易日屬正常。')
    for issue in report.get('issues') or []:
        st.markdown('• '+esc(issue))
    for note in report.get('notes') or []:
        st.caption(note)
    with st.expander('查詢上市上櫃來源、排除原因與錯誤明細', expanded=False):
        source = report.get('market_source') or {}
        st.caption(f"上市來源：{source.get('twse','—')}｜上櫃來源：{source.get('tpex','—')}")
        for key in ('上市母體','上櫃母體','排除過期資料'):
            if key in metrics:
                st.caption(f'{key}：{metrics[key]:,} 檔')
        cov=(snap or {}).get('coverage') or {}
        core = cov.get('model_core') or {}
        refresh = cov.get('history_refresh') or {}
        if core:
            st.caption('核心重用：同日沿用 ' + str(int(core.get('reused_same_date') or 0))
                       + ' 檔，新增評估 ' + str(int(core.get('newly_computed') or 0)) + ' 檔')
        if refresh:
            st.caption('歷史行情下載：' + str(refresh.get('mode') or '未記錄')
                       + '／請求 ' + str(int(refresh.get('requested') or 0))
                       + ' 檔／已寫入 ' + str(int(refresh.get('downloaded_tickers') or 0))
                       + ' 檔（最新完整交易日仍以官方檢查為準）')
        for k, title in (('history_insufficient','歷史不足'),('below_price_excluded','價格門檻'),
                          ('below_turnover_excluded','流動性門檻'),('stale_excluded','個股交易日落後')):
            if cov.get(k):
                st.caption(f'{title}排除：{int(cov[k]):,} 檔')
        if report.get('stock_date_counts'):
            st.caption('入模個股實際價格日分布：'+str(report['stock_date_counts']))
        errs=report.get('source_errors') or []
        if errs:
            for error in errs[:15]:
                st.caption(str(error)[:300])
        else:
            st.caption('無留存的擷取錯誤紀錄。')
    st.caption('如日期顯示「未驗證」，請勿反覆清除全部歷史快取；先檢查資料來源並按背景更新即可。')


def _render_evidence_coverage(snap: dict | None):
    """Explain partial/absent fields without retrying expensive data providers."""
    from v18_multifactor import FACTOR_GROUPS
    stocks=[s for s in ((snap or {}).get('stocks') or []) if isinstance(s,dict)]
    if not stocks:
        return
    n=len(stocks)
    counts={}
    for group,fields in FACTOR_GROUPS.items():
        if group=='risk':
            continue
        for key,label in fields:
            count=0; verified=0
            for s in stocks:
                f=((s.get('v18_profile') or {}).get('factors') or {}).get(key) or {}
                if finite(f.get('score')) is not None:
                    count+=1
                    if str(f.get('status') or '') in ('VERIFIED','PARTIAL','OBSERVED_ONLY') and finite(f.get('reliability')):
                        verified+=1
            counts[label]=(count,verified)
    with st.expander('為何有些因子顯示缺資料？｜查看 13 項資料可用率',expanded=False):
        st.caption('以下為完整日線模型實際存下的證據狀態，不會重新逐檔連線或把缺值填成利多。'
                   '「有數值」與「有可靠時序證據」不同，仍須逐因子核對 PIT；可用率低不代表公司表現差。')
        st.dataframe(pd.DataFrame([{'因子':name,'有數值':f'{obs:,}/{n:,}',
                                   '有時序佐證':f'{ver:,}/{n:,}',
                                   '數值覆蓋率':f'{100*obs/n:.0f}%'}
                                  for name,(obs,ver) in counts.items()]),
                     hide_index=True,use_container_width=True)
        st.caption('集保持股、融資週變化及現金流若來源無完整歷史／無授權，不會捏造資料。'
                   '技術類通常較完整，財報季別與股權資料需等公告或可用來源；正式推薦應同時看 DQ／MC。')


def _realtime_batch(snap: dict):
    """Fetch the liquid full-market quote universe, with a stable-core fallback."""
    tickers = full_market_tickers(snap, max_total=int(LIVE_SCAN.get("full_market_quote_max", 1800)))
    if not tickers:
        return [], pd.DataFrame(), None
    df, status = _cached_realtime_snapshot(str(snap.get("snapshot_id") or ""), tuple(tickers))
    if status is not None and status.available and not df.empty:
        return tickers, df, status
    # Provider limits/rate errors on a very large batch should not disable the
    # entire execution layer. Fall back to the completed-day core for this tick.
    core = candidate_tickers(
        snap,
        per_horizon=int(LIVE_SCAN.get("per_horizon", 80)),
        max_total=int(LIVE_SCAN.get("daily_core_max", 220)),
    )
    if core and core != tickers:
        df2, status2 = _cached_realtime_snapshot(str(snap.get("snapshot_id") or "") + "_core", tuple(core))
        if status2 is not None and status2.available and not df2.empty:
            return core, df2, status2
    return tickers, df, status


def _intraday_buy_render_once(snap: dict, horizon: str = "short", top_n: int = 6):
    now = _taipei_timestamp()
    if horizon not in {"short", "mid", "long"}:
        horizon = "short"
    if not market_is_open(now):
        st.markdown(
            f"<div class='offflag'>{esc(session_reference_policy(now).get('label'))} · {esc(now.strftime('%Y-%m-%d %H:%M:%S'))} · 盤中買點僅於 09:00–13:30 啟用</div>",
            unsafe_allow_html=True,
        )
        st.info("目前不是交易時段；改看『明日關注』，不要用昨日收盤報價冒充即時買點。")
        return

    tickers, df, status = _realtime_batch(snap)
    if status is None or not status.available or df.empty:
        reason = getattr(status, "reason", "NO_REALTIME") if status is not None else "NO_REALTIME"
        st.warning(f"盤中即時資料暫不可用：{reason}。完整日線排名仍可在短／中／長線頁查看。")
        return

    global_ctx = _cached_global_context()
    dynamic = dynamic_candidate_pool(snap, df, now=now, global_context=global_ctx)
    detail_tickers = dynamic.get("detail_tickers") or []
    # Price correctness is stricter than discovery. The broad one-minute pass may
    # use the fastest provider, but every stock that can produce a BUY/SELL/HOLD
    # decision is re-quoted from official TWSE/TPEx MIS before execution scoring.
    verified_df, execution_status = _cached_verified_execution_snapshot(
        str(snap.get("snapshot_id") or "") + "_exec", tuple(detail_tickers)
    ) if detail_tickers else (pd.DataFrame(), None)
    if execution_status is None or not execution_status.available or not getattr(execution_status, "verified", False) or verified_df.empty:
        reason = getattr(execution_status, "reason", "NO_VERIFIED_REALTIME") if execution_status is not None else "NO_VERIFIED_REALTIME"
        st.warning(f"已完成全市場異動掃描，但官方即時價驗證失敗（{reason}）；為避免用昨收或舊報價冒充盤中價，本輪不產生買入訊號。")
        return
    detail_df = verified_df
    # Only the bounded, official-price-verified detail pool receives the full
    # entry/forecast execution evaluation.
    live_pool = rerank_snapshot(snap, horizon, detail_df, top_n=max(len(detail_tickers), top_n), now=now)
    ranked_all = rank_intraday_buys(live_pool, horizon=horizon, n=max(1, len(live_pool)), global_context=global_ctx) if live_pool else []
    picks = ranked_all[:top_n]
    summary = summarize_buy_gate_failures(ranked_all, top_n=int(LIVE_SCAN.get("gate_summary_top", 6)))
    twii = verified_df[verified_df["stock_id"].astype(str) == "001"] if "stock_id" in verified_df.columns else pd.DataFrame()
    idx_change = finite(twii.iloc[-1].get("change_rate")) if not twii.empty else None
    fetched = (execution_status.fetched_at or now.isoformat(timespec="seconds")).replace("T", " ")[:19]
    stats = dynamic.get("stats") or {}
    eligible_total = int(summary.get("eligible") or 0)
    shock = finite(global_ctx.get("shock_score")) if global_ctx.get("available") and not global_ctx.get("stale") else None
    shock_html = f"<span>隔夜壓力 <b>{shock:.0f}/100</b></span>" if shock is not None else ""
    st.markdown(
        f"<div class='marketbar'><span class='liveflag'><i class='dot'></i>1 分鐘全市場雷達</span>"
        f"<span>{esc(fetched)}</span><span>執行價 {esc(execution_status.source)}（官方驗證）</span>"
        f"<span>即時報價 {int(stats.get('quoted_n') or len(tickers))} 檔</span>"
        f"<span>動態精算 {int(stats.get('detail_n') or len(detail_tickers))} 檔</span>"
        f"<span>錯殺韌性候選 {int(stats.get('panic_discovery_n') or 0)} 檔</span>"
        f"<span>可買 {eligible_total} 檔</span><span>加權 {esc(pp(idx_change,2))}</span>{shock_html}"
        f"<span>{esc(H_LABEL[horizon])}</span></div>",
        unsafe_allow_html=True,
    )
    if bool(global_ctx.get("panic_mode")) and not bool(global_ctx.get("stale")):
        st.warning("隔夜外部壓力偏高：已啟用『恐慌錯殺反轉』掃描。大跌本身不是買點，只有仍守住模型防守、相對大盤有韌性、從日低回拉且報酬/風險通過的股票，才允許小量試單。")
    if int(stats.get("discovery_only_n") or 0) > 0:
        st.caption(f"另發現 {int(stats.get('discovery_only_n'))} 檔盤中新異動尚未進入完整日線模型；只列為發現，不會在缺乏歷史證據時直接升格為買進推薦。")
    if not picks:
        st.info("即時資料已取得，但目前沒有足夠資料形成可比較買點。")
        return
    if eligible_total == 0:
        st.markdown(
            "<div class='buyempty'><b>目前沒有符合『可買』門檻的標的。</b><br>系統仍列出最接近條件的候選；現在會同時顯示每檔真正卡住的門檻，不再把所有情況都寫成『等拉回』。</div>",
            unsafe_allow_html=True,
        )
    else:
        st.caption(f"動態精算池共有 {eligible_total} 檔通過可執行門檻；目前支援拉回承接、強勢續攻，以及廣泛殺盤時才啟用的恐慌錯殺反轉。")
    primary = summary.get("primary") or []
    if primary:
        chips = "".join(f"<span class='gatechip'>{esc(x.get('label'))} {int(x.get('count') or 0)} 檔</span>" for x in primary)
        st.markdown(f"<div class='gatebar'>{chips}</div>", unsafe_allow_html=True)
    for i, stock in enumerate(picks, 1):
        _row(stock, horizon, snap, i, live=True)


if hasattr(st, "fragment"):
    @st.fragment(run_every=int(LIVE_SCAN.get("refresh_seconds", 60)))
    def intraday_buy_fragment(snap: dict, horizon: str = "short"):
        _intraday_buy_render_once(snap, horizon)
else:
    def intraday_buy_fragment(snap: dict, horizon: str = "short"):
        _intraday_buy_render_once(snap, horizon)


def _postclose_preview(snap: dict, horizon: str, n: int = 5):
    """Use today's final quote snapshot only as a labelled provisional preview."""
    now = _taipei_timestamp()
    policy = session_reference_policy(now)
    sess = snap.get("session") or {}
    if policy.get("phase") != POSTCLOSE or sess.get("official_today_complete"):
        return None, None
    tickers, df, status = _realtime_batch(snap)
    if status is None or not status.available or df.empty:
        return None, status
    quote_date = str(getattr(status, "quote_date", "") or "")
    if quote_date != str(policy.get("today") or ""):
        return None, status
    live_pool = rerank_snapshot(snap, horizon, df, top_n=max(len(tickers), n), now=now)
    return rank_next_session_live(live_pool, horizon=horizon, n=n, global_context=_cached_global_context()), status


def _render_next_session(snap: dict | None, horizon: str = "short", n: int = 5):
    if not snap:
        st.info("請先更新市場資料。")
        return
    if horizon not in {"short", "mid", "long"}:
        horizon = "short"
    preview, status = _postclose_preview(snap, horizon, n=n)
    if preview:
        fetched = (getattr(status, "fetched_at", "") or "").replace("T", " ")[:19]
        source = str(getattr(status, "source", "") or "暫行行情")
        st.warning(f"今日官方完整收盤資料尚未齊備；以下為 {fetched or '收盤後'} 的『暫行』明日清單（行情來源：{source}）。這不是盤中執行價；官方 EOD 齊備後再按更新，會完整重跑短中長線模型。")
        picks = preview
    else:
        gctx = _cached_global_context()
        picks = _cached_readiness_picks(str(snap.get('snapshot_id') or snap.get('price_date') or ''),
                                        horizon,n,snap, str(gctx.get('fetched_at') or ''),gctx)
        sess = snap.get("session") or {}
        if sess.get("analysis_mode") == "POSTCLOSE_FINAL":
            st.caption("已使用今日正式收盤資料，供下一交易日盤前規劃。")
        else:
            st.caption(f"以最近完整交易日 {snap.get('price_date') or '—'} 規劃下一交易日；建議買入區是條件區間，不代表開盤直接追價。")
    if not picks:
        st.info("目前沒有可產生下一交易日清單的標的。")
        return
    for i, stock in enumerate(picks, 1):
        # Post-close preview retains intraday fields to show today's final quote;
        # the attached `next_session` object remains the decision layer.
        _row(stock, horizon, snap, i, live=bool(preview))


def _overview(snap: dict | None):
    """Completed-day strategic overview. Realtime execution is rendered separately."""
    if not snap:
        st.info("請先更新市場資料。")
        return

    themes = snap.get("theme_leadership") or {}
    sectors = snap.get("sector_leadership") or {}
    pills = []
    source_map = themes if themes else sectors
    for industry, info in list(source_map.items())[:7]:
        score = finite((info or {}).get("score")) or 50.0
        cls = "hot" if score >= 68 else "weak" if score < 40 else ""
        pills.append(f"<span class='sectorpill {cls}'>{esc(industry)} <b>{score:.0f}</b></span>")
    if pills:
        st.markdown("<div class='section-kicker'>MARKET MAINLINE</div><div class='sectorbar'>" + "".join(pills) + "</div>", unsafe_allow_html=True)

    mapping = [("short", "短線 · 約2週"), ("mid", "中線 · 約2個月"), ("long", "長線 · 約6個月")]
    raw = {h: service.select_market_best(snap, h, n=5) for h, _ in mapping}
    cols_html = []
    dup_counts = {}
    for h, title in mapping:
        picks = raw.get(h, [])[:5]
        body = []
        for i, stock in enumerate(picks, 1):
            code = str(stock.get("ticker") or "")
            dup_counts[code] = dup_counts.get(code, 0) + 1
            name = str(stock.get("name") or code)
            fine = str(stock.get("fine_industry") or stock.get("industry") or "")
            block = (stock.get("horizons", {}) or {}).get(h, {}) or {}
            score = finite(block.get("ranking_score"))
            timing = block.get("entry_timing") or {}
            entry_score = finite(timing.get("score"))
            action = str(timing.get("action") or "")
            meta = f"{fine} · 標的 {nfmt(score,1)}"
            if entry_score is not None:
                meta += f" · 買點 {entry_score:.0f}"
            if "不追" in action or "暫不" in action:
                meta += " · 不追"
            body.append(f"<div class='ovitem'><div class='ovrank'>#{i}</div><div><div class='ovname'>{esc(name)} <span class='stockcode'>{esc(code)}</span></div><div class='ovmeta'>{esc(meta)}</div></div></div>")
        cols_html.append(f"<div class='ovcol'><div class='ovhead'>{esc(title)}</div>{''.join(body)}</div>")

    consensus = []
    for code, cnt in sorted(dup_counts.items(), key=lambda x: (-x[1], x[0])):
        if cnt >= 2:
            stock = next((s for s in snap.get("stocks", []) if s.get("ticker") == code), None)
            if stock:
                consensus.append(f"{stock.get('name')}（{cnt}/3）")
    if consensus:
        st.markdown("<div class='consensus'><b>跨週期共識</b><div class='mini-note'>" + esc("、".join(consensus[:6])) + "</div><div class='consensus-note'>共識是多個時間尺度都強，不代表此刻就是買點。</div></div>", unsafe_allow_html=True)

    unique_n = len(set(code for code in dup_counts if code))
    st.markdown(f"<div class='mini-note'>完整日線訊號｜15 個訊號席位共 {unique_n} 檔股票；盤中買點另由即時頁處理。</div>", unsafe_allow_html=True)
    st.markdown("<div class='overview'>" + "".join(cols_html) + "</div>", unsafe_allow_html=True)

    allocation = service.select_cross_horizon_shortlists(snap, n=5, max_appearances=2)
    metrics = service.shortlist_overlap_metrics(allocation)
    st.markdown(f"<div class='mini-note'>若用於實戰配置：去重後約 {metrics.get('unique_tickers',0)} 檔不同股票，單一股票最多跨 2 個週期。</div>", unsafe_allow_html=True)


def _compact_pick_record(stock: dict, horizon: str, rank: int, list_type: str = "完整日線") -> dict:
    block = (stock.get("horizons") or {}).get(horizon, {}) or {}
    timing = block.get("entry_timing") or {}
    plan = block.get("plan") or {}
    next_session = stock.get("next_session") or {} if list_type == "明日關注" else {}
    buy_zone = next_session.get("buy_zone") or {}
    low = buy_zone.get("low") if buy_zone.get("low") is not None else plan.get("zone_low")
    high = buy_zone.get("high") if buy_zone.get("high") is not None else plan.get("zone_high")
    zone_low = nfmt(low) if low is not None else "—"
    zone_high = nfmt(high) if high is not None else "—"
    zone = f"{zone_low}–{zone_high}" if zone_low != "—" and zone_high != "—" else "—"
    return {
        "清單": list_type,
        "週期": H_LABEL.get(horizon, horizon),
        "排名": rank,
        "股票": str(stock.get("name") or stock.get("ticker") or ""),
        "代號": str(stock.get("ticker") or ""),
        "產業": str(stock.get("fine_industry") or fine_industry(stock.get("ticker"),stock.get("name"),stock.get("industry"))),
        "現價": nfmt(stock.get("price")),
        "標的分數": nfmt(block.get("ranking_score"), 1),
        "買點分數": nfmt(next_session.get("entry_score") if next_session else timing.get("score"), 0),
        "建議動作": str(next_session.get("stance") or timing.get("action") or block.get("view_title") or "觀察"),
        "建議區間": zone,
        "轉強確認": nfmt(plan.get("trigger")),
        "跌破重看": nfmt(plan.get("invalidation")),
        "V18因子": nfmt((block.get("rank_v18") or {}).get("score_v18"),1),
        "資料品質DQ": nfmt((block.get("rank_v18") or {}).get("dq_score"),0),
        "模型信心MC": nfmt((block.get("rank_v18") or {}).get("mc_score"),0),
        "證據資格": str(((block.get("rank_v18") or {}).get("eligibility") or {}).get("status_label") or "資料待更新"),
        "有數值因子": f"{int((stock.get('v18_profile') or {}).get('evidence_n') or 0)}/13",
        "因子階段": str((stock.get("v18_profile") or {}).get("stage") or "待確認"),
        "買點燈號": str((next_session.get('readiness') or {}).get('code') or ''),
        "買點文字": str((next_session.get('readiness') or {}).get('label') or ''),
        "買點原因": str((next_session.get('readiness') or {}).get('reason') or ''),
        "準備排序": int((next_session.get('readiness') or {}).get('priority',99)),
    }


def _recommendation_board_df(snap: dict | None, top_n: int = 5) -> pd.DataFrame:
    rows = []
    if not snap:
        return pd.DataFrame()
    for horizon in ("short", "mid", "long"):
        picks = service.select_market_best(snap, horizon, n=top_n)
        for i, stock in enumerate(picks, 1):
            rows.append(_compact_pick_record(stock, horizon, i, list_type="完整日線"))
    gctx=_cached_global_context()
    global_stamp=str(gctx.get('fetched_at') or '') if not gctx.get('stale') else ''
    for horizon in ("short", "mid", "long"):
        picks = _cached_readiness_picks(str(snap.get('snapshot_id') or snap.get('price_date') or ''),
                                        horizon, top_n, snap, global_stamp, gctx)
        for i, stock in enumerate(picks, 1):
            rows.append(_compact_pick_record(stock, horizon, i, list_type="明日關注"))
    return pd.DataFrame(rows)


def _board_filtered_rows(snap: dict | None, top_n: int, category: str, period: str, consensus_only: bool) -> pd.DataFrame:
    """Presentation-only filtering; does not change selection or execution ranking."""
    df = _recommendation_board_df(snap, top_n=top_n)
    if df.empty:
        return df
    if category != "全部":
        df = df[df["清單"] == category]
    if period != "全部":
        df = df[df["週期"].str.startswith(period)]
    if consensus_only:
        # Consensus counts DISTINCT strategic horizons, never counts the same
        # ticker twice because it also appears in the next-session list.
        core = _recommendation_board_df(snap, top_n=top_n)
        core = core[core["清單"] == "完整日線"]
        consensus = core.groupby("代號")["週期"].nunique()
        codes = set(consensus[consensus >= 2].index)
        df = df[df["代號"].isin(codes)]
    return df.reset_index(drop=True)


def _board_unique_records(df: pd.DataFrame, *, compact: bool = True, focus_ready: bool = False) -> list[dict]:
    """Deduplicate visual cards by ticker; preserve each original strategy row.

    "完整日線" is an investment-quality ranking based on completed EOD,
    while "明日關注" adds next-session entry constraints.  A shared ticker is
    NOT evidence of an extra independently validated forecast; never add scores.
    """
    if df is None or df.empty:
        return []
    records: dict[str, dict] = {}
    for _, row in df.iterrows():
        rec = row.to_dict()
        ticker = str(rec.get("代號") or "")
        if not ticker:
            continue
        if ticker not in records:
            records[ticker] = {"primary": rec, "periods": [], "lists": [], "signals": []}
        bundle = records[ticker]
        period = str(rec.get("週期") or "")
        origin = str(rec.get("清單") or "")
        if period not in bundle["periods"]:
            bundle["periods"].append(period)
        if origin not in bundle["lists"]:
            bundle["lists"].append(origin)
        bundle["signals"].append(rec)
        current = bundle["primary"]
        # For an aggregated view, prefer the selected next-session action if
        # one exists. Keep the score and zone *from this one signal*, never mix
        # short/mid/long entry scores or create an overall score.
        if origin == "明日關注" and (current.get("清單") != "明日關注" or
              int(rec.get('準備排序') if rec.get('準備排序') is not None else 99) < int(current.get('準備排序') if current.get('準備排序') is not None else 99)):
            bundle["primary"] = rec
    bundles = list(records.values())
    horizon_order = {"短線": 0, "中線": 1, "長線": 2}
    def visual_order(bundle):
        keys = []
        for row in bundle["signals"]:
            horizon = str(row.get("週期") or "").split(" · ")[0]
            keys.append((int(row.get("排名") or 9999), horizon_order.get(horizon, 9),
                         0 if row.get("清單") == "明日關注" else 1))
        return min(keys) if keys else (9999, 9, 9)
    if focus_ready:
        bundles.sort(key=lambda b:(min((int(r.get('準備排序') if r.get('準備排序') is not None else 99) for r in b['signals']
                                 if r.get('清單') == '明日關注'), default=99),visual_order(b)))
    else:
        bundles.sort(key=visual_order)
    return bundles if compact else [dict(x) for x in bundles]


V188_CSS = r"""
<style>
:root{--paper:#f4f6f7;--surface:#ffffff;--ink:#152339;--muted:#5d6b7f;--line:#dde4eb;--blue:#2b538b;--green:#15815a;--amber:#a46b26;--soft:#eaf0f5;}
.stApp{background:var(--paper)}
.block-container{max-width:1020px}
.mast{border-top:3px solid #274268;border-bottom:1px solid #cfd9e3;padding:9px 0;margin-bottom:9px}
.brand{font-size:1.45rem;letter-spacing:-.04em}.version{font-size:.69rem}
.stRadio [role='radiogroup']{gap:.3rem!important}
.stRadio label{background:#fff!important;border:1px solid #d6e0e9!important;border-radius:11px!important;padding:.26rem .65rem!important}
.stRadio label p{font-size:.87rem!important;letter-spacing:0;font-weight:750!important}
.section-title{font-size:1.12rem!important;line-height:1.3!important;font-weight:850!important}
.section-kicker{font-size:.64rem!important;letter-spacing:.13em!important;margin:12px 0 3px!important}
.focus-head{display:flex;justify-content:space-between;align-items:flex-end;gap:8px;margin:9px 0 12px;flex-wrap:wrap}
.focus-head h3{font-size:1.04rem;margin:0;color:#152339;letter-spacing:-.02em}
.focus-head small{font-size:.75rem;color:#6b7787}
.focus-note{font-size:.78rem;line-height:1.6;color:#52657c;padding:8px 10px;background:#eaf0f7;border-radius:10px;margin:8px 0}
.focus-list{display:grid;gap:9px}
.focus-card{background:#fff;border:1px solid #d9e3ed;border-left:4px solid #6589ba;border-radius:14px;padding:12px 14px;box-shadow:0 4px 13px rgba(20,43,73,.034);overflow:hidden}
.focus-card .top{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.focus-card .rank{font-size:.72rem;font-weight:850;color:#3c6594;background:#edf3f9;padding:4px 7px;border-radius:6px;flex:none}
.focus-card .company{font-size:1.00rem;color:#172944;font-weight:900;line-height:1.2}
.focus-card .code{font-size:.76rem;color:#7b8797;font-weight:650}
.focus-card .industry-tag{font-size:.71rem;color:#425c79;background:#f0f5fa;border-radius:8px;padding:3px 7px;max-width:100%;overflow-wrap:anywhere}
.focus-card .facts{display:flex;flex-wrap:wrap;gap:5px;margin:9px 0 8px;align-items:center}
.focus-card .fact{font-size:.73rem;font-weight:750;border:1px solid #d9e2ed;border-radius:8px;padding:5px 8px;color:#34506b;white-space:nowrap}
.focus-card .fact strong{color:#1e304c;font-weight:950}
.focus-card .fact.green{background:#eef8f3;border-color:#c4e7d4;color:#1c7854}
.focus-card .fact.amber{background:#fff6e8;border-color:#edd8b6;color:#975f1b}
.focus-card .fact.red{background:#fff0ee;border-color:#f2cfc9;color:#ac4944}
.focus-card .fact.gray{background:#f5f6f7;border-color:#e0e3e7;color:#677484}
.focus-card .planline{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;background:#f7f9fb;border-radius:9px;padding:8px 10px;line-height:1.45;font-size:.79rem}
.focus-card .planline b{font-size:.81rem;color:#253953}
.focus-card .sub{font-size:.74rem;color:#66768a;line-height:1.5;margin-top:7px}
.focus-card .price-note{margin-left:auto;font-size:.88rem;font-weight:850;color:#253953}
.focus-legend{color:#5f6d7c;font-size:.76rem;line-height:1.55;margin:7px 0 9px}
.status-mini{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 7px;font-size:.74rem;color:#53687d}
.status-mini b{background:#edf3f8;padding:5px 8px;border-radius:7px;color:#275781}
.data-compact{background:#fff;border:1px solid #dee5ed;border-radius:12px;padding:10px 12px;font-size:.79rem;margin:6px 0;color:#40556e}
.data-compact .dates{display:flex;flex-wrap:wrap;gap:5px 12px;margin-top:6px}
.data-compact .dates b{font-weight:850;color:#1d3759}
.reading-intro h2{font-size:1.16rem!important;letter-spacing:-.02em!important}
.reading-intro{padding:12px 13px!important;border-radius:13px}
@media(max-width:720px){
  .block-container{padding:.9rem .65rem 2.7rem!important;padding-top:4.3rem!important}
  .focus-card{padding:11px 10px;border-radius:12px}.focus-card .company{font-size:1.04rem}
  .focus-card .fact{font-size:.69rem;padding:5px 6px}.focus-card .industry-tag{font-size:.69rem}
  .focus-card .planline{font-size:.75rem;gap:4px}.focus-head{align-items:flex-start}
  .stRadio label{padding:.22rem .48rem!important}.stRadio label p{font-size:.79rem!important}
  .version{font-size:.65rem}.section-title{font-size:1.03rem!important}
}
</style>
"""


def _tone(value, kind):
    return signal_light(value,kind)


def _render_canonical_board(snap:dict|None, *, home:bool=False):
    """Production UI always publishes the untouched three-horizon Alpha order."""
    if not snap:
        st.info('尚未產生完整日線模型，請於上方啟動背景更新。')
        return
    safe_report=assess_snapshot(snap)
    trade_reference_ok=bool(safe_report.get('sync_state')=='ALIGNED' and safe_report.get('safe_for_research'))
    hlabels={'短線':'short','中線':'mid','長線':'long'}
    st.markdown("<div class='section-kicker'>CANONICAL STOCK SELECTION</div>",unsafe_allow_html=True)
    st.markdown("<div class='focus-head'><h3>"+('今日精選｜依模型原始排名' if home else '策略選股｜原始排名')+"</h3><small>同樣資料日，兩版名單應一致</small></div>",unsafe_allow_html=True)
    c1,c2=st.columns([1.4,1.0]) if not home else (None,None)
    if home:
        selected=st.radio('持有週期',['短線','中線','長線'],horizontal=True,key='canonical_home_h_v188')
        num=5
    else:
        with c1:
            selected=st.radio('持有週期',['短線','中線','長線'],horizontal=True,key='canonical_board_h_v188')
        with c2:
            num=st.selectbox('顯示筆數',[5,10,20],index=1,key='canonical_board_n_v188')
    h=hlabels[selected]
    st.markdown("<div class='focus-note'>"+esc('這是 '+selected+' 股票品質排名；買點狀態只是該股票的附加判斷，不會改動名次。')+"</div>",unsafe_allow_html=True)
    gctx=_cached_global_context() if trade_reference_ok else {}
    rows=canonical_shortlist(snap,h,n=int(num),global_context=gctx,with_timing=trade_reference_ok)
    if not rows:
        st.info('此週期暫無符合流動性、歷史案例及基本資料門檻的候選。')
        return
    cards=[]
    for row in rows:
        stock=row['stock']; block=(stock.get('horizons') or {}).get(h) or {}
        name=stock.get('name') or row['ticker'];ticker=row['ticker']
        industry=stock.get('fine_industry') or fine_industry(ticker,name,stock.get('industry',''))
        a=_tone(row.get('alpha'),'alpha');dq=_tone(row.get('dq'),'dq');mc=_tone(row.get('mc'),'mc')
        decision=row.get('decision') or {}; ready=row.get('readiness') or {}
        code=ready.get('code') or 'DATA'
        label=ready.get('label') or '買點待核'
        desc=ready.get('reason') or '資料未充分，暫不判斷即時買點'
        if not trade_reference_ok:
            label,desc,code='資料日期待核','官方來源未通過同步核對；僅展示原始策略研究排名','DATA'
        color={'READY_SETUP':'green','TRIGGER':'amber','TIMING':'amber','PULLBACK':'amber',
               'BELOW':'amber','OVERHEAT':'red','EDGE':'red','EVIDENCE':'gray','DATA':'gray'}.get(code,'gray')
        plan=block.get('plan') or {}
        zone=decision.get('buy_zone') or {}
        low=finite(zone.get('low')) if trade_reference_ok else None
        high=finite(zone.get('high')) if trade_reference_ok else None
        if low is None:low=finite(plan.get('zone_low')) if trade_reference_ok else None
        if high is None:high=finite(plan.get('zone_high')) if trade_reference_ok else None
        zone_text=f'{nfmt(low)}–{nfmt(high)}' if low is not None and high is not None else '待確認'
        score_text=nfmt(row.get('alpha'),1)
        ent=nfmt(decision.get('entry_score'),0) if trade_reference_ok else '—'
        dql=nfmt(row.get('dq'),0);mcl=nfmt(row.get('mc'),0)
        detail=''.join(f"<span class='fact {light['color']}'>{esc(key)} <strong>{esc(value)}</strong></span>" for key,light,value in
                       [('Alpha',a,score_text),('DQ',dq,dql),('MC',mc,mcl)])
        cards.append(
            "<article class='focus-card'><div class='top'>"
            f"<span class='rank'>#{row['rank']:02d}</span>"
            f"<span class='company'>{esc(name)}</span><span class='code'>{esc(ticker)}</span>"
            f"<span class='industry-tag'>{esc(industry)}</span>"
            f"<span class='price-note'>{esc(nfmt(stock.get('price')))}</span></div>"
            f"<div class='facts'>{detail}<span class='fact {color}'>{esc('買點 · '+label)}</span></div>"
            f"<div class='planline'><span>參考區 <b>{esc(zone_text)}</b></span>"
            f"<span>買點分數 <b>{esc(ent)}</b></span><span>模型日 <b>{esc(snap.get('price_date') or '—')}</b></span></div>"
            f"<div class='sub'>{esc(desc)}</div></article>")
    st.markdown("<div class='focus-list'>"+''.join(cards)+"</div>",unsafe_allow_html=True)
    st.markdown("<div class='focus-legend'>綠色 Alpha＝值得研究，不代表現在可買；買點必須另經價格、量能與風險條件確認。DQ／MC 是證據品質，不是勝率。</div>",unsafe_allow_html=True)
    if home:
        st.caption('想看其他週期、因子詳解或買點觀察名單，可使用「選股與買點」。')
        return
    if st.toggle('展開這檔股票的因子與資料證據',value=False,key='show_factor_v188'):
        tickers={r['ticker']:str(r['stock'].get('name') or r['ticker']) for r in rows}
        selection=st.selectbox('股票',list(tickers),format_func=lambda key:tickers[key],key='canonical_factor_t_v188')
        v18_ui.render_page(st,snap,(load_brief(DATA_DIR).get('global') or {}),selected_ticker=selection,selected_horizon=h,compact=True)
    with st.expander('模型權重與驗效狀態',expanded=False):
        from strategy_validation_v188 import weight_audit
        audit=weight_audit();w=audit['horizons'][h]
        st.caption('短中長線是三套獨立權重；權重算式可重現，但未取得完整歷史 PIT／下市母體，並未宣稱實證最優。')
        st.dataframe(pd.DataFrame([{'基本模型因子':k,'權重':f'{v*100:.0f}%'} for k,v in w['core'].items()]),hide_index=True,use_container_width=True)
        st.caption('V18 新因子權重（需 OOS 驗效）：'+', '.join(f'{k} {v:.0%}' for k,v in w['dimension'].items()))
        st.caption('本版保留原有分數計算及門檻，不依短期榜單變動暗中重調參。')
    show_extra=st.toggle('另外查看接近買點的候選（非策略 Top 名單）',value=False,key='extra_watch_v188')
    if show_extra:
        st.info('下列以進場準備度排序，可能包含 Alpha 排名較後的股票；不能與上面原始 Top 榜混為一談。')
        try:
            stamp=str(gctx.get('fetched_at') or '') if isinstance(gctx,dict) else ''
            watch=_cached_readiness_picks(str(snap.get('snapshot_id') or snap.get('price_date') or ''),h,8,snap,stamp,gctx)
            simplified=[]
            for stock in watch:
                ready=((stock.get('next_session') or {}).get('readiness') or {})
                simplified.append({'名稱':stock.get('name'),'代號':stock.get('ticker'),
                    '選股 Alpha':((stock.get('horizons') or {}).get(h) or {}).get('ranking_score'),
                    '買點條件':ready.get('label') or '待核對',
                    '原因':ready.get('reason') or '—'})
            st.dataframe(pd.DataFrame(simplified),use_container_width=True,hide_index=True)
        except Exception:
            st.warning('買點觀察資料暫時無法取得，原始策略榜仍正常。')

def _render_recommendation_board(snap: dict | None, *, home: bool = False):
    """One stock, one card.  Strategy and next-session timing are columns, not lists."""
    st.markdown("<div class='section-kicker'>STOCK SHORTLIST</div><div class='section-title'>"+
                ('今天先看這些股票' if home else '選股雷達｜研究分數 × 進場時機')+
                "</div>",unsafe_allow_html=True)
    if not snap:
        st.info('尚無完成的日線模型；可先在「今日決策」閱讀市場快讀；背景更新完成後會顯示選股名單。')
        return
    audit = assess_snapshot(snap)
    action_safe = audit.get('sync_state') == 'ALIGNED' and audit.get('safe_for_research')
    if not action_safe:
        st.warning('日期／來源尚未完全核對：以下只供過往策略研究，不顯示可執行買點。請在「今日決策」展開資料來源查核。')
    _render_score_guide()
    if not home:
        st.caption('一檔股票只顯示一張卡；買點優先是「下次交易日前的條件排序」，盤中才能核價產生真正可買訊號。')
        left, right = st.columns([1.15,1.0])
        with left:
            period = st.radio('持有時間', ['全部','短線','中線','長線'], horizontal=True,
                              key='board_horizon_v185')
        with right:
            limit = st.selectbox('最多顯示', [8,15,25], key='board_show_v185')
        sort_mode = st.radio('看股票的順序', ['買點優先','標的排名'], horizontal=True,
                             key='board_mode_v187', help='切換顯示順序，不修改任何 Alpha 選股分數')
    else:
        period, limit, sort_mode = '全部', 6, '買點優先'
    # Each source retains its own score and horizon; deduping never adds scores.
    df = _board_filtered_rows(snap, top_n=10, category='全部', period=period, consensus_only=False)
    bundles = _board_unique_records(df,focus_ready=(sort_mode=='買點優先'))
    if not bundles:
        st.info('目前沒有符合資料及選股條件的標的；不會為湊名額製造推薦。')
        return
    if action_safe:
        near_df = df[df['清單']=='明日關注'] if '清單' in df.columns else pd.DataFrame()
        counts = (near_df.groupby('代號')['買點燈號'].agg(lambda vals: sorted(vals, key=lambda v: {'READY_SETUP':0,'TRIGGER':1}.get(v,9))[0])
                  if not near_df.empty and '買點燈號' in near_df.columns else pd.Series(dtype=str))
        ready_n = int((counts=='READY_SETUP').sum()) if not counts.empty else 0
        trigger_n = int((counts=='TRIGGER').sum()) if not counts.empty else 0
        st.markdown(f"<div class='setup-banner'><b>🟢 盤前條件就緒 {ready_n} 檔</b>　"
                    f"🟡 接近突破 {trigger_n} 檔　"
                    f"<small>僅統計本頁短中長線候選；非已核價可買，若為 0 不會強行湊數</small></div>",
                    unsafe_allow_html=True)
        if ready_n == 0 and not near_df.empty and '買點文字' in near_df.columns:
            # Explain why there are no immediate setups without changing any
            # selection or execution threshold just to manufacture a green BUY.
            reasons = (near_df[['代號','買點文字']].drop_duplicates('代號')['買點文字']
                       .replace('', '待確認').value_counts())
            top_reasons = '、'.join(f'{esc(str(k))} {int(v)} 檔' for k,v in reasons.head(3).items())
            st.caption('目前沒有符合完整盤前條件的研究候選，主要原因：' + top_reasons + '。'
                       '假日與盤前不能取得可執行即時買點；開盤後仍由 MIS 官方核價決定。')
    st.markdown(f"<div class='board-headline'><b>{'今日重點' if home else '跨週期去重'} · {min(limit,len(bundles))} 檔</b>"
                f"<span>最近完整模型日 {esc(snap.get('price_date') or '待確認')} · 同股不重複</span></div>",
                unsafe_allow_html=True)
    cards=[]
    for bundle in bundles[:int(limit)]:
        r=bundle['primary']; has_next='明日關注' in bundle['lists']
        status=str(r.get('買點文字') or '') if has_next else ''
        reason=str(r.get('買點原因') or '') if has_next else ''
        signal=str(r.get('買點燈號') or '') if has_next else ''
        action=(status or str(r.get('建議動作') or '觀察')) if action_safe else '資料未核，暫不判斷買點'
        tag=('含下次交易日觀察' if has_next else '策略候選') if action_safe else '待核對 · 策略研究'
        periods='／'.join(str(x).split(' · ')[0] for x in bundle['periods'])
        primary=str(r.get('週期') or '').split(' · ')[0]
        score=str(r.get('標的分數') or '—')
        entry=str(r.get('買點分數') or '—') if action_safe else '—'
        zone=str(r.get('建議區間') or '—') if action_safe else '—'
        stop=str(r.get('跌破重看') or '—') if action_safe else '—'
        price=str(r.get('現價') or '—')
        dq=str(r.get('資料品質DQ') or '—'); mc=str(r.get('模型信心MC') or '—')
        qualification=str(r.get('證據資格') or '研究觀察')
        industry=str(r.get('產業') or '產業定位待補')
        readiness_cls='ready-green' if signal=='READY_SETUP' else 'ready-amber' if signal in {'TRIGGER','TIMING','PULLBACK','BELOW'} else 'ready-gray'
        cards.append(
            f"<article class='pick-mini {'next' if has_next else 'core'} {readiness_cls}'>"
            f"<div class='pick-mini-main'><div class='pick-name'>{esc(r.get('股票'))} <small>{esc(r.get('代號'))}</small></div>"
            f"<div class='pick-tags'><span class='pick-label'>{esc(tag)}</span><span>{esc(periods)}</span>"
            f"<span class='industry-mini'>{esc(industry)}</span></div></div>"
            f"<div class='pick-mini-center'><b>{esc(action)}</b><small>以 {esc(primary)} 訊號顯示 · 標的 {esc(score)} ／ 買點 {esc(entry)}</small></div>"
            f"<div class='pick-mini-price'><b>{esc(price)}</b><small>日線參考價</small></div>"
            f"<div class='pick-mini-factor'>{_mini_traffic(score,'alpha','Alpha')}"
            f"{_mini_traffic(dq,'dq','資料DQ')}{_mini_traffic(mc,'mc','信心MC')}"
            f"<span class='f-pill {'stage' if '正式' in qualification else 'low'}'>{esc(qualification)}</span></div>"
            f"<div class='pick-mini-extra'>參考區間 <b>{esc(zone)}</b>　防守 <b>{esc(stop)}</b>"
            f"<small>{esc(reason) if action_safe else '須確認官方價格與日期，暫停交易判斷'}</small>"
            f"<em>同股跨週期只是不同策略看法，不等於多次獨立驗證</em></div></article>"
        )
    st.markdown("<div class='pick-mini-list'>"+''.join(cards)+"</div>",unsafe_allow_html=True)
    st.caption('紅綠燈只是各分數的判讀：Alpha 綠燈不等於買點；DQ／MC 紅燈表示資料或證據不足，不代表公司差。盤前條件就緒也須等開盤官方即時價確認。')
    if home:
        return
    with st.expander('進階比較表｜短、中、長線與次日訊號分開看',expanded=False):
        rows=[]
        for bundle in bundles:
            r=bundle['primary']
            rows.append({'股票':r.get('股票'),'代號':r.get('代號'),
                         '入選週期':'／'.join(str(x).split(' · ')[0] for x in bundle['periods']),
                         '是否有下次買點觀察':'是' if '明日關注' in bundle['lists'] else '否',
                         '本卡採用週期':r.get('週期'),'標的分':r.get('標的分數'),
                         '買點分':r.get('買點分數'),'產業分類':r.get('產業'), '買點狀態':r.get('買點文字'),'資料DQ':r.get('資料品質DQ'),
                         '信心MC':r.get('模型信心MC'),'參考價格':r.get('現價'),
                         '建議動作':r.get('建議動作')})
        st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    with st.expander('為什麼推薦這檔？｜展開個股因子透視與資料證據',expanded=False):
        titles={str(b['primary']['代號']):str(b['primary'].get('股票') or b['primary']['代號']) for b in bundles}
        st.caption('因子透視不是另一份選股名單：先由上方策略找股票，對某檔的選股理由、DQ/MC 或缺值有疑問時再展開。')
        ticker=st.selectbox('選一檔股票查看理由',list(titles),format_func=lambda v:titles.get(v,v),key='factor_pick_v185')
        selected_bundle=next((b for b in bundles if str(b['primary']['代號'])==ticker),None)
        pnames={'短線':'short','中線':'mid','長線':'long'}
        horizons=[pnames[p.split(' · ')[0]] for p in selected_bundle['periods'] if p.split(' · ')[0] in pnames]
        preferred=pnames.get(str(selected_bundle['primary'].get('週期') or '').split(' · ')[0], 'short')
        h=st.radio('看哪個策略週期',horizons or ['short'],horizontal=True,
                   index=horizons.index(preferred) if preferred in horizons else 0,
                   format_func=lambda key:H_LABEL.get(key,key),key='factor_h_v185')
        v18_ui.render_page(st,snap,(load_brief(DATA_DIR).get('global') or {}),
                           selected_ticker=ticker,selected_horizon=h,compact=True)


def _prime(snap: dict | None):
    if not snap:
        return
    used: set[str] = set()
    for h in HORIZONS:
        picks = service.select_market_best(snap, h, n=5)
        stock = next((x for x in picks if x.get("ticker") not in used), picks[0] if picks else None)
        if stock:
            used.add(str(stock.get("ticker")))
            st.markdown(f"<div class='section-kicker'>{esc(H_LABEL[h])}</div>", unsafe_allow_html=True)
            _row(stock, h, snap, 1, live=False)

def _doctor(snap: dict | None):
    # Date/provider verification must guard ALL actionable surfaces, not only
    # shortlist cards and next-session detail. Never offer BUY/SELL from a
    # snapshot with unverified or deliberately rolled-back market dates.
    if snap and assess_snapshot(snap).get('sync_state') != 'ALIGNED':
        st.warning('目前兩市日期尚未完整核對；個股診斷暫不提供買入／賣出判定。請在「今日決策」展開「資料來源與同步狀態」查核，並先使用已保存的研究名單作為歷史參考。')
        return
    st.markdown("<div class='mini-note'>輸入股票後，系統會依目前時段自動切換：盤中使用即時價量判斷是否可買；盤後／盤前使用最近完整交易日規劃下一交易日。已有持股則改用持股結構判斷續抱、減碼或退出，不會拿『新買進分數』直接決定賣出。</div>", unsafe_allow_html=True)

    mode = st.radio(
        "診斷目的",
        ["考慮買入", "已有持股"],
        horizontal=True,
        key="doctor_mode_v17",
    )
    with st.form("doctor_v17"):
        code = st.text_input("股票代碼", value="2330", placeholder="例如 2330 或 6187.TWO")
        h = st.selectbox("評估週期", list(H_LABEL), format_func=lambda x: H_LABEL[x], index=0)
        cost_text = ""
        stop_text = ""
        trail_text = ""
        thesis = "尚未確認"
        if mode == "已有持股":
            cost_text = st.text_input("持股成本（元／股，建議填寫）", value="", placeholder="例如 980；留白仍可做結構判斷")
            thesis = st.selectbox("原買進理由目前狀態", ["尚未確認", "仍成立", "已不成立"], index=0)
            st.caption("以下兩項只有你原本就有明確紀律時才填；留白時系統會顯示自己的『模型防守價／獲利保護參考』，不再用 0.00 代表未知。")
            c1, c2 = st.columns(2)
            with c1:
                stop_text = st.text_input("你原本的停損／結構失效價（選填）", value="", placeholder="例如 920")
            with c2:
                trail_text = st.text_input("你原本的獲利保護價（選填）", value="", placeholder="例如 1040")
        button_text = "分析現在是否適合買" if mode == "考慮買入" else "分析續抱／減碼／賣出"
        go_ = st.form_submit_button(button_text, type="primary", use_container_width=True)

    if go_:
        if not snap:
            st.warning("請先按上方更新建立市場快照；個股診斷需要與短／中／長線模型使用同一個完整交易日基準。")
            st.session_state.pop("alpha_doctor", None)
        else:
            with st.spinner("讀取個股歷史、目前時段與交易決策…"):
                dr = service.diagnose_with_live(code, snap, DATA_DIR, horizon=h, now=_taipei_timestamp())
            dr["h"] = h
            dr["doctor_input"] = {
                "mode": mode,
                "cost": _optional_float(cost_text),
                "original_stop": _optional_float(stop_text),
                "trailing_protection": _optional_float(trail_text),
                "thesis": thesis,
            }
            st.session_state["alpha_doctor"] = dr

    dr = st.session_state.get("alpha_doctor")
    if not dr:
        return
    if dr.get("error"):
        st.error("找不到足夠行情資料，請確認股票代碼或稍後重試。")
        return
    stock = dr.get("stock")
    if not stock:
        return

    h = dr.get("h", "short")
    inputs = dr.get("doctor_input") or {"mode": "考慮買入"}
    phase_info = dr.get("phase") or session_reference_policy(_taipei_timestamp())
    phase = str(phase_info.get("phase") or "")
    phase_label = str(phase_info.get("label") or "")
    live_status = dr.get("live_status") or {}
    mode_used = str(inputs.get("mode") or "考慮買入")

    if mode_used == "已有持股":
        thesis_code = {"已不成立": "BROKEN", "仍成立": "INTACT"}.get(str(inputs.get("thesis")), "UNKNOWN")
        decision = holding_decision(
            stock,
            h,
            phase,
            cost=finite(inputs.get("cost")),
            original_stop=finite(inputs.get("original_stop")),
            trailing_protection=finite(inputs.get("trailing_protection")),
            thesis_state=thesis_code,
        )
        action = str(decision.get("action_code") or "HOLD")
        cls = "sell" if action == "SELL" else "wait" if action in {"REDUCE", "PROTECT", "DATA"} else "hold"
        kicker = f"{phase_label}持股判定 · {H_LABEL.get(h,h)}"
        subtitle = f"價格來源：{decision.get('price_source','—')}｜完整日線基準：{stock.get('price_date') or dr.get('analysis_date') or '—'}"
        if phase == INTRADAY and live_status.get("verified"):
            stamp = str(live_status.get("fetched_at") or "").replace("T", " ")[:19]
            subtitle += f"｜官方驗證 {stamp or '本次'}"
        elif phase == INTRADAY:
            subtitle += "｜官方即時驗證未通過，本次不使用即時價"
        st.markdown(
            f"<div class='doctor-hero {cls}'><div class='doctor-kicker'>{esc(kicker)}</div><div class='doctor-title'>{esc(stock.get('name'))} {esc(stock.get('ticker'))}｜{esc(decision.get('headline'))}</div><div class='doctor-sub'>{esc(subtitle)}</div></div>",
            unsafe_allow_html=True,
        )
        pnl = decision.get("pnl_pct")
        expected = finite(decision.get("remaining_expected_return"))
        display_price = decision.get("price") if decision.get("price") is not None else decision.get("reference_price")
        price_label = "官方即時價" if phase == INTRADAY and live_status.get("verified") else ("完整日線參考價（非即時）" if phase == INTRADAY else "完整日線基準價")
        metrics = [
            (price_label, nfmt(display_price,2)),
            ("持股評分", nfmt(decision.get("score"),1)),
            ("目前損益", "—" if pnl is None else f"{pnl:+.1f}%"),
            ("模型防守價", nfmt(decision.get("model_stop"),2)),
            ("獲利保護參考", nfmt(decision.get("profit_protection"),2)),
            ("剩餘預期報酬", "—" if expected is None else f"{expected*100:+.1f}%"),
        ]
        st.markdown("<div class='doctor-grid'>"+"".join(f"<div><small>{esc(k)}</small><b>{esc(v)}</b></div>" for k,v in metrics)+"</div>", unsafe_allow_html=True)
        reasons = decision.get("reasons") or []
        if reasons:
            st.markdown("<div class='doctor-reasons'>"+"<br>".join("• "+esc(x) for x in reasons)+"</div>", unsafe_allow_html=True)
        st.caption("模型防守價是依最近完整交易日結構計算；若你有原始停損／獲利保護價，系統會優先尊重你輸入的既有紀律。獲利保護參考只有在已有足夠浮盈時才出現。")
    else:
        global_ctx = _cached_global_context()
        decision = buy_decision(stock, h, phase, global_context=global_ctx)
        action = str(decision.get("action_code") or "WAIT")
        cls = "buy" if action in {"BUY", "SMALL", "READY"} else "wait"
        kicker = f"{phase_label}買入判定 · {H_LABEL.get(h,h)}"
        subtitle = f"價格來源：{decision.get('price_source','—')}｜完整日線基準：{stock.get('price_date') or dr.get('analysis_date') or '—'}"
        if phase == INTRADAY and live_status.get("verified"):
            stamp = str(live_status.get("fetched_at") or "").replace("T", " ")[:19]
            subtitle += f"｜官方驗證 {stamp or '本次'}"
        elif phase == INTRADAY:
            subtitle += "｜官方即時驗證未通過，本次不使用即時價"
        st.markdown(
            f"<div class='doctor-hero {cls}'><div class='doctor-kicker'>{esc(kicker)}</div><div class='doctor-title'>{esc(stock.get('name'))} {esc(stock.get('ticker'))}｜{esc(decision.get('headline'))}</div><div class='doctor-sub'>{esc(subtitle)}</div></div>",
            unsafe_allow_html=True,
        )
        zone = decision.get("buy_zone") or {}
        expected = finite(decision.get("expected_return"))
        rr = finite(decision.get("reward_risk"))
        price_label = "官方即時價" if phase == INTRADAY and live_status.get("verified") else ("完整日線參考價（非即時）" if phase == INTRADAY else "完整日線基準價")
        metrics = [
            (price_label, nfmt(decision.get("price"),2)),
            ("標的分數", nfmt(decision.get("selection_score"),1)),
            ("進場分數", nfmt(decision.get("entry_score"),1)),
            ("買入優先分數", nfmt(decision.get("score"),1)),
            ("剩餘預期報酬", "—" if expected is None else f"{expected*100:+.1f}%"),
            ("報酬／風險", "—" if rr is None else f"{rr:.2f}"),
        ]
        gshock = finite(decision.get("global_shock_score"))
        if gshock is not None:
            metrics.append(("隔夜壓力", f"{gshock:.0f}/100"))
        st.markdown("<div class='doctor-grid'>"+"".join(f"<div><small>{esc(k)}</small><b>{esc(v)}</b></div>" for k,v in metrics)+"</div>", unsafe_allow_html=True)
        if zone.get("available"):
            plan_items = [
                ("建議買入區", f"{nfmt(zone.get('low'),2)}–{nfmt(zone.get('high'),2)}"),
                ("突破確認價", nfmt(zone.get("trigger"),2)),
                ("不追價上限", nfmt(zone.get("chase_limit"),2)),
                ("模型防守價", nfmt(zone.get("stop"),2)),
            ]
            st.markdown("<div class='doctor-plan'>"+"".join(f"<div><small>{esc(k)}</small><b>{esc(v)}</b></div>" for k,v in plan_items)+"</div>", unsafe_allow_html=True)
        blockers = [str(x.get("label") or "") for x in (decision.get("failed_gates") or []) if isinstance(x, dict) and x.get("label")]
        detail = str(decision.get("detail") or "")
        lines = ([detail] if detail else []) + blockers[:4]
        if lines:
            st.markdown("<div class='doctor-reasons'>"+"<br>".join("• "+esc(x) for x in lines)+"</div>", unsafe_allow_html=True)
        if phase == INTRADAY and not bool(live_status.get("verified")):
            st.warning("盤中執行價未通過官方驗證，因此本次只保留完整日線參考，不會誤標為『現在可買』。")

    if phase == INTRADAY and not bool(live_status.get("verified")) and mode_used == "已有持股":
        st.warning("盤中執行價未通過官方驗證，因此本次不會用昨收代替目前價格做續抱／減碼／賣出判斷。")

    with st.expander("查看完整模型資料、技術圖、營收／EPS／法人", expanded=False):
        _row(stock, h, snap or {"market": {}, "snapshot_id": "doctor"}, 1, live=bool(stock.get("intraday")))



def _render_scan_status():
    job = scan_status(DATA_DIR)
    current = str(job.get("state") or "idle")
    if current == "running":
        percent = float(job.get("percent") or 0.0)
        st.progress(min(100, int(percent)), text=f"日線背景更新 {percent:.0f}%｜{job.get('stage') or '處理中'}")
        stamp = str(job.get("started_at") or "").replace("T", " ")[:19]
        st.caption(f"開始：{stamp}。可切換其他分頁、鎖定手機或關閉瀏覽器；只要伺服器持續運行，這項工作仍會進行。重新開啟會接續查看進度。")
    elif current == "complete":
        active = (st.session_state.get("alpha_snapshot") or {}).get("snapshot_id")
        new_id = str(job.get("snapshot_id") or "")
        if active != new_id and new_id:
            fresh = service.load_dashboard(DATA_DIR / "dashboard_snapshot.json")
            if fresh and fresh.get("snapshot_id") == new_id and fresh.get("model_version") == service.OPERATIONS_VERSION:
                st.session_state["alpha_snapshot"] = service.compact_session_dashboard(fresh)
                st.session_state.pop("alpha_error", None)
                if hasattr(st, "rerun"):
                    st.rerun()
        elapsed = f" · 耗時 {float(job['elapsed_seconds']):.0f} 秒" if job.get("elapsed_seconds") is not None else ""
        st.success(f"上一個日線更新已完成：模型日 {job.get('price_date') or '—'} · 分析 {int(job.get('model_count') or 0):,} 檔{elapsed}；資料完整度：{job.get('integrity') or '待驗'}")
    elif current in {"failed", "busy", "interrupted"}:
        st.warning(str(job.get("stage") or "更新未完成。"))
        if job.get("error"):
            with st.expander("更新錯誤資訊"):
                st.caption(str(job["error"]))


if hasattr(st, "fragment"):
    @st.fragment(run_every="8s")
    def _scan_status_fragment():
        _render_scan_status()

    @st.fragment(run_every="20s")
    def _reading_fragment():
        brief = load_brief(DATA_DIR)
        brief["previous"] = previous_model_digest(st.session_state.get("alpha_snapshot"))
        _render_waiting_briefing(brief)
else:
    def _scan_status_fragment():
        _render_scan_status()

    def _reading_fragment():
        brief = load_brief(DATA_DIR)
        brief["previous"] = previous_model_digest(st.session_state.get("alpha_snapshot"))
        _render_waiting_briefing(brief)


def main():
    st.set_page_config(page_title="Alpha Radar V18.8", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")
    _bootstrap_secrets()
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(V188_CSS, unsafe_allow_html=True)
    st.markdown(v18_ui.CSS, unsafe_allow_html=True)
    st.markdown("""<style>
    /* Three clear destinations. No redundant function tabs or fake button links. */
    .marketbar-compact{background:#fff;border:1px solid #dce5f3;border-radius:12px;
        padding:10px 13px;margin:7px 0 12px;line-height:1.35}
    .marketbar .health-green{color:#0a7a5b;font-weight:900}
    .marketbar .health-amber{color:#a46720;font-weight:900}
    .marketbar .health-red{color:#bd3a4b;font-weight:900}
    [data-testid="stRadio"] [role="radiogroup"]{flex-wrap:wrap;gap:7px}
    [data-testid="stRadio"] [role="radiogroup"] label{min-height:41px;display:flex;align-items:center;justify-content:center}
    .pick-mini{border-radius:15px;box-shadow:0 6px 16px rgba(30,55,97,.06)}
    .pick-mini-factor .f-pill.alpha{background:#f2edff;color:#5f46ad}
    .pick-mini-price small{color:#7b849b}
    .pick-mini,.pick-mini-main,.pick-mini-center,.pick-mini-extra{min-width:0}
    .pick-name,.pick-mini-center b,.pick-mini-extra{overflow-wrap:anywhere}
    [data-testid="stDataFrame"]{max-width:100%;overflow-x:auto}
    .stButton>button:focus-visible,.stRadio label:focus-within{outline:3px solid #4c78d8;outline-offset:2px}
    @media(max-width:720px){
       [data-testid="stRadio"][aria-label="功能"] [role="radiogroup"] {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));width:100%;gap:7px}
       [data-testid="stRadio"][aria-label="功能"] label {margin:0!important;min-width:0!important}
       .marketbar-compact{padding:9px 10px}.marketbar-compact span{font-size:.77rem}
       .pick-mini{border-radius:13px}
       .pick-mini-center b{font-size:.90rem;line-height:1.35}
       .pick-mini-factor{gap:5px}
       .pick-mini-factor .f-pill{font-size:.73rem;padding:4px 7px}
    }
    @media(max-width:380px){
       .pick-mini{grid-template-columns:minmax(0,1fr) 82px}
       .pick-mini-price>b{font-size:1.02rem}
       .pick-mini-extra{font-size:.77rem}
    }

    .watch-details{background:#fff;border:1px solid #dce6f4;border-radius:12px;padding:11px 13px;margin:11px 0}
    .watch-details summary{cursor:pointer;color:#2f52a2;font-size:.89rem;font-weight:900;padding:2px 0}
    .watch-details[open] summary{margin-bottom:12px}
    .light-guide{background:linear-gradient(110deg,#f4f9ff,#fffdf7);border:1px solid #d9e4f1;border-radius:13px;padding:12px 13px;display:flex;flex-wrap:wrap;gap:6px 12px;font-size:.80rem;margin:9px 0 12px;line-height:1.5}
    .light-guide>b{width:100%;color:#1e3262;font-size:.95rem}.light-guide span{padding:2px 6px;background:#fff;border:1px solid #e2e8f1;border-radius:8px;color:#33445e}.light-guide small{width:100%;color:#5c677b}.light-guide small strong{color:#7e491c}
    .setup-banner{border:1px solid #cde5d9;border-left:4px solid #21856d;border-radius:10px;background:#effaf6;padding:9px 11px;margin-bottom:11px;font-size:.86rem;color:#195c4d;line-height:1.55}.setup-banner small{color:#567a6e}
    .industry-mini{display:block;flex-basis:100%;font-size:.78rem!important;font-weight:800;color:#32507a;line-height:1.45}
    .traffic{display:inline-flex;flex-direction:column;gap:2px;min-width:99px;border:1px solid;border-radius:9px;padding:6px 8px;flex:1 1 auto;text-align:left}.traffic b{font-size:.82rem;line-height:1.25}.traffic small{font-size:.72rem;line-height:1.25;font-weight:700}
    .traffic-green{background:#e6f5eb;border-color:#abd7bf;color:#136947}.traffic-amber{background:#fff4dd;border-color:#e9d6a5;color:#906019}.traffic-red{background:#fff0ee;border-color:#ecbbb5;color:#9a3b35}.traffic-gray{background:#f2f4f8;border-color:#d7deea;color:#5b677b}
    .pick-mini.ready-green{border-left:5px solid #258d62!important}.pick-mini.ready-amber{border-left:5px solid #d7a33d!important}.pick-mini.ready-gray{border-left:5px solid #a7b1c5!important}
    @media(max-width:720px){.light-guide{font-size:.77rem;padding:10px}.light-guide span{flex:1 1 42%}.traffic{min-width:0;flex:1 1 30%;padding:6px 5px}.traffic b{font-size:.71rem}.traffic small{font-size:.66rem}.pick-mini-factor{display:flex;gap:5px}}
    </style>""", unsafe_allow_html=True)
    st.markdown("<div class='mast'><div class='brand'>ALPHA<span>/TW</span></div><div class='version'>V18.8 · 固定排名／獨立買點</div></div>", unsafe_allow_html=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    now = _taipei_timestamp()
    phase_policy = session_reference_policy(now)
    st.markdown(
        f"<div class='phasebar'><b>{esc(phase_policy.get('label'))}模式</b><div class='mini-note'>{esc(phase_policy.get('notice') or '')}</div></div>",
        unsafe_allow_html=True,
    )

    if st.session_state.get("alpha_version") != service.OPERATIONS_VERSION:
        for k in ["alpha_snapshot", "alpha_doctor", "alpha_error"]:
            st.session_state.pop(k, None)
        st.session_state["alpha_version"] = service.OPERATIONS_VERSION
        previous = service.load_dashboard(DATA_DIR / "dashboard_snapshot.json")
        if previous and previous.get("model_version") == service.OPERATIONS_VERSION:
            st.session_state["alpha_snapshot"] = service.compact_session_dashboard(previous)

    # Doctor/execution hotfixes should invalidate only stale diagnosis state, not
    # force a costly full-market rebuild when the frozen selection core is unchanged.
    if st.session_state.get("alpha_doctor_version") != DOCTOR_VERSION:
        st.session_state.pop("alpha_doctor", None)
        st.session_state["alpha_doctor_version"] = DOCTOR_VERSION

    with st.sidebar:
        st.markdown("### V18 標準模型 · 多因子版")
        st.caption("正式排名已鎖定：流動性合格股票全數計算，不再由 400/700/1000 候選數改變結果。")
        st.caption(f"歷史基準：{CANONICAL_MODEL_POLICY['history_period']}；資料模式：完整資料；公司/法人採全市場可比官方快照，前段標的另載入完整明細。")
        st.caption("短／中／長線多因子各自加權；缺少的大戶、融資、現金流不可代填。美股隔夜情勢僅影響進場與部位，不改股票品質排名。")
        provider_runtime_status()
        st.caption("完整日線：任何時段都可手動更新；盤中不把未收盤日K寫進模型。")
        st.caption(f"盤中買點：每 {int(LIVE_SCAN.get('refresh_seconds',60))} 秒掃描流動性合格全市場，再對最多 {int(LIVE_SCAN.get('detail_max',360))} 檔執行官方價驗證與買點精算。")
        st.caption("盤後：若今日官方 EOD 已發布，完整重算；尚未發布時保留最近正式基準。")
        with st.expander("進階維護（一般更新不需清除快取）", expanded=False):
            busy = scan_status(DATA_DIR).get("state") == "running"
            if st.button("清除全部歷史快取", disabled=busy):
                DailyPriceStore(DATA_DIR / "daily_prices.sqlite").clear()
                service.remove_saved_dashboard(DATA_DIR / "dashboard_snapshot.json")
                st.session_state.pop("alpha_snapshot", None)
                st.warning("已清除本機歷史快取；下次完整掃描需重新抓取多年資料。")

    settings = service.RunSettings(
        canonical_mode=True,
        history_period=str(CANONICAL_MODEL_POLICY["history_period"]),
        model_family=str(CANONICAL_MODEL_POLICY["model_family"]),
    )

    # Async warm cache: rendering and reconnect never waits for Yahoo/RSS.
    ensure_brief(DATA_DIR)
    update_label = "背景更新日線模型"
    l, r = st.columns([1.5, 1.0])
    with l:
        begin_update = st.button(update_label, type="primary", use_container_width=True)
    with r:
        force_refresh = st.button("強制重算｜忽略近期快取", use_container_width=True)
    if begin_update or force_refresh:
        outcome = start_scan(
            DATA_DIR, settings, service.run_scan, snapshot=st.session_state.get("alpha_snapshot"),
            force=bool(force_refresh), now=_taipei_timestamp(),
            expected_model_version=service.OPERATIONS_VERSION,
        )
        st.session_state["alpha_scan_message"] = str(outcome.get("message") or "")
        # Move readers to a useful independent page while the server does the
        # heavy scan.  This does not block or tie the job to the browser socket.
        if outcome.get("started"):
            ensure_brief(DATA_DIR)
    if st.session_state.get("alpha_scan_message"):
        st.caption(st.session_state.get("alpha_scan_message"))
    _scan_status_fragment()

    snap = st.session_state.get("alpha_snapshot")
    _market_header(snap)
    if st.session_state.get("alpha_error"):
        st.error(st.session_state.get("alpha_error"))

    # V18.5 navigation migration.  The engine version is intentionally unchanged:
    # switching layouts must not force a costly multi-year market rescan.
    prior = st.session_state.get('view_v172')
    old_to_new = {'總覽':'今日決策','推薦總表':'選股與買點',
                  '因子透視':'選股與買點','國際市場':'今日決策',
                  '盤中買點':'選股與買點','明日關注':'選股與買點',
                  '短線':'選股與買點','中線':'選股與買點','長線':'選股與買點',
                  '個股':'個股診斷','資料健檢':'今日決策','市場與資料':'今日決策'}
    if prior and prior not in VIEWS:
        st.session_state['view_v172'] = old_to_new.get(prior, '今日決策')
    view = st.radio("功能", VIEWS, horizontal=True, label_visibility="collapsed", key="view_v172")
    phase_now = session_reference_policy(_taipei_timestamp()).get("phase")

    if view == '今日決策':
        brief = load_brief(DATA_DIR)
        tape = {str(r.get('key')):r for r in ((brief.get('global') or {}).get('market_tape') or []) if isinstance(r,dict)}
        if tape:
            show=['dji','spx','nasdaq','sox','tsm_adr']
            st.markdown("<div class='market-mini-strip'>"+''.join(
                f"<span><b>{esc({'dji':'道瓊','spx':'標普','nasdaq':'那斯達克','sox':'費半','tsm_adr':'台積電ADR'}[k])}</b>"
                f" <em class='{'positive' if finite(tape.get(k,{}).get('pct')) is not None and finite(tape.get(k,{}).get('pct'))>=0 else 'negative' if finite(tape.get(k,{}).get('pct')) is not None else 'muted'}'>"
                f"{(f'{finite(tape.get(k,{}).get("pct")):+.2f}%' if finite(tape.get(k,{}).get('pct')) is not None else '待取得')}</em></span>" for k in show
            )+"</div>",unsafe_allow_html=True)
        st.markdown("<div class='section-kicker'>WHAT TO WATCH</div><div class='section-title'>今天的決策重點</div>",unsafe_allow_html=True)
        if snap and assess_snapshot(snap)['sync_state'] != 'ALIGNED':
            st.warning('官方日期尚未完整核對；暫停即時買點，歷史研究榜仍可參考。詳細資料請展開頁面下方的資料來源。')
        elif snap and phase_now == INTRADAY:
            st.caption('盤中買進訊號須有 MIS 官方核價；原始股票品質排名仍固定。')
            intraday_buy_fragment(snap,'short')
        elif snap:
            st.caption('盤前／盤後或休市：下次交易日觀察已合併在下方同一張選股卡，不再重複列一套名單；仍須以實際開盤行情核對。')
        else:
            st.info('尚未有完整選股模型。可先閱讀市場快讀，按上方背景更新建立基準。')
        _render_canonical_board(snap,home=True)
        if st.toggle('國際市場、科技供應鏈與最新消息（需要時展開）',value=False,key='show_market_v188'):
            _reading_fragment()
            _render_taiwan_supply_chain(snap)
        if st.toggle('資料來源與同步狀態（進階）',value=False,key='show_data_v188'):
            _render_data_health(snap,compact=True)
            _render_evidence_coverage(snap)
            if st.button('背景更新美股與新聞',key='brief_manual_refresh'):
                ensure_brief(DATA_DIR,force=True)

    elif view == '選股與買點':
        _render_canonical_board(snap,home=False)
        if snap and assess_snapshot(snap)['sync_state'] == 'ALIGNED':
            with st.expander('完整買點檢核｜盤中即時或下一交易日', expanded=False):
                h=st.radio('選擇持有週期',['short','mid','long'],horizontal=True,
                           format_func=lambda key:H_LABEL[key],key='timing_h_v185')
                if phase_now == INTRADAY:
                    intraday_buy_fragment(snap,h)
                else:
                    _render_next_session(snap,h,n=5)

    else:
        st.markdown("<div class='section-kicker'>STOCK DOCTOR</div><div class='section-title'>個股診斷｜買入或持有怎麼辦</div>",unsafe_allow_html=True)
        _doctor(snap)

    st.caption('V18.8｜原始 Alpha 排名固定，買點另判斷；DQ／MC 是證據品質，現有權重尚未通過完整 PIT 樣本外績效驗證。')


if __name__ == "__main__":
    main()
