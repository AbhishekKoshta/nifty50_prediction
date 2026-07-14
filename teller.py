#!/usr/bin/env python3
"""teller.py — the morning-plan TELLER for the Nifty50 dashboard.

Runs on the latest market CLOSE and tells you exactly what to do at the NEXT
session's open, with hard trigger LEVELS. It only encodes validated **GO** edges
(from the market-analysis backtest library) so the dashboard never surfaces a
NO-GO setup:

  Intraday (net of ~10 pt/RT Zerodha futures, lot 75, EOD square-off):
    A DownDayBounce  (long)  — armed at close when today o->c < -0.9%
    B GapFade-short  (short) — fires IF tomorrow opens gap-up >= 0.30% AND close>20DMA
    F GapHalfFill    (short) — fires IF tomorrow opens gap-up >= 0.35% (half-gap target)
    C Squeeze-ORB    (long)  — armed at close when 5d span in bottom 25%; break of 30m OR
    M MarubozuGapReclaim (long) — fires IF tomorrow gaps DOWN below a green close-on-high
                                  candle's low (MARGINAL, rare satellite)
    P GapDownBounce  (long)  — fires IF tomorrow gaps DOWN after a big 3-day drop (exhaustion
                                bounce; buy open, exit close; skip in a crash). MARGINAL-GO (30)
  Swing (daily):
    R RSI2 mean-rev  (long)  — armed at close when close>200DMA and RSI(2)<10
    D BearRallyFade  (short) — armed at close when up-day>+0.6% AND close<50DMA (MARGINAL-GO)
    G CarryFwdMomentum(long) — which side to CARRY overnight: momentum persists on NIFTY (32).
    N MomentumCarryBook(long)— the validated 4-leg carry book (33): 20-DMA reclaim / wide-range
                                thrust / gap-up hold / 3-up-days, gated close>20DMA; buy the close,
                                prior-day-low trail. GO. Reports which leg fired.
    T Donchian trend (both)  — daily 20/10 channel proxy for the validated 1h Donchian (12, GO-thin):
                                break the 20-day high → trend LONG, the 20-day low → trend SHORT.
    E EMA5BreakdownShort(short)— fresh loss of the 5-EMA while close<50DMA → short next open, 2-day
                                hold. MARGINAL-GO (34), correction-harvester.
    K Breakdown20DMAShort(short)— fresh close below the 20-DMA while close<50DMA → short, cover on a
                                close above the prior high. MARGINAL-GO (43).
    S BearShootingStar(short)— shooting star + RSI2>90 + 500-pt stretch → sell-stop the star's low.
                                MARGINAL, rare (~1.7/yr, 27).
    O OverboughtReversalFade(short)— bearish reversal candle at RSI14>70 → short next open. MARGINAL,
                                ~96% of its edge is 2024 — superseded by BearRallyFade (29).

Most are validated GO edges; several are MARGINAL satellites (BearRallyFade, GapDownBounce,
EMA5/20-DMA breakdown shorts, MarubozuGapReclaim, BearShootingStar, OverboughtReversalFade) —
each labelled with its stats so it's taken small, not treated as core. 13_RegimeSystem is the
Bull/Bear regime banner + running RSI2 (R) and Donchian (T) as a portfolio.

The two GAP-UP shorts (B, F) are the "if it opens gap-up, go short — above what
level" answer: their trigger levels are printed as absolute NIFTY prices.

Signals are one of:
  ARMED   -> a close-known condition is TRUE; act at tomorrow's open (level given).
  CONDITIONAL -> depends on tomorrow's OPEN (a gap); trigger level given now.
  IDLE    -> condition not met; nothing to do for this edge.

Once today's OPEN is known (the 09:10-IST run writes today_open.json), the plan
RESOLVES against the real open:
  ACTIVATED -> the setup fired for today; concrete open-based entry/stop/target.
  PASSED    -> a gap-conditional edge that did NOT get its gap today; stand down.

Pure module: `build_plan(daily_df, today_open=None)` -> dict. No Streamlit import
here so it can be unit-tested and reused. `load_daily()` builds the daily indicator
frame from the dashboard's daily OHLC feed (Nifty_Features.csv). `load_today_open()`
reads today_open.json if the morning run has written it.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

# Daily OHLC lives next to this file in the deploy repo.
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Nifty_Features.csv")
OPEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "today_open.json")
LOT = 75
FEE_PTS = 10.0

# ---- edge parameters (a-priori, from the validated GO configs) ----------------
DD_THRESH = -0.9          # A: prior o->c return (%) to arm DownDayBounce
DD_ATR_MULT = 1.0         # A: stop/target = 1 x ATR14
GAPFADE_GMIN = 0.30       # B: min gap-up (%) to short (uptrend-filtered)
GAPHALF_GMIN = 0.35       # F: min gap-up (%) to short (half-gap target)
GAPHALF_SL_PTS = 50.0     # F: fixed stop points
GAPHALF_TFRAC = 0.5       # F: take profit at half the gap
SQUEEZE_Q = 0.25          # C: 5d-span percentile (bottom 25%) over trailing 60d
RSI2_TH = 10.0            # R: RSI(2) oversold threshold
RSI2_REGIME_SMA = 200     # R: long-only regime filter
BEARFADE_UP_THRESH = 0.6  # D: prior o->c up-day (%) to arm BearRallyFade
BEARFADE_ATR_MULT = 2.0   # D: protective stop = 2 x ATR14
BEARFADE_REGIME_SMA = 50  # D: short only below the 50-DMA (active downtrend)
MARU_HIWICK = 0.18        # M: upper-wick %% max to call a green "close-on-high" marubozu
MARU_SL_PTS = 50.0        # M: fixed stop points
CARRY_REGIME_SMA = 20     # G: carry-momentum uptrend gate (close>20DMA)
CARRY_SPAN = 1.17         # G: daily range %% proxy for the validated 4-green-hourly burst
CARRY_CLOSE_WICK = 0.30   # G: max upper-wick %% — closed strong / near the high

# --- expanded validated GO/MARGINAL library (added) ---------------------------
DONCH_N_ENTRY = 20        # T: daily Donchian breakout channel (proxy for the validated 1h 20/10)
DONCH_N_EXIT = 10         # T: opposite 10-bar channel = trailing exit
GDB_GAP = -0.15           # P: gap-down threshold (%) to consider the exhaustion bounce
GDB_DROP = 3.5            # P: prior 3-day high -> open drop (%) that marks exhaustion
GDB_RVOL_MAX = 2.5        # P: skip if 20-day realized vol (%) above this (crash circuit-breaker)
BOOK_THRUST_ATR = 1.5     # N: wide-range-thrust leg = range > 1.5 x ATR14
BOOK_THRUST_POS = 0.8     # N: close in the top 20% of the day's range
BOOK_GAPUP = 1.003        # N: gap-up-hold leg = open > prev_close x 1.003
EMA5_SPAN = 5             # E: fast EMA for the breakdown short
EMA5_HOLD = 2             # E: EMA5-breakdown short = fixed 2-day hold
SHOOT_STRETCH_PT = 500    # S: rally stretch (pts over 15 sessions) for the shooting-star short
SHOOT_LOOKBACK = 15       # S: stretch lookback (sessions)
SHOOT_RSI2 = 90           # S: RSI(2) overbought (rolling 3-day max) during the rally
SHOOT_SL_PTS = None       # S: stop = entry + 2xATR14 (computed live)
ORFADE_RSI14 = 70         # O: RSI(14) overbought for the reversal-candle fade


# ---- data / indicators --------------------------------------------------------
def _rsi(series: pd.Series, n: int) -> pd.Series:
    d = series.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def load_daily(data_file: str = DATA_FILE) -> pd.DataFrame:
    """Daily OHLC + all indicators the teller needs, from the dashboard feed."""
    if not os.path.exists(data_file):
        raise FileNotFoundError(f"daily feed not found: {data_file}")
    raw = pd.read_csv(data_file, usecols=["date", "open", "high", "low", "close"])
    raw["date"] = pd.to_datetime(raw["date"])
    d = (raw.sort_values("date").drop_duplicates("date").set_index("date")
         [["open", "high", "low", "close"]].astype(float))
    d["ret"] = (d["close"] - d["open"]) / d["open"] * 100          # intraday o->c %
    d["sma20"] = d["close"].rolling(20).mean()
    d["sma50"] = d["close"].rolling(BEARFADE_REGIME_SMA).mean()
    d["sma200"] = d["close"].rolling(RSI2_REGIME_SMA).mean()
    d["atr14"] = (d["high"] - d["low"]).rolling(14).mean()
    d["rsi2"] = _rsi(d["close"], 2)
    span5 = (d["high"].rolling(5).max() - d["low"].rolling(5).min()) / d["close"] * 100
    d["span5"] = span5
    d["span5_thr"] = span5.rolling(60, min_periods=20).quantile(SQUEEZE_Q)

    # ---- extra indicators for the expanded GO library ------------------------
    d["ema5"] = d["close"].ewm(span=EMA5_SPAN, adjust=False).mean()
    d["rsi14"] = _rsi(d["close"], 14)
    d["green"] = d["close"] > d["open"]
    d["body"] = (d["close"] - d["open"]).abs()
    d["rng"] = (d["high"] - d["low"]).replace(0, np.nan)
    d["uwick"] = d["high"] - d[["open", "close"]].max(axis=1)
    d["lwick"] = d[["open", "close"]].min(axis=1) - d["low"]
    d["close_pos"] = (d["close"] - d["low"]) / d["rng"]           # 1 = closed on the high
    prevc = d["close"].shift(1)
    prevsma20 = d["sma20"].shift(1)

    # N — the 4 carry-forward momentum legs (all long; gated on close>20DMA in build)
    d["leg_reclaim20"] = (prevc <= prevsma20) & (d["close"] > d["sma20"])          # R (⭐ champion)
    d["leg_thrust"] = d["green"] & (d["rng"] > BOOK_THRUST_ATR * d["atr14"]) & \
        (d["close_pos"] >= BOOK_THRUST_POS)                                        # W
    d["leg_gapup"] = (d["open"] > prevc * BOOK_GAPUP) & d["green"]                 # G
    d["leg_3up"] = d["green"] & d["green"].shift(1) & d["green"].shift(2)          # U

    # K / E — fresh downtrend-continuation breakdowns (need close<50DMA in build)
    d["fresh_bd20"] = (prevc >= prevsma20) & (d["close"] < d["sma20"])             # 43
    e = d["ema5"]; c = d["close"]
    d["fresh_ema5_bd"] = (c.shift(2) >= e.shift(2)) & (c.shift(1) < e.shift(1)) & (c < e)  # 34

    # P — gap-down exhaustion bounce (precondition known at close; gap resolved vs open)
    d["hi3"] = d["high"].rolling(3).max()
    d["rvol20"] = d["close"].pct_change().rolling(20).std() * 100

    # T — daily Donchian channel (proxy for the validated 1h 20/10)
    d["donch_hi20"] = d["high"].rolling(DONCH_N_ENTRY).max()
    d["donch_lo20"] = d["low"].rolling(DONCH_N_ENTRY).min()
    d["donch_hi10"] = d["high"].rolling(DONCH_N_EXIT).max()
    d["donch_lo10"] = d["low"].rolling(DONCH_N_EXIT).min()

    # S — bear shooting star (exhausted-top short)
    d["star"] = (d["uwick"] >= 1.5 * d["body"]) & (d["uwick"] >= 0.45 * d["rng"]) & \
        (d["lwick"] <= d["body"])
    d["stretch15"] = d["close"] - d["close"].shift(SHOOT_LOOKBACK)
    d["rsi2_max3"] = d["rsi2"].rolling(3).max()

    # O — bearish reversal candle at RSI14 overbought
    d["bear_engulf"] = (~d["green"]) & (d["close"].shift(1) > d["open"].shift(1)) & \
        (d["open"] >= d["close"].shift(1)) & (d["close"] <= d["open"].shift(1))
    return d


def load_today_open(open_file: str = OPEN_FILE) -> dict | None:
    """Read today_open.json if the morning run has produced it; else None.

    Never raises — a missing/corrupt file just means "pre-open plan", which is a
    perfectly valid state (weekend, holiday, or Yahoo not yet updated).
    """
    if not os.path.exists(open_file):
        return None
    try:
        with open(open_file) as f:
            data = json.load(f)
        if data.get("open") is None or not data.get("anchor_date"):
            return None
        return data
    except Exception:  # noqa: BLE001
        return None


# ---- plan model ---------------------------------------------------------------
@dataclass
class Signal:
    key: str                    # A/B/F/C/R/D/M
    name: str
    side: str                   # LONG / SHORT
    status: str                 # ACTIVATED / ARMED / CONDITIONAL / PASSED / IDLE
    horizon: str                # intraday / swing
    headline: str               # one-line what-to-do
    trigger: str                # the exact condition + level in words
    level: float | None = None  # absolute NIFTY price that arms the trade (gap shorts)
    entry: str = ""
    stop: str = ""
    target: str = ""
    note: str = ""
    stats: str = ""             # validated edge stats

    def as_dict(self):
        return asdict(self)


def _resolve_against_open(sigs: list[Signal], open_px: float, close: float, atr: float,
                          low: float, b_level: float, f_level: float,
                          green_maru: bool, uptrend20: bool,
                          extra: dict | None = None) -> None:
    """Mutate signals in place once today's real OPEN is known.

    ARMED (close-known) edges become ACTIVATED with concrete open-based prices.
    CONDITIONAL (gap-dependent) edges become ACTIVATED if their gap fired today,
    else PASSED. IDLE stays IDLE.
    """
    gap = open_px - close
    gap_pct = gap / close * 100 if close else 0.0
    for s in sigs:
        k = s.key
        if k == "A" and s.status == "ARMED":
            s.status = "ACTIVATED"
            s.headline = f"Down day → BUY now at the open {open_px:,.0f}"
            s.entry = f"BUY at open {open_px:,.0f}"
            s.stop = f"{open_px - DD_ATR_MULT * atr:,.0f}  (open − 1×ATR14, ATR={atr:,.0f})"
            s.target = f"{open_px + DD_ATR_MULT * atr:,.0f}  (open + 1×ATR14), else square-off 15:20"
        elif k == "R" and s.status == "ARMED":
            s.status = "ACTIVATED"
            s.entry = f"BUY at the open {open_px:,.0f} (swing)"
        elif k == "D" and s.status == "ARMED":
            s.status = "ACTIVATED"
            s.headline = f"Bear-rally pop → SHORT now at the open {open_px:,.0f}"
            s.entry = f"SHORT at open {open_px:,.0f}"
            s.stop = f"{open_px + BEARFADE_ATR_MULT * atr:,.0f}  (open + 2×ATR14, ATR={atr:,.0f})"
        elif k == "C" and s.status == "ARMED":
            s.status = "ACTIVATED"  # squeeze confirmed; OR high/low still print ~09:45
        elif k == "B" and s.status == "CONDITIONAL":
            if uptrend20 and open_px >= b_level:
                s.status = "ACTIVATED"
                s.headline = f"Gap-up {gap_pct:+.2f}% (open {open_px:,.0f} ≥ {b_level:,.0f}) → SHORT the open"
                s.entry = f"SHORT at open {open_px:,.0f} (limit at/just below)"
                s.stop = f"{open_px + gap:,.0f}  (open + 1×gap, gap={gap:,.0f})"
                s.target = f"{close:,.0f}  (prev close, full-gap fill), else square-off 15:20"
            else:
                s.status = "PASSED"
                s.headline = (f"Open {open_px:,.0f} ({gap_pct:+.2f}%) — no qualifying gap-up "
                              f"(needs ≥ {b_level:,.0f}) → GapFade-short passed")
        elif k == "F" and s.status == "CONDITIONAL":
            if open_px >= f_level:
                s.status = "ACTIVATED"
                s.headline = f"Gap-up {gap_pct:+.2f}% (open {open_px:,.0f} ≥ {f_level:,.0f}) → SHORT, target ½ the gap"
                s.entry = f"SHORT at open {open_px:,.0f} (limit at/just below)"
                s.stop = f"{open_px + GAPHALF_SL_PTS:,.0f}  (open + {GAPHALF_SL_PTS:.0f} pt)"
                s.target = f"{open_px - GAPHALF_TFRAC * gap:,.0f}  (open − ½ gap), else square-off 15:20"
            else:
                s.status = "PASSED"
                s.headline = (f"Open {open_px:,.0f} ({gap_pct:+.2f}%) — gap-up < {GAPHALF_GMIN:.2f}% "
                              f"(needs ≥ {f_level:,.0f}) → GapHalfFill passed")
        elif k == "M" and s.status == "CONDITIONAL":
            if green_maru and open_px < low:
                s.status = "ACTIVATED"
                s.headline = f"Gap-DOWN below {low:,.0f} (open {open_px:,.0f}) → BUY the open, reclaim to EOD"
                s.entry = f"BUY at open {open_px:,.0f}"
                s.stop = f"{open_px - MARU_SL_PTS:,.0f}  (open − {MARU_SL_PTS:.0f} pt)"
                s.target = "exit at the close (reclaim to EOD, no overnight risk)"
            else:
                s.status = "PASSED"
                s.headline = (f"Open {open_px:,.0f} did not gap down below {low:,.0f} "
                              f"→ MarubozuGapReclaim passed")
        elif k == "P" and s.status == "CONDITIONAL":
            hi3 = (extra or {}).get("hi3")
            rvol_ok = (extra or {}).get("rvol_ok", True)
            drop_pct = (hi3 - open_px) / open_px * 100 if hi3 else 0.0
            if gap_pct <= GDB_GAP and hi3 and drop_pct >= GDB_DROP and rvol_ok:
                s.status = "ACTIVATED"
                s.headline = (f"Gap-DOWN {gap_pct:+.2f}% after a {drop_pct:.1f}% 3-day drop "
                              f"→ BUY the open, exit at the close")
                s.entry = f"BUY at open {open_px:,.0f}"
                s.stop = "none — the daily bar is the trade (worst historical −460 pt)"
                s.target = "exit at TODAY'S close (intraday; no overnight hold)"
            else:
                why = ("crash circuit-breaker ON (20-day rvol high)" if not rvol_ok else
                       f"no exhausted gap-down (gap {gap_pct:+.2f}%, 3-day drop {drop_pct:.1f}% "
                       f"< {GDB_DROP:.1f}%)")
                s.status = "PASSED"
                s.headline = f"Open {open_px:,.0f} — {why} → GapDownBounce passed"
        elif k == "N" and s.status == "ARMED":
            s.status = "ACTIVATED"
            s.entry = f"BUY at open {open_px:,.0f} (carry; validated entry = the signal-day close)"
        elif k in ("E", "K", "O") and s.status == "ARMED":
            s.status = "ACTIVATED"
            s.entry = f"SHORT at open {open_px:,.0f}"


def _regime(close: float, sma20, sma50, sma200, ret: float) -> dict:
    """Bull / Bear / Neutral call from the daily DMAs, per the house rules:
    longs are favoured above the 200-DMA; short only below the 50-DMA; below the
    200 but holding the 50 is a recovery/squeeze zone with no structural edge."""
    a20 = sma20 is not None and not np.isnan(sma20) and close > sma20
    a50 = sma50 is not None and not np.isnan(sma50) and close > sma50
    a200 = sma200 is not None and not np.isnan(sma200) and close > sma200
    have200 = sma200 is not None and not np.isnan(sma200)

    if not have200:
        return {"label": "NEUTRAL", "tone": "neutral", "icon": "⚖️", "lean": "NONE",
                "favor": "Not enough history for a 200-DMA regime call — trade the intraday book only.",
                "rationale": f"close {close:,.0f} · 200-DMA warming up"}

    if a200:
        if a20:
            favor = ("Favouring LONGS — buy dips (RSI2), DownDayBounce, carry momentum long. "
                     "Shorts only as intraday gap-fades.")
            sub = "uptrend intact"
        else:
            favor = ("Bullish structure (above 200-DMA) but pulling back under the 20-DMA — "
                     "buy-the-dip watch, don't chase shorts.")
            sub = "pullback in an uptrend"
        return {"label": "BULL", "tone": "bull", "icon": "🐂", "lean": "LONG", "favor": favor,
                "rationale": f"close {close:,.0f} > 200-DMA {sma200:,.0f} ({sub})"}

    if not a50:
        return {"label": "BEAR", "tone": "bear", "icon": "🐻", "lean": "SHORT",
                "favor": ("Favouring SHORTS — BearRallyFade (fade up-day pops below the 50-DMA). "
                          "Longs off except exhaustion bounces (DownDayBounce)."),
                "rationale": f"close {close:,.0f} < 50-DMA {sma50:,.0f} < 200-DMA {sma200:,.0f}"}

    return {"label": "NEUTRAL", "tone": "neutral", "icon": "⚖️", "lean": "NONE",
            "favor": ("Recovery / no structural edge — below the 200-DMA but holding the 50-DMA "
                      "(the V-bottom squeeze zone). Trade the intraday book; don't carry directional."),
            "rationale": f"50-DMA {sma50:,.0f} < close {close:,.0f} < 200-DMA {sma200:,.0f}"}


def build_plan(daily: pd.DataFrame, today_open: dict | None = None) -> dict:
    """Given the daily indicator frame, return the morning plan for the NEXT session.

    If `today_open` (from today_open.json) is present AND its anchor_date matches
    this plan's anchor, the plan is RESOLVED against the real open — gap-dependent
    edges become ACTIVATED/PASSED and armed edges get concrete open-based prices.
    """
    d = daily.dropna(subset=["sma20", "atr14"]).copy()
    if len(d) == 0:
        raise ValueError("not enough history to build a plan")
    t = d.iloc[-1]                     # today's completed session = the anchor close
    close = float(t["close"])
    atr = float(t["atr14"])
    ret = float(t["ret"])
    uptrend20 = bool(close > t["sma20"]) if not np.isnan(t["sma20"]) else False
    below50 = bool(close < t["sma50"]) if not np.isnan(t["sma50"]) else False
    above200 = bool(close > t["sma200"]) if not np.isnan(t["sma200"]) else False
    rsi2 = float(t["rsi2"])
    squeeze = (not np.isnan(t["span5_thr"])) and bool(t["span5"] <= t["span5_thr"])
    high = float(t["high"])
    low = float(t["low"])
    uwpct = (high - close) / close * 100 if close else np.nan   # upper-wick %
    green_maru = bool(ret > 0 and uwpct <= MARU_HIWICK)         # green close-on-high candle

    # ---- extra reads for the expanded library --------------------------------
    rsi14 = float(t["rsi14"]) if not np.isnan(t["rsi14"]) else 50.0
    hi3 = float(t["hi3"]) if not np.isnan(t["hi3"]) else high
    rvol20 = float(t["rvol20"]) if not np.isnan(t["rvol20"]) else 0.0
    rvol_ok = rvol20 <= GDB_RVOL_MAX
    donch_hi20 = float(t["donch_hi20"]) if not np.isnan(t["donch_hi20"]) else high
    donch_lo20 = float(t["donch_lo20"]) if not np.isnan(t["donch_lo20"]) else low
    donch_hi10 = float(t["donch_hi10"]) if not np.isnan(t["donch_hi10"]) else high
    donch_lo10 = float(t["donch_lo10"]) if not np.isnan(t["donch_lo10"]) else low
    leg_reclaim = bool(t["leg_reclaim20"])
    leg_thrust = bool(t["leg_thrust"])
    leg_gapup = bool(t["leg_gapup"])
    leg_3up = bool(t["leg_3up"])
    fresh_bd20 = bool(t["fresh_bd20"])
    fresh_ema5 = bool(t["fresh_ema5_bd"])
    is_star = bool(t["star"])
    stretch15 = float(t["stretch15"]) if not np.isnan(t["stretch15"]) else 0.0
    rsi2_max3 = float(t["rsi2_max3"]) if not np.isnan(t["rsi2_max3"]) else 0.0
    bear_engulf = bool(t["bear_engulf"])

    sigs: list[Signal] = []

    # ---- B: GapFade-short (uptrend-filtered) — the primary "gap-up -> short" ----
    b_level = close * (1 + GAPFADE_GMIN / 100)
    if uptrend20:
        sigs.append(Signal(
            key="B", name="GapFade-short", side="SHORT", horizon="intraday",
            status="CONDITIONAL",
            headline=f"If open GAPS UP ≥ {GAPFADE_GMIN:.2f}% (above {b_level:,.0f}) → SHORT the open",
            trigger=f"open ≥ {b_level:,.0f}  (= close {close:,.0f} × {1+GAPFADE_GMIN/100:.4f}); uptrend filter close>20DMA ✔",
            level=b_level,
            entry="SHORT at 09:15 open (use a limit at/just below the open — edge lives in the 1st minute)",
            stop="open + 1.0 × gap  (gap = open − prev_close)",
            target=f"prev_close {close:,.0f}  (full-gap fill), else square-off 15:20",
            stats="GO · +1,937 pt · PF 1.52",
        ))
    else:
        sigs.append(Signal(
            key="B", name="GapFade-short", side="SHORT", horizon="intraday", status="IDLE",
            headline="Uptrend filter OFF (close ≤ 20-DMA) → GapFade-short not armed",
            trigger=f"needs close>20DMA; close {close:,.0f} vs 20DMA {t['sma20']:,.0f}",
            level=b_level,
            note="Fading a gap-up only validated in an uptrend; skip today.",
            stats="GO · +1,937 pt · PF 1.52",
        ))

    # ---- F: GapHalfFill short (short-only, no uptrend filter) -------------------
    f_level = close * (1 + GAPHALF_GMIN / 100)
    sigs.append(Signal(
        key="F", name="GapHalfFill-short", side="SHORT", horizon="intraday",
        status="CONDITIONAL",
        headline=f"If open GAPS UP ≥ {GAPHALF_GMIN:.2f}% (above {f_level:,.0f}) → SHORT, target half the gap",
        trigger=f"open ≥ {f_level:,.0f}  (= close {close:,.0f} × {1+GAPHALF_GMIN/100:.4f})",
        level=f_level,
        entry="SHORT at 09:15 open (limit at/just below open; fill-sensitive)",
        stop=f"open + {GAPHALF_SL_PTS:.0f} pt",
        target=f"open − {GAPHALF_TFRAC:.1f} × gap  (half-gap fill), else square-off 15:20",
        note="Highest-win gap short (66%). Don't chase if you miss the first minute.",
        stats="GO · +2,440 pt · PF 1.67 · 66% win",
    ))

    # ---- A: DownDayBounce (long) — armed by TODAY's close ----------------------
    if ret < DD_THRESH:
        stop = close - DD_ATR_MULT * atr        # entry ~ tomorrow open; close is best proxy now
        tgt = close + DD_ATR_MULT * atr
        sigs.append(Signal(
            key="A", name="DownDayBounce", side="LONG", horizon="intraday", status="ARMED",
            headline=f"Down day ({ret:+.2f}%) → BUY tomorrow's 09:15 open",
            trigger=f"today o→c {ret:+.2f}% < {DD_THRESH:.1f}%  ✔ armed",
            entry="BUY at 09:15 open",
            stop=f"open − 1.0 × ATR14  (ATR14={atr:,.0f} → ≈ {stop:,.0f} off today's close)",
            target=f"open + 1.0 × ATR14  (≈ {tgt:,.0f}), else square-off 15:20",
            note="Best single intraday edge; mean-reversion bounce after a selloff.",
            stats="GO · PF 1.72 · every year +",
        ))
    else:
        sigs.append(Signal(
            key="A", name="DownDayBounce", side="LONG", horizon="intraday", status="IDLE",
            headline=f"Not a down day (o→c {ret:+.2f}%) → no bounce buy",
            trigger=f"needs today o→c < {DD_THRESH:.1f}%; today {ret:+.2f}%",
            stats="GO · PF 1.72 · every year +",
        ))

    # ---- C: Squeeze-ORB (long) — squeeze armed by close, breakout intraday -----
    if squeeze:
        sigs.append(Signal(
            key="C", name="Squeeze-ORB", side="LONG", horizon="intraday", status="ARMED",
            headline="Volatility squeeze ON → watch a break of the 30-min opening range HIGH",
            trigger=f"5d span {t['span5']:.2f}% ≤ 25th-pctile {t['span5_thr']:.2f}%  ✔ armed",
            entry="LONG on a break of the 30-min OR high (first 6 bars), after 09:45, no new entry after 15:00",
            stop="opening-range LOW",
            target="no target — ride to 15:25 square-off (or OR-low stop)",
            note="OR high/low are only known ~09:45; the squeeze itself is confirmed now.",
            stats="GO leg of the intraday book",
        ))
    else:
        sigs.append(Signal(
            key="C", name="Squeeze-ORB", side="LONG", horizon="intraday", status="IDLE",
            headline="No squeeze (5d span not in bottom 25%) → ORB leg idle",
            trigger=f"5d span {t['span5']:.2f}% vs thr {t['span5_thr']:.2f}%",
            stats="GO leg of the intraday book",
        ))

    # ---- R: RSI2 mean-reversion (swing long) -----------------------------------
    if above200 and rsi2 < RSI2_TH:
        sigs.append(Signal(
            key="R", name="RSI2 mean-rev", side="LONG", horizon="swing", status="ARMED",
            headline=f"Oversold in an uptrend (RSI2 {rsi2:.1f}) → BUY tomorrow's open (swing)",
            trigger=f"close>200DMA ✔  and  RSI(2) {rsi2:.1f} < {RSI2_TH:.0f}  ✔ armed",
            entry="BUY at next day's open",
            stop="none (200-DMA regime is the risk control)",
            target="exit on the FIRST UP-CLOSE (preferred variant), 10-day time-stop backstop",
            note="High-edge low-frequency starter; ~13 trades/yr.",
            stats="GO · PF ~3.3 · ~80% win",
        ))
    else:
        why = []
        if not above200:
            why.append(f"close {close:,.0f} ≤ 200DMA {t['sma200']:,.0f}")
        if not (rsi2 < RSI2_TH):
            why.append(f"RSI2 {rsi2:.1f} ≥ {RSI2_TH:.0f}")
        sigs.append(Signal(
            key="R", name="RSI2 mean-rev", side="LONG", horizon="swing", status="IDLE",
            headline="Not oversold-in-uptrend → RSI2 swing idle",
            trigger="; ".join(why) if why else "conditions not met",
            stats="GO · PF ~3.3 · ~80% win",
        ))

    # ---- D: BearRallyFade (swing short) — fade a bear-rally pop below the 50-DMA -
    if below50 and ret > BEARFADE_UP_THRESH:
        stop = close + BEARFADE_ATR_MULT * atr     # entry ~ tomorrow open; close is best proxy now
        sigs.append(Signal(
            key="D", name="BearRallyFade", side="SHORT", horizon="swing", status="ARMED",
            headline=f"Bear-rally pop ({ret:+.2f}% up-day below 50-DMA) → SHORT tomorrow's open",
            trigger=f"close<50DMA ✔  and  up-day o→c {ret:+.2f}% > +{BEARFADE_UP_THRESH:.1f}%  ✔ armed",
            entry="SHORT at tomorrow's open",
            stop=f"open + 2.0 × ATR14  (ATR14={atr:,.0f} → ≈ {stop:,.0f} off today's close)",
            target="cover at the NEXT day's close (~1–2 day hold; the snapback edge decays — don't hold longer)",
            note="The one NIFTY short that profits in real bears (2020 +1,534, 2022 +1,157). Mirror of DownDayBounce.",
            stats="MARGINAL-GO · PF 1.41 · +4,036 pt · ~13/yr",
        ))
    else:
        why = []
        if not below50:
            why.append(f"close {close:,.0f} ≥ 50DMA {t['sma50']:,.0f}"
                       if not np.isnan(t["sma50"]) else "50DMA n/a")
        if not (ret > BEARFADE_UP_THRESH):
            why.append(f"o→c {ret:+.2f}% ≤ +{BEARFADE_UP_THRESH:.1f}% (not a rally pop)")
        sigs.append(Signal(
            key="D", name="BearRallyFade", side="SHORT", horizon="swing", status="IDLE",
            headline="No bear-rally pop (needs an up-day >+0.6% below the 50-DMA) → BearRallyFade idle",
            trigger="; ".join(why) if why else "conditions not met",
            stats="MARGINAL-GO · PF 1.41 · +4,036 pt · ~13/yr",
        ))

    # ---- M: MarubozuGapReclaim (intraday long) — gap-down below a green close-on-high -
    if green_maru:
        sigs.append(Signal(
            key="M", name="MarubozuGapReclaim", side="LONG", horizon="intraday",
            status="CONDITIONAL",
            headline=f"Green close-on-high candle → if open GAPS DOWN below {low:,.0f} → BUY the open",
            trigger=f"today green, upper-wick {uwpct:.2f}% ≤ {MARU_HIWICK:.2f}% ✔ (armed); "
                    f"needs tomorrow open < today's low {low:,.0f} (gap-down through the candle)",
            level=low,
            entry="BUY at 09:15 open (only if it opens below today's low)",
            stop=f"open − {MARU_SL_PTS:.0f} pt",
            target="exit at the close (reclaim to EOD, no overnight risk)",
            note="Rare defined-risk satellite (~2/yr, NIFTY-only, n=22) — take it if it appears, size small.",
            stats="MARGINAL · PF 2.53 · 59% win · ~2/yr",
        ))
    else:
        why = []
        if not (ret > 0):
            why.append(f"not a green day (o→c {ret:+.2f}%)")
        elif not (uwpct <= MARU_HIWICK):
            why.append(f"upper-wick {uwpct:.2f}% > {MARU_HIWICK:.2f}% (didn't close on its high)")
        sigs.append(Signal(
            key="M", name="MarubozuGapReclaim", side="LONG", horizon="intraday", status="IDLE",
            headline="No green close-on-high candle → MarubozuGapReclaim not armed",
            trigger="; ".join(why) if why else "conditions not met",
            stats="MARGINAL · PF 2.53 · 59% win · ~2/yr",
        ))

    # ---- P: GapDownBounce (intraday long) — exhausted gap-down bounce -----------
    drop_level = hi3 / (1 + GDB_DROP / 100)              # open must be ≤ this for a ≥3.5% 3-day drop
    gap_level = close * (1 + GDB_GAP / 100)              # and a gap-down vs prev close
    p_level = min(drop_level, gap_level)
    if rvol_ok:
        sigs.append(Signal(
            key="P", name="GapDownBounce", side="LONG", horizon="intraday", status="CONDITIONAL",
            headline=f"If open GAPS DOWN below {p_level:,.0f} (big 3-day drop) → BUY the open, exit at close",
            trigger=f"needs gap-down & (3-day high {hi3:,.0f} − open)/open ≥ {GDB_DROP:.1f}%; "
                    f"crash-breaker OFF (20d rvol {rvol20:.2f}% ≤ {GDB_RVOL_MAX:.1f}%) ✔",
            level=p_level,
            entry="BUY at 09:15 open (only if it gaps down through the level)",
            stop="none — the daily bar is the trade (worst historical −460 pt)",
            target="exit at today's CLOSE (intraday; do NOT hold — the bounce fades by day 3)",
            note="Buys exhaustion, not a crash — the rvol circuit-breaker skips waterfall days.",
            stats="MARGINAL-GO · PF 1.74 · 58% win · +2,654 pt · ~6/yr",
        ))
    else:
        sigs.append(Signal(
            key="P", name="GapDownBounce", side="LONG", horizon="intraday", status="IDLE",
            headline=f"Crash circuit-breaker ON (20-day rvol {rvol20:.2f}% > {GDB_RVOL_MAX:.1f}%) → no gap-down buy",
            trigger="extreme-vol regime — don't catch a falling knife",
            stats="MARGINAL-GO · PF 1.74 · 58% win · +2,654 pt · ~6/yr",
        ))

    # ---- T: Donchian trend (both sides) — daily-channel proxy for the 1h 20/10 --
    _pos = ("above the 20-day high (trend LONG in force)" if close >= donch_hi20 else
            "below the 20-day low (trend SHORT in force)" if close <= donch_lo20 else
            "inside the channel (no trend position)")
    sigs.append(Signal(
        key="T", name="Donchian trend", side="BOTH", horizon="swing", status="CONDITIONAL",
        headline=f"Trend LONG on a break above {donch_hi20:,.0f} · trend SHORT below {donch_lo20:,.0f}",
        trigger=f"daily 20/10 channel · currently {_pos}",
        level=donch_hi20,
        entry=f"LONG if it breaks {donch_hi20:,.0f} (20-day high) · SHORT if it breaks {donch_lo20:,.0f} (20-day low)",
        stop="opposite 10-day channel is the trail",
        target=f"trail: long exits on a break below {donch_lo10:,.0f} (10-day low); "
               f"short exits above {donch_hi10:,.0f} (10-day high)",
        note="Positional trend-rider (holds overnight). Thin edge — the validated version is 1-HOUR "
             "Donchian; this daily channel is the proxy the daily feed can show.",
        stats="GO (thin) · PF 1.16 · +3,718 pt · ~72/yr (1h)",
    ))

    # ---- N: MomentumCarryBook (swing long) — the validated 4-leg carry book -----
    book_legs = []
    if leg_reclaim: book_legs.append("20-DMA reclaim ⭐")
    if leg_thrust:  book_legs.append("wide-range thrust")
    if leg_gapup:   book_legs.append("gap-up hold")
    if leg_3up:     book_legs.append("3 up-days")
    if uptrend20 and book_legs:
        sigs.append(Signal(
            key="N", name="MomentumCarryBook", side="LONG", horizon="swing", status="ARMED",
            headline=f"Carry-momentum fired ({', '.join(book_legs)}) → BUY & carry (prior-day-low trail)",
            trigger=f"close>20DMA ✔ · leg(s): {', '.join(book_legs)}",
            entry="BUY the signal-day close and carry (in the morning, enter at the open as the practical proxy)",
            stop=f"initial = today's low {low:,.0f}",
            target="none — trail: exit on the first daily CLOSE below the prior day's low (40-day cap, ~5-day hold)",
            note="The 4 legs are correlated (all bullish continuation) — run as ONE book, one position at a time. "
                 "20-DMA reclaim (leg R / strategy 39) is the champion leg.",
            stats="GO · PF 1.81 · +12,732 pt · ~20/yr · NIFTY+SENSEX",
        ))
    else:
        why = ("close ≤ 20-DMA (uptrend gate off)" if not uptrend20
               else "no carry leg fired (no reclaim / thrust / gap-up-hold / 3-up-days)")
        sigs.append(Signal(
            key="N", name="MomentumCarryBook", side="LONG", horizon="swing", status="IDLE",
            headline="No carry-momentum entry today → book flat",
            trigger=why,
            stats="GO · PF 1.81 · +12,732 pt · ~20/yr · NIFTY+SENSEX",
        ))

    # ---- E: EMA5BreakdownShort (swing short) — fresh 5-EMA loss in a downtrend ---
    if below50 and fresh_ema5:
        sigs.append(Signal(
            key="E", name="EMA5BreakdownShort", side="SHORT", horizon="swing", status="ARMED",
            headline="Fresh 5-EMA breakdown below the 50-DMA → SHORT tomorrow's open (2-day hold)",
            trigger="clean above → two consecutive closes below the 5-EMA ✔ and close<50DMA ✔ (confirmed downtrend)",
            entry="SHORT at tomorrow's open",
            stop=f"open + 1.5 × ATR14 (informational; ATR14={atr:,.0f}) — headline is a fixed 2-day time-exit",
            target=f"cover at the close of day t+{EMA5_HOLD} (fixed {EMA5_HOLD}-day hold)",
            note="Continuation-down short (fades the DOWN leg). Distinct from BearRallyFade (fades an up-pop). "
                 "Thin correction-harvester — size small.",
            stats="MARGINAL-GO · ~7.6/yr · net ~+200/yr",
        ))
    else:
        why = []
        if not below50:
            why.append(f"close {close:,.0f} ≥ 50DMA (no downtrend)" if not np.isnan(t["sma50"]) else "50DMA n/a")
        if not fresh_ema5:
            why.append("no fresh 5-EMA breakdown (needs a clean above → 2 closes below)")
        sigs.append(Signal(
            key="E", name="EMA5BreakdownShort", side="SHORT", horizon="swing", status="IDLE",
            headline="No fresh 5-EMA breakdown in a downtrend → idle",
            trigger="; ".join(why) if why else "conditions not met",
            stats="MARGINAL-GO · ~7.6/yr · net ~+200/yr",
        ))

    # ---- K: Breakdown20DMAShort (swing short) — fresh 20-DMA loss in a downtrend -
    if below50 and fresh_bd20:
        sigs.append(Signal(
            key="K", name="Breakdown20DMAShort", side="SHORT", horizon="swing", status="ARMED",
            headline="Fresh 20-DMA breakdown below the 50-DMA → SHORT & carry (cover on a close above the prior high)",
            trigger="close crossed BELOW the 20-DMA (prev close ≥ prev 20-DMA) ✔ and close<50DMA ✔",
            entry="SHORT the signal-day close (in the morning, enter at the open as the practical proxy)",
            stop=f"initial = today's high {high:,.0f}",
            target="cover on the first daily CLOSE above the prior day's high (40-day cap)",
            note="The one short mirror of the momentum-carry legs that survives. Lumpy — top-3 trades ≈ 89% of "
                 "net; a small bear-side satellite, not standalone income.",
            stats="MARGINAL-GO · PF 1.92 · +5,037 pt · ~5/yr",
        ))
    else:
        why = []
        if not below50:
            why.append(f"close {close:,.0f} ≥ 50DMA (no downtrend)" if not np.isnan(t["sma50"]) else "50DMA n/a")
        if not fresh_bd20:
            why.append("no fresh 20-DMA breakdown")
        sigs.append(Signal(
            key="K", name="Breakdown20DMAShort", side="SHORT", horizon="swing", status="IDLE",
            headline="No fresh 20-DMA breakdown in a downtrend → idle",
            trigger="; ".join(why) if why else "conditions not met",
            stats="MARGINAL-GO · PF 1.92 · +5,037 pt · ~5/yr",
        ))

    # ---- S: BearShootingStar (swing short, rare satellite) ---------------------
    if stretch15 >= SHOOT_STRETCH_PT and rsi2_max3 > SHOOT_RSI2 and is_star:
        sigs.append(Signal(
            key="S", name="BearShootingStar", side="SHORT", horizon="swing", status="ARMED",
            headline=f"Exhausted top (shooting star, +{stretch15:,.0f}pt/15d, RSI2>90) → sell-stop the star's low {low:,.0f}",
            trigger=f"stretch +{stretch15:,.0f}pt ≥ {SHOOT_STRETCH_PT} ✔ · RSI2(3d max) {rsi2_max3:.0f} > {SHOOT_RSI2} ✔ · shooting star ✔",
            level=low,
            entry=f"SHORT on a break below the star's low {low:,.0f} (sell-stop, valid 2 sessions; else cancel)",
            stop=f"entry + 2.0 × ATR14 (wide; ATR14={atr:,.0f} → ≈ {close + 2*atr:,.0f})",
            target=f"entry − 3.0 × ATR14 (big swing-down objective ≈ {close - 3*atr:,.0f}), else 10-day time-exit",
            note="Rare exhaustion short (~1.7/yr, n=12) — the shooting star is the essential filter. Size small.",
            stats="MARGINAL · PF 1.82 · 58% win · ~1.7/yr",
        ))
    else:
        why = []
        if stretch15 < SHOOT_STRETCH_PT:
            why.append(f"stretch +{stretch15:,.0f}pt < {SHOOT_STRETCH_PT} (no extended rally)")
        if not (rsi2_max3 > SHOOT_RSI2):
            why.append(f"RSI2(3d max) {rsi2_max3:.0f} ≤ {SHOOT_RSI2}")
        if not is_star:
            why.append("no shooting-star candle")
        sigs.append(Signal(
            key="S", name="BearShootingStar", side="SHORT", horizon="swing", status="IDLE",
            headline="No exhausted-top shooting star → idle",
            trigger="; ".join(why) if why else "conditions not met",
            stats="MARGINAL · PF 1.82 · 58% win · ~1.7/yr",
        ))

    # ---- O: OverboughtReversalFade (swing short) -------------------------------
    or_candle = is_star or bear_engulf
    if rsi14 > ORFADE_RSI14 and or_candle:
        which = "shooting star" if is_star else "bearish engulfing"
        sigs.append(Signal(
            key="O", name="OverboughtReversalFade", side="SHORT", horizon="swing", status="ARMED",
            headline=f"Bearish reversal candle ({which}) at RSI14 {rsi14:.0f}>70 → SHORT tomorrow's open",
            trigger=f"RSI14 {rsi14:.1f} > {ORFADE_RSI14} ✔ · rejection candle ({which}) ✔",
            entry="SHORT at tomorrow's open (a break-of-low entry destroys the edge — NIFTY V-bounces there)",
            stop=f"open + 2.0 × ATR14 (ATR14={atr:,.0f} → ≈ {close + 2*atr:,.0f})",
            target=f"entry − 2.0 × ATR14 (≈ {close - 2*atr:,.0f}), else 7-day time-exit",
            note="⚠️ ~96% of this edge came from 2024 and it's candle-fragile — superseded by BearRallyFade (D). "
                 "Treat as informational, not core.",
            stats="MARGINAL · PF 2.03 · 64% win · ~2.5/yr · 2024-concentrated",
        ))
    else:
        why = []
        if not (rsi14 > ORFADE_RSI14):
            why.append(f"RSI14 {rsi14:.1f} ≤ {ORFADE_RSI14} (not overbought)")
        if not or_candle:
            why.append("no bearish reversal candle")
        sigs.append(Signal(
            key="O", name="OverboughtReversalFade", side="SHORT", horizon="swing", status="IDLE",
            headline="No overbought bearish-reversal candle → idle",
            trigger="; ".join(why) if why else "conditions not met",
            stats="MARGINAL · PF 2.03 · 64% win · ~2.5/yr · 2024-concentrated",
        ))

    # ---- resolve against today's real open, if the morning run captured it -----
    anchor_date = pd.Timestamp(t.name).strftime("%Y-%m-%d")
    open_info = None
    if today_open:
        try:
            if str(today_open.get("anchor_date")) == anchor_date and today_open.get("open") is not None:
                open_px = float(today_open["open"])
                _resolve_against_open(sigs, open_px, close, atr, low,
                                      b_level, f_level, green_maru, uptrend20,
                                      extra={"hi3": hi3, "rvol_ok": rvol_ok})
                open_info = {
                    "date": today_open.get("date"),
                    "open": open_px,
                    "gap_pct": (open_px - close) / close * 100 if close else 0.0,
                    "fetched_utc": today_open.get("fetched_utc"),
                }
        except Exception:  # noqa: BLE001 — a bad open file must never break the plan
            open_info = None

    # ---- G: CarryFwdMomentum — which side to CARRY overnight (long vs short) ----
    # Validated 32_CarryFwdMomentum is LONG-only: a momentum burst IN AN UPTREND
    # continues over the next days; carrying SHORT has no edge on NIFTY (momentum
    # persists up, shorts squeeze). The exact burst = 4 green hourly candles (needs
    # intraday bars); here we gate on the daily uptrend + a daily strong-up-day proxy.
    carry_range = (high - low) / close * 100 if close else np.nan
    carry_burst = bool(ret > 0 and carry_range >= CARRY_SPAN and uwpct <= CARRY_CLOSE_WICK)
    if uptrend20:
        # In an uptrend the winning carry side is LONG (momentum persists on NIFTY;
        # short-carry has no edge). A fresh momentum burst = the validated entry;
        # otherwise it's the standing long-carry bias.
        if carry_burst:
            entry = ("carry/BUY the close and HOLD — fresh momentum burst is the validated entry; "
                     "confirm the intraday 4-green-hourly run to add size")
            note = ("Fresh momentum burst today. Momentum PERSISTS on NIFTY → carry LONG. Carrying a "
                    "SHORT forward has NO validated edge (shorts squeeze). Overnight/gap risk, ~6-day hold.")
            burst_txt = (f"fresh burst ✔ (range {carry_range:.2f}% ≥ {CARRY_SPAN:.2f}%, "
                         f"closed strong, wick {uwpct:.2f}%)")
        else:
            entry = ("HOLD/accumulate longs while close > 20-DMA; add on a fresh 4-green-hourly "
                     "momentum burst (the validated add trigger)")
            note = ("Standing bias — no fresh burst today, but LONG is the only side with a next-day "
                    "carry edge on NIFTY (short-carry has none). NB: this is the 20-DMA momentum gate; "
                    "the top regime banner uses the structural 200-DMA.")
            burst_txt = f"no fresh burst (day's range {carry_range:.2f}% < {CARRY_SPAN:.2f}%)"
        sigs.append(Signal(
            key="G", name="CarryFwdMomentum", side="LONG", horizon="swing", status="ARMED",
            headline="CARRY LONG — long has the higher next-day win prob on NIFTY (short-carry has no edge)",
            trigger=f"close>20DMA ✔ (uptrend gate on) · {burst_txt}",
            entry=entry,
            stop=f"prior-day-low trail (close-based); initial ≈ today's low {low:,.0f}",
            target="none — let the drift run; flip flat on a daily CLOSE below the prior day's low / 20-DMA",
            note=note,
            stats="GO · PF 3.30 · 61% win · +2,068 pt · ~6/yr · cross-index",
        ))
    else:
        sigs.append(Signal(
            key="G", name="CarryFwdMomentum", side="LONG", horizon="swing", status="IDLE",
            headline="Below the 20-DMA → carry-momentum OFF (long-only; short-carry has no edge either)",
            trigger=f"needs close>20DMA; close {close:,.0f} ≤ 20DMA {t['sma20']:,.0f}",
            stats="GO · PF 3.30 · 61% win · +2,068 pt · ~6/yr · cross-index",
        ))

    regime = _regime(close, t["sma20"], t["sma50"], t["sma200"], ret)

    context = {
        "date": anchor_date,
        "today_open": open_info,
        "regime": regime,
        "close": close,
        "ret": ret,
        "atr14": atr,
        "sma20": None if np.isnan(t["sma20"]) else float(t["sma20"]),
        "sma50": None if np.isnan(t["sma50"]) else float(t["sma50"]),
        "sma200": None if np.isnan(t["sma200"]) else float(t["sma200"]),
        "rsi2": rsi2,
        "uptrend20": uptrend20,
        "below50": below50,
        "above200": above200,
        "squeeze": squeeze,
        "gap_up_levels": {
            "gapfade_0.30pct": b_level,
            "gaphalf_0.35pct": f_level,
        },
    }
    return {"context": context, "signals": [s.as_dict() for s in sigs]}


def _fmt_cli(plan: dict) -> str:
    c = plan["context"]
    out = []
    out.append("=" * 78)
    out.append(f"NIFTY TELLER — morning plan for the session AFTER {c['date']}")
    out.append("=" * 78)
    if c.get("regime"):
        r = c["regime"]
        out.append(f"{r['icon']} REGIME: {r['label']}  (favouring {r['lean']}) — {r['favor']}")
        out.append(f"   {r['rationale']}")
    out.append(f"anchor close {c['close']:,.1f} | today o→c {c['ret']:+.2f}% | ATR14 {c['atr14']:,.0f} "
               f"| RSI2 {c['rsi2']:.1f} | {'>' if c['uptrend20'] else '≤'}20DMA "
               f"| {'>' if c['above200'] else '≤'}200DMA | squeeze {'ON' if c['squeeze'] else 'off'}")
    if c.get("today_open"):
        o = c["today_open"]
        out.append(f"TODAY'S OPEN {o['open']:,.1f}  (gap {o['gap_pct']:+.2f}% vs anchor) "
                   f"— plan RESOLVED against the real open")
    order = {"ACTIVATED": 0, "ARMED": 1, "CONDITIONAL": 2, "PASSED": 3, "IDLE": 4}
    tags = {"ACTIVATED": "🎉 ACTIVE", "ARMED": "🟢 ARMED", "CONDITIONAL": "🟠 IF-GAP",
            "PASSED": "⚫ PASSED", "IDLE": "⚪ idle"}
    for s in sorted(plan["signals"], key=lambda x: order[x["status"]]):
        tag = tags[s["status"]]
        out.append("")
        out.append(f"[{s['key']}] {s['name']:16s} {s['side']:5s} {tag:11s} · {s['stats']}")
        out.append(f"     {s['headline']}")
        if s["status"] in ("ACTIVATED", "ARMED", "CONDITIONAL"):
            if s["entry"]:  out.append(f"     entry : {s['entry']}")
            if s["stop"]:   out.append(f"     stop  : {s['stop']}")
            if s["target"]: out.append(f"     target: {s['target']}")
            if s["note"]:   out.append(f"     note  : {s['note']}")
        else:  # PASSED / IDLE
            out.append(f"     ({s['trigger']})")
    return "\n".join(out)


if __name__ == "__main__":
    plan = build_plan(load_daily(), load_today_open())
    print(_fmt_cli(plan))
