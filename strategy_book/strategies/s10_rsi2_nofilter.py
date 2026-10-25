"""10 · RSI(2) oversold, NO trend filter → buy next open, exit first up-close.

The validated `10_RSI2_MeanRev` first-up-close variant (rsi2_firstupclose.py) with the
200-DMA gate removed (tested 2026-10-25). The teller's R (200-DMA) and R2 (50-DMA) cards
are the filtered versions; this card fires on every oversold close, in any regime.
The engine replays the rule over the whole daily feed, so the card knows whether a trade
is open right now, and it gives the close that would arm the next one.
"""
import pandas as pd

from ..core import ACTIVE, ARMED, CONDITIONAL, Level, Strategy, TodaySignal, fmt

TH, MAX_HOLD, FEE = 10.0, 10, 10.0


def _wilder(close: pd.Series, n: int = 2):
    """Same RSI as teller._rsi (EWM alpha 1/n); also returns the up/down averages."""
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, float("nan")))).fillna(50), up, dn


def replay(daily: pd.DataFrame):
    """All trades of the rule on `daily`. Returns (closed trades DataFrame, open trade dict | None)."""
    o, c = daily.open.values, daily.close.values
    rsi, _, _ = _wilder(daily.close)
    r, idx, n = rsi.values, daily.index, len(daily)
    trades, open_tr, i = [], None, 2
    while i < n:
        if r[i] < TH:
            ei = i + 1
            if ei >= n:                       # signal on the last close → entry is the next open
                break
            xi = next((j for j in range(ei, min(n, ei + MAX_HOLD)) if c[j] > c[j - 1]), None)
            if xi is None and ei + MAX_HOLD - 1 < n:
                xi = ei + MAX_HOLD - 1        # time stop at the 10th session's close
            if xi is None:                    # still holding at the last close
                open_tr = dict(signal=idx[i], entry_date=idx[ei], entry=float(o[ei]), days=n - ei)
                break
            g = float(c[xi] - o[ei])
            trades.append(dict(entry_ts=idx[ei].strftime("%Y-%m-%d 09:15"), exit_ts=idx[xi].strftime("%Y-%m-%d 15:30"),
                               side="LONG", entry=round(float(o[ei]), 1), exit=round(float(c[xi]), 1),
                               exit_reason="first up-close" if c[xi] > c[xi - 1] else "10-day time stop",
                               net_pts=round(g - FEE, 1)))
            i = xi + 1
            continue
        i += 1
    return pd.DataFrame(trades), open_tr


