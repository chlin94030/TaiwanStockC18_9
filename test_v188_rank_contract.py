"""V18.8 contract QA. Synthetic evidence is NEVER a trading-performance validation."""
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch
import hashlib
import pandas as pd
from v188_board import canonical_shortlist,optional_entry_watchlist,compare_snapshot_ranks
from strategy_validation_v188 import weight_audit,evaluate_oos

HASHES = {
 'radar_service.py':'ce4d67b1ff','v18_multifactor.py':'74ee638705',
 'v18_confidence_engine.py':'51457bafcb','v18_rank_stability.py':'29b17deee3',
 'return_first_model.py':'0cfa78032f','recommendation_engine.py':'0e64c2635c',
 'strategy_config.py':'ceb3324a9e','policy_engine.py':'8e38529d9f',
 'market_data.py':'a054280305',
}


def synth(ticker, scores, timing):
    return {'ticker':ticker,'name':ticker,'price':100,'price_date':'2026-10-08',
            'horizons':{h:{'ranking_score':value,'entry_timing':{'score':timing},
                'rank_v18':{'dq_score':80,'mc_score':67}}
                for h,value in scores.items()}}


def test_exact_original_scoring_files_are_unchanged_from_v186():
    for filename,expected_hash in HASHES.items():
        actual=hashlib.sha256((Path(__file__).parent/filename).read_bytes()).hexdigest()
        assert actual.startswith(expected_hash),filename


def test_selection_ranks_never_reordered_by_entry_readiness():
    a=synth('2330.TW',{'short':95,'mid':83,'long':77},40)
    b=synth('2382.TW',{'short':78,'mid':97,'long':87},83)
    c=synth('6669.TW',{'short':84,'mid':78,'long':91},75)
    snap={'stocks':[a,b,c]}
    with patch('recommendation_engine.evaluate_next_session',side_effect=lambda stock,horizon,global_context=None:{'score':10 if stock is a else 90}):
        from unittest.mock import patch as xpatch
        with xpatch('v187_experience.entry_readiness',side_effect=lambda stock,h,e:{'code':'TIMING','label':'待確認'}):
            for h,expected in [('short',['2330.TW','6669.TW','2382.TW']),
                               ('mid',['2382.TW','2330.TW','6669.TW']),
                               ('long',['6669.TW','2382.TW','2330.TW'])]:
                rows=canonical_shortlist(snap,h,n=3)
                assert [r['ticker'] for r in rows]==expected,rows
                assert [r['horizon'] for r in rows]==[h]*3
                assert [r['alpha'] for r in rows]==[x['horizons'][h]['ranking_score'] for x in
                       sorted([a,b,c],key=lambda x:-x['horizons'][h]['ranking_score'])]
                assert compare_snapshot_ranks(snap,3)[h]['identical']


def test_optional_next_session_list_cannot_edit_original_selection_scores():
    a=synth('A.TW',{'short':95,'mid':83,'long':77},40)
    b=synth('B.TW',{'short':78,'mid':88,'long':81},83)
    snap={'stocks':[a,b]}
    before=[dict(x['horizons']['short']) for x in snap['stocks']]
    with patch('v187_experience.prioritize_next_session',return_value=[b,a]):
        result=optional_entry_watchlist(snap,'short',n=2)
    assert [x['ticker'] for x in result]==['B.TW','A.TW']
    after=[dict(x['horizons']['short']) for x in snap['stocks']]
    assert before==after


def test_strategy_weights_arithmetic_does_not_equal_profit_proof():
    r=weight_audit()
    assert r['weight_arithmetic_verified'] is True
    assert r['profitability_oos_verified'] is False
    assert r['weight_calibration_status']=='UNVERIFIED_REAL_MARKET_OOS'
    for h,v in r['horizons'].items():
        assert all(abs(total-1)<1e-9 for total in v['weight_sums'].values()),h
        assert len(v['thirteen_factor'])==13,h
    assert evaluate_oos(None)['status']=='NOT_RUN'


