"""V18.5 four-destination UI and honest source/snapshot diagnostic tests."""
from __future__ import annotations
from contextlib import nullcontext
from pathlib import Path
import datetime as dt
import tempfile
import types

import pandas as pd
from v174_integrity import assess_snapshot
from test_v17_1_sync_briefing import _run_with_official_sequence
from test_v17_5_experience import sample_snapshot, TZ

DAY='2026-10-09'

def snap(*, tw=DAY, otc=DAY, common=DAY):
    return {
        'snapshot_id':'mock-data','price_date':common,'generated_at':'2026-10-11T09:40:00+08:00',
        'session':{'common_complete_date':common,'postclose_pending':False},
        'coverage':{'requested':10,'feature_valid':8,'twse_universe':5,'tpex_universe':5,
                    'stale_excluded':1,'official_eod':{'raw_twse_date':tw,'raw_tpex_date':otc,
                                                     'twse_source':'TWSE official','tpex_source':'TPEx official'}},
        'full_universe_model_n':4,'candidate_n':4,'research_breadth_n':2,
        'stocks':[{'ticker':f'{3000+i}.TW','price_date':common} for i in range(4)],
    }


def test_true_sync_clear_and_price_coverage_separate():
    r=assess_snapshot(snap())
    assert r['status']=='OK' and r['sync_state']=='ALIGNED',r
    assert r['safe_to_claim_synced']
    assert r['rates']['history_pct']==80 and r['rates']['investable_pct']==40 and r['rates']['research_pct']==50
    assert len(r['stock_date_counts'])==1


def test_cross_exchange_rollback_is_not_data_corruption():
    r=assess_snapshot(snap(otc='2026-10-10',common=DAY))
    assert r['status']=='CHECK' and r['sync_state']=='ROLLBACK',r
    assert r['safe_for_research'] and not r['safe_to_claim_synced']
    assert '回退' in r['issues'][0] or '共同模型日' in r['issues'][0]


def test_missing_official_source_is_not_cloned_from_cache():
    r=assess_snapshot(snap(otc=''))
    assert r['status']=='CHECK' and r['sync_state']=='UNVERIFIED',r
    assert not r['source_verified'] and not r['safe_to_claim_synced']
    scanned,_ = _run_with_official_sequence([{'twse_date':DAY,'tpex_date':'','index_date':DAY}])
    off=scanned['coverage']['official_eod']
    assert off['raw_tpex_date']=='',off
    assert off['date_provenance']['tpex']=='missing'
    assert assess_snapshot(scanned)['sync_state']=='UNVERIFIED'


def test_price_mismatch_is_hard_block_even_if_sources_match():
    a=snap()
    a['stocks'][2]['price_date']='2026-10-08'
    r=assess_snapshot(a)
    assert r['status']=='BLOCK' and r['sync_state']=='INVALID'
    assert '價格' in ' '.join(r['blockers'])
    b=snap(); b['price_date']='2026-10-10'
    assert assess_snapshot(b)['status']=='BLOCK'


def test_repaired_source_warning_does_not_erase_date_sync():
    a=snap()
    a['coverage']['errors']=['early provider timeout']
    a['coverage']['official_eod']['reconciled_retry']=True
    r=assess_snapshot(a)
    assert r['sync_state']=='ALIGNED' and r['safe_to_claim_synced'],r
    assert r['notes'] and not r['blockers']


def test_legacy_snapshot_without_provenance_remains_diagnosable():
    a=snap()
    a['coverage']['official_eod']={}
    r=assess_snapshot(a)
    assert r['sync_state']=='UNVERIFIED' and r['status']=='CHECK'
    assert r['rates']['history_pct']==80
    assert assess_snapshot(None)['status']=='NO_DATA'



def test_doctor_avoids_actionable_decisions_on_unverified_market_days():
    from smoke_app_import import app
    import streamlit as st
    captured = []
    warn = getattr(st, 'warning', None)
    st.warning = lambda message, **kw: captured.append(message)
    try:
        app._doctor(snap(otc=''))
        assert len(captured) == 1 and '暫不提供買入' in captured[0]
    finally:
        if warn is None: delattr(st, 'warning')
        else: st.warning = warn

def test_four_core_destinations_and_factor_not_standalone():
    from smoke_app_import import app
    assert app.VIEWS == ['今日決策','選股與買點','個股診斷']
    assert '因子透視' not in app.VIEWS
    assert '策略優選' not in app.VIEWS and '明日關注' not in app.VIEWS
    assert 'v18_ui.render_page' in Path('app.py').read_text(encoding='utf-8')
    assert 'board_simple_mode_v175' not in Path('app.py').read_text(encoding='utf-8')


def test_combined_card_and_health_panels_render_offline():
    from smoke_app_import import app
    import streamlit as st
    collected=[]
    backup={}
    class Ctx:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def metric(self,label,value,**k):collected.append((label,value))
    replacements={
        'markdown':lambda x,**k:collected.append(str(x)),
        'caption':lambda x,**k:collected.append(str(x)),
        'warning':lambda x,**k:collected.append(str(x)),
        'success':lambda x,**k:collected.append(str(x)),
        'error':lambda x,**k:collected.append(str(x)),
        'info':lambda x,**k:collected.append(str(x)),
        'columns':lambda spec,**k:[Ctx() for _ in range(spec if isinstance(spec,int) else len(spec))],
        'expander':lambda *a,**k:Ctx(),
        'radio':lambda label,opts,**k:opts[0],
        'selectbox':lambda label,opts,**k:list(opts)[0],
        'dataframe':lambda *a,**k:None,
    }
    for key,value in replacements.items():
        backup[key]=getattr(st,key,None)
        setattr(st,key,value)
    orig=app._board_filtered_rows
    orig_factors=app.v18_ui.render_page
    try:
        app._render_data_health(snap())
        assert ('統一模型日',DAY) in collected
        assert any('80.0%' in str(x) for x in collected)
        rows=pd.DataFrame([
            {'清單':cl,'週期':'短線 · 約2週','排名':1,'代號':'2330.TW','股票':'測試股','標的分數':'86',
             '買點分數':'77','建議動作':'等量價確認','現價':'150','建議區間':'145–148','跌破重看':'139',
             '資料品質DQ':'78','模型信心MC':'71','證據資格':'研究觀察'}
            for cl in ('完整日線','明日關注')])
        app._board_filtered_rows=lambda *a,**k:rows
        app.v18_ui.render_page=lambda *a,**k:collected.append('因子透視已嵌入')
        collected.clear()
        app._render_recommendation_board(snap(),home=False)
        out='\n'.join(str(x) for x in collected)
        assert out.count('class=\'pick-name\'')==1,out
        assert 'Alpha 86' in out and '買點 77' in out
        assert '含下次交易日觀察' in out and '因子透視已嵌入' in out
        # Unverified official time removes BUY / zone, not the historical stock.
        collected.clear()
        app._render_recommendation_board(snap(otc=''),home=True)
        out='\n'.join(str(x) for x in collected)
        assert '資料未核，暫不判斷買點' in out and '參考區間 <b>—</b>' in out,out
    finally:
        app._board_filtered_rows=orig
        app.v18_ui.render_page=orig_factors
        for key,value in backup.items():
            if value is None: delattr(st,key)
            else: setattr(st,key,value)


if __name__=='__main__':
    tests=[value for name,value in sorted(globals().items()) if name.startswith('test_') and callable(value)]
    for t in tests:
        t(); print('PASS',t.__name__,flush=True)
    print(f'ALL {len(tests)} V18.5 EXPERIENCE TESTS PASSED')
