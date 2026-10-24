"""62 · S3 PA4 week-level break → 10R (the 1:10 strategy), priced on monthly ATM options.

Spot signal = `59_PatternAnalyst` PA4 (book.py: WEEK_BREAK, min HTF score 2, after 11:00,
R10_swing, first test only). HTF score = monthly + weekly + daily bias, as in
`pa_lib.htf_context` — reproduced here from completed daily bars only.
"""
import numpy as np
import pandas as pd

from ..core import (ARMED, CONDITIONAL, IDLE, Level, Strategy, TodaySignal, fmt, weekly_expiries)

SL_MIN, SL_CAP = 15, 35


def htf(daily: pd.DataFrame, session) -> dict:
    """Monthly/weekly/daily bias + prior-week H/L known BEFORE `session` opens (completed bars only)."""
    d = daily[["open", "high", "low", "close"]]
    t = pd.Timestamp(session)
    mo = d.resample("ME").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                              close=("close", "last")).dropna()
    mo["m_bias"] = np.sign(mo.close - mo.close.rolling(10).mean())
    wk = d.resample("W-FRI").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                 close=("close", "last")).dropna()
    e10, e20 = wk.close.ewm(span=10, adjust=False).mean(), wk.close.ewm(span=20, adjust=False).mean()
    wk["w_bias"] = np.where((wk.close > e10) & (e10 > e20), 1, np.where((wk.close < e10) & (e10 < e20), -1, 0))
    de20, de50 = d.close.ewm(span=20, adjust=False).mean(), d.close.ewm(span=50, adjust=False).mean()
    d_bias = int(np.where((d.close > de20) & (de20 > de50), 1, np.where((d.close < de20) & (de20 < de50), -1, 0))[-1])
    m = mo[mo.index < t].iloc[-1]          # label strictly before the session = completed month
    w = wk[wk.index < t].iloc[-1]          # completed week (Fri label)
    m_bias = 0 if pd.isna(m.m_bias) else int(m.m_bias)
    score = m_bias + int(w.w_bias) + d_bias
    week_rows = d[d.index > w.name]        # sessions already traded this week, before `session`
    return dict(m_bias=m_bias, w_bias=int(w.w_bias), d_bias=d_bias, score=score,
                pwh=float(w.high), pwl=float(w.low), week_label=w.name.date(), week_rows=week_rows)


def signal(ctx) -> TodaySignal:
    h = htf(ctx.daily, ctx.next_session)
    s = 1 if h["score"] >= 2 else -1 if h["score"] <= -2 else 0
    why = (f"HTF score {h['score']:+d} (month {h['m_bias']:+d}, week {h['w_bias']:+d}, day {h['d_bias']:+d}); "
           f"prior week (to {h['week_label']}) H {fmt(h['pwh'])} · L {fmt(h['pwl'])}")
    if s == 0:
        return TodaySignal(IDLE, "No HTF bias (|score| < 2) — no PA4 trade.", why=why)
    side, kind = ("LONG", "CE") if s == 1 else ("SHORT", "PE")
    lvl = h["pwh"] if s == 1 else h["pwl"]
    wr = h["week_rows"]
    inside = wr[(wr.open - lvl) * s < 0]            # only a day that OPENED inside can have signalled
    tested = bool(((inside.close - lvl) * s > 0).any())
    if tested:
        return TodaySignal(IDLE, f"Prior-week {'high' if s == 1 else 'low'} {fmt(lvl)} already closed through "
                                 "earlier this week from inside — PA4 trades the FIRST test only.", side=side, why=why)
    pierced = inside[((inside.high if s == 1 else inside.low) - lvl) * s > 0]
    if len(pierced):
        why += (f" · ⚠ pierced intraday on {', '.join(str(x.date()) for x in pierced.index)} — if a 5m candle "
                "CLOSED through it after 11:00 that day, the first test is used up")
    po = ctx.open_px
    if po is not None and (po - lvl) * s >= 0:
        return TodaySignal(IDLE, f"Opened {fmt(po)} already beyond {fmt(lvl)} — the break must happen intraday.",
                           side=side, why=why)
    exp = [e for e in weekly_expiries(ctx.next_session, 6) if (e - ctx.next_session).days >= 7]
    levels = [Level(f"Prior-week {'high' if s == 1 else 'low'}", lvl, "first 5m CLOSE through it after 11:00"),
              Level("Stop", None, f"last 5m swing {'low' if s == 1 else 'high'} ∓2, {SL_MIN}–{SL_CAP} pts"),
              Level("Target", None, "10R · stop → breakeven at +3R · ≤7 sessions")]
    steps = [f"From 11:00: wait for a 5m candle to CLOSE {'above' if s == 1 else 'below'} {fmt(lvl)} "
             "(no earlier close through it today) → buy " + kind + ".",
             f"Instrument: MONTHLY or far-weekly ATM, ≥5 DTE (e.g. weekly {exp[0] if exp else '—'}). "
             "Avoid weeklies at 3–4 DTE (loses ~½ the edge).",
             f"Stop at the last confirmed 5m swing {'low' if s == 1 else 'high'} ∓2, risk clamped {SL_MIN}–{SL_CAP} pts.",
             "Time stop: exit if not +0.5R within 2 h of the fill (59 book rule).",
             "Move stop to breakeven at +3R; target 10R; hard exit after 7 sessions. Exit the option when the spot rule exits."]
    status = ARMED if po is not None else CONDITIONAL
    head = (f"{side} watch — 5m close {'above' if s == 1 else 'below'} {fmt(lvl)} after 11:00"
            + ("" if po is not None else " (if the open is still inside)"))
    return TodaySignal(status, head, side=side, levels=levels, instrument=f"{kind} · monthly / ≥5-DTE ATM",
                       steps=steps, why=why)


STRATEGY = Strategy(
    id="62_S3_PA4_WEEKBREAK",
    name="PA4 week-level break → 10R (the 1:10 strategy)",
    source="Algo_Nifty50/strategies/59_PatternAnalyst (PA4) · options: 62_PoS_OptionBuying/phase2B (audit claim 4)",
    verdict="MARGINAL-GO",
    horizon="swing (≤7 sessions)",
    instrument="Option BUY: CE / PE monthly (or far-weekly) ATM, ≥5 DTE",
    summary="Rare, asymmetric: avg win ≈ +8R vs avg loss −1.1R, seven 10R hits in 3.8 yrs. Thin sample (n=30).",
    rules=[
        "HTF score = monthly (close vs 10-month SMA) + weekly (close>EMA10>EMA20) + daily (close>EMA20>EMA50); "
        "trade only with |score| ≥ 2, in that direction.",
        "Level = prior completed week's high (long) / low (short). Today must open inside it.",
        "Entry: first 5m close through the level at/after 11:00; first test of that week's level only.",
        "Stop at the pattern invalidation (last 5m swing ∓2), 15–35 pts. BE at 3R, target 10R, ≤7 sessions. "
        "Time stop: out if not +0.5R within 2 h.",
    ],
    stats={"Trades": "30 (2023-03 → 2026-10)", "Win": "37%", "PF": "4.5", "Net / lot 65": "+₹78k",
           "≥10R trades": "7", "Every year": "positive"},
    caveats=["n = 30 and the top-3 trades are 61% of net — the thinnest of the three.",
             "Stale-VIX vega flatters H2 (+₹21k → +₹9k with frozen VIX and −2 pt/side).",
             "Option P&L is MODEL-priced — PAPER-TRADE FIRST."],
    trade_log="62_S3_PA4_WEEKBREAK.csv",
    signal=signal,
    order=3,
)