def test_synthetic_or_missing_history_cannot_be_called_validated():
    df=pd.DataFrame([{'decision_date':'2025-01-02','ticker':'2330.TW',
                      'horizon':'short','base_score':75,'candidate_score':85,
                      'net_forward_return':.11,'market_forward_return':.01,
                      'pit_verified':True,'historical_universe_verified':True,
                      'corporate_action_verified':True,'execution_verified':True,
                      'real_market_data':False,'evidence_version_id':'mock-123456',
                      'universe_observation_id':'mock-123456'}])
    result=evaluate_oos(df)
    assert result['status']=='NOT_VERIFIED'
    assert 'real_market_data' in result['reason']
    df['real_market_data']=True
    df['universe_observation_id']=''
    assert evaluate_oos(df)['status']=='NOT_VERIFIED'


def test_three_main_pages_and_safely_deemphasized_diagnostics():
    from smoke_app_import import app
    assert app.VIEWS==['今日決策','選股與買點','個股診斷']
    source=Path('app.py').read_text(encoding='utf-8')
    assert "_render_canonical_board(snap,home=True)" in source
    assert "_render_canonical_board(snap,home=False)" in source
    assert "_render_data_health(snap,compact=True)" in source
    assert "elif view == '市場與資料':" not in source


def test_canonical_main_card_render_preserves_order_on_mobile():
    from smoke_app_import import app
    import streamlit as st
    a=synth('AAA.TW',{'short':95,'mid':83,'long':77},40)
    b=synth('BBB.TW',{'short':79,'mid':97,'long':87},83)
    captured=[]
    class C:
        def __enter__(self):return self
        def __exit__(self,*args):return False
    old={key:getattr(st,key,None) for key in ('markdown','caption','radio','info','warning','columns')}
    backup=app.assess_snapshot
    bg=app._cached_global_context
    try:
        st.markdown=lambda content,**kw:captured.append(str(content))
        st.caption=lambda content,**kw:captured.append(str(content))
        st.radio=lambda label,opts,**kw:opts[0]
        st.info=lambda *a,**kw:None
        st.warning=lambda *a,**kw:None
        st.columns=lambda options,**kw:[C() for _ in range(len(options))]
        app.assess_snapshot=lambda snap:{'sync_state':'ALIGNED','safe_for_research':True}
        app._cached_global_context=lambda:{}
        with patch('app.canonical_shortlist',return_value=[
             {'rank':1,'ticker':'AAA.TW','stock':a,'alpha':95,'dq':80,'mc':67,
              'readiness':{'code':'TIMING','label':'待量價確認','reason':'觀察量能'},'decision':{'entry_score':40}},
             {'rank':2,'ticker':'BBB.TW','stock':b,'alpha':79,'dq':80,'mc':67,
              'readiness':{'code':'READY_SETUP','label':'盤前條件就緒','reason':'下個交易日仍需核價'},'decision':{'entry_score':83}},
        ]):
            app._render_canonical_board({'stocks':[a,b],'price_date':'2026-10-08'},home=True)
        html='\n'.join(captured)
        assert html.count("class='focus-card'")==2
        assert html.index('AAA.TW')<html.index('BBB.TW'),html
        assert 'Alpha' in html and '95' in html and '79' in html
        assert '盤前條件就緒' in html and '待量價確認' in html
    finally:
        for key,v in old.items():
            if v is None:delattr(st,key)
            else:setattr(st,key,v)
        app.assess_snapshot=backup
        app._cached_global_context=bg


def test_compact_data_health_has_no_large_metric_date_heading():
    from smoke_app_import import app
    from test_v185_experience import snap
    import streamlit as st
    output=[]
    old={k:getattr(st,k,None) for k in ('markdown','caption','expander')}
    class C:
        def __enter__(self):return self
        def __exit__(self,*a):return False
    try:
        st.markdown=lambda value,**kw:output.append(str(value))
        st.caption=lambda value,**kw:output.append(str(value))
        st.expander=lambda *a,**kw:C()
        app._render_data_health(snap(),compact=True)
        text=' '.join(output)
        assert 'data-compact' in text
        assert 'section-title' not in text
        assert '統一模型日' not in text or '資料完整度與同步日期' not in text
    finally:
        for k,v in old.items():
            if v is None:delattr(st,k)
            else:setattr(st,k,v)

