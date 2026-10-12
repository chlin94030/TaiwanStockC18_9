"""Offline tests for V17.5 UI, market tape and browser-independent jobs."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
import tempfile
import threading
import time
import os
import types

import pandas as pd

import briefing_jobs
import global_context as gc
from market_briefing import _only_recent_news, _rss_description, news_impact, market_tape_insights
from scan_job_manager import _atomic_json, _recent_success, start_scan, status
from v174_integrity import assess_snapshot

TZ = dt.timezone(dt.timedelta(hours=8))


def sample_snapshot(now=None):
    now = now or dt.datetime.now(TZ)
    day = now.date().isoformat()
    return {
        "generated_at": now.isoformat(timespec="seconds"), "price_date": day,
        "snapshot_id": "test_" + now.strftime("%Y%m%d%H%M%S"),
        "full_universe_model_n": 4, "candidate_n": 4,
        "coverage": {"requested": 10, "feature_valid": 8,
                     "twse_universe": 5, "tpex_universe": 5,
                     "official_eod": {"raw_twse_date": day, "raw_tpex_date": day}},
        "session": {"common_complete_date": day, "official_today_complete": True},
        "stocks": [],
    }


def test_background_job_persists_across_client_disconnect():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        done = threading.Event()
        calls = []
        def run(data_dir, settings, progress=None, now=None):
            calls.append("scan")
            progress("正在核對", .43)
            done.wait(0.7)
            snap = sample_snapshot(now)
            _atomic_json(root / "dashboard_snapshot.json", snap)
            return snap
        first = start_scan(root, object(), run, force=True)
        assert first["started"] and first["state"] == "running"
        # A reconnect creates a new UI state but not another server job.
        second = start_scan(root, object(), run, force=True)
        assert second["state"] == "running" and not second["started"]
        assert status(root)["state"] == "running"
        done.set()
        for _ in range(70):
            if status(root)["state"] != "running":
                break
            time.sleep(.025)
        result = status(root)
        assert result["state"] == "complete", result
        assert result["price_date"] == sample_snapshot()["price_date"]
        assert len(calls) == 1, calls
        previous = sample_snapshot()
        recent = start_scan(root, object(), run, snapshot=previous)
        # At POSTCLOSE, a verified same-day snapshot may be reused.  At
        # offday/preopen/intraday, same-phase recent snapshot may be reused.
        assert recent["state"] in {"reused", "running"}, recent
        assert recent.get("started") is False


def test_failed_scan_keeps_last_completed_snapshot():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        last = sample_snapshot()
        _atomic_json(root / "dashboard_snapshot.json", last)
        def fail(*args, **kwargs):
            raise ValueError("mock provider timeout")
        start_scan(root, object(), fail, force=True)
        for _ in range(60):
            if status(root).get("state") == "failed": break
            time.sleep(.02)
        assert status(root)["state"] == "failed"
        assert __import__('json').loads((root / "dashboard_snapshot.json").read_text())["snapshot_id"] == last["snapshot_id"]


def test_scan_cache_skip_is_conservative():
    now = dt.datetime(2026, 10, 11, 14, tzinfo=TZ)  # Sunday / OFFDAY
    clean = sample_snapshot(now)
    assert assess_snapshot(clean)["status"] == "OK"
    assert _recent_success(clean, now, 1200)
    bad = sample_snapshot(now)
    bad["coverage"]["official_eod"]["raw_tpex_date"] = "2026-10-10"
    assert not _recent_success(bad, now, 1200)
    assert not _recent_success(clean, now + dt.timedelta(hours=2), 1200)
    monday_post = dt.datetime(2026, 10, 12, 16, tzinfo=TZ)
    badpost = sample_snapshot(monday_post)
    badpost["session"]["official_today_complete"] = False
    assert not _recent_success(badpost, monday_post, 1200)


def test_briefing_background_is_nonblocking_and_never_scrubs_previous():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        os.environ["ALPHA_RADAR_DISABLE_GLOBAL_NETWORK"] = "1"
        os.environ["ALPHA_RADAR_DISABLE_NEWS_NETWORK"] = "1"
        start = time.monotonic()
        state = briefing_jobs.ensure_brief(root, force=True)
        assert time.monotonic() - start < 1
        assert state["state"] == "running"
        for _ in range(120):
            if not briefing_jobs.load_brief(root).get("refreshing"):
                break
            time.sleep(.02)
        result = briefing_jobs.load_brief(root)
        assert result["global_summary"]["headline"], result
        assert isinstance(result.get("news"), dict)


def test_news_no_old_article_and_no_fake_full_text():
    now = dt.datetime(2026, 10, 11, 12, tzinfo=TZ)
    recent = now - dt.timedelta(hours=2)
    ancient = now - dt.timedelta(days=36)
    sample = {"available": True, "items": [
        {"title": "36天以前的舊消息", "published_iso": ancient.isoformat()},
        {"title": "近兩小時的消息", "published_iso": recent.isoformat(), "topic": "半導體／AI", "bias": "待判讀"},
    ]}
    filtered = _only_recent_news(sample, now=now, max_age_hours=96)
    assert len(filtered["items"]) == 1
    assert filtered["items"][0]["title"] == "近兩小時的消息"
    assert "半導體" in filtered["items"][0]["impact"]
    assert _rss_description("<a>Test heading</a>", "Test heading") == ""
    assert "政策" in news_impact("政策／地緣", "可能偏空", "美國關稅調整")


def test_tape_insights_and_source_dates():
    g = {"market_tape": [
        {"key":"sox", "date":"2026-10-09", "pct":-2.6, "close": 6200},
        {"key":"tsm_adr", "date":"2026-10-09", "pct":-1.8, "close": 315},
        {"key":"ndx", "date":"2026-10-09", "pct":-1.2, "close": 24550},
        {"key":"spx", "date":"2026-10-09", "pct":-0.5, "close": 7300},
        {"key":"vix", "date":"2026-10-09", "pct":+5.1, "close": 23.4},
    ]}
    insight = market_tape_insights(g)
    assert len(insight) >= 3
    assert "晶片" in insight[0]["title"]
    assert "-2.60%" in insight[0]["detail"]
    assert market_tape_insights({}) == []


def test_yahoo_batch_tape_levels_from_single_download():
    keys = gc.SYMBOLS
    idx = pd.DatetimeIndex(["2026-10-08", "2026-10-09"])
    frame = pd.DataFrame(index=idx)
    for symbol in keys.values():
        frame[(symbol, "Close")] = [100.0, 105.0]
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    original = gc.yf
    fake = types.SimpleNamespace(download=lambda *args, **kwargs: frame if kwargs.get("interval") == "1d" else pd.DataFrame())
    try:
        gc.yf = fake
        returns, day, tape = gc._download_returns()
        assert day == "2026-10-09"
        assert abs(returns["spx"] - 5.0) < 1e-9
        assert abs(next(row for row in tape if row["key"] == "spx")["change"] - 5.0) < 1e-9
        assert all(row["date"] for row in tape)
        assert "dji" in {row["key"] for row in tape}
    finally:
        gc.yf = original


def test_visual_board_balances_periods_and_no_double_counting():
    from smoke_app_import import app
    df = pd.DataFrame([
        {"清單": src, "週期": horizon, "排名": rank, "代號": code, "股票": code, "買點分數": 70}
        for src in ("完整日線", "明日關注")
        for horizon, abbr in (("短線 · 約2週", "S"), ("中線 · 約2個月", "M"), ("長線 · 約6個月", "L"))
        for rank in range(1, 6)
        for code in [abbr + str(rank)]
    ])
    recs = app._board_unique_records(df)
    assert len(recs) == 15, len(recs)
    assert {r["primary"]["代號"] for r in recs[:3]} == {"S1", "M1", "L1"}, recs[:3]
    assert all(len(r["signals"]) == 2 for r in recs)
    assert all(r["primary"]["清單"] == "明日關注" for r in recs)


def test_next_session_action_not_mislabeled_as_daily_action():
    from smoke_app_import import app
    stock = {"ticker": "2330.TW", "name": "假資料", "price": 100,
             "horizons": {"short": {"ranking_score": 91,
                                    "entry_timing": {"score": 40, "action": "日線等回測"},
                                    "plan": {"zone_low": 88, "zone_high": 92, "invalidation": 85}}},
             "next_session": {"entry_score": 77, "stance": "明日突破後再進場",
                              "buy_zone": {"low": 93, "high": 96}}}
    daily = app._compact_pick_record(stock, "short", 1, "完整日線")
    tomorrow = app._compact_pick_record(stock, "short", 1, "明日關注")
    assert daily["建議動作"] == "日線等回測" and daily["建議區間"] == "88–92"
    assert tomorrow["建議動作"] == "明日突破後再進場", tomorrow
    assert tomorrow["建議區間"] == "93–96"
    assert tomorrow["買點分數"] == "77"
    assert daily["買點分數"] == "40" # Regression: nfmt(..., d=0) must preserve 0
    assert app.nfmt(80, 0) == "80" and app.nfmt(100, 0) == "100"
    assert tomorrow["標的分數"] == daily["標的分數"]


def test_full_screen_html_contains_news_digest_without_leaving_page():
    from smoke_app_import import app
    import streamlit as st
    output = []
    st.markdown = lambda value, **kw: output.append(str(value))
    st.caption = lambda value, **kw: output.append(str(value))
    st.info = lambda value, **kw: output.append(str(value))
    st.warning = lambda value, **kw: output.append(str(value))
    st.expander = lambda *args, **kw: types.SimpleNamespace(__enter__=lambda self: self, __exit__=lambda self, *args: None)
    g = {"available": True, "asof": "2026-10-09", "fetched_at": "2026-10-11T10:00:00+08:00",
         "shock_score": 35, "mode":"NORMAL", "components":{}, "returns_pct": {},
         "market_tape": [{"key":"dji", "date":"2026-10-09", "close": 48000,
                         "previous": 47600, "change": 400, "pct": .84}]}
    brief = {"global": g, "news": {"available": True, "items": [
        {"title": "<script>alert(1)</script>台股消息", "summary": "公開資訊摘要", "impact": "關注台股成交量", "topic": "市場", "published": "10/11 10:00", "source": "模擬來源", "link": "https://example.com"}
    ]}}
    app._render_waiting_briefing(brief)
    html = "\n".join(output)
    assert "48,000.00" in html
    assert "公開資訊摘要" in html and "關注台股成交量" in html
    assert "&lt;script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "target='_blank'" in html


if __name__ == "__main__":
    funcs = [v for k,v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in funcs:
        fn()
        print("PASS", fn.__name__, flush=True)
    print("ALL V17.5 EXPERIENCE TESTS PASSED")
