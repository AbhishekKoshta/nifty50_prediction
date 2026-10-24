"""62 · S2 PDHL swing — open inside yesterday's range, first break of PDH/PDL by 10:30, trail ≤5 sessions.

Exact rule: `62_PoS_OptionBuying/spot_screen/ss_hyps.py::pdhl_first(md=5, win_end=10:30,
cap_pct=0.15, min_risk=10, pe_full_ce_half=True)` with the SWING 1h-pivot trail.
"""
from ..core import (ARMED, CONDITIONAL, IDLE, Level, Strategy, TodaySignal, fmt, itm_strike,
                    next_weekly)

BUF, MIN_RISK, CAP_PCT = 2, 10, 0.15


def signal(ctx) -> TodaySignal:
    p = ctx.last
    pdh, pdl = float(p.high), float(p.low)
    up, dn = pdh + BUF, pdl - BUF
    cap = CAP_PCT * float(p.close) / 100
    exp = next_weekly(ctx.next_session)
    po = ctx.open_px
    why = f"{ctx.last_date}: PDH {fmt(pdh)} · PDL {fmt(pdl)} (range {fmt(pdh - pdl)} pts)"
    levels = [Level("Buy CE above", up, "PDH + 2 — CE at HALF size"),
              Level("Buy PE below", dn, "PDL − 2 — PE at FULL size"),
              Level("Initial stop", None, f"beyond the last 3 five-min candles before the fill, clamped "
                                          f"{MIN_RISK}–{cap:.0f} pts")]
    steps = [f"09:15–10:30 only: stop-orders BUY above {fmt(up)} (→ CE, ½ size) / SELL below {fmt(dn)} (→ PE, full).",
             "First trigger is the trade; each side at most once; no new trade while one is still open.",
             f"Strike ≈ 1-ITM at the fill ({itm_strike(up, 'CE')} CE / {itm_strike(dn, 'PE')} PE), expiry {exp} "
             "(next weekly, or monthly with ≥5 sessions). Never hold the current weekly into expiry.",
             f"Stop: beyond the extreme of the 3 five-min candles before the fill (±1), clamped {MIN_RISK}–{cap:.0f} pts.",
             "Exit: trail the index stop to each new CONFIRMED 1-hour swing (5m pivot, 12 bars each side); never "
             "loosen. Hard exit 15:20 on the 5th session. NO target — the 10R+ winners ARE the strategy."]
    if po is None:
        return TodaySignal(CONDITIONAL, f"Arms only if NIFTY opens INSIDE {fmt(pdl)} – {fmt(pdh)}", side="BOTH",
                           levels=levels, instrument=f"CE ½ / PE full · next weekly {exp} · 1-ITM",
                           steps=steps, why=why)
    if not (pdl < po < pdh):
        return TodaySignal(IDLE, f"Opened {fmt(po)} OUTSIDE yesterday's range — no PDHL trade today.", why=why)
    return TodaySignal(ARMED, f"Armed — open {fmt(po)} is inside the range; watch {fmt(up)} / {fmt(dn)} till 10:30",
                       side="BOTH", levels=levels, instrument=f"CE ½ / PE full · next weekly {exp} · 1-ITM",
                       steps=steps, why=why + f" · open {fmt(po)}")


STRATEGY = Strategy(
    id="62_S2_PDHL_SWING",
    name="PDHL swing — prior-day high/low break, 1h-swing trail ≤5 sessions",
    source="Algo_Nifty50/strategies/62_PoS_OptionBuying (PLAYBOOK S2, audit claim 1)",
    verdict="MARGINAL-GO",
    horizon="swing (≤5 sessions)",
    instrument="Option BUY: CE half size / PE full size, next weekly, 1 strike ITM",
    summary="Trend capture with a structural trail. Every year positive (2023-26: +26k/+89k/+85k/+79k at full "
            "size). 25% win — the big winners pay for everything.",
    rules=[
        "Only if today OPENS INSIDE yesterday's high–low range.",
        "09:15–10:30: stop-orders buy above PDH+2 / sell below PDL−2; first trigger is the trade; max 2 trades/day "
        "(each side once); no new trade while one is open.",
        "Stop beyond the last 3 five-min candles' extreme before the fill, clamped 10 pts – 0.15% of spot.",
        "Trail to each new confirmed 1-hour swing (5m pivot, 12 bars each side); never loosen; exit 15:20 on "
        "the 5th session. No target.",
        "Size: PE full, CE HALF (keeps ~85% of profit, DD ≈ −₹24k).",
    ],
    stats={"Trades": "303 (2023-01 → 2026-10)", "Win": "25%", "PF": "1.76", "Net / lot 65": "+₹2.40 L (full size)",
           "≥10R trades": "18", "Every year": "positive", "Edge vs coin": "t = 1.7 (weak)"},
    caveats=["Option P&L is MODEL-priced — PAPER-TRADE FIRST against real fills.",
             "A random entry with the same trail also makes money: much of the edge is the exit + trend.",
             "Without its top-10 trades it is ~breakeven — never skip a signal or exit early. Size ≈ 1 lot per ₹2L.",
             "Bundled log is FULL size both sides; the recommended CE-half sizing is in the rules."],
    trade_log="62_S2_PDHL_SWING.csv",
    signal=signal,
    order=2,
)
