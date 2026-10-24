"""__ID__ · __NAME__

Research + verdict: __SOURCE__
Copied from the template by `python -m strategy_book.new_strategy`. Fill in every TODO,
then reload the app — the card appears in the 🎯 Strategy Book tab automatically.
"""
from ..core import (ARMED, CONDITIONAL, IDLE, NEEDS_DATA, Level, Strategy, TodaySignal, fmt,
                    itm_strike, level_beyond, next_weekly)


def signal(ctx) -> TodaySignal:
    """What to do at the NEXT session (ctx.next_session), using completed data only.

    ctx.daily       date-indexed open/high/low/close, last row = last completed session
    ctx.last        that last row (ctx.last.high, ctx.last.close, ...)
    ctx.open_px     the next session's open once captured (09:10 IST run) or typed in, else None
    ctx.last_hour   {"open","high","low","close"} of the last session's 14:30-15:30, or None

    Return one TodaySignal: ARMED (trade it), CONDITIONAL (depends on the open / an intraday
    level), NO TRADE (IDLE), or NEEDS_DATA. Keep the rule IDENTICAL to the backtest.
    """
    p = ctx.last
    # TODO: replace with the strategy's exact, causal rule.
    return TodaySignal(IDLE, "TODO: implement the signal", why=f"last close {fmt(p.close)}")


STRATEGY = Strategy(
    id="__ID__",
    name="__NAME__",
    source="__SOURCE__",
    verdict="__VERDICT__",                      # GO / MARGINAL-GO / MARGINAL / PAPER
    horizon="TODO intraday / swing (≤N sessions)",
    instrument="TODO e.g. NIFTY futures 1 lot / CE-PE next weekly 1-ITM",
    summary="TODO one or two lines: what the edge is and why it should persist.",
    rules=["TODO numbered, codeable rules: bias / entry / stop / target / exit"],
    stats={"Trades": "TODO", "Win": "TODO", "PF": "TODO", "Net / lot 65": "TODO",
           "Walk-forward": "TODO H1 / H2", "Robustness": "TODO"},
    caveats=["TODO: what could make this fail live"],
    trade_log=__TRADE_LOG__,                    # file in strategy_book/trades/ or None
    signal=signal,                              # or None for a static (no live signal) card
)
