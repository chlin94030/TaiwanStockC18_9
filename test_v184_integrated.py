"""V18.4 offline PIT/DQ/MC/universe/entry separation integration suite."""
from __future__ import annotations
import datetime as dt
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from pit_data_manager import (PITLedger, asof_check, check_pit_validity,
                              annotate_breadth_live, load_breadth_replay, is_timely)
from universe_archive import UniverseArchive
from v18_confidence_engine import (FactorEvidence, FactorStatus, calculate_dq_and_mc,
                                   weights_for, fuse_alpha_scores, evaluate_recommendation_eligibility)
from v18_rank_stability import apply_rank_stability
from v18_multifactor import build_profile, score_horizon, price_signals
from recommendation_engine import _v184_global_entry_penalty, evaluate_next_session
from strategy_config import SELECTION_WEIGHTS, V18_DIMENSION_WEIGHTS
from test_v18_multifactor import bars

NOW='2026-10-11T09:30:00+08:00'
EARLY='2026-10-08T14:00:00+08:00'


def ev(fid='revenue',status=FactorStatus.VERIFIED,score=90,reliability=.90,category='quality',
       period='2026-08-31',first_seen='2026-09-10T12:00:00+08:00',verified=True):
    return FactorEvidence(fid,status,score,reliability,category,
                          period_end=period,first_seen_at=first_seen,pit_validated=verified)


def test_weights_and_frozen_original_model():
    assert {k: sum(v.values()) for k,v in SELECTION_WEIGHTS.items()} == {'short':1.0,'mid':1.0,'long':1.0}
    for h in ('short','mid','long'):
        assert abs(sum(weights_for(h).values())-1)<1e-12
        assert len(weights_for(h))==13
        assert abs(sum(V18_DIMENSION_WEIGHTS[h].values())-1)<1e-12
    assert 'macro' not in weights_for('long')


def test_missing_factors_remain_in_denominator():
    one=[ev()]
    short=calculate_dq_and_mc(one,'short')
    assert 0 < short['dq_score'] < 12, short
    assert short['v18_pure_alpha']==90
    assert short['mc_score'] is None  # no fake 70/80 default
    assert short['independent_categories_count']==1


def test_imputation_no_punishment_and_no_invention():
    assert fuse_alpha_scores(78, None,0,.55)['score']==78
    low=fuse_alpha_scores(78,90,35,.55)
    high=fuse_alpha_scores(78,90,95,.55)
    assert 78 < low['score'] < high['score'] < 90


def test_fake_verified_without_provenance_is_rejected():
    unknown=ev(first_seen=None, verified=False)
    p=calculate_dq_and_mc([unknown],'short')
    assert p['dq_score']==0 and p['v18_pure_alpha'] is None
    unknown2=ev(verified=False)
    assert calculate_dq_and_mc([unknown2],'short')['dq_score']==0


def test_pending_requires_real_prior_observation_and_valid_until():
    pending=ev(status=FactorStatus.PENDING)
    assert calculate_dq_and_mc([pending],'short')['dq_score']==0
    good=FactorEvidence('revenue', FactorStatus.PENDING,75,.7,'quality',
                        period_end='2026-08-31',first_seen_at='2026-09-09T12:00:00+08:00',
                        pit_validated=True,valid_until='2026-10-15T23:59:59+08:00',decision_at=NOW)
    assert calculate_dq_and_mc([good],'short')['dq_score']>0
    from dataclasses import replace
    assert calculate_dq_and_mc([replace(good,decision_at='2026-10-16T08:00:00+08:00')], 'short')['dq_score']==0
    assert calculate_dq_and_mc([replace(good,decision_at='2026-09-01T12:00:00+08:00')], 'short')['dq_score']==0


def test_not_applicable_is_excluded_missing_is_not():
    normal=calculate_dq_and_mc([ev()],'long')['dq_score']
    fin=calculate_dq_and_mc([ev(),FactorEvidence('margin',FactorStatus.NOT_APPLICABLE,None),
                                  FactorEvidence('cash',FactorStatus.NOT_APPLICABLE,None)],'long')
    assert fin['dq_score']>normal, (fin,normal)
    assert set(fin['excluded_not_applicable'])=={'margin','cash'}


