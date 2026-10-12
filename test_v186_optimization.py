"""V18.6 offline optimization contracts. No external provider access."""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from market_data import DailyPriceStore, _sqlite_connect, _sqlite_write_retry, provider_runtime_status
from radar_service import RunSettings, model_candidates
from return_first_model import estimate_all_horizons


def test_sqlite_transaction_closed_and_wal():
    with tempfile.TemporaryDirectory() as td:
        store = DailyPriceStore(Path(td) / "prices.sqlite")
        with _sqlite_connect(store.db_path) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
            conn.execute("INSERT INTO store_meta VALUES ('probe','ok')")
        try:
            conn.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            pass
        else:
            raise AssertionError("the committed SQLite connection leaked")
        assert store._meta_get("probe") == "ok"
        try:
            with _sqlite_connect(store.db_path) as broken:
                broken.execute("INSERT INTO store_meta VALUES ('rolled_back','wrong')")
                raise ValueError("rollback")
        except ValueError:
            pass
        assert store._meta_get("rolled_back") == ""
        assert provider_runtime_status(store)["sqlite_connected"] is True


def test_limit_applies_after_cutoff_and_reader_streams():
    with tempfile.TemporaryDirectory() as td:
        store = DailyPriceStore(Path(td) / "prices.sqlite")
        rows = [("2330.TW", f"2026-10-{day:02d}", 100., 102., 99., float(day), 100_000.)
                for day in range(1, 11)]
        _sqlite_write_retry(store.db_path, "INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?)", rows, many=True)
        target = store.get_prices("2330.TW", limit=3, end_date="2026-10-05")
        assert [x.strftime("%Y-%m-%d") for x in target.index] == ["2026-10-03", "2026-10-04", "2026-10-05"]
        streamed = dict(store.iter_prices(["2330.TW"], limit=3, end_date="2026-10-05"))
        pd.testing.assert_frame_equal(streamed["2330.TW"], target)


def test_parallel_reads_and_transactional_writes():
    with tempfile.TemporaryDirectory() as td:
        store = DailyPriceStore(Path(td) / "prices.sqlite")
        sql = "INSERT OR REPLACE INTO daily_prices VALUES (?,?,?,?,?,?,?)"
        def write(k):
            ticker = f"{2200+k}.TW"
            for batch in range(7):
                rows = [(ticker, f"2026-06-{d:02d}", 10., 11., 9., 10., 1000.)
                        for d in range(1 + batch*3, 4 + batch*3)]
                _sqlite_write_retry(store.db_path, sql, rows, many=True)
            return ticker
        def read(k):
            for _ in range(30):
                store.get_prices(f"{2200+k}.TW")
            return True
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            tasks = [pool.submit(write, i) for i in range(4)] + [pool.submit(read, i) for i in range(4)]
            assert all(f.result(timeout=50) for f in tasks)
        for i in range(4):
            assert len(store.get_prices(f"{2200+i}.TW")) == 21


def test_roc_date_parsing_rejects_invalid_dates():
    for value in ("1151009", "115/10/9", "115-10-09", "115.10.09", "20261009", "2026-10-09", "115\u5e7410\u67089\u65e5"):
        assert DailyPriceStore._roc_date(value) == "2026-10-09", value
    for value in ("115/02/31", "2026-13-01", "0000000", None, "2026abc"):
        assert DailyPriceStore._roc_date(value) == "", value


def test_yahoo_bad_ohlc_never_written():
    with tempfile.TemporaryDirectory() as td:
        store = DailyPriceStore(Path(td) / "prices.sqlite")
        index = pd.to_datetime(["2026-10-05", "2026-10-06", "2026-10-07"])
        raw = pd.DataFrame({"Open":[100.,100.,100.], "High":[102.,88.,102.], "Low":[99.,99.,99.],
                            "Close":[101.,101.,101.], "Volume":[12000.,12000.,-200.]}, index=index)
        fake = SimpleNamespace(download=lambda *args, **kwargs: raw.copy())
        with patch("market_data.yf", fake):
            outcome = store.batch_fetch_and_update(["2330.TW"], period="5y", chunk_size=1)
        assert outcome["downloaded_tickers"] == 1
        prices = store.get_prices("2330.TW")
        assert len(prices) == 1
        assert prices.index[0].strftime("%Y-%m-%d") == "2026-10-05"


def test_analog_golden_no_strategy_drift():
    rng = np.random.default_rng(12345)
    ix = pd.bdate_range("2020-01-02", periods=1150)
    price = 75 * np.exp(np.cumsum(rng.normal(0.00035, 0.018, len(ix))))
    index = 12000 * np.exp(np.cumsum(rng.normal(0.00025, 0.009, len(ix))))
    frame = pd.DataFrame({"Open":price*.998,"High":price*1.013,"Low":price*.985,
                          "Close":price,"Volume":rng.integers(800_000,3_000_000,len(ix))},index=ix)
    benchmark = pd.DataFrame({"Close":index},index=ix)
    result = estimate_all_horizons(frame, RunSettings(), benchmark_df=benchmark)
    reference = {
        "short": (65.2, 62.1, 44.0, 100, 0.986),
        "mid": (73.1, 59.9, 60.4, 100, 1.361),
        "long": (81.4, 42.8, 66.3, 36, 1.341),
    }
    for h, expected in reference.items():
        row = result[h]
        observed = (row["composite_factor_score"], row["confidence_score"],
                    row["empirical_quality_score"], int(row["local_effective_n"]),
                    row["analog_median_distance"])
        assert all(abs(float(x) - float(y)) < 0.051 for x,y in zip(observed, expected)), (h, observed)


def test_full_market_contract_and_three_page_ui():
    names = [{"ticker":f"{2000+i}.TW"} for i in range(1600)]
    assert len(model_candidates(names, RunSettings(canonical_mode=True,candidate_size=100))) == 1600
    source = (Path(__file__).parent / "app.py").read_text(encoding="utf-8")
    assert 'VIEWS = ["\u4eca\u65e5\u6c7a\u7b56", "\u9078\u80a1\u8207\u8cb7\u9ede", "\u500b\u80a1\u8a3a\u65b7"]' in source
    assert "MIS" in (Path(__file__).parent / "intraday_engine.py").read_text(encoding="utf-8")


if __name__ == "__main__":
    tests = [obj for name,obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    for fn in tests:
        fn(); print("PASS", fn.__name__)
    print(f"V18.6 OPTIMIZATION: {len(tests)} OFFLINE TESTS PASSED")
