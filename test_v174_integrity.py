from v174_integrity import assess_snapshot

def test_none():
    assert assess_snapshot(None)["status"] == "NO_DATA"

def test_sync():
    x={"price_date":"2026-10-08","session":{"common_complete_date":"2026-10-08"},"coverage":{"requested":1800,"twse_universe":1000,"tpex_universe":800,"feature_valid":1650,"official_eod":{"raw_twse_date":"2026-10-08","raw_tpex_date":"2026-10-09"}},"full_universe_model_n":1600}
    r=assess_snapshot(x)
    assert r["status"]=="CHECK" and "2026-10-09" in r["issues"][0]
    x["coverage"]["official_eod"]["raw_tpex_date"]="2026-10-08"
    assert assess_snapshot(x)["status"]=="OK"

def test_bad_count():
    x={"price_date":"2026-10-08","session":{"common_complete_date":"2026-10-08"},"coverage":{"requested":100,"twse_universe":70,"tpex_universe":30,"feature_valid":80,"official_eod":{"raw_twse_date":"2026-10-08","raw_tpex_date":"2026-10-08"}},"full_universe_model_n":110}
    assert any("超過" in i for i in assess_snapshot(x)["issues"])

if __name__ == '__main__':
    test_none();test_sync();test_bad_count();print('V17.4 integrity PASS')
