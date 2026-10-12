"""V18.7 mobile traffic lights / SOX recovery / full-universe readiness tests.

All fixtures are synthetic. No price/network dependency and no trading
performance is inferred from PASS.
"""
from __future__ import annotations
from contextlib import contextmanager
import datetime as dt
import types
from unittest.mock import patch

import numpy as np
import pandas as pd

import global_context as gc
from v187_experience import (US_WATCH_GROUPS,US_WATCH_SYMBOLS,TW_WATCH_GROUPS,
    signal_light,entry_readiness,prioritize_next_session,taiwan_watch_snapshot)


def stock(ticker='2330.TW',score=83.,entry=83.,confidence=85.,samples=65,
          median=.18,chase='低', action='結構正常',penalty=0.):
    return {'ticker':ticker,'name':ticker,'price':100.0,'price_date':'2026-10-08',
            'avg_turnover_20d':150_000_000,
            'v18_profile':{'risk_penalty_for_entry':penalty},
            'horizons':{'short':{
                'ranking_score':score,
                'score_components':{'evidence_coverage':.95},
                'forecast':{'estimate_available':True,'local_effective_n':samples,
                    'confidence_score':confidence,'strategy':{
                        'median':median,'mean':median,'p10':-.055,
                        'smoothed_positive_rate':.70}},
                'entry_timing':{'score':entry,'chase_risk':chase,'action':action},
                'position_guidance':{'percent':30},
                'plan':{'reference_close':100.,'atr14':3.,'zone_low':98.,
                        'zone_high':102.,'trigger':99.5,'chase_limit':105.,
                        'invalidation':96.}}}}


def test_main_semiconductor_index_is_always_sox_not_soxx():
    assert gc.SYMBOLS['sox']=='^SOX'
    assert 'soxx' not in gc.SYMBOLS and 'smh' not in gc.SYMBOLS
    assert len(US_WATCH_SYMBOLS)>=11
    assert sum(map(len,US_WATCH_GROUPS.values()))>=15


def test_sox_single_download_recovers_missing_batch_index():
    from unittest.mock import Mock
    class F:
        @staticmethod
        def download(symbols,**kwargs):
            if kwargs.get('interval')=='5m':
                return pd.DataFrame()
            symbols=[x for x in symbols if x!='^SOX']
            index=pd.to_datetime(['2026-10-08','2026-10-09'])
            return pd.DataFrame({(x,'Close'):[100,101] for x in symbols},index=index)
        @staticmethod
        def Ticker(symbol):
            assert symbol=='^SOX'
            return types.SimpleNamespace(history=lambda **kw:pd.DataFrame(
                {'Close':[12000.,12500.]},index=pd.to_datetime(['2026-10-08','2026-10-09'])))
    with patch.object(gc,'yf',F):
        ret,asof,tape=gc._download_returns()
    sox=[r for r in tape if r['key']=='sox']
    assert len(sox)==1,sox
    assert sox[0]['close']==12500 and sox[0]['date']=='2026-10-09'
    assert '重試' in sox[0]['source'] and ret['sox']>4
    assert asof=='2026-10-09'


def test_sox_chart_fallback_fails_closed_if_symbol_differs():
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'chart':{'result':[{'meta':{'symbol':'SOXX'},
            'timestamp':[1791482400,1791568800],
            'indicators':{'quote':[{'close':[12000,12400]}]}}]}}
    with patch('requests.get',return_value=Response()):
        assert gc._sox_chart_fallback() is None


def test_missing_index_tile_does_not_invent_zero_or_vanish():
    from smoke_app_import import app
    import streamlit as st
    out=[]
    class Dummy:
        def __enter__(self):return self
        def __exit__(self,*args):return False
    old={k:getattr(st,k,None) for k in ('markdown','caption','warning','expander')}
    try:
        st.markdown=lambda data,**kw:out.append(str(data))
        st.caption=lambda data,**kw:out.append(str(data))
        st.warning=lambda data,**kw:out.append(str(data))
        st.expander=lambda *a,**kw:Dummy()
        app._render_plain_global_context({'market_tape':[{'key':'spx','close':5000,'pct':1.2,'change':59,'date':'2026-10-09'}]})
        html='\n'.join(out)
        assert '費城半導體' in html and '待取得' in html
        assert '美股科技供應鏈' in html and '艾司摩爾' in html
    finally:
        for k,v in old.items():
            if v is None:delattr(st,k)
            else:setattr(st,k,v)


