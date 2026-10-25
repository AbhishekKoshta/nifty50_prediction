"""strategy_book.core — the plug-in contract for the 🎯 Strategy Book tab.

A validated strategy is ONE file in `strategy_book/strategies/` that defines a
module-level `STRATEGY = Strategy(...)`. It is discovered automatically — no edit
to streamlit_app.py or to this package is needed to add one. Files starting with
"_" (e.g. `_template.py`) are skipped.

    Strategy  = static card: verdict, rules, backtest stats, caveats, trade log
    signal()  = optional fn(ctx) -> TodaySignal: what to do at the NEXT session
    Context   = everything a signal may read (daily OHLC, today's open, last hour)

Scaffold a new one with:  python -m strategy_book.new_strategy --help
"""
from __future__ import annotations

import importlib
import json
import math
import os
import pkgutil
import traceback
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
TRADES_DIR = os.path.join(HERE, "trades")
LAST_HOUR_FILE = os.path.join(APP_DIR, "last_hour.json")

LOT = 65                 # current NIFTY lot
STRIKE_STEP = 50
VERDICT_ORDER = {"GO": 0, "MARGINAL-GO": 1, "MARGINAL": 2, "PAPER": 3}

# status values a signal may return (drives the colour + sort in the tab)
ACTIVE, ARMED, CONDITIONAL, IDLE, NEEDS_DATA = "ACTIVE", "ARMED", "CONDITIONAL", "NO TRADE", "NEEDS DATA"


@dataclass
class Level:
    label: str
    value: float | None
    note: str = ""


@dataclass
class TodaySignal:
    status: str                      # ACTIVE / ARMED / CONDITIONAL / NO TRADE / NEEDS DATA
    headline: str                    # one line: what to do
    side: str = ""                   # LONG / SHORT / BOTH / ""
    levels: list[Level] = field(default_factory=list)
    instrument: str = ""             # e.g. "CE · next weekly 2026-11-03 · 1-ITM"
    steps: list[str] = field(default_factory=list)   # execution checklist for today
    why: str = ""                    # the inputs that produced the call
    recent_trades: object = None     # optional DataFrame of the latest closed trades (newest first)


@dataclass
class Strategy:
    id: str                          # stable key, e.g. "62_S1_GOLD0915"
    name: str
    source: str                      # research folder the verdict lives in
    verdict: str                     # GO / MARGINAL-GO / MARGINAL / PAPER
    horizon: str                     # intraday / swing (≤N sessions)
    instrument: str                  # what you actually buy/sell
    summary: str                     # 1-2 lines
    rules: list[str]
    stats: dict[str, str]            # headline backtest numbers, already net of costs
    caveats: list[str] = field(default_factory=list)
    trade_log: str | None = None     # filename in strategy_book/trades/ (normalised CSV)
    signal: Callable[["Context"], TodaySignal] | None = None
    order: int = 100                 # display order within the same verdict


# ----------------------------------------------------------------------------- context
@dataclass
class Context:
    daily: pd.DataFrame              # date-indexed open/high/low/close (completed sessions only)
    today_open: float | None         # the NEXT session's open, once the 09:10 run captured it
    last_hour: dict | None           # {"session": "YYYY-MM-DD", "open","high","low","close","bars"} of the LAST daily row
    manual: dict = field(default_factory=dict)   # user overrides typed into the tab

    @property
    def last(self) -> pd.Series:
        return self.daily.iloc[-1]

    @property
    def last_date(self) -> date:
        return self.daily.index[-1].date()

    @property
    def next_session(self) -> date:
        return next_weekday(self.last_date)

    @property
    def open_px(self) -> float | None:
        return self.manual.get("open") or self.today_open


def build_context(daily: pd.DataFrame, today_open: dict | None, manual: dict | None = None) -> Context:
    d = daily[["open", "high", "low", "close"]].dropna().sort_index()
    last_d = d.index[-1].strftime("%Y-%m-%d")
    op = None
    if today_open and str(today_open.get("anchor_date")) == last_d:
        op = float(today_open["open"])
    return Context(daily=d, today_open=op, last_hour=load_last_hour(last_d), manual=manual or {})


