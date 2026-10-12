"""Deterministic V18 contract tests; never call live exchanges or providers."""
from __future__ import annotations

import datetime as dt
import numpy as np
import pandas as pd
from unittest.mock import patch

from strategy_config import V18_DIMENSION_WEIGHTS, V18_OVERLAY_BLEND
from v18_multifactor import FACTOR_GROUPS, build_profile, price_signals, sector_context, score_horizon
import radar_service as rs
import recommendation_engine as re


def bars(n=250, falling=False, seed=12):
    x = np.arange(n)
    rng = np.random.default_rng(seed)
    if falling:
        values = np.r_[np.linspace(80, 43, n-55), np.linspace(43, 39,55)]
    else:
        values = np.linspace(52, 72, n) + 1.3*np.sin(x/9)
    values = values + rng.normal(0,0.08,n)
    vols = 1_500_000 + 70_000*np.cos(x/8)
    index = pd.bdate_range('2025-01-02', periods=n)
    return pd.DataFrame({'Open':values*.998,'High':values*1.008,'Low':values*.994,
                         'Close':values,'Volume':vols},index=index)


def test_factor_catalog_exactly_15():
    assert {x:len(v) for x,v in FACTOR_GROUPS.items()} == {'quality':3,'chip':4,'momentum':4,'sector':2,'risk':2}
    all_names = [k for rows in FACTOR_GROUPS.values() for k,_ in rows]
    assert len(set(all_names)) == 15
    for h,weights in V18_DIMENSION_WEIGHTS.items():
        assert abs(sum(weights.values())-1) < 1e-12, h
        assert 0 < V18_OVERLAY_BLEND[h] < 1
    assert V18_DIMENSION_WEIGHTS['short']['momentum'] > V18_DIMENSION_WEIGHTS['long']['momentum']
    assert V18_DIMENSION_WEIGHTS['long']['quality'] > V18_DIMENSION_WEIGHTS['short']['quality']


def test_missing_is_not_invented():
    sig = price_signals(bars(),.02)
    p = build_profile(sig,{}, {},avg_vol_shares=1_000_000)
    assert p['evidence_total']==13
    assert 0 < p['evidence_n'] < 13
    for k in ('revenue','margin','cash','trust','holders','margin_clean','institutions','peers','breadth','macro'):
        assert p['factors'][k]['score'] is None, k
    assert p['dimensions']['quality'] == 50
    assert p['dimensions']['chip'] == 50
    assert p['factors']['squeeze']['score'] is not None
    assert score_horizon({'dimensions': {'quality':50},'evidence_n':0, 'evidence_coverage':0},'short',73)['score'] == 73


def test_official_breadth_one_month_no_fabricated_acceleration():
    sig = price_signals(bars(),0.01)
    redata = {
        'monthly_revenue': {'available':True,'latest':{'month':'2026-09','yoy_pct':25},'rows':[{'month':'2026-09','yoy_pct':25}], 'source':'official breadth'},
        'financials': {'available':True,'gross_margin_latest_pct':37,'gross_margin_4q_avg_pct':None,'quarters':[]},
        'institutional_flow': {'available':True,'sessions':1,'trust_net_lots':2.2,'total_net_lots':4.0,'source':'official breadth'},
    }
    p=build_profile(sig,redata,{},avg_vol_shares=2_000_000)
    assert p['factors']['revenue']['score'] > 50
    assert '未宣稱加速' in p['factors']['revenue']['note']
    assert p['factors']['margin']['score'] is None
    assert p['factors']['cash']['score'] is None
    assert p['factors']['holders']['score'] is None
    assert p['factors']['margin_clean']['score'] is None
    assert p['factors']['trust']['score'] is not None
    assert p['factors']['institutions']['score'] is not None
    assert p['evidence_n'] >= 6


def test_revenue_acceleration_uses_actual_previous_month():
    sig=price_signals(bars(),.015)
    p=build_profile(sig,{'monthly_revenue': {'latest':{'month':'2026-09','yoy_pct':30},
             'rows':[{'month':'2026-09','yoy_pct':30},{'month':'2026-08','yoy_pct':8}]}}
                  ,{},avg_vol_shares=0)
    assert '加速度 +22.0' in p['factors']['revenue']['note']
    assert p['factors']['revenue']['score'] > 65


