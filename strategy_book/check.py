"""Sanity-check the Strategy Book before pushing:  python -m strategy_book.check

1. every strategy file imports and defines STRATEGY;
2. every bundled trade log has the standard columns;
3. each live signal runs on the current data;
4. REPLAY: for every backtest trade date in the bundled log, the signal computed from the
   data available before that session (plus that session's real open) must NOT say
   "NO TRADE" and must agree on the side. This catches a signal that drifted from the backtest.
"""
import sys

import pandas as pd

from .core import IDLE, NEEDS_DATA, TRADE_COLS, Context, discover, load_trades, run_signal
from teller import load_daily


def replay(s, daily: pd.DataFrame, trades: pd.DataFrame) -> tuple[int, int, list]:
    ok = n = 0
    bad = []
    for _, t in trades.iterrows():
        day = pd.Timestamp(t.date)
        if day not in daily.index:
            continue
        hist = daily[daily.index < day]
        if len(hist) < 300:
            continue
        ctx = Context(daily=hist, today_open=float(daily.loc[day, "open"]), last_hour=None)
        if ctx.next_session != day.date():          # holiday in between — next_weekday can't know
            continue
        sig = run_signal(s, ctx)
        if sig is None or sig.status == NEEDS_DATA:
            continue
        n += 1
        side_ok = sig.side in ("BOTH", t.side)
        if sig.status != IDLE and side_ok:
            ok += 1
        else:
            bad.append(f"{t.date:%Y-%m-%d} {t.side}: {sig.status} {sig.side} — {sig.headline}")
    return ok, n, bad


def main() -> int:
    strategies, errors = discover()
    for e in errors:
        print("LOAD ERROR", e)
    daily = load_daily()[["open", "high", "low", "close"]]
    fail = bool(errors)
    for s in strategies:
        print(f"\n== {s.id}  [{s.verdict}]  {s.name}")
        t = load_trades(s.trade_log)
        if s.trade_log and t is None:
            print("  ✗ trade log missing:", s.trade_log); fail = True
        if t is not None:
            missing = [c for c in TRADE_COLS if c not in t.columns]
            print(f"  log: {len(t)} trades, net ₹{t.net_rs.sum():,.0f}/lot" + (f"  ✗ missing {missing}" if missing else ""))
            fail |= bool(missing)
        sig = run_signal(s, Context(daily=daily, today_open=None, last_hour=None))
        if sig:
            print(f"  today: {sig.status} — {sig.headline}")
        if t is not None and s.signal is not None:
            ok, n, bad = replay(s, daily, t)
            if n:
                rate = ok / n
                print(f"  replay: {ok}/{n} backtest trade days re-signalled ({rate:.0%})")
                for b in bad[:5]:
                    print("    ·", b)
                fail |= rate < 0.9
            else:
                print("  replay: n/a (signal needs intraday inputs)")
    print("\nFAIL" if fail else "\nOK")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
