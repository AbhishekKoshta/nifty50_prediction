"""64 · 1h bullish engulfing → 3×ATR chandelier trail (long NIFTY futures).

Research + verdict: Algo_Nifty50/strategies/64_BigMovePrecursors (VERDICT.md, Parts D–F).
Live data: free Upstox v3 API (no auth) — 1h candles are 09:15-aligned and identical to the backtest
bars. The engine below is the exact backtest rule (b_frameworks.simulate, TRAIL mode): it replays the
last ~170 days so the card knows whether a trade is open right now and where its trailing stop is.
"""
from __future__ import annotations

import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ..core import (ACTIVE, ARMED, IDLE, LOT, NEEDS_DATA, Level, Strategy, TodaySignal, fmt)

IST = timezone(timedelta(hours=5, minutes=30))
KEY = urllib.parse.quote("NSE_INDEX|Nifty 50", safe="")
ATR_N, STOP_ATR, TRAIL_ATR, MAX_BARS = 14, 1.0, 3.0, 105
GAP_UP_PCT, FEE_PTS = 0.30, 10.0
_CACHE: dict = {}


# ----------------------------------------------------------------------------- data
def _get(url: str) -> list:
    import requests
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=20)
    r.raise_for_status()
    return (r.json().get("data") or {}).get("candles") or []


def _frame(c: list) -> pd.DataFrame:
    if not c:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    d = pd.DataFrame(c, columns=["t", "open", "high", "low", "close", "v", "oi"])
    d["t"] = pd.to_datetime(d.t).dt.tz_localize(None)
    return d.drop_duplicates("t").set_index("t").sort_index()[["open", "high", "low", "close"]]


def load_bars(days: int = 170, ttl: int = 300) -> tuple[pd.DataFrame, pd.Series]:
    """Completed 1h bars (09:15-aligned) for the last `days` + daily closes. Cached `ttl` s.
    Upstox caps an hourly request at ~1 quarter, so it is pulled in 85-day chunks."""
    now = datetime.now(IST).replace(tzinfo=None)
    if _CACHE.get("t", 0) > time.time() - ttl:
        return _CACHE["h"], _CACHE["d"]
    to, fr = now.date(), now.date() - timedelta(days=days)
    parts, hi_d = [], to
    while hi_d > fr:
        lo_d = max(fr, hi_d - timedelta(days=85))
        parts.append(_frame(_get(f"https://api.upstox.com/v3/historical-candle/{KEY}/hours/1/{hi_d}/{lo_d}")))
        hi_d = lo_d - timedelta(days=1)
    h = pd.concat(parts)
    try:
        h = pd.concat([h, _frame(_get(f"https://api.upstox.com/v3/historical-candle/intraday/{KEY}/hours/1"))])
    except Exception:  # noqa: BLE001  (intraday endpoint is empty/closed outside market hours)
        pass
    h = h[~h.index.duplicated(keep="last")].sort_index()
    # drop the bar still forming: 1h bars end at +60 min, the 15:15 bar ends at 15:30
    end = h.index + pd.Timedelta(minutes=60)
    end = end.where(h.index.time != pd.Timestamp("15:15").time(), h.index + pd.Timedelta(minutes=15))
    h = h[end <= now]
    d = _frame(_get(f"https://api.upstox.com/v3/historical-candle/{KEY}/days/1/{to}/{fr - timedelta(days=10)}")).close
    d.index = d.index.normalize()
    _CACHE.update(t=time.time(), h=h, d=d)
    return h, d


