"""UI regression: periods are categorical and one-point histories do not draw fake time axes."""
from __future__ import annotations
import importlib, sys, types

class _Ctx:
    def __enter__(self): return self
    def __exit__(self, *args): return False

plots=[]
st=types.ModuleType('streamlit')
st.session_state={}
st.secrets={}
st.sidebar=_Ctx()
def deco(*a,**k):
    if a and callable(a[0]) and len(a)==1 and not k: return a[0]
    return lambda fn: fn
st.cache_data=deco; st.fragment=deco; st.expander=lambda *a,**k:_Ctx()
st.markdown=lambda *a,**k:None; st.caption=lambda *a,**k:None; st.info=lambda *a,**k:None
st.error=lambda *a,**k:None; st.warning=lambda *a,**k:None; st.success=lambda *a,**k:None
st.dataframe=lambda *a,**k:None
st.plotly_chart=lambda fig,*a,**k: plots.append(fig)
st.toggle=lambda *a,**k:False
sys.modules['streamlit']=st
app=importlib.import_module('app')

one={
 'ticker':'6691.TW','price_date':'2026-10-06',
 'research':{
   'monthly_revenue':{'available':True,'rows':[{'month':'2026-08','revenue_billion':52.87,'mom_pct':10.99,'yoy_pct':213.7}]},
   'financials':{'available':True,'history_quarters':[{'quarter':'2026Q2','eps':None,'eps_ytd_sum_unadjusted':17.46,'gross_margin_pct':18.33}],'eps_ytd_basis':'official_cumulative'},
   'institutional_flow':{'available':False},
 }
}
app._render_fundamental_details(one,'single')
assert len(plots)==0, f'single-point histories should not chart: {len(plots)}'

two={
 'ticker':'6691.TW','price_date':'2026-10-06',
 'research':{
   'monthly_revenue':{'available':True,'rows':[
      {'month':'2026-07','revenue_billion':47.63},{'month':'2026-08','revenue_billion':52.87}]},
   'financials':{'available':True,'history_quarters':[
      {'quarter':'2026Q1','eps':7.19,'eps_ytd_sum_unadjusted':7.19},{'quarter':'2026Q2','eps':None,'eps_ytd_sum_unadjusted':17.46}],'eps_ytd_basis':'official_cumulative'},
   'institutional_flow':{'available':False},
 }
}
app._render_fundamental_details(two,'two')
assert len(plots)==2, len(plots)
assert plots[0].layout.xaxis.type=='category', plots[0].layout.xaxis.type
assert plots[1].layout.xaxis.type=='category', plots[1].layout.xaxis.type
print('Alpha Radar V16.6.2 research UI axis regression: PASS')
