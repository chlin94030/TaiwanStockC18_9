"""Regression: ON detail toggle must update top research summary in the same rerun."""
from __future__ import annotations
import importlib, sys, types
class _Ctx:
    def __enter__(self): return self
    def __exit__(self,*a): return False
st=types.ModuleType('streamlit')
st.session_state={'fund_toggle_short_1_snapA_6691_TW': True}
st.secrets={}; st.sidebar=_Ctx()
def dec(*a,**k):
    if a and callable(a[0]) and len(a)==1 and not k:return a[0]
    return lambda fn:fn
st.cache_data=dec; st.fragment=dec; st.expander=lambda *a,**k:_Ctx()
marks=[]
st.markdown=lambda body,*a,**k: marks.append(str(body))
st.caption=lambda *a,**k:None; st.info=lambda *a,**k:None; st.error=lambda *a,**k:None; st.warning=lambda *a,**k:None; st.success=lambda *a,**k:None
st.dataframe=lambda *a,**k:None; st.plotly_chart=lambda *a,**k:None
st.toggle=lambda *a, key=None, **k: bool(st.session_state.get(key,False))
sys.modules['streamlit']=st
app=importlib.import_module('app')
fresh={
 'monthly_revenue':{'available':True,'latest':{'month':'2026-08','revenue_billion':52.87},'rows':[{'month':'2026-08','revenue_billion':52.87}]},
 'financials':{'available':True,'latest_ytd_eps':17.46,'latest_period':'2026Q2'},
 'valuation':{'available':True,'pe':20},
 'institutional_flow':{'available':True,'foreign_net_lots':1000,'trust_net_lots':200,'dealer_net_lots':-100,'total_net_lots':1100,'scope_label':'最近 8 個交易日（官方）'},
 'main_force_proxy':{'available':False}, 'source':'official'
}
app.service.research_on_demand=lambda *a,**k:fresh
stock={'ticker':'6691.TW','price_date':'2026-10-06','research':{}}
app._render_research(stock,'short_1_snapA')
flow='\n'.join(x for x in marks if 'flowbar' in x)
assert '1,100' in flow or '1100' in flow, flow
assert '—' not in flow, flow
print('Alpha Radar V16.6.2 summary preload regression: PASS')
