"""V18.8 canonical stock-selection vs entry-readiness contract.

The main list is *always* produced by select_market_best. No buy readiness,
market news, UI deduplication or card display preference can promote a stock
into this ranking. Optional setup discovery is visibly a different list.
"""
from __future__ import annotations
from typing import Any

HORIZONS = ('short', 'mid', 'long')


def canonical_shortlist(snap: dict | None, horizon: str = 'short', n: int = 8,
                        *, global_context: dict | None = None, with_timing: bool = True) -> list[dict]:
    """Return canonical rank with *same-ticker, same-horizon* optional entry detail."""
    if horizon not in HORIZONS:
        raise ValueError('invalid investment horizon')
    from radar_service import select_market_best
    from recommendation_engine import evaluate_next_session
    from v187_experience import entry_readiness

    picks = select_market_best(snap, horizon, n=n)
    results = []
    for rank, stock in enumerate(picks, 1):
        block = ((stock.get('horizons') or {}).get(horizon) or {})
        decision, readiness = {}, {}
        if with_timing:
            try:
                decision = evaluate_next_session(stock, horizon, global_context=global_context) or {}
                readiness = entry_readiness(stock, horizon, decision)
            except Exception as exc:
                # Timing may fail; the independent canonical alpha MUST survive.
                readiness = {'code': 'DATA', 'label': '買點資料待核', 'color': 'gray',
                             'reason': '進場資料取得失敗，保留原始策略排名', 'is_live_buy': False}
        results.append({
            'rank': rank, 'ticker': str(stock.get('ticker') or ''),
            'horizon': horizon, 'stock': stock,
            'alpha': block.get('ranking_score'),
            'dq': (block.get('rank_v18') or {}).get('dq_score'),
            'mc': (block.get('rank_v18') or {}).get('mc_score'),
            'decision': decision, 'readiness': readiness,
        })
    return results


def optional_entry_watchlist(snap: dict | None, horizon: str = 'short', n: int = 8,
                             *, global_context: dict | None = None) -> list[dict]:
    """A clearly separate discovery list; NEVER labeled canonical top picks."""
    from v187_experience import prioritize_next_session
    return prioritize_next_session(snap, horizon=horizon, n=n,
                                   global_context=global_context)


def compare_snapshot_ranks(snap: dict | None, n: int = 10) -> dict:
    """Offline evidence that entry order and presentation do not rewrite alpha."""
    from radar_service import select_market_best
    result = {}
    for horizon in HORIZONS:
        picks = select_market_best(snap, horizon, n=n)
        primary = canonical_shortlist(snap, horizon, n=n, with_timing=False)
        before = [(x.get('ticker'), ((x.get('horizons') or {}).get(horizon) or {}).get('ranking_score')) for x in picks]
        after = [(r['ticker'], r['alpha']) for r in primary]
        result[horizon] = {'identical': before == after, 'top': after, 'n': len(after)}
    return result
