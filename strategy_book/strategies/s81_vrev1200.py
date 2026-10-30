"""81_VRev1200 · 12:00 V-reversal continuation (gated)

Research + verdict: Algo_Nifty50/strategies/81_Move200_RuleBook (VERDICT.md, RULEBOOK.md) — MARGINAL-GO, paper first.
The rule (= the backtest, c_rules.r5_vrev / c2_vrev):
  by the 11:55 5m close the day has made a leg >= 0.5 x ATR14 (yesterday's) from the 09:15 open and retraced >= 50% of it
  -> enter at the 12:00 open WITH the retrace (down-then-up V = LONG, up-then-down V = SHORT); stop 0.25% of price;
  chandelier trail 0.5 x ATR from the best price since entry; square off 15:25. One trade per day.
Deploy gate (confirmed on both walk-forward halves): VOL2 (>= 2 of VIX_prev >= 16, ATR% rank >= 0.67 vs 252 d, yesterday |ret| >= 1%,
09:15-10:15 range rank >= 0.67 vs 60 sessions) OR the daily tape is not bull (yesterday's close above a rising 50-DMA = bull).
This card knows the daily context (ATR, tape, ATR rank, prev-day move) and fetches yesterday's India VIX from Upstox; the opening-hour
rank and the 11:55 V geometry are intraday facts the trader checks at 10:15 / 11:55 — the card prints the exact thresholds in points.
"""
from __future__ import annotations

import time
import urllib.parse
from datetime import timedelta

import numpy as np
import pandas as pd

from ..core import (ARMED, CONDITIONAL, IDLE, LOT, NEEDS_DATA, Level, Strategy, TodaySignal, fmt)

LEG_ATR, RETR, STOP_PCT, TRAIL_ATR = 0.5, 0.5, 0.0025, 0.5
VIX_KEY = urllib.parse.quote("NSE_INDEX|India VIX", safe="")
_VIX: dict = {}