def test_out_of_sample_unknown_not_invented():
    p=calculate_dq_and_mc([ev()],'short',historical_sample_score=100,signal_stability_score=100)
    assert p['historical_score']==74
    assert p['mc_score'] is not None
    assert p['historical_oos_verified'] is False


def test_sector_technical_not_independent_families():
    e=[ev('rs',category='momentum',period='2026-10-09'),ev('peers',category='sector',period='2026-10-09')]
    q=calculate_dq_and_mc(e,'short')
    assert q['independent_categories_count']==1
    assert q['evidence_families']==['price']


def test_pit_fails_closed_even_with_legal_deadline():
    valid,msg=check_pit_validity('2026-08-31',None,None,EARLY)
    assert not valid and 'UNVERIFIED_TIME' in msg
    assert asof_check(decision_at=EARLY,first_seen_at='2026-10-09T09:00:00+08:00')['status']=='LOOKAHEAD'
    assert asof_check(decision_at=EARLY,published_at='2026-10-09',first_seen_at='2026-10-07')['status']=='LOOKAHEAD'
    assert asof_check(decision_at=NOW,published_at='2026-09-08',first_seen_at='2026-09-09')['valid']


def test_pit_freshness_type_specific():
    assert is_timely('monthly_revenue','2026-08-31',NOW)
    assert not is_timely('monthly_revenue','2026-07-31',NOW)
    assert is_timely('financials','2026-06-30',NOW)
    assert not is_timely('institutional_flow','2026-09-25',NOW)


def test_pit_ledger_versions_and_replay_immutability():
    with tempfile.TemporaryDirectory() as d:
        db=Path(d)/'pit.sqlite'
        ledger=PITLedger(db)
        row={'ticker':'2330.TW','data_type':'monthly_revenue','period_end':'2026-08-31',
             'source':'official','payload':{'available':True,'latest':{'month':'2026-08','yoy_pct':17}}}
        assert ledger.record_many([row],observed_at='2026-09-10T10:00:00+08:00')['new']==1
        assert ledger.record_many([row],observed_at='2026-09-11T10:00:00+08:00')['new']==0
        assert ledger.latest_asof('2330.TW','monthly_revenue','2026-09-10T09:59:59+08:00') is None
        old=ledger.latest_asof('2330.TW','monthly_revenue','2026-09-10T10:00:01+08:00')
        assert old['payload']['latest']['yoy_pct']==17
        row2=dict(row);row2['payload']={'available':True,'latest':{'month':'2026-08','yoy_pct':19}}
        assert ledger.record_many([row2],observed_at='2026-09-15T10:00:00+08:00')['new']==1
        assert ledger.latest_asof('2330.TW','monthly_revenue','2026-09-14T10:00:00+08:00')['payload']['latest']['yoy_pct']==17
        assert ledger.latest_asof('2330.TW','monthly_revenue','2026-09-16T10:00:00+08:00')['payload']['latest']['yoy_pct']==19


def test_live_evidence_cannot_backfill_historical_replay():
    with tempfile.TemporaryDirectory() as d:
        db=Path(d)/'pit.sqlite'
        research={'2330.TW':{'monthly_revenue':{'available':True,'latest':{'month':'2026-09','yoy_pct':27},
                         'source':'TWSE official'},'institutional_flow':{'available':False}}}
        annotate_breadth_live(research, db, decision_at=NOW)
        p=research['2330.TW']['monthly_revenue']['pit']
        assert p['valid'] and p['status']=='OBSERVED_ONLY',p
        old=load_breadth_replay(['2330.TW'],db,'2026-10-09T15:00:00+08:00')
        assert not old['2330.TW']['monthly_revenue']['available']
        later=load_breadth_replay(['2330.TW'],db,'2026-10-11T10:00:00+08:00')
        assert later['2330.TW']['monthly_revenue']['available']
        assert later['2330.TW']['monthly_revenue']['pit']['first_seen_at'].startswith('2026-10-11')


