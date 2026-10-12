"""Fail-closed OOS readiness audit. Does NOT claim profit or win-rate.

Usage: python audit_v18_pit_oos.py --data-dir data --decision-at 2026-10-09T18:00:00+08:00 --tickers 2330.TW,2317.TW
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from pit_data_manager import load_breadth_replay
from universe_archive import UniverseArchive


def audit(data_dir: Path, decision_at: str, tickers: list[str]) -> dict:
    u=UniverseArchive(data_dir / 'universe_observations.sqlite').replay(decision_at)
    allowed = {x['ticker'] for x in u.get('members',[])}
    result={'decision_at':decision_at, 'universe_verified':bool(u['valid']),
            'universe_size':len(allowed), 'universe_reason':u.get('reason'),
            'lookahead_checks':{}, 'strict_oos_ready': False,
            'unresolved':['未有涵蓋歷史下市股票及交易停止的完整資料','未有歷史公司行為、清算價及成交可行性的完整還原',
                          '未完成時間序列樣本外績效測試']}
    historical=load_breadth_replay(tickers,data_dir/'pit_observations.sqlite',decision_at)
    for ticker,sections in historical.items():
        observed={k:v.get('pit',{}).get('valid',False) for k,v in sections.items() if k in
                  ('monthly_revenue','financials','institutional_flow','valuation')}
        result['lookahead_checks'][ticker]={'member_at_decision':ticker in allowed,
                                           'archived_sections':observed,
                                           'verified_sections':sum(bool(v) for v in observed.values())}
    result['ready_for_archived_pit_factor_replay']=bool(u['valid']) and all(
        x['member_at_decision'] and x['verified_sections']>0
        for x in result['lookahead_checks'].values())
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--data-dir',type=Path,default=Path('data'))
    p.add_argument('--decision-at',required=True);p.add_argument('--tickers',required=True)
    a=p.parse_args()
    print(json.dumps(audit(a.data_dir,a.decision_at,[t.strip() for t in a.tickers.split(',') if t.strip()]),
                     ensure_ascii=False,indent=2))

if __name__=='__main__':main()