def test_four_sensible_light_states_and_not_buy_signal():
    assert signal_light(84,'alpha')['label']=='值得優先研究'
    assert signal_light(61,'alpha')['color']=='amber'
    assert signal_light(59,'dq')['label']=='部分證據缺漏'
    assert signal_light(42,'mc')['color']=='red'
    assert signal_light(None,'mc')['color']=='gray'
    assert '買' not in signal_light(99,'alpha')['label']


def test_next_session_can_find_ready_eod_setup_without_fake_trade():
    s=stock()
    rd=entry_readiness(s,'short')
    assert rd['code']=='READY_SETUP',rd
    assert rd['is_live_buy'] is False
    assert '官方即時價' in rd['reason']
    assert rd['reference_zone']['low'] <= s['price'] <= rd['reference_zone']['high']


def test_safety_guardrails_do_not_promote_missing_data():
    s=stock(samples=0)
    assert entry_readiness(s,'short')['code']=='EVIDENCE'
    assert entry_readiness(stock(chase='極高'),'short')['code']=='OVERHEAT'
    assert entry_readiness(stock(penalty=5),'short')['code']=='OVERHEAT'
    assert entry_readiness(stock(median=-.05),'short')['code']=='EDGE'
    assert entry_readiness(stock(entry=40),'short')['code']!='READY_SETUP'


def test_fresh_macro_penalty_can_block_ready_without_affecting_alpha():
    s=stock(score=88,penalty=0)
    next_session={'v18_entry_penalty':6.5,'v184_macro_entry_penalty':6.5}
    a=entry_readiness(s,'short',next_session)
    assert a['code']=='OVERHEAT',a
    assert '隔夜' in a['reason']
    assert s['horizons']['short']['ranking_score']==88
    assert not a['is_live_buy']


def test_ready_first_uses_full_pool_and_does_not_modify_alpha():
    # A higher-ranking research stock has no good entry; the lower one is ready.
    a=stock('2330.TW',score=95,entry=40)
    b=stock('2382.TW',score=77,entry=83)
    snap={'stocks':[a,b]}
    before=[a['horizons']['short']['ranking_score'],b['horizons']['short']['ranking_score']]
    candidates=prioritize_next_session(snap,horizon='short',n=2)
    assert len(candidates)==2
    assert candidates[0]['ticker']=='2382.TW',[(x['ticker'],x['next_session']['readiness']) for x in candidates]
    assert [a['horizons']['short']['ranking_score'],b['horizons']['short']['ranking_score']]==before
    assert not candidates[0]['next_session']['readiness']['is_live_buy']


def test_supply_chain_watch_is_classified_and_from_snapshot_only():
    s1=stock('2382.TW');s1['name']='廣達'
    s2=stock('2330.TW');s2['name']='台積電'
    groups=taiwan_watch_snapshot({'stocks':[s1,s2]})
    assert any(x['category'].startswith('電子中游') for x in groups['AI伺服器與組裝'])
    assert any(x['category'].startswith('半導體') for x in groups['先進半導體／封測'])
    assert all(x['ticker'].split('.')[0] in ('2330','2382') for rows in groups.values() for x in rows)
    assert sum(map(len,TW_WATCH_GROUPS.values()))>=40


def test_app_now_keeps_three_clear_destinations_and_explanations_visible():
    from pathlib import Path
    source=Path('app.py').read_text(encoding='utf-8')
    assert 'VIEWS = ["今日決策", "選股與買點", "個股診斷"]' in source
    assert '_render_score_guide()' in source
    assert '_render_canonical_board(snap,home=True)' in source
    assert "_render_evidence_coverage(snap)" in source


if __name__=='__main__':
    tests=[v for k,v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for t in tests:
        t(); print('PASS',t.__name__,flush=True)
    print(f'V18.7 USER-EXPERIENCE TESTS: {len(tests)} PASS')