def test_historical_universe_does_not_use_todays_survivors():
    with tempfile.TemporaryDirectory() as d:
        u=UniverseArchive(Path(d)/'u.sqlite')
        u.record([{'ticker':'2330.TW','name':'台積電'},{'ticker':'1234.TW','name':'後來下市'}],observed_at='2026-10-11T10:00:00+08:00')
        assert not u.replay('2026-10-10T18:00:00+08:00')['valid']
        assert len(u.replay('2026-10-11T11:00:00+08:00')['members'])==2
        u.record([{'ticker':'2330.TW','name':'台積電'}],observed_at='2026-10-12T10:00:00+08:00')
        assert len(u.replay('2026-10-11T18:00:00+08:00')['members'])==2
        assert len(u.replay('2026-10-12T18:00:00+08:00')['members'])==1


def test_no_macro_shock_in_quality_ranking():
    sig=price_signals(bars(), .01)
    a=build_profile(sig,{}, {}, avg_vol_shares=1e6,global_shock=0)
    b=build_profile(sig,{}, {}, avg_vol_shares=1e6,global_shock=96)
    for h in ('short','mid','long'):
        assert score_horizon(a,h,72)['score']==score_horizon(b,h,72)['score']
    assert b['risk_penalty_for_entry']>a['risk_penalty_for_entry']


def test_macro_entry_stale_not_penalized():
    assert _v184_global_entry_penalty({'shock_score':99,'available':True,'stale':True})==0
    assert _v184_global_entry_penalty({'shock_score':99,'available':True,'stale':False})>0
    assert _v184_global_entry_penalty({'shock_score':None,'available':True,'stale':False})==0


def test_universe_rank_stability_preserves_alpha():
    stocks=[]
    for i in range(30):
        s={'ticker':f'{2000+i}.TW','v18_profile':{'dimensions':{'momentum':78+i*.2,'quality':65+i*.1}},
           'horizons':{}}
        for h in ('short','mid','long'):
            s['horizons'][h]={'rank_v18':{'score':68+i*.3,'score_v17':65+i*.35,
                                  'overlay_weight':.28,'score_v18':75,
                                  'dq_score':80,'mc_score':60,
                                  'confidence':{'historical_score':64,'independent_categories_count':3}}}
        stocks.append(s)
    before=[tuple(s['horizons'][h]['rank_v18']['score'] for h in ('short','mid','long')) for s in stocks]
    report=apply_rank_stability(stocks)
    assert all(report[h]['measured'] for h in report), report
    assert [tuple(s['horizons'][h]['rank_v18']['score'] for h in ('short','mid','long')) for s in stocks]==before
    assert all(s['horizons']['short']['rank_v18']['signal_stability_type']=='cross_sectional_rank_sensitivity' for s in stocks)


def test_eligibility_observation_not_synthetic_winners():
    e=evaluate_recommendation_eligibility(99,30,None,1,'long')
    assert not e['is_eligible'] and len(e['failed_reasons'])>=3
    assert e['oos_status']=='樣本外績效待驗證'


def test_live_api_refuses_backdated_observations():
    from market_data import ResearchDataClient
    with tempfile.TemporaryDirectory() as d:
        client=ResearchDataClient(Path(d)/'research.sqlite',token='')
        with patch.object(client,'_public_json_snapshot',side_effect=AssertionError('must reject before network')):
            try:
                client.official_breadth_snapshot(['2330.TW'],decision_at='2020-06-30T10:00:00+08:00')
                raise AssertionError('A live API must not accept an old decision_at')
            except ValueError as e:
                assert 'historical_replay' in str(e)


def test_replay_never_calls_live_market_data():
    from market_data import ResearchDataClient
    with tempfile.TemporaryDirectory() as d:
        client=ResearchDataClient(Path(d)/'research.sqlite',token='')
        with patch.object(client,'_public_json_snapshot',side_effect=AssertionError('live API forbidden')):
            p=client.official_breadth_snapshot(['2330.TW'],decision_at=EARLY,historical_replay=True)
        assert not p['2330.TW']['financials']['available']
        assert p['2330.TW']['monthly_revenue']['pit']['status']=='NO_PIT_ARCHIVE'


if __name__ == '__main__':
    functions=[obj for name,obj in sorted(globals().items()) if name.startswith('test_') and callable(obj)]
    for fn in functions:
        fn()
        print('PASS',fn.__name__)
    print(f'ALL V18.4 INTEGRATED TESTS PASSED ({len(functions)})')
