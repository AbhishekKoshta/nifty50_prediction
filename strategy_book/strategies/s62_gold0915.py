"""62 · S1 GOLD 09:15 — yesterday's last-hour candle sets the bias, trade the next 100-pt level.

Exact rule: `62_PoS_OptionBuying/spot_screen/ss_hyps.py::gold_hourly(anchor=09:15)` with
grid 100, buf 5, stop 25, X=25, Y=50, 4 attempts, junction 2, EOD exit.
"""
from ..core import (ARMED, CONDITIONAL, IDLE, NEEDS_DATA, Level, Strategy, TodaySignal,
                    fmt, itm_strike, level_beyond, next_weekly)

GRID, BUF, STOP, X, Y = 100, 5, 25, 25, 50


def bias_of(o, h, l, c, x=X, y=Y) -> int:
    r = h - l
    if r <= 0:
        return 0
    pos, body = (c - l) / r, abs(c - o) / r
    if c > o and pos >= 1 - x / 100 and body >= y / 100:
        return 1
    if c < o and pos <= x / 100 and body >= y / 100:
        return -1
    return 0


def signal(ctx) -> TodaySignal:
    lh = ctx.manual.get("last_hour") or ctx.last_hour
    if not lh:
        return TodaySignal(NEEDS_DATA, f"Need {ctx.last_date}'s 14:30–15:30 candle (O/H/L/C) — "
                                       "enter it in the inputs above.")
    o, h, l, c = (float(lh[k]) for k in ("open", "high", "low", "close"))
    s = bias_of(o, h, l, c)
    r = h - l
    why = (f"{ctx.last_date} last hour O {fmt(o)} H {fmt(h)} L {fmt(l)} C {fmt(c)} → close at "
           f"{(c - l) / r * 100 if r else 0:.0f}% of range, body {abs(c - o) / r * 100 if r else 0:.0f}% "
           f"(need close in top/bottom 25% and body ≥50%)")
    if s == 0:
        return TodaySignal(IDLE, "No bias — yesterday's last hour was not a strong candle. No trade today.", why=why)

    side, kind = ("LONG", "CE") if s == 1 else ("SHORT", "PE")
    lv = level_beyond(c, s, GRID)
    po = ctx.open_px
    gapped = po is not None and (po - (lv + s * BUF)) * s >= 0
    if gapped:
        lv = level_beyond(po, s, GRID)
    trig = lv + s * BUF
    exp = next_weekly(ctx.next_session)
    levels = [Level("Trigger (stop-order on index)", trig, f"{'above' if s == 1 else 'below'} the {lv} round level"),
              Level("Stop (index)", trig - s * STOP, f"{STOP} pts from fill"),
              Level("Re-entry level after 2 stops", lv + s * GRID + s * BUF, "move the order 100 pts further")]
    steps = [f"09:15–14:30 only: index stop-order {'BUY' if s == 1 else 'SELL'} at {fmt(trig)} → buy {kind}.",
             f"Strike ≈ {itm_strike(trig, kind)} {kind} (1-ITM at the fill), expiry {exp} (next weekly — never the 0-DTE).",
             f"Stop {STOP} index pts from the fill. NO target — hold to 15:20 square-off.",
             "Up to 4 attempts; after 2 stops at one level, move the order 100 pts further.",
             "Stop for the day after any non-stop exit. Use LIMIT-protected orders (slippage ≥3 pt/side kills H1)."]
    if po is None:
        steps.insert(0, f"Pre-open: if NIFTY opens {'≥' if s == 1 else '≤'} {fmt(trig)} the trigger moves to the "
                        "next 100-pt level beyond the open.")
        status = CONDITIONAL
        head = f"{side} bias — {kind} if NIFTY trades {'above' if s == 1 else 'below'} {fmt(trig)} (09:15–14:30)"
    else:
        status = ARMED
        head = (f"{side} armed — open {fmt(po)}{' gapped through, level moved' if gapped else ''}; "
                f"stop-order at {fmt(trig)}")
    return TodaySignal(status, head, side=side, levels=levels,
                       instrument=f"{kind} · next weekly {exp} · 1-ITM", steps=steps, why=why)


STRATEGY = Strategy(
    id="62_S1_GOLD0915",
    name="GOLD 09:15 — last-hour bias → 100-pt level break",
    source="Algo_Nifty50/strategies/62_PoS_OptionBuying (PLAYBOOK S1, audit claim 2)",
    verdict="MARGINAL-GO",
    horizon="intraday (EOD)",
    instrument="Option BUY: CE (bull) / PE (bear), next weekly, 1 strike ITM",
    summary="Strongest genuine entry edge in the 62 study (t≈2.9 vs a coin-toss entry, the only one surviving "
            "Bonferroni). Rare big winners: 26% win, 13 trades ≥10R.",
    rules=[
        "Bias from YESTERDAY's 14:30–15:30 candle: bullish if it closed up, close in the top 25% of its range and "
        "body ≥50% of range; bearish = mirror; anything else = NO TRADE.",
        "Level = next 100-pt round level beyond that close in the bias direction. If today OPENS beyond level±5, "
        "use the next 100-pt level beyond the open.",
        "Entry: index stop-order at level+5 (buy) / level−5 (sell), live 09:15–14:30, bias side only. "
        "Up to 4 attempts; after 2 stops at one level move 100 pts further.",
        "Stop 25 index pts from fill. No target — exit at 15:20. Done for the day after any non-stop exit.",
    ],
    stats={"Trades": "210 (2023-01 → 2026-10)", "Win": "26%", "PF": "1.78", "Net / lot 65": "+₹1.22 L",
           "Max DD": "≈ −₹30k", "Losing streak": "up to 18", "Edge vs coin": "t = 2.9 (survives ×15)"},
    caveats=["Option P&L is MODEL-priced (BS on prev-close VIX, calibrated on 22 real days) — PAPER-TRADE FIRST.",
             "2023 flat; first half turns NEGATIVE at ≥3 pt/side slippage → limit orders, liquid strikes.",
             "Most profit is the PE side (+₹89k of +₹1.22L). Size ≈ 1 lot per ₹1.5–2L."],
    trade_log="62_S1_GOLD0915.csv",
    signal=signal,
    order=1,
)
