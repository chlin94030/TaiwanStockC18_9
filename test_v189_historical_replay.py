"""Engineering tests with SYNTHETIC fixtures only; no claims of real alpha."""
from __future__ import annotations
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
import numpy as np
import pandas as pd

from historical_replay_v189 import (ReplayConfig, run, get_ohlcv, random_historical_dates,
                                   future_stats, apply_confidence_free_rank_tech_emp, main)
from radar_service import _combined_ranking_score
from strategy_config import SELECTION_WEIGHTS
from return_first_model import estimate_all_horizons
from radar_service import RunSettings


def synthetic_database(path: Path, stocks: int = 16, bars: int = 1080, seed: int = 20261012):
    """Temporary unit-test input, NEVER shipped as a market historical dataset."""
    idx = pd.bdate_range('2020-01-01', periods=bars)
    rng=np.random.default_rng(seed)
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE daily_prices (ticker TEXT,date TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,PRIMARY KEY(ticker,date))')
        for i in range(stocks+1):
            ticker='^TWII' if i==stocks else f'{1101+i:04d}.TW'
            daily=rng.normal(.0004 if i==stocks else .00045 + .00008*i,.018 if i!=stocks else .010,len(idx))
            close=100*np.exp(np.cumsum(daily))
            open_=close*(1+rng.normal(0,.003,len(idx)))
            high=np.maximum(open_,close)*1.005
            low=np.minimum(open_,close)*.995
            vol=np.maximum(100000,1000000*(1+rng.normal(0,.15,len(idx))))
            db.executemany('INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?)',[
                (ticker,str(d.date()),float(o),float(h),float(l),float(c),float(v))
                for d,o,h,l,c,v in zip(idx,open_,high,low,close,vol)
            ])
        db.commit()
    return idx


class ValidationTests(unittest.TestCase):
    def test_fail_closed_no_price_database(self):
        with tempfile.TemporaryDirectory() as td:
            rc=main(['--db',str(Path(td)/'absent.sqlite'),'--out',str(Path(td)/'results')])
            self.assertEqual(rc,2)
            self.assertTrue((Path(td)/'results'/'VALIDATION_NOT_RUN.md').exists())
            self.assertFalse((Path(td)/'results'/'historical_top5_real.csv').exists())

    def test_dates_repeatable_and_matured(self):
        with tempfile.TemporaryDirectory() as td:
            dbp=Path(td)/'test.sqlite'
            synthetic_database(dbp,stocks=9,bars=1050)
            with closing(sqlite3.connect(dbp)) as conn:
                bm=get_ohlcv(conn,'^TWII')
            cfg=ReplayConfig(dbp,Path(td)/'r',start='2021-01-01',end='2023-11-30',repeats=3,per_repeat=4)
            a=random_historical_dates(bm,cfg)
            b=random_historical_dates(bm,cfg)
            self.assertEqual(a,b)
            self.assertEqual(len(a),12)
            self.assertEqual(len(set(x['cutoff_date'] for x in a)),12)
            for x in a:
                self.assertLess(bm.index.get_loc(pd.Timestamp(x['cutoff_date']))+120,len(bm))

    def test_next_open_not_signal_close_and_missing_days_blocked(self):
        d=pd.bdate_range('2024-01-01',periods=180)
        bm=pd.DataFrame({'Open':100.,'Close':101.,'Low':99.,'High':102.,'Volume':500000},index=d)
        s=bm.copy();s.iloc[11,s.columns.get_loc('Open')]=110.
        s.iloc[20,s.columns.get_loc('Close')]=115.
        data=future_stats(s,bm,str(d[10].date()),10,0.00685)
        self.assertEqual(data['entry_open'],110.)
        self.assertEqual(data['exit_close'],115.)
        self.assertAlmostEqual(data['net_return'],115/110-1-.00685)
        self.assertEqual(data['entry_date'],str(d[11].date()))
        suspend=s.drop(index=d[15])
        bad=future_stats(suspend,bm,str(d[10].date()),10,0.00685)
        self.assertEqual(bad['exit_status'],'MISSING_SESSION_OR_SUSPENDED')
        self.assertIsNone(bad['net_return'])

    def test_price_only_matches_production_score(self):
        for h in ('short','mid','long'):
            tech=82.1;emp=67.2
            w=SELECTION_WEIGHTS[h]
            share=w['technical']/(w['technical']+w['empirical'])
            score=apply_confidence_free_rank_tech_emp(tech,emp,share)
            stock={'horizons':{h:{'forecast':{'technical_factor_score':tech,'empirical_quality_score':emp,
                                               'confidence_score':99.0,'estimate_available':True}}},
                   'ranking_research':{},'avg_volume_20d':2000000}
            _combined_ranking_score(stock,h,'price_only')
            self.assertAlmostEqual(score,stock['horizons'][h]['ranking_score'],delta=0.12)

    def test_future_price_cannot_change_asof_scores(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'fake.sqlite'
            idx=synthetic_database(path,stocks=10,bars=900)
            with closing(sqlite3.connect(path)) as con:
                stock=get_ohlcv(con,'1101.TW');bm=get_ohlcv(con,'^TWII')
            cutoff=idx[680]
            settings=RunSettings(model_family='price_only')
            a=estimate_all_horizons(stock.loc[:cutoff],settings,benchmark_df=bm.loc[:cutoff])
            corrupted=stock.copy()
            corrupted.loc[corrupted.index>cutoff,'Close'] *= 1.7
            b=estimate_all_horizons(corrupted.loc[:cutoff],settings,benchmark_df=bm.loc[:cutoff])
            self.assertEqual(a,b)

    def test_full_pipeline_generates_lists_but_only_synthetic_test(self):
        with tempfile.TemporaryDirectory() as td:
            dbp=Path(td)/'synthetic_only.sqlite'
            synthetic_database(dbp,stocks=16,bars=1100)
            cfg=ReplayConfig(db=dbp,out=Path(td)/'result',start='2021-12-01',end='2023-07-01',
                             repeats=2,per_repeat=3,min_pool=8,min_history=380,max_stocks=0)
            report=run(cfg)
            self.assertEqual(report['status'],'COMPLETED_LIMITED_PRICE_ONLY')
            self.assertFalse(report['weights_modified'])
            result=pd.read_csv(cfg.out/'historical_top5_real.csv')
            self.assertEqual(len(result),2*3*3*5)
            self.assertEqual(result.groupby(['cutoff_date','horizon']).size().min(),5)
            self.assertEqual(result.groupby(['cutoff_date','horizon']).size().max(),5)
            self.assertTrue(result['exit_status'].isin(['VALUED_PRICE_ONLY','MISSING_SESSION_OR_SUSPENDED','CORPORATE_ACTION_REVIEW']).all())
            self.assertTrue((cfg.out/'weight_research_only.json').exists())


if __name__=='__main__':
    unittest.main(verbosity=2)
