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
  Swing (daily):
    R RSI2 mean-rev  (long)  — armed at close when close>200DMA and RSI(2)<10
    D BearRallyFade  (short) — armed at close when up-day>+0.6% AND close<50DMA (MARGINAL-GO)

Most are validated GO edges. Two are MARGINAL satellites — BearRallyFade (the best
NIFTY short, profits in real bears) and MarubozuGapReclaim (rare, defined-risk) —
labelled as such so they're taken small, not treated as core.

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
                          green_maru: bool, uptrend20: bool) -> None:
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

    # ---- resolve against today's real open, if the morning run captured it -----
    anchor_date = pd.Timestamp(t.name).strftime("%Y-%m-%d")
    open_info = None
    if today_open:
        try:
            if str(today_open.get("anchor_date")) == anchor_date and today_open.get("open") is not None:
                open_px = float(today_open["open"])
                _resolve_against_open(sigs, open_px, close, atr, low,
                                      b_level, f_level, green_maru, uptrend20)
                open_info = {
                    "date": today_open.get("date"),
                    "open": open_px,
                    "gap_pct": (open_px - close) / close * 100 if close else 0.0,
                    "fetched_utc": today_open.get("fetched_utc"),
                }
        except Exception:  # noqa: BLE001 — a bad open file must never break the plan
            open_info = None

    context = {
        "date": anchor_date,
        "today_open": open_info,
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
    out.append(f"anchor close {c['close']:,.1f} | today o→c {c['ret']:+.2f}% | ATR14 {c['atr14']:,.0f} "
               f"| RSI2 {c['rsi2']:.1f} | {'>' if c['uptrend20'] else '≤'}20DMA "
               f"| {'>' if c['above200'] else '≤'}200DMA | squeeze {'ON' if c['squeeze'] else 'off'}")
    if c.get("today_open"):
        o = c["today_open"]
        out.append(f"TODAY'S OPEN {o['open']:,.1f}  (gap {o['gap_pct']:+.2f}% vs anchor) "
                   f"— plan RESOLVED against the real open")
    order = {"ACTIVATED": 0, "ARMED": 1, "CONDITIONAL": 2, "PASSED": 3, "IDLE": 4}
    tags = {"ACTIVATED": "🎉 ACTIVE", "ARMED": "🟢 ARMED", "CONDITIONAL": "🟡 IF-GAP",
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
