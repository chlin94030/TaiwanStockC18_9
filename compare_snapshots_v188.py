"""Compare two completed Alpha Radar EOD snapshots without network calls.

Useful when a phone displays different lists despite identical selection code.
Includes data/version checks; never assumes a same calendar date means the
same company/institutional evidence payload was fetched.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path


def _scores(snap:dict,horizon:str) -> dict:
    result={}
    for s in snap.get('stocks') or []:
        if not isinstance(s,dict):continue
        ticker=str(s.get('ticker') or '')
        h=((s.get('horizons') or {}).get(horizon) or {})
        if ticker and h.get('ranking_score') is not None:
            result[ticker]={'score':h['ranking_score'],'price_date':s.get('price_date'),
                            'price':s.get('price')}
    return result


def compare_snapshots(old:dict,new:dict,top_n:int=15)->dict:
    out={'old':{k:old.get(k) for k in ('snapshot_id','price_date','model_version','candidate_n')},
         'new':{k:new.get(k) for k in ('snapshot_id','price_date','model_version','candidate_n')},
         'same_model_version':old.get('model_version')==new.get('model_version'),
         'same_model_day':old.get('price_date')==new.get('price_date'),
         'horizons':{}}
    for h in ('short','mid','long'):
        a=_scores(old,h);b=_scores(new,h)
        oa=sorted(a,key=lambda t:(-float(a[t]['score']),t))
        ob=sorted(b,key=lambda t:(-float(b[t]['score']),t))
        shared=set(a)&set(b)
        changed=[t for t in shared if a[t]!=b[t]]
        out['horizons'][h]={
          'old_scored':len(a),'new_scored':len(b),
          'old_top':oa[:top_n],'new_top':ob[:top_n],
          'overlap_top':len(set(oa[:top_n]) & set(ob[:top_n])),
          'same_full_ranking':oa==ob,
          'same_scores_in_shared_universe':not changed,
          'different_record_count':len(changed),
          'examples':[{'ticker':t,'before':a[t],'after':b[t]} for t in sorted(changed)[:10]],
          'old_only_count':len(set(a)-set(b)),
          'new_only_count':len(set(b)-set(a))}
    out['interpretation']=(
       '同一份股票分數與排名；清單差異主要來自介面排序或買點觀察。'
       if all(x['same_full_ranking'] and x['same_scores_in_shared_universe'] for x in out['horizons'].values())
       else '兩份完成快照的入模股票、價格或原始分數不同；須核對資料日期、來源版本與研究資料，不可歸因於介面排序單一原因。')
    return out


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('older');ap.add_argument('newer');ap.add_argument('--top-n',type=int,default=15)
    ap.add_argument('--output',default='snapshot_compare_v188.json')
    args=ap.parse_args()
    old=json.loads(Path(args.older).read_text(encoding='utf-8'))
    new=json.loads(Path(args.newer).read_text(encoding='utf-8'))
    report=compare_snapshots(old,new,args.top_n)
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(report['interpretation']);print('Written',args.output)
if __name__=='__main__':main()