def test_bearish_squeeze_not_buy_signal():
    df=bars(falling=True)
    # Last 30 days nearly flat below MA60: a bearish squeeze is not 100 points.
    df.loc[df.index[-30:], 'Close'] = np.linspace(38.4, 38.0,30)
    sig=price_signals(df,.0)
    assert not sig['trend_ok']
    assert not sig['breakout_confirmed']
    assert sig['technical_factors']['breakout']['score'] <= 48
    if sig['bb_percentile'] is not None and sig['bb_percentile'] <= .22:
        assert sig['technical_factors']['squeeze']['score'] <= 60


def test_sector_peer_excludes_target_and_requires_same_date():
    codes=('2308','3017','3324','3653','2421')
    liquid=[]
    for i,code in enumerate(codes):
        liquid.append({'ticker':code+'.TW','name':code,'industry':'電子零組件業',
                       'avg_turnover_20d':(i+1)*100_000_000,
                       'v18_price':{'ret20':(.60 if i==0 else .04+i*.002), 'price_date':'2026-10-09','above_ma20': True}})
    ctx=sector_context(liquid)
    a=ctx['2308.TW']
    assert a['available'] and a['peer_count']==4
    assert a['precision']=='curated_supply_chain'
    assert a['sector']=='水冷散熱與電源'
    assert .04 <= a['peer_median_ret20'] <= .06, a
    assert '2308.TW' not in a['leader_tickers']
    liquid[-2]['v18_price']['price_date']='2026-10-08'
    liquid[-1]['v18_price']['price_date']='2026-10-08'
    a=sector_context(liquid)['2308.TW']
    assert not a['available'] and a['peer_count']==2


def test_macro_and_heat_are_entry_only():
    sig=price_signals(bars(),.01)
    # 25% above MA20 -> high heat but still a strong selection candidate.
    sig['bias_ma20_pct']=25
    base={'monthly_revenue':{'latest':{'yoy_pct':20}}}
    p0=build_profile(sig,base,{},avg_vol_shares=0,global_shock=0)
    p1=build_profile(sig,base,{},avg_vol_shares=0,global_shock=95)
    assert p0['risk_penalty_for_entry'] >= 5.5
    assert p1['risk_penalty_for_entry'] > p0['risk_penalty_for_entry']
    for h in ('short','mid','long'):
        r0=score_horizon(p0,h,76)
        r1=score_horizon(p1,h,76)
        assert r0['score']==r1['score'],h
        assert r0['risk_adjusted_watch'] > r1['risk_adjusted_watch']


def test_full_service_rank_integrates_new_profile():
    sig=price_signals(bars(),.01)
    p=build_profile(sig,{'monthly_revenue':{'latest':{'yoy_pct':30}}},{},avg_vol_shares=0)
    stock={'ticker':'2330.TW','price':50.0,'avg_volume_20d':1000000,
        'ranking_research':{},'v18_profile':p,
        'horizons':{'short':{'forecast':{'technical_factor_score':74,'estimate_available':False}},
                    'mid':{'forecast':{'technical_factor_score':74,'estimate_available':False}},
                    'long':{'forecast':{'technical_factor_score':74,'estimate_available':False}}}}
    for h in ('short','mid','long'):
        rs._combined_ranking_score(stock,h,'full')
        b=stock['horizons'][h]
        assert 0 <= b['ranking_score'] <= 100
        assert b['rank_v18']['score']==b['ranking_score']
        assert b['score_components']['v17_preblend']==b['rank_v18']['score_v17']
    assert stock['horizons']['short']['rank_v18']['overlay_weight'] != stock['horizons']['long']['rank_v18']['overlay_weight']