def load_last_hour(expect_session: str) -> dict | None:
    """last_hour.json (written by update_last_hour.py in CI) — only if it is for `expect_session`."""
    try:
        with open(LAST_HOUR_FILE) as f:
            lh = json.load(f)
        if lh.get("session") != expect_session or lh.get("bars", 0) < 10:
            return None
        return lh
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------------------- helpers
def next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def level_beyond(px: float, s: int, grid: int = 100) -> int:
    """Nearest round level strictly beyond px in direction s (+1 up / -1 down)."""
    return (math.floor(px / grid) + 1) * grid if s == 1 else (math.ceil(px / grid) - 1) * grid


def itm_strike(spot: float, kind: str, n: int = 1) -> int:
    atm = round(spot / STRIKE_STEP) * STRIKE_STEP
    return int(atm - n * STRIKE_STEP if kind == "CE" else atm + n * STRIKE_STEP)


def weekly_expiries(after: date, n: int = 3) -> list[date]:
    """NIFTY weekly expiries strictly after `after`: Thursday until 2025-08-31, Tuesday from
    2025-09-01. Holidays are NOT known here — on a holiday the expiry is the previous session."""
    out, d = [], after
    while len(out) < n:
        d += timedelta(days=1)
        wd = 3 if d < date(2025, 9, 1) else 1
        if d.weekday() == wd:
            out.append(d)
    return out


def next_weekly(session: date) -> date:
    """The weekly AFTER the front one as seen on `session` (front = first expiry ≥ session).
    This is the playbook's 'NEXT weekly — never the 0-DTE contract'."""
    front_and_next = weekly_expiries(session - timedelta(days=1), 2)
    return front_and_next[1]


def fmt(x: float | None, nd: int = 0) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:,.{nd}f}"


# ----------------------------------------------------------------------------- trade logs
TRADE_COLS = ["date", "side", "entry_ts", "exit_ts", "exit_reason", "R", "net_rs"]


def load_trades(fname: str | None) -> pd.DataFrame | None:
    if not fname:
        return None
    p = os.path.join(TRADES_DIR, fname)
    if not os.path.exists(p):
        return None
    t = pd.read_csv(p)
    t["date"] = pd.to_datetime(t["date"])
    return t.sort_values("date").reset_index(drop=True)


def trade_stats(t: pd.DataFrame) -> dict:
    """Recomputed from the bundled log (net ₹ per lot) — the card shows these next to the verdict."""
    n = len(t)
    wins, losses = t.loc[t.net_rs > 0, "net_rs"], t.loc[t.net_rs <= 0, "net_rs"]
    eq = t.net_rs.cumsum()
    dd = float((eq - eq.cummax()).min()) if n else 0.0
    mid = t.date.iloc[n // 2] if n else None
    h1, h2 = (t.loc[t.date < mid, "net_rs"].sum(), t.loc[t.date >= mid, "net_rs"].sum()) if n else (0, 0)
    streak = cur = 0
    for v in t.net_rs:
        cur = cur + 1 if v <= 0 else 0
        streak = max(streak, cur)
    yrs = max((t.date.iloc[-1] - t.date.iloc[0]).days / 365.25, 1e-9) if n else 1
    return dict(
        trades=n, per_year=n / yrs, win=len(wins) / n if n else 0,
        pf=float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else float("inf"),
        net=float(t.net_rs.sum()), dd=dd, h1=float(h1), h2=float(h2), max_loss_streak=streak,
        big=int((t.get("R", pd.Series(dtype=float)) >= 10).sum()),
        by_year=t.groupby(t.date.dt.year).net_rs.sum().round(0).to_dict(),
    )


# ----------------------------------------------------------------------------- registry
def discover() -> tuple[list[Strategy], list[str]]:
    """Import every strategy_book/strategies/*.py that defines STRATEGY. A broken file
    is reported, never fatal — one bad strategy can't take the tab down."""
    from . import strategies as pkg
    found, errors = [], []
    for m in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if m.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{pkg.__name__}.{m.name}")
            s = getattr(mod, "STRATEGY", None)
            if isinstance(s, Strategy):
                found.append(s)
            else:
                errors.append(f"{m.name}: no STRATEGY = Strategy(...) defined")
        except Exception:  # noqa: BLE001
            errors.append(f"{m.name}: {traceback.format_exc(limit=2)}")
    found.sort(key=lambda s: (VERDICT_ORDER.get(s.verdict, 9), s.order, s.id))
    return found, errors


def run_signal(s: Strategy, ctx: Context) -> TodaySignal | None:
    if s.signal is None:
        return None
    try:
        return s.signal(ctx)
    except Exception as e:  # noqa: BLE001
        return TodaySignal(NEEDS_DATA, f"signal error: {e}")
