"""Acquire REAL market-history source data for V18.9 walk-forward replay.

Data comes from yfinance and the exchange-listed-company universe. This is a
CURRENT survivor universe unless the user adds historical membership sources.
The data acquisition program is separate from the strictly offline validator
so an unreachable provider can never be silently replaced by synthetic prices.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import random
import sys

from market_data import DailyPriceStore, fetch_twse_universe


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--db', type=Path, default=Path('data/validation_prices.sqlite'))
    p.add_argument('--stocks', type=int, default=250,
                   help='0 = 全部目前上市櫃普通股（耗時較長）；正整數 = 隨機抽樣（非全市場）')
    p.add_argument('--seed', type=int, default=20261012)
    p.add_argument('--period', default='8y')
    p.add_argument('--out', type=Path, default=Path('data/historical_replay_v189'))
    a = p.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    try:
        u = fetch_twse_universe()
        if u is None or u.empty or 'ticker' not in u.columns:
            raise ValueError('TWSE/TPEx 真實股票名單無法取得')
        all_names = [str(x) for x in u['ticker'].dropna().unique()
                     if len(str(x).split('.')[0]) == 4 and str(x).split('.')[0].isdigit()
                     and not str(x).split('.')[0].startswith('0')
                     and str(x).endswith(('.TW', '.TWO'))]
        all_names = sorted(set(all_names))
        if not all_names:
            raise ValueError('上市櫃普通股母體為空')
        if a.stocks > 0 and a.stocks < len(all_names):
            selected = sorted(random.Random(a.seed).sample(all_names, a.stocks))
            coverage = 'RANDOM_CURRENT_SURVIVORS_SAMPLE'
        else:
            selected = all_names
            coverage = 'ALL_CURRENT_SURVIVORS'
        # Write explicit source/provenance metadata; don't mistake today's
        # listings for a complete historical membership directory.
        metadata = {
            'status':'FETCHING_REAL_DATA', 'source':'yfinance adjusted OHLCV',
            'membership':'TWSE/TPEx CURRENT common stocks',
            'scope':coverage, 'historical_delisted_included':False,
            'universe_count':len(all_names), 'requested_stocks':len(selected),
            'download_period':a.period,
        }
        (a.out/'data_acquisition.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
        store = DailyPriceStore(a.db)
        result = store.batch_fetch_and_update(['^TWII', *selected], period=a.period,
                force_full=True, chunk_size=40)
        counts = store._coverage_counts(['^TWII', *selected])
        sufficient = [t for t in selected if int(counts.get(t, 0)) >= 520]
        if counts.get('^TWII',0) < 520 or len(sufficient) < 50:
            raise ValueError(f'真實行情不足：加權指數 {counts.get("^TWII",0)} 筆、股票 {len(sufficient)}/{len(selected)} 檔')
        metadata.update({'status':'REAL_PRICES_READY','valid_histories_ge520':len(sufficient),
                         'download_stats':result,'benchmark_rows':counts.get('^TWII')})
        (a.out/'data_acquisition.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print(json.dumps({'status':'REAL_PRICES_READY','benchmark_rows':counts['^TWII'],
                          'stock_count':len(sufficient),'db':str(a.db)},ensure_ascii=False))
        return 0
    except Exception as exc:
        status={'status':'DATA_FETCH_FAILED','error':f'{type(exc).__name__}: {exc}',
                'message':'拒絕用合成股價代替。請提供真實 daily_prices.sqlite。'}
        (a.out/'data_acquisition_error.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(status,ensure_ascii=False),file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
