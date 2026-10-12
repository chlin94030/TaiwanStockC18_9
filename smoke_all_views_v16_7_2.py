from __future__ import annotations
import copy
import datetime as dt
import importlib
import sys
import types
import pandas as pd

class _Ctx:
    def __enter__(self): return self
    def __exit__(self,*a): return False
class _Progress:
    def progress(self,*a,**k): return self
    def empty(self): return None

st=types.ModuleType('streamlit')
st.session_state={}
st.secrets={}
st.sidebar=_Ctx()
def deco(*args,**kwargs):
    if args and callable(args[0]) and len(args)==1 and not kwargs: return args[0]
    return lambda fn: fn
st.cache_data=deco; st.fragment=deco
for name in ('set_page_config','markdown','caption','info','warning','error','success','dataframe','plotly_chart'):
    setattr(st,name,lambda *a,**k: None)
st.button=lambda *a,**k: False
st.progress=lambda *a,**k:_Progress()
st.selectbox=lambda label,options,index=0,**k:list(options)[index]
st.radio=lambda label,options,index=0,**k:list(options)[index]
st.checkbox=lambda *a,**k:False
st.number_input=lambda *a,value=0.0,**k:value
st.text_input=lambda *a,value='',**k:value
st.form=lambda *a,**k:_Ctx()
st.form_submit_button=lambda *a,**k:False
st.columns=lambda spec,**k:[_Ctx() for _ in range(spec if isinstance(spec,int) else len(spec))]
st.expander=lambda *a,**k:_Ctx()
st.toggle=lambda *a,value=False,**k:value
st.select_slider=lambda label,options,value=None,**k:value
st.spinner=lambda *a,**k:_Ctx()
sys.modules['streamlit']=st

app=importlib.import_module('app')
from intraday_engine import RealtimeStatus

TZ=dt.timezone(dt.timedelta(hours=8))
plan={'reference_close':100.0,'breakout_level':100.0,'trigger':100.5,'zone_low':98.0,'zone_high':101.0,'chase_limit':103.0,'atr14':3.0,'ma5':99.0,'ma20':96.0,'invalidation':94.0}
forecast={'estimate_available':True,'confidence_score':75.0,'local_effective_n':80,'strategy':{'median':0.10,'mean':0.11,'p10':-0.05,'smoothed_positive_rate':0.64},'technical_factor_score':80,'empirical_quality_score':70,'composite_factor_score':78}
def mk(code,name,score):
    block={'ranking_score':score,'plan':copy.deepcopy(plan),'entry_timing':{'score':78.0,'action':'可分批','chase_risk':'低','reasons':[]},'position_guidance':{'percent':70,'label':'分批'},'forecast':copy.deepcopy(forecast),'qualification':{'research_qualified':True}}
    return {'ticker':code,'name':name,'industry':'電子','fine_industry':'測試','price':100.0,'price_date':'2026-10-06','avg_volume_20d':2_000_000,'avg_turnover_20d':200_000_000,'pre_score':60,'research':{},'setup':{'ma5':99,'ma20':96,'ma60':90,'ma120':82,'ma240':75,'atr14':3},'horizons':{'short':copy.deepcopy(block),'mid':copy.deepcopy(block),'long':copy.deepcopy(block)}}
stocks=[mk('1111.TW','甲',90),mk('2222.TW','乙',84)]
snap={'snapshot_id':'qa','price_date':'2026-10-06','generated_at':'2026-10-06T18:00:00+08:00','model_version':'v16.6.0-dynamic-intraday','market':{'regime':'BULL','day_change_pct':1.0},'coverage':{'official_eod':{},'history_refresh':{}},'session':{'analysis_mode':'POSTCLOSE_FINAL','official_today_complete':True,'twse_complete_date':'2026-10-06','tpex_complete_date':'2026-10-06'},'stocks':stocks,'intraday_universe':[{'ticker':'1111.TW','name':'甲','industry':'電子','avg_volume_20d':2_000_000,'pre_score':60},{'ticker':'2222.TW','name':'乙','industry':'電子','avg_volume_20d':2_000_000,'pre_score':55}]}

# completed-day surfaces
app._overview(snap)
for h in ('short','mid','long'):
    app._render_daily_list(snap,h,2)
app._taipei_timestamp=lambda:dt.datetime(2026,10,7,8,30,tzinfo=TZ)
app._render_next_session(snap,'short',2)

# intraday buy surface with discovery + official verified execution quotes
now=dt.datetime(2026,10,7,10,30,tzinfo=TZ)
app._taipei_timestamp=lambda:now
broad=pd.DataFrame([
    {'stock_id':'001','close':27100,'open':27000,'high':27200,'low':26900,'change_rate':0.8,'date':'2026-10-07 10:30:00'},
    {'stock_id':'1111','close':101,'open':100,'high':102,'low':99.5,'change_rate':1.0,'total_volume':1_200_000,'date':'2026-10-07 10:30:00'},
    {'stock_id':'2222','close':102,'open':100,'high':102.5,'low':99.8,'change_rate':2.0,'total_volume':1_500_000,'date':'2026-10-07 10:30:00'},
])
verified=broad.copy(); verified['source']='TWSE/TPEx MIS'
status=RealtimeStatus(True,quote_date='2026-10-07',source='TWSE/TPEx MIS',market_open=True,fetched_at=now.isoformat(),verified=True,verification='official_mis',requested_count=2,executable_count=2)
app._realtime_batch=lambda s: (['1111.TW','2222.TW'], broad, status)
app._cached_verified_execution_snapshot=lambda sid,tickers:(verified,status)
app._intraday_buy_render_once(snap,'short',2)

# doctor buy + hold render surfaces; no form submit needed because session state is preloaded
from intraday_engine import rerank_snapshot
mini={'stocks':[copy.deepcopy(stocks[0])],'market':{'regime':'BULL'},'snapshot_id':'doc'}
live_stock=rerank_snapshot(mini,'short',verified[verified.stock_id.isin(['001','1111'])],top_n=1,now=now)[0]
base_dr={'snapshot_id':'doc','stock':live_stock,'analysis_date':'2026-10-06','phase':{'phase':'INTRADAY','label':'盤中'},'live_status':{'available':True,'verified':True,'source':'TWSE/TPEx MIS','fetched_at':now.isoformat()},'h':'short'}
st.session_state['alpha_doctor']={**copy.deepcopy(base_dr),'doctor_input':{'mode':'考慮買入','cost':None,'original_stop':None,'trailing_protection':None,'thesis':'尚未確認'}}
app._doctor(snap)
st.session_state['alpha_doctor']={**copy.deepcopy(base_dr),'doctor_input':{'mode':'已有持股','cost':90.0,'original_stop':None,'trailing_protection':None,'thesis':'仍成立'}}
app._doctor(snap)

# doctor unverified price must render without crashing and without actionable live decision
unverified_dr={'snapshot_id':'doc2','stock':copy.deepcopy(stocks[0]),'analysis_date':'2026-10-06','phase':{'phase':'INTRADAY','label':'盤中'},'live_status':{'available':False,'verified':False,'reason':'MIS_NO_EXECUTABLE_PRICE'},'h':'short','doctor_input':{'mode':'已有持股','cost':90.0,'original_stop':None,'trailing_protection':None,'thesis':'仍成立'}}
st.session_state['alpha_doctor']=unverified_dr
app._doctor(snap)

print('ALL APP SURFACE SMOKES PASSED | V16.7.2')