# ----------------------------------------------------------------------------- engine (= backtest)
def wilder_atr(h: pd.DataFrame, n: int = ATR_N) -> pd.Series:
    pc = h.close.shift()
    tr = pd.concat([h.high - h.low, (h.high - pc).abs(), (h.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def engulfing(h: pd.DataFrame) -> pd.Series:
    """TA-Lib CDLENGULFING > 0: green candle whose body engulfs the previous red body (one edge may tie)."""
    o, c, po, pc = h.open, h.close, h.open.shift(), h.close.shift()
    return (c >= o) & (pc < po) & (((c >= po) & (o < pc)) | ((c > po) & (o <= pc)))


def run(h: pd.DataFrame, daily_close: pd.Series):
    """Replay the no-overlap long system. Returns (closed_trades_df, open_position_dict | None)."""
    a = wilder_atr(h).values
    sig = engulfing(h).fillna(False).values
    o, hi, lo, c = (h[k].values for k in ("open", "high", "low", "close"))
    idx = h.index
    day_open = h.groupby(idx.normalize()).open.transform("first")
    prev_close = daily_close.shift(1).reindex(idx.normalize()).values
    gap = (day_open.values / prev_close - 1) * 100
    trades, t, n = [], 0, len(h)
    pos = None
    while t < n:
        if not sig[t] or np.isnan(a[t]):
            t += 1
            continue
        if t == n - 1:                         # signal on the last completed bar → buy the next open
            pos = dict(state="ARMED", signal_ts=idx[t], atr=a[t], close=c[t], gap=gap[t])
            break
        e = t + 1
        ep, risk = o[e], STOP_ATR * a[t]
        stop0 = stop = ep - risk
        ext, xp, xi, why = ep, None, None, None
        for j in range(e, min(n, e + MAX_BARS)):
            if lo[j] <= stop:
                xp, xi = min(stop, o[j]), j
                why = "Initial stop" if stop == stop0 else "Trail stop"
                break
            ext = max(ext, hi[j])
            stop = max(stop, ext - TRAIL_ATR * a[j])
        if xp is None and e + MAX_BARS <= n:
            xi = e + MAX_BARS - 1
            xp, why = c[xi], "Time cap"
        lots = 2 if (not np.isnan(gap[t]) and gap[t] >= GAP_UP_PCT) else 1
        if xp is None:                         # still open on the last completed bar
            pos = dict(state="ACTIVE", signal_ts=idx[t], entry_ts=idx[e], entry=ep, stop0=stop0, stop=stop,
                       high=ext, last=c[-1], risk=risk, gap=gap[t], lots=lots, bars=n - e)
            break
        pts = xp - ep - FEE_PTS
        trades.append(dict(date=idx[e].strftime("%Y-%m-%d"), side="LONG", signal_ts=idx[t].strftime("%Y-%m-%d %H:%M"),
                           entry_ts=idx[e].strftime("%Y-%m-%d %H:%M"), exit_ts=idx[xi].strftime("%Y-%m-%d %H:%M"),
                           entry=round(ep, 2), stop=round(stop0, 2), exit=round(xp, 2), exit_reason=why,
                           net_pts=round(pts, 1), R=round(pts / risk, 2), net_rs=round(pts * LOT, 0),
                           gap_pct=round(float(gap[t]), 2) if not np.isnan(gap[t]) else None, lots=lots))
        t = xi + 1
    return pd.DataFrame(trades), pos


# ----------------------------------------------------------------------------- live card
def _next_bar_time(ts: pd.Timestamp) -> str:
    return "09:15 next session" if ts.time() >= pd.Timestamp("15:15").time() else (ts + pd.Timedelta(hours=1)).strftime("%H:%M")


def signal(ctx) -> TodaySignal:
    if (date.today() - ctx.last_date).days > 7:      # strategy_book.check replay: needs live 1h bars
        return TodaySignal(NEEDS_DATA, "1h intraday strategy — replay not available from daily data")
    try:
        h, d = load_bars()
    except Exception as e:  # noqa: BLE001
        return TodaySignal(NEEDS_DATA, f"Upstox 1h feed unavailable ({e.__class__.__name__}) — check manually")
    if len(h) < 60:
        return TodaySignal(NEEDS_DATA, "Not enough 1h bars from Upstox")
    closed, pos = run(h, d)
    last = h.iloc[-1]
    atr = float(wilder_atr(h).iloc[-1])
    why = (f"last completed 1h bar {h.index[-1]:%d %b %H:%M} O {fmt(last.open)} H {fmt(last.high)} "
           f"L {fmt(last.low)} C {fmt(last.close)} · ATR14(1h) {fmt(atr)}")
    recent = closed.tail(5).iloc[::-1] if len(closed) else None
    if pos and pos["state"] == "ACTIVE":
        unreal = pos["last"] - pos["entry"]
        sig = TodaySignal(
            ACTIVE, f"LONG open since {pos['entry_ts']:%d %b %H:%M} @ {fmt(pos['entry'])} — trail stop now "
                    f"{fmt(pos['stop'])} ({unreal:+,.0f} pts, {unreal / pos['risk']:+.1f}R)",
            side="LONG",
            levels=[Level("Entry", pos["entry"], f"{pos['entry_ts']:%d %b %H:%M}"),
                    Level("Trail stop", pos["stop"], f"highest high {fmt(pos['high'])} − 3×ATR"),
                    Level("Initial stop", pos["stop0"], f"risk {fmt(pos['risk'])} pts"),
                    Level("Lots", pos["lots"], "2 = gap-up day signal" if pos["lots"] == 2 else "1 = normal day")],
            instrument="NIFTY futures (current month)",
            steps=["Hold. Exit only when a 1h candle trades at or below the trail stop (gap below → exit at the open).",
                   "After each 1h close, raise the stop to (highest high since entry − 3 × ATR14). Never lower it.",
                   "Ignore new engulfing signals while this trade is open."],
            why=why)
    elif pos and pos["state"] == "ARMED":
        g = pos["gap"]
        lots = 2 if (g is not None and not np.isnan(g) and g >= GAP_UP_PCT) else 1
        sig = TodaySignal(
            ARMED, f"BUY at the next 1h open ({_next_bar_time(pos['signal_ts'])}) — bullish engulfing closed "
                   f"{pos['signal_ts']:%d %b %H:%M} · {lots} lot{'s' if lots > 1 else ''}",
            side="LONG",
            levels=[Level("Signal close", pos["close"], f"{pos['signal_ts']:%d %b %H:%M}"),
                    Level("Stop ≈", pos["close"] - STOP_ATR * pos["atr"], "entry − 1×ATR14 (set from the actual fill)"),
                    Level("ATR14 (1h)", pos["atr"], "trail = highest high − 3×ATR")],
            instrument="NIFTY futures (current month)",
            steps=[f"Buy at the next 1h open ({_next_bar_time(pos['signal_ts'])}).",
                   f"Initial stop = fill − {fmt(pos['atr'])} (1×ATR14). No target.",
                   "After every 1h close: stop = max(stop, highest high since entry − 3×ATR14).",
                   f"Size: {'2 lots — signal on a gap-up day (≥ +0.3%)' if lots == 2 else '1 lot (2 only on a ≥ +0.3% gap-up day)'}."],
            why=why)
    else:
        red = last.close < last.open
        arm = (f"Arms if the {_next_bar_time(h.index[-1])} candle closes ≥ {fmt(last.open)} (and opens ≤ {fmt(last.close)})"
               if red else "Last 1h candle is green — no engulfing possible on the next candle")
        sig = TodaySignal(IDLE, f"Flat — waiting for a 1h bullish engulfing. {arm}.", side="LONG",
                          levels=[Level("Prev 1h open", float(last.open), "the body to engulf (if red)"),
                                  Level("Prev 1h close", float(last.close), ""),
                                  Level("ATR14 (1h)", atr, "stop distance")],
                          why=why)
    sig.recent_trades = recent
    return sig


STRATEGY = Strategy(
    id="64_ENGULF1H",
    name="1h bullish engulfing → 3×ATR trail",
    source="Algo_Nifty50/strategies/64_BigMovePrecursors",
    verdict="GO",
    horizon="swing (median ~6 sessions, holds overnight)",
    instrument="NIFTY futures · 1 lot (2 lots on a gap-up day)",
    summary="Buy the next 1h open after a 1h bullish engulfing candle; 1×ATR stop, then a 3×ATR chandelier trail, "
            "no target. Small −1R losses, occasional 5–20R runs. Works in bull AND bear tapes (regime-agnostic).",
    rules=[
        "Chart: NIFTY 50 spot, 1h candles aligned to 09:15 (09:15 … 14:15, then 15:15–15:30). ATR = Wilder ATR(14) on these bars.",
        "Signal at a 1h close: candle is green (C ≥ O), previous is red (C < O), and the body engulfs it: "
        "[C ≥ prev O and O < prev C] or [C > prev O and O ≤ prev C]. No trend / time / size filter.",
        "Enter LONG at the next 1h open (15:15 signal → 09:15 next day). Only one position at a time.",
        "Initial stop = entry − 1 × ATR14 (at the signal bar).",
        "Trail: after every 1h close, stop = max(stop, highest high since entry − 3 × ATR14). Exit when a candle trades "
        "through the stop (gap below → exit at the open). Safety cap 105 bars.",
        "Size: 2 lots when the signal comes on a day that opened ≥ +0.3% above the previous close, else 1 lot.",
    ],
    stats={"Trades": "241 (~48/yr)", "Win": "36%", "PF": "1.80", "Net (1 lot)": "+11,029 pts",
           "With gap-up 2× sizing": "+16,714 pts", "Walk-forward": "+4,280 / +6,750", "Every year +": "2022–2026",
           "Robustness": "9/9 stop/trail grid", "vs random entries": "100th pct (1000 sims)"},
    caveats=[
        "Only 4.8 years of 1h history (Upstox intraday starts 2022). Longest losing streak 10, max drawdown −1,425 pts.",
        "Gap-up 2× sizing was picked after seeing both halves (data-derived) — paper-trade it before trusting the 2×.",
        "Bull/bear/VIX filters do NOT help: they flipped between 2022-24 and 2025-26. Take every signal.",
        "Backtest on spot with 10 pts/round trip; futures basis and slippage on 09:15 gap fills can differ.",
        "The live card replays the last ~170 days of Upstox 1h data; if Upstox is unreachable it shows NEEDS DATA.",
    ],
    trade_log="64_ENGULF1H.csv",
    signal=signal,
    order=0,
)
