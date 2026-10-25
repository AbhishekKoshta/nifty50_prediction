"""extra_edges.py — two Teller edges whose state needs more than the anchor bar.

  Y  GOLD 09:15 (option buy, intraday) — 62_PoS_OptionBuying PLAYBOOK S1. Bias from the
     previous session's 14:30-15:30 candle (last_hour.json, written by update_last_hour.py),
     stop-order at the next 100-pt level ±5. Rule = ss_hyps.gold_hourly(anchor=09:15).
  R0 RSI(2) oversold, NO trend filter (swing long) — 10_RSI2_MeanRev first-up-close with the
     200-DMA gate removed. Replays the rule over the whole feed so a trade bought on an
     earlier day shows as live until its first up-close.

Each function returns Signal(**kwargs) dicts; teller.build_plan wraps them (no import of
teller here, so there's no circular import). Never raises — a failure becomes an IDLE card.
"""
from __future__ import annotations

import json
import math
import os
from datetime import date, timedelta

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
LAST_HOUR_FILE = os.path.join(HERE, "last_hour.json")
GOLD_LOG = os.path.join(HERE, "trade_logs", "GOLD0915.csv")

GOLD_GRID, GOLD_BUF, GOLD_STOP = 100, 5, 25
GOLD_STATS = ("MARGINAL-GO · options · PF 1.78 · 26% win · ~55/yr · +₹1.22 L/lot 65 · 13 trades ≥10R "
              "(model-priced — paper first)")
R0_TH, R0_MAX_HOLD = 10.0, 10
R0_STATS = ("MARGINAL · PF 2.50 · 71% win · ~17/yr · +4,109 pt (2023-26) · DD −1,041 · "
            "fails 2011-23 · ½ size below 200-DMA")


# ----------------------------------------------------------------------------- helpers
def _next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _next_weekly(session: date) -> date:
    """The weekly expiry AFTER the front one (Thu until 2025-08-31, Tue after; holidays → prior day)."""
    out, d = [], session - timedelta(days=1)
    while len(out) < 2:
        d += timedelta(days=1)
        if d.weekday() == (3 if d < date(2025, 9, 1) else 1):
            out.append(d)
    return out[1]


def _level_beyond(px: float, s: int, g: int = GOLD_GRID) -> int:
    return (math.floor(px / g) + 1) * g if s == 1 else (math.ceil(px / g) - 1) * g


def _itm(spot: float, kind: str) -> int:
    atm = round(spot / 50) * 50
    return int(atm - 50 if kind == "CE" else atm + 50)


def gold_bias(o, h, l, c, x=25, y=50) -> int:
    r = h - l
    if r <= 0:
        return 0
    pos, body = (c - l) / r, abs(c - o) / r
    if c > o and pos >= 1 - x / 100 and body >= y / 100:
        return 1
    if c < o and pos <= x / 100 and body >= y / 100:
        return -1
    return 0


def _load_last_hour(session: str) -> dict | None:
    try:
        with open(LAST_HOUR_FILE) as f:
            lh = json.load(f)
        return lh if lh.get("session") == session and lh.get("bars", 0) >= 10 else None
    except Exception:  # noqa: BLE001
        return None


def _gold_last_trade() -> str:
    try:
        r = pd.read_csv(GOLD_LOG).sort_values("date").iloc[-1]
        return (f"{pd.Timestamp(r.date):%d %b %Y}: {r.side} {'CE' if r.side == 'LONG' else 'PE'} · "
                f"{r.exit_reason} at {str(r.exit_ts)[11:16]} · {r.R:+.1f}R spot · "
                f"₹{r.net_rs:+,.0f}/lot (model)")
    except Exception:  # noqa: BLE001
        return ""


# ----------------------------------------------------------------------------- Y · GOLD 09:15
def gold_signal(d: pd.DataFrame, open_px: float | None) -> dict:
    anchor = d.index[-1].date()
    session = _next_weekday(anchor)
    base = dict(key="Y", name="GOLD 09:15 (option buy)", horizon="intraday", stats=GOLD_STATS,
                cond_kind="breakout", last_trade=_gold_last_trade())
    lh = _load_last_hour(str(anchor))
    if lh is None:
        return dict(base, side="BOTH", status="IDLE",
                    headline="Last-hour candle feed missing → GOLD 09:15 can't be read today",
                    trigger=f"needs {anchor}'s 14:30-15:30 candle (last_hour.json, written by the 16:00 IST run)")
    o, h, l, c = (float(lh[k]) for k in ("open", "high", "low", "close"))
    s = gold_bias(o, h, l, c)
    rng = h - l
    candle = (f"{anchor:%d %b} last hour O {o:,.0f} H {h:,.0f} L {l:,.0f} C {c:,.0f}: close at "
              f"{(c - l) / rng * 100 if rng else 0:.0f}% of range, body {abs(c - o) / rng * 100 if rng else 0:.0f}%")
    if s == 0:
        return dict(base, side="BOTH", status="IDLE",
                    headline="Yesterday's last hour was not a strong candle → GOLD 09:15 idle",
                    trigger=f"{candle} (needs close in top/bottom 25% AND body ≥ 50%)")
    side, kind, word = ("LONG", "CE", "above") if s == 1 else ("SHORT", "PE", "below")
    lv = _level_beyond(c, s)
    gapped = open_px is not None and (open_px - (lv + s * GOLD_BUF)) * s >= 0
    if gapped:
        lv = _level_beyond(open_px, s)
    trig = lv + s * GOLD_BUF
    exp = _next_weekly(session)
    head = (f"{'Bull' if s == 1 else 'Bear'} bias → BUY {kind} if NIFTY trades {word} {trig:,.0f} (09:15-14:30)"
            + (f" · open {open_px:,.0f} gapped through, level moved" if gapped else ""))
    return dict(base, side=side, status="CONDITIONAL", level=float(trig), headline=head,
                trigger=f"index stop-order {'BUY' if s == 1 else 'SELL'} at {trig:,.0f} ({lv} round level "
                        f"{'+' if s == 1 else '−'}{GOLD_BUF}) · {candle}",
                entry=f"buy {_itm(trig, kind)} {kind} (1-ITM at the fill), expiry {exp:%d %b} — next weekly, "
                      f"never the 0-DTE. Up to 4 attempts; after 2 stops at one level move 100 pts further.",
                stop=f"{trig - s * GOLD_STOP:,.0f} on the index ({GOLD_STOP} pts from the fill)",
                target="none — hold to the 15:20 square-off; done for the day after any non-stop exit",
                note="Option P&L is model-priced (62 audit: CONFIRMED-WITH-CAVEATS). Use limit-protected orders — "
                     "≥3 pt/side slippage turns the first half negative. Most of the profit is the PE side.")