def _wilder_atr(d: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = d.close.shift()
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def vix_close(on_date, ttl: int = 900) -> float | None:
    """India VIX close of `on_date` (the last completed session) from the free Upstox v3 API; None on failure."""
    key = str(on_date)
    hit = _VIX.get(key)
    if hit and hit[0] > time.time() - ttl:
        return hit[1]
    try:
        import requests
        fr = on_date - timedelta(days=10)
        r = requests.get(f"https://api.upstox.com/v3/historical-candle/{VIX_KEY}/days/1/{on_date}/{fr}",
                         headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=10)
        c = (r.json().get("data") or {}).get("candles") or []
        if not c:
            return None
        c = sorted(c, key=lambda x: x[0])
        v = float(c[-1][4])
        _VIX[key] = (time.time(), v)
        return v
    except Exception:  # noqa: BLE001
        return None


def signal(ctx) -> TodaySignal:
    d = ctx.daily
    if len(d) < 260:
        return TodaySignal(NEEDS_DATA, "Need 260 daily bars for the ATR rank / 50-DMA", why=f"have {len(d)}")
    atr = _wilder_atr(d)
    atr_pct = atr / d.close
    a = float(atr.iloc[-1])
    atr_rank = float((atr_pct.iloc[-252:] <= atr_pct.iloc[-1]).mean())
    dma50 = d.close.rolling(50).mean()
    bull = bool(d.close.iloc[-1] > dma50.iloc[-1] and dma50.iloc[-1] > dma50.iloc[-11])
    prev_ret = float(d.close.iloc[-1] / d.close.iloc[-2] - 1) * 100
    prev_big = abs(prev_ret) >= 1.0
    vix = vix_close(ctx.last_date)
    vix_hi = (vix is not None) and vix >= 16
    vol_known = int(atr_rank >= 0.67) + int(prev_big) + int(vix_hi)
    ref = ctx.open_px or float(d.close.iloc[-1])
    leg_pts, stop_pts, trail_pts = LEG_ATR * a, STOP_PCT * ref, TRAIL_ATR * a
    levels = [Level("V leg needed (0.5 x ATR)", round(leg_pts), f"ATR14 {fmt(a)} · the open->extreme move by 11:55 must be >= this"),
              Level("Retrace needed", None, ">= 50% of that leg, back toward the open, by the 11:55 close"),
              Level("Stop (0.25%)", round(stop_pts), "from the 12:00 fill"),
              Level("Trail (0.5 x ATR)", round(trail_pts), "chandelier from the best price since entry; out 15:25")]
    why = (f"tape {'BULL' if bull else 'not bull'} (close {fmt(d.close.iloc[-1])} vs 50-DMA {fmt(dma50.iloc[-1])}) · ATR rank {atr_rank:.2f} · "
           f"yesterday {prev_ret:+.2f}% · VIX {fmt(vix, 1) if vix is not None else 'n/a'}")
    steps = ["10:15 — opening-hour (09:15-10:15) range vs the last 60 sessions: top third = +1 to VOL2",
             "11:55 — leg from the open >= the 'V leg needed' AND retraced >= 50% (extreme printed before 11:55)? side = direction of the retrace",
             "12:00 — enter at the open: futures 1 lot, or ATM / 1st-OTM of the current week (CE long, PE short); 200-ITM for futures-like P&L",
             "stop 0.25% · trail 0.5 x ATR from the best price · square off 15:25 · optional: exit at 13:00 if not +0.5R"]
    if not bull:
        return TodaySignal(CONDITIONAL, f"Tape not bull → ELIGIBLE. Trade the 12:00 V if the leg is >= {fmt(leg_pts)} pt and half retraced.", side="BOTH",
                           levels=levels, instrument="NIFTY fut 1 lot / ATM-1OTM current weekly", steps=steps, why=why)
    if vol_known >= 2:
        return TodaySignal(CONDITIONAL, f"Bull tape but VOL2 met ({vol_known} of VIX>=16 / ATR rank / big yesterday) → ELIGIBLE. Trade the 12:00 V.", side="BOTH",
                           levels=levels, instrument="NIFTY fut 1 lot / ATM-1OTM current weekly", steps=steps, why=why)
    need = 2 - vol_known
    return TodaySignal(CONDITIONAL, f"Bull tape, VOL2 has {vol_known}/2 → eligible only if the opening hour (09:15-10:15) ranks in the top third"
                       + (" AND VIX >= 16" if (vix is None and need >= 2) else "") + "; otherwise SIT OUT (bull quiet tape lost in both halves).",
                       side="BOTH", levels=levels, instrument="NIFTY fut 1 lot / ATM-1OTM current weekly", steps=steps, why=why)


STRATEGY = Strategy(
    id="81_VRev1200",
    name="12:00 V-reversal continuation (gated)",
    source="Algo_Nifty50/strategies/81_Move200_RuleBook",
    verdict="MARGINAL-GO",
    horizon="intraday (12:00 → 15:25)",
    instrument="NIFTY futures 1 lot, or ATM / 1st-OTM of the current weekly (CE long, PE short); 200-ITM tracks futures (~0.8 Δ-capture)",
    summary="The market makes a >= 0.5-ATR leg from the open and takes half of it back by noon; the second leg of the V runs into the afternoon. "
            "Size is predictable (vol state), direction only once the reversal is proven — so enter at 12:00 with the retrace and trail. 80's 'fast moves reverse to origin' as a noon trade.",
    rules=["Gate: VOL2 (>= 2 of VIX_prev >= 16, ATR% rank >= 0.67, yesterday |ret| >= 1%, opening-hour range rank >= 0.67) OR tape not bull (close not above a rising 50-DMA). Quiet bull tape = sit out.",
           "11:55 close: open->extreme leg >= 0.5 x ATR14 (yesterday's) and >= 50% retraced toward the open; extreme printed before 11:55.",
           "12:00 open: enter WITH the retrace (down-then-up = LONG, up-then-down = SHORT). One trade per day.",
           "Stop 0.25% of price. Exit: chandelier trail 0.5 x ATR from the best price since entry, else 15:25 square-off. No fixed target (100-pt target = coin flip).",
           "Optional time stop: if not +0.5R by 13:00, exit (same PF, max DD -753 -> -542)."],
    stats={"Trades": "85 (2022-01 → 2026-10, ~18/yr) gated; 122 all days", "Win": "46% (avg win 118 / avg loss 56 pt)",
           "PF": "1.79 gated (1.37 all days); 1.49 at the post-Apr-2026 17-pt cost", "Net / lot 65": "+2,017 pt = ₹1.31L gated · options ATM +₹99k (PF 2.3, DD −₹11.5k) · 200-ITM +₹136k",
           "Walk-forward": "H1 +616 / H2 +1,400 · 5/5 years · placebo 100th pct", "Robustness": "stop x trail 16/16 both halves; leg x retrace 11/16; decision time only 12:00-12:30 (narrow)"},
    caveats=["Narrow time plateau: the same V at 11:00/11:30/13:00 does not pay — the entry is the 12:00 open, not 'around noon'.",
             "0.4-ATR legs fail H1; 2025 was −358 ungated (the bull quiet tape) — the gate is what makes it 5/5 years.",
             "Futures STT 0.05% from Apr-2026 → ~17 pt/RT; the gated version keeps PF 1.49, the ungated one is thin (PF 1.13). Options avoid most of that cost.",
             "Options: 3x winners are 1-5% of trades; the edge is PF 2.2-2.3 from 1.5-2.5x winners vs −30-50% losers (stop is on the index). ₹20 lottery strikes are the worst leg (3/5 years).",
             "Paper first: re-judge after 30 trades (PF >= 1.4 at 17 pt, both sides >= 0); a 7-trade losing run exceeds the backtest max (5-6)."],
    trade_log="81_VRev1200.csv",
    signal=signal,
    order=81,
)