def test_next_session_risk_changes_priority_not_selection():
    def case(penalty):
        c={'price':90.0, 'selection':78.0,'entry':76.0,'expected':0.12,'positive':.65,'reward_risk':2.0,
           'timing':{'action':'等待確認'}, 'zone':{'needs_trigger':False},'confidence':80,'sample_n':45,
           'raw_expected':.13,'profile':{'target_price':108.0}}
        stock={'v18_profile':{'risk_penalty_for_entry':penalty},'horizons':{'short':{'ranking_score':78}}}
        with patch.object(re,'_components',return_value=c):
            return re.evaluate_next_session(stock,'short')
    a=case(0);b=case(6)
    assert b['score'] < a['score']
    assert a['selection_score']==b['selection_score']==78
    assert b['v18_entry_penalty']==6
    assert '等回測' in b['stance']



def test_very_hot_breakout_is_marked_wait_pullback():
    sig = price_signals(bars(), 0.01)
    sig['bias_ma20_pct'] = 23
    sig['breakout_confirmed'] = True
    pro = build_profile(sig, {}, {}, avg_vol_shares=0)
    assert pro['stage'] == '過熱待回測'
    assert pro['risk_penalty_for_entry'] > 0


def test_old_snapshot_does_not_skip_new_v18_scan():
    # A clean V17 file produced just now cannot suppress the new version.
    import tempfile
    from pathlib import Path
    import time
    from scan_job_manager import start_scan, status
    from test_v17_5_experience import sample_snapshot, TZ
    now = dt.datetime(2026, 10, 11, 12, tzinfo=TZ)
    prev = sample_snapshot(now)
    prev['model_version'] = 'v17.1.0-synced-briefing'
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        from scan_job_manager import _atomic_json
        def save_new(*args, **kwargs):
            new = sample_snapshot(now)
            _atomic_json(root / 'dashboard_snapshot.json', new)
            return new
        result = start_scan(root, object(), save_new,
                            snapshot=prev, now=now, expected_model_version='v18.0.0-multifactor-evidence')
        assert result['started'] and result['state'] == 'running', result
        for _ in range(100):
            if status(root)['state'] != 'running':
                break
            time.sleep(0.01)
        assert status(root)['state'] == 'complete', status(root)


def test_v18_factor_page_offline_render():
    import v18_ui
    from contextlib import nullcontext
    class MiniStreamlit:
        def __init__(self): self.html = []
        def markdown(self, txt, **kwargs): self.html.append(str(txt))
        def caption(self, txt): self.html.append(str(txt))
        def radio(self, label, options, **kwargs): return options[0]
        def select_slider(self, label, options, **kwargs): return options[1]
        def selectbox(self, label, options, **kwargs): return options[0]
        def expander(self, *args, **kwargs): return nullcontext()
        def dataframe(self, df, **kwargs): assert 'V18因子分' in df.columns
        def warning(self, txt): raise AssertionError('unexpected warning: '+txt)
        def info(self, txt): raise AssertionError('unexpected info: '+txt)
    sig = price_signals(bars(), .01)
    pro = build_profile(sig, {}, {}, avg_vol_shares=1e6)
    stock = {'name':'測試台積電','ticker':'2330.TW','fine_industry':'半導體',
             'v18_profile':pro,'horizons': {'short':{'ranking_score':81.0,
             'rank_v18': {'score':81,'score_v17':75,'score_v18':86}}}}
    fake = MiniStreamlit()
    snap = {'model_version':'v18.0.0-multifactor-evidence','price_date':'2026-10-09',
            'stocks':[stock], 'v18_factor_summary': {'avg_observed_factors':4.2}}
    v18_ui.render_page(fake, snap, {'available':False})
    combined = ''.join(fake.html)
    assert '測試台積電' in combined
    assert '缺資料' in combined
    assert '選股評分（非買點）' in combined
    assert '國際壓力未取得' in combined


def test_v18_no_new_network_requests():
    from pathlib import Path
    src=(Path(__file__).parent/'v18_multifactor.py').read_text(encoding='utf-8')
    assert 'requests.get' not in src and 'yfinance' not in src and 'http' not in src
    ui = (Path(__file__).parent/'app.py').read_text(encoding='utf-8')
    assert 'VIEWS = ' in ui and '今日決策' in ui and '選股與買點' in ui and '市場與資料' in ui and '個股診斷' in ui


if __name__=='__main__':
    tests=[v for k,v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for test in tests:
        test(); print('PASS',test.__name__)
    print(f'ALL {len(tests)} V18 FACTOR CONTRACT TESTS PASSED')