# ----------------------------------------------------------------------------- R0 · RSI2 no filter
def _rsi2(close: pd.Series):
    """Same as teller._rsi(·, 2); also returns the Wilder up/down averages."""
    dlt = close.diff()
    up = dlt.clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    dn = (-dlt.clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, float("nan")))).fillna(50), up, dn


def rsi2_replay(d: pd.DataFrame):
    """All trades of the rule on `d`: (closed trades DataFrame, open trade dict | None)."""
    o, c = d.open.values, d.close.values
    r = _rsi2(d.close)[0].values
    idx, n = d.index, len(d)
    trades, open_tr, i = [], None, 2
    while i < n:
        if r[i] < R0_TH:
            ei = i + 1
            if ei >= n:
                break
            xi = next((j for j in range(ei, min(n, ei + R0_MAX_HOLD)) if c[j] > c[j - 1]), None)
            if xi is None and ei + R0_MAX_HOLD - 1 < n:
                xi = ei + R0_MAX_HOLD - 1
            if xi is None:
                open_tr = dict(entry_date=idx[ei], entry=float(o[ei]), held=n - ei)
                break
            trades.append(dict(date=idx[ei].strftime("%Y-%m-%d"), side="long",
                               entry=round(float(o[ei]), 2), exit=round(float(c[xi]), 2),
                               exit_reason="first-up-close" if c[xi] > c[xi - 1] else "time",
                               entry_dt=idx[ei].strftime("%Y-%m-%d 09:15"), exit_dt=idx[xi].strftime("%Y-%m-%d 15:30")))
            i = xi + 1
            continue
        i += 1
    return pd.DataFrame(trades), open_tr


def rsi2_nofilter_signal(d: pd.DataFrame, open_px: float | None) -> dict:
    rsi, up, dn = _rsi2(d.close)
    close, r = float(d.close.iloc[-1]), float(rsi.iloc[-1])
    sma200 = d.close.rolling(200).mean().iloc[-1]
    above = bool(close > sma200)
    size = "full size" if above else "½ size (below the 200-DMA — the weaker half of the trades)"
    base = dict(key="R0", name="RSI2 mean-rev (no filter)", side="LONG", horizon="swing", stats=R0_STATS)
    _, open_tr = rsi2_replay(d)
    regime = f"close {close:,.0f} {'>' if above else '<'} 200DMA {sma200:,.0f}"
    if open_tr is not None:
        return dict(base, status="ACTIVATED", since=f"{open_tr['entry_date']:%d %b %Y} @ {open_tr['entry']:,.0f}",
                    headline=f"HOLDING (session {open_tr['held']} of {R0_MAX_HOLD}) → SELL at today's close "
                             f"if NIFTY closes above {close:,.0f}",
                    trigger=f"bought {open_tr['entry_date']:%d %b} open {open_tr['entry']:,.0f}",
                    entry=f"in since {open_tr['entry_date']:%d %b} open {open_tr['entry']:,.0f}",
                    stop="none — the exit is the first up-close (10-session time stop)",
                    target=f"sell near 15:25 if NIFTY > {close:,.0f} (yesterday's close)")
    if r < R0_TH:
        live = open_px is not None
        return dict(base, status="ACTIVATED" if live else "ARMED",
                    headline=(f"Oversold (RSI2 {r:.1f}), any trend → BUY "
                              + (f"now at the open {open_px:,.0f}" if live else "tomorrow's open") + f" · {size}"),
                    trigger=f"RSI(2) {r:.1f} < {R0_TH:.0f} ✔ armed · no trend filter · {regime}",
                    entry=f"BUY at the open{f' {open_px:,.0f}' if live else ''} (swing) · {size}",
                    stop="none (no regime guard on this variant — size accordingly)",
                    target="exit on the FIRST UP-CLOSE (entry day counts), 10-day time stop",
                    note="Same rule as R without the 200-DMA filter: more trades, lower quality "
                         "(blocked-by-filter trades: 54% win, PF 1.68). Edge only in 2023-26.")
    arm = close + float(dn.iloc[-1]) - 9 * float(up.iloc[-1])      # close that makes RSI(2) < 10
    return dict(base, status="IDLE", headline="Not oversold → RSI2 (no filter) idle",
                trigger=f"RSI2 {r:.1f} ≥ {R0_TH:.0f} · arms if the next close is below {arm:,.0f} "
                        f"({arm - close:+,.0f} pt) → buy the following open")