def signal(ctx) -> TodaySignal:
    d = ctx.daily
    rsi, up, dn = _wilder(d.close)
    last_c, last_r = float(d.close.iloc[-1]), float(rsi.iloc[-1])
    sma200 = float(d.close.rolling(200).mean().iloc[-1])
    above = last_c > sma200
    size = "1 lot" if above else "½ size (below the 200-DMA: the weaker half of the trades)"
    trades, open_tr = replay(d)
    regime = f"close {fmt(last_c)} {'>' if above else '<'} 200-DMA {fmt(sma200)}"
    why = f"{ctx.last_date}: RSI(2) {last_r:.1f} · {regime}"

    if open_tr is not None:
        sig = TodaySignal(
            ACTIVE, f"HOLDING since {open_tr['entry_date']:%d %b} @ {fmt(open_tr['entry'])} — exit at the close "
                    f"of {ctx.next_session:%a} if NIFTY closes above {fmt(last_c)}",
            side="LONG", instrument="NIFTY futures (long)",
            levels=[Level("Entry", open_tr["entry"], f"{open_tr['entry_date']:%Y-%m-%d} open"),
                    Level("Exit if close >", last_c, "first up-close"),
                    Level("Sessions held", float(open_tr["days"]), f"time stop after {MAX_HOLD}")],
            steps=[f"Sell near 15:25 on {ctx.next_session:%a %d %b} if NIFTY is above {fmt(last_c)} (yesterday's close).",
                   f"Otherwise keep holding; hard exit at the close of session {MAX_HOLD}. No stop-loss."],
            why=why)
    elif last_r < TH:
        sig = TodaySignal(
            ARMED, f"Oversold (RSI2 {last_r:.1f}) → BUY {ctx.next_session:%a}'s open · {size}",
            side="LONG", instrument="NIFTY futures (long)",
            levels=[Level("RSI(2)", last_r, f"< {TH:.0f} ✔"), Level("Last close", last_c, "")],
            steps=[f"Buy NIFTY at the 09:15 open on {ctx.next_session:%a %d %b} — {size}.",
                   "Exit at the close of the first session that closes above the previous close (the entry day counts).",
                   f"No up-close within {MAX_HOLD} sessions → exit at the 10th close. No stop-loss."],
            why=why)
    else:
        trig = last_c + float(dn.iloc[-1]) - 9 * float(up.iloc[-1])   # close that makes RSI(2) < 10
        sig = TodaySignal(
            CONDITIONAL, f"Not oversold (RSI2 {last_r:.1f}). Arms at {ctx.next_session:%a}'s close if NIFTY closes "
                         f"below {fmt(trig)} → buy the following open",
            side="LONG", instrument="NIFTY futures (long)",
            levels=[Level("RSI(2)", last_r, f"needs < {TH:.0f}"),
                    Level("Arming close below", trig, f"{trig - last_c:+,.0f} pts vs last close")],
            steps=[f"After {ctx.next_session:%a}'s close: if NIFTY closed below {fmt(trig)}, buy the next 09:15 open ({size}).",
                   "Exit at the first up-close; 10-session time stop; no stop-loss."],
            why=why)
    if len(trades):
        sig.recent_trades = trades.iloc[::-1].head(5)
    return sig


STRATEGY = Strategy(
    id="10_RSI2_NOFILTER",
    name="RSI(2) oversold — no trend filter → first up-close",
    source="Algo_Nifty50/strategies/10_RSI2_MeanRev (rsi2_firstupclose.py, 200-DMA gate removed, 2026-10-25)",
    verdict="MARGINAL",
    horizon="swing (~3 sessions, ≤10)",
    instrument="NIFTY futures · long only · ½ size when below the 200-DMA",
    summary="Buy the open after any daily close with RSI(2) < 10, in any trend; sell at the first up-close. More trades "
            "than the 200-DMA version (R) but lower quality and about twice the drawdown.",
    rules=[
        "At the daily close: RSI(2) < 10 (Wilder smoothing, alpha 1/2, on daily closes). No moving-average filter.",
        "Buy NIFTY at the next session's 09:15 open. Long only, one position at a time.",
        "Exit at the close of the first session that closes above the previous close (the entry day counts).",
        "Time stop: exit at the 10th session's close. No price stop.",
        "Sizing (recommended): full size above the 200-DMA, half size below it.",
    ],
    stats={"Trades": "52 (2023-10 → 2026-10, ~17/yr)", "Win": "71%", "PF": "2.50", "Net": "+4,109 pts (₹2.67 L/lot)",
           "Max DD": "−1,041 pts", "Walk-forward": "+2,472 / +1,638", "Every year +": "2023–2026",
           "vs 200-DMA version": "PF 3.65 · DD −524 · +2,884"},
    caveats=["The edge exists only in 2023–26. On Yahoo daily 2011–2023 the same rule LOSES (PF 0.55–0.98); "
             "no regime filter fixes that.",
             "The trades the 200-DMA filter normally blocks are weak: 54% win, PF 1.68, net +49 pts per trade.",
             "No stop-loss — worst trade −478 pts (2025-02-24). Max drawdown is ~2× the filtered version.",
             "Backtest uses 5m last-bar closes; this card runs on the Yahoo daily feed (official close, ~9 pts "
             "different), so an RSI2 near 10 can flip between the two."],
    trade_log="10_RSI2_NOFILTER.csv",
    signal=signal,
    order=5,
)