# Historical validator arithmetic is checked separately; synthetic paths are
# deliberately never construed as proof of actual strategy profitability.
def test_postclose_validator_enters_at_next_open_not_signal_close():
    from validate_v17_2026_walkforward import future_path_stats, benchmark_future
    index=pd.date_range('2026-09-01',periods=17,freq='B')
    df=pd.DataFrame({'Open':[100.0,110.0]+[115.0]*15,
                     'Close':[100.0,112.0]+[118.0]*15,
                     'High':[101.0,120.0]+[121.0]*15,
                     'Low':[99.0,105.0]+[106.0]*15},index=index)
    v=future_path_stats(df,index[0],10,.006)
    assert v['entry_open']==110 and v['entry_date']==str(index[1].date())
    assert v['exit_date']==str(index[10].date())
    assert abs(v['realized_net_return'] - (118/110 -1-.006))<1e-10
    assert v['mae']<0 and v['mfe']>0
    assert abs(benchmark_future(df,index[0],10) - (118/110 -1))<1e-10
    assert future_path_stats(df,index[0]-pd.Timedelta(days=1),10,.006) is None



def test_snapshot_diff_distinguishes_display_sort_from_data_drift():
    from compare_snapshots_v188 import compare_snapshots
    a={'snapshot_id':'a','model_version':'v18.4','price_date':'2026-10-08',
       'stocks':[synth('A.TW',{'short':93,'mid':70,'long':61},60),
                 synth('B.TW',{'short':82,'mid':76,'long':69},70)]}
    b={'snapshot_id':'b','model_version':'v18.4','price_date':'2026-10-08',
       'stocks':[a['stocks'][1],a['stocks'][0]]}
    result=compare_snapshots(a,b)
    assert all(h['same_full_ranking'] and h['same_scores_in_shared_universe'] for h in result['horizons'].values())
    b['stocks'][0]=synth('B.TW',{'short':96,'mid':76,'long':69},70)
    drift=compare_snapshots(a,b)
    assert drift['horizons']['short']['same_full_ranking'] is False
    assert drift['horizons']['short']['different_record_count']==1

def test_prediction_journal_is_append_only_and_does_not_rewrite_alpha():
    import tempfile, gzip, json
    from prediction_journal_v188 import freeze_completed_ranking
    from test_v185_experience import snap as base_snap
    a=base_snap()
    s=synth('2330.TW',{'short':91,'mid':81,'long':74},76)
    s['price_date']=a['price_date']
    # Retain the complete 4-row internally consistent snapshot: a one-row
    # synthetic archive would correctly fail the production integrity gate.
    a['stocks'][0]=s
    a['snapshot_id']='v188-test-001'
    orig=s['horizons']['short']['ranking_score']
    with tempfile.TemporaryDirectory() as d:
        x=freeze_completed_ranking(Path(d),a)
        assert x['status']=='RECORDED',x
        with gzip.open(x['path'],'rt',encoding='utf-8') as f:
            doc=json.load(f)
        assert doc['stocks'][0]['horizons']['short']['alpha']==91
        assert orig==s['horizons']['short']['ranking_score']
        assert freeze_completed_ranking(Path(d),a)['status']=='EXISTING'
        b=dict(a);b['snapshot_id']='v188-test-002'
        y=freeze_completed_ranking(Path(d),b)
        assert y['status']=='RECORDED' and y['path']!=x['path']


def test_unverified_or_mixed_day_archives_are_refused():
    import tempfile
    from prediction_journal_v188 import freeze_completed_ranking
    from test_v185_experience import snap as base_snap
    a=base_snap()
    a['stocks'][0]=synth('2330.TW',{'short':90,'mid':81,'long':70},72)
    a['stocks'][0]['price_date']=a['price_date']
    with tempfile.TemporaryDirectory() as d:
        unverified=base_snap(otc='')
        unverified['stocks'][0]=a['stocks'][0]
        assert freeze_completed_ranking(Path(d),unverified)['status']=='SKIPPED'
        mixed=base_snap()
        mixed['stocks'][0]=a['stocks'][0]
        mixed['stocks'][1]['price_date']='2026-10-08'
        assert freeze_completed_ranking(Path(d),mixed)['status']=='SKIPPED'
        assert not list((Path(d)/'prediction_journal').glob('*.gz')) if (Path(d)/'prediction_journal').exists() else True


if __name__=='__main__':
    tests=[v for k,v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for test in tests:
        test(); print('PASS',test.__name__,flush=True)
    print(f'V18.8 RANK/WEIGHT/UX CONTRACT: {len(tests)} PASS')


