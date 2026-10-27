"""pattern_twins.py — renders the 🔁 Pattern Twins tab.

Pick any day; find the past days (2022 onward, so 1-min charts exist) whose DAILY and WEEKLY candle + trend look
like it, and chart each match day + the session after it on 1m / 5m / 15m / 1h / 1d.
Research copy: `~/Trading/Observe_Pattern/NIFTY_PatternTwins/twins.py` (+ `latest_match_chart.py`) — keep the rules identical.

Similarity
  * Candle gate (hard): on the anchor bar, body %, upper-wick % and lower-wick % of the range each within ±tol of the
    anchor's, same colour, on the daily AND the weekly bar. If fewer than N days pass, tol widens in 5-pt steps.
  * Ranking: weighted RMS of robust-z differences — candle (anchor + 2 prior bars: body/wicks, range/ATR, return/ATR,
    gap/ATR) and trend (close vs EMA fast/slow + SMA long in ATRs, EMA slopes, RSI14, position in 20-bar range,
    distance from 52-bar high, 5/20-bar ROC). Daily 20/50/200, weekly 10/20/40.
  * Midweek anchor: weekly bar = week-to-date, compared with candidate weeks cut at the same trading-day number.
Self-contained: pulls the free Upstox v3 feed itself (NIFTY daily since 2015; 1-min only for the charted days).
"""
from __future__ import annotations

import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import streamlit as st

IST = timezone(timedelta(hours=5, minutes=30))
NIFTY = urllib.parse.quote("NSE_INDEX|Nifty 50", safe="")
INTRADAY_FROM = pd.Timestamp("2022-01-03")     # Upstox minute data starts 2022-01
TREND_P = {"day": dict(f=20, s=50, l=200, hi=250), "week": dict(f=10, s=20, l=40, hi=52)}
LAG_W = [1.0, 0.5, 0.25]
CANDLE_F = ["body", "uw", "lw", "rng_atr", "ret_atr", "gap_atr"]
TREND_F = ["d_fast", "d_slow", "d_long", "slope_fast", "slope_slow", "rsi", "pos20", "dd_hi", "roc5", "roc20"]
UP, DN = "#26a69a", "#ef5350"
MATCH_C, NEXT_C = "#3d5a80", "#e07a1f"
RANGE_BAND, LEVEL, INK = "#f8f9fa", "#9c7a3c", "#2b2d42"


# ----------------------------------------------------------------------------- data
def _get(url: str) -> list:
    import requests
    for i in range(3):
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=25)
        if r.ok:
            return (r.json().get("data") or {}).get("candles") or []
        time.sleep(1 + i)
    r.raise_for_status()
    return []


def _frame(c: list) -> pd.DataFrame:
    if not c:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    d = pd.DataFrame(c, columns=["t", "open", "high", "low", "close", "v", "oi"])
    d["t"] = pd.to_datetime(d.t).dt.tz_localize(None)
    return d.drop_duplicates("t").set_index("t").sort_index()[["open", "high", "low", "close"]].astype(float)


@st.cache_data(ttl=3 * 3600, show_spinner=False)
def load_daily(today: date) -> pd.DataFrame:
    parts, hi = [], today
    while hi > date(2015, 1, 1):
        lo = max(date(2015, 1, 1), hi - timedelta(days=3 * 365))
        parts.append(_frame(_get(f"https://api.upstox.com/v3/historical-candle/{NIFTY}/days/1/{hi}/{lo}")))
        hi = lo - timedelta(days=1)
    d = pd.concat(parts)
    d.index = d.index.normalize()
    d = d[~d.index.duplicated(keep="last")].sort_index()
    return d[(d.index.dayofweek < 5) & ((d.high - d.low) > 0)]


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_1m(d0: date, d1: date) -> pd.DataFrame:
    m = _frame(_get(f"https://api.upstox.com/v3/historical-candle/{NIFTY}/minutes/1/{d1}/{d0}"))
    if d1 >= datetime.now(IST).date():
        try:
            m = pd.concat([m, _frame(_get(f"https://api.upstox.com/v3/historical-candle/intraday/{NIFTY}/minutes/1"))])
        except Exception:  # noqa: BLE001
            pass
    m = m[~m.index.duplicated(keep="last")].sort_index()
    return m[(m.index.strftime("%H:%M") >= "09:15") & (m.index.strftime("%H:%M") <= "15:29")]


# ----------------------------------------------------------------------------- features (= 75 twins.py)
def _atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def features(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    p = TREND_P[tf]
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    a = _atr(df); ap = a.shift(1)
    rng = (h - l).replace(0, np.nan)
    base = pd.DataFrame({"body": (c - o) / rng, "uw": (h - np.maximum(o, c)) / rng, "lw": (np.minimum(o, c) - l) / rng,
                         "rng_atr": (h - l) / ap, "ret_atr": (c - c.shift(1)) / ap, "gap_atr": (o - c.shift(1)) / ap})
    f = pd.DataFrame(index=df.index)
    for k in range(len(LAG_W)):
        for col in CANDLE_F:
            f[f"{col}_{k}"] = base[col].shift(k)
    ef, es = c.ewm(span=p["f"], adjust=False).mean(), c.ewm(span=p["s"], adjust=False).mean()
    sl = c.rolling(p["l"]).mean()
    f["d_fast"], f["d_slow"], f["d_long"] = (c - ef) / a, (c - es) / a, (c - sl) / a
    f["slope_fast"], f["slope_slow"] = (ef - ef.shift(5)) / a, (es - es.shift(10)) / a
    f["rsi"] = _rsi(c)
    lo20, hi20 = l.rolling(20).min(), h.rolling(20).max()
    f["pos20"] = (c - lo20) / (hi20 - lo20)
    f["dd_hi"] = (c - h.rolling(p["hi"]).max()) / a
    f["roc5"], f["roc20"] = (c - c.shift(5)) / a, (c - c.shift(20)) / a
    return f


def weekly(d: pd.DataFrame) -> pd.DataFrame:
    g = d.groupby(d.index.to_period("W-FRI"))
    w = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
    w.index = g.apply(lambda x: x.index.max())
    return w


def weekly_features_at(d: pd.DataFrame, n: int, since: pd.Timestamp) -> pd.DataFrame:
    """Week-to-date features: every week (since `since`) cut at its n-th trading day; history = completed weeks."""
    w = weekly(d)
    rows = {}
    for _, x in d[d.index >= since - timedelta(days=7)].groupby(d[d.index >= since - timedelta(days=7)].index.to_period("W-FRI")):
        if len(x) < n:
            continue
        x = x.iloc[:n]
        bar = pd.DataFrame({"open": [x["open"].iloc[0]], "high": [x["high"].max()], "low": [x["low"].min()],
                            "close": [x["close"].iloc[-1]]}, index=[x.index[-1]])
        hist = w[w.index < x.index[0]].iloc[-300:]
        if len(hist) >= 60:
            rows[x.index[-1]] = features(pd.concat([hist, bar]), "week").iloc[-1]
    return pd.DataFrame(rows).T


def _dist(f: pd.DataFrame, anchor: pd.Series):
    cols_c = [f"{c}_{k}" for k in range(len(LAG_W)) for c in CANDLE_F]
    w_c = np.array([LAG_W[k] for k in range(len(LAG_W)) for _ in CANDLE_F])

    def rms(cols, w):
        x = f[cols].astype(float)
        spread = 1.4826 * (x - x.median()).abs().median()
        spread = spread.where(spread > 1e-9, x.std())
        z = (x - anchor[cols].astype(float)) / spread
        return np.sqrt((z.pow(2) * w).sum(axis=1) / w.sum())
    return np.sqrt(0.5 * rms(cols_c, w_c) ** 2 + 0.5 * rms(TREND_F, np.ones(len(TREND_F))) ** 2)


def _gate(f: pd.DataFrame, a: pd.Series, tol: float) -> pd.Series:
    b, ba = f["body_0"], a["body_0"]
    colour = (b.abs() < 0.35) if abs(ba) < 0.15 else (np.sign(b) == np.sign(ba))
    return colour & ((b - ba).abs() <= tol) & ((f["uw_0"] - a["uw_0"]).abs() <= tol) & ((f["lw_0"] - a["lw_0"]).abs() <= tol)


def _dedupe(score: pd.Series, gap_days: int) -> pd.Series:
    keep = []
    for t, _ in score.sort_values(ascending=False).items():
        if all(abs((t - k).days) > gap_days for k in keep):
            keep.append(t)
    return score.loc[keep]


@st.cache_data(ttl=3 * 3600, show_spinner=False)
def find_twins(d: pd.DataFrame, anchor: pd.Timestamp, mode: str, n: int, tol0: float):
    """Returns (matches DataFrame latest-first, tolerance used, anchor info dict)."""
    fd = features(d, "day")
    per = d.index.to_period("W-FRI")
    i = d.index.get_loc(anchor)
    day_no = int((per[:i + 1] == per[i]).sum())
    later_same_week = i + 1 < len(d) and per[i + 1] == per[i]
    week_done = (not later_same_week) and (i + 1 < len(d) or anchor.dayofweek == 4)
    if week_done:
        fw = features(weekly(d), "week")
    else:
        fw = weekly_features_at(d, day_no, INTRADAY_FROM)
        fw = pd.concat([fw, weekly_features_at(d[d.index <= anchor], day_no, anchor - timedelta(days=6))]).groupby(level=0).last()
    # candidates: 2022+ (1-min exists), at least 30 days before the anchor, and with a next session
    last_ok = d.index[-2] if len(d) > 1 else d.index[-1]
    cand_d = fd[(fd.index >= INTRADAY_FROM) & (fd.index < anchor - timedelta(days=30)) & (fd.index <= last_ok)].dropna()
    dist_d = _dist(cand_d, fd.loc[anchor])
    if mode == "Day only":
        dist, gap = dist_d, 7
        gate_fn = lambda tol: _gate(cand_d, fd.loc[anchor], tol)
    else:
        cand_w = fw[fw.index.isin(cand_d.index)].dropna()
        dist_w = _dist(cand_w, fw.loc[anchor])
        gap = 21 if mode == "Day + week" else 21
        if mode == "Week only":
            dist = dist_w
            gate_fn = lambda tol: _gate(cand_w, fw.loc[anchor], tol)
        else:
            common = dist_w.index.intersection(dist_d.index)
            dist = np.sqrt(0.5 * dist_d[common] ** 2 + 0.5 * dist_w[common] ** 2)
            gate_fn = lambda tol: _gate(cand_d.loc[common], fd.loc[anchor], tol) & _gate(cand_w.loc[common], fw.loc[anchor], tol)
    score = (100 * (1 - dist.rank(pct=True)) + 100 / len(dist)).clip(upper=100)
    tol, best = tol0, pd.Series(dtype=float)
    while tol <= 0.40 + 1e-9:
        g = gate_fn(tol).reindex(score.index).fillna(False)
        best = _dedupe(score[g], gap)
        if len(best) >= n:
            break
        tol += 0.05
    best = best.iloc[:n]
    rows = []
    for t, s in best.items():
        j = d.index.get_loc(t)
        nx = d.index[j + 1]
        r = {"match day": t.date(), "next day": nx.date(), "score": round(s, 1),
             "D body/uw/lw %": f"{100 * fd.at[t, 'body_0']:+.0f} / {100 * fd.at[t, 'uw_0']:.0f} / {100 * fd.at[t, 'lw_0']:.0f}"}
        if mode != "Day only":
            r["W body/uw/lw %"] = f"{100 * fw.at[t, 'body_0']:+.0f} / {100 * fw.at[t, 'uw_0']:.0f} / {100 * fw.at[t, 'lw_0']:.0f}"
        pc, o, c = d.close.iloc[j], d.open.iloc[j + 1], d.close.iloc[j + 1]
        r["next open gap"] = round(o - pc)
        r["open vs match range"] = "inside" if d.low.iloc[j] <= o <= d.high.iloc[j] else ("above" if o > d.high.iloc[j] else "below")
        r["next close Δ pt"] = round(c - pc)
        r["next close Δ %"] = round(100 * (c / pc - 1), 2)
        rows.append(r)
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("match day", ascending=False).reset_index(drop=True)
    info = {"day": fd.loc[anchor], "week": fw.loc[anchor], "week_done": week_done, "day_no": day_no}
    return out, tol, info


# ----------------------------------------------------------------------------- chart (= 75 latest_match_chart.py)
def _rs(m, rule):
    return m.resample(rule, origin="start_day", offset="9h15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def _panel(ax, bars, title, hi, lo, days, fmt, step, next_open, band_labels=True):
    from matplotlib.patches import Rectangle
    n = len(bars)
    width = 0.7 if n > 300 else 0.62
    first = {}
    tr = ax.get_xaxis_transform()
    for k, dd in enumerate(days):
        idx = [i for i, t in enumerate(bars.index) if t.normalize() == dd]
        if not idx:
            continue
        first[dd] = idx[0]
        col = MATCH_C if k == 0 else NEXT_C
        ax.add_patch(Rectangle((idx[0] - 0.5, 1.0), len(idx), 0.045, transform=tr, color=col, clip_on=False, lw=0, zorder=5))
        if k == 1:
            ax.axvline(idx[0] - 0.5, color="#6c757d", lw=1.4, zorder=4)
        if band_labels:
            ax.text((idx[0] + idx[-1]) / 2, 1.0225, f"{dd:%a %d %b %Y}  ·  {'MATCH DAY' if k == 0 else 'NEXT DAY'}",
                    transform=tr, ha="center", va="center", fontsize=9.5, fontweight="bold", color="white", zorder=6,
                    clip_on=False)
    ax.fill_between([-1, n - 0.5], lo, hi, color=RANGE_BAND, zorder=1, lw=0)
    for i, r in enumerate(bars.itertuples()):
        c = UP if r.close >= r.open else DN
        ax.vlines(i, r.low, r.high, color=c, lw=0.6 if n > 300 else 1.1, zorder=2)
        ax.add_patch(Rectangle((i - width / 2, min(r.open, r.close)), width, max(abs(r.close - r.open), 1e-9),
                               color=c, lw=0, zorder=3))
    # levels only — every text annotation lives in the bold header line at the top of the figure (user rule 2026-10-28)
    for y in (hi, lo):
        ax.hlines(y, -1, n - 0.5, color=LEVEL, ls=(0, (5, 3)), lw=1.3, zorder=4)
    if days[1] in first:
        ax.plot(first[days[1]] - 0.6, next_open[0], marker=">", color=NEXT_C, ms=9, zorder=6)
    ticks = list(range(0, n, step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([bars.index[i].strftime(fmt) for i in ticks], fontsize=8, color="#555")
    ax.set_title(title, fontsize=12.5, fontweight="bold", loc="left", pad=24, color=INK)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color("#bbb")
    ax.set_xlim(-1, n + 0.5)
    lo_y, hi_y = min(bars["low"].min(), lo), max(bars["high"].max(), hi)
    pad = 0.05 * (hi_y - lo_y)
    ax.set_ylim(lo_y - pad, hi_y + pad)
    ax.tick_params(axis="y", labelsize=8, colors="#555")
    ax.tick_params(axis="x", length=0)
    ax.grid(axis="y", color="#e6e6e6", lw=0.7, zorder=0)
    ax.set_axisbelow(True)


def match_figure(d: pd.DataFrame, t: pd.Timestamp, anchor: pd.Timestamp):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    j = d.index.get_loc(t)
    nxt = d.index[j + 1]
    m = load_1m(t.date(), nxt.date())
    md, nd = m[m.index.normalize() == t], m[m.index.normalize() == nxt]
    if md.empty or nd.empty:
        return None
    hi, lo = md["high"].max(), md["low"].min()
    pc, c = d.close.iloc[j], d.close.iloc[j + 1]          # official NSE closes
    o = nd["open"].iloc[0]
    no = (o, o - pc)
    days = [t, nxt]
    fig = plt.figure(figsize=(22, 20), facecolor="white")
    gs = fig.add_gridspec(3, 2, height_ratios=[1.1, 1, 1], hspace=0.28, wspace=0.12)
    _panel(fig.add_subplot(gs[0, :]), m, "1 minute", hi, lo, days, "%H:%M", 30, no)
    _panel(fig.add_subplot(gs[1, 0]), _rs(m, "5min"), "5 minute", hi, lo, days, "%H:%M", 12, no)
    _panel(fig.add_subplot(gs[1, 1]), _rs(m, "15min"), "15 minute", hi, lo, days, "%H:%M", 4, no)
    _panel(fig.add_subplot(gs[2, 0]), _rs(m, "60min"), "1 hour", hi, lo, days, "%H:%M", 1, no)
    _panel(fig.add_subplot(gs[2, 1]), d.iloc[max(0, j - 24): j + 2], "1 day (last 25 sessions)", hi, lo, days,
           "%d %b", 4, no, band_labels=False)
    where = "INSIDE" if lo <= o <= hi else ("ABOVE" if o > hi else "BELOW")
    broke = [s for s, ok in [(f"high +{nd['high'].max() - hi:.0f}", nd["high"].max() > hi),
                             (f"low −{lo - nd['low'].min():.0f}", nd["low"].min() < lo)] if ok]
    fig.text(0.5, 0.975, f"NIFTY — twin of {anchor:%a %d %b %Y}:  {t:%a %d %b %Y} (match day)  →  {nxt:%a %d %b %Y} (next day)",
             ha="center", va="center", fontsize=13, color=INK)
    fig.text(0.5, 0.958, f"Match High {hi:,.1f}     ·     Match Low {lo:,.1f}     ·     Match range {hi - lo:,.0f} pt     ·     "
             f"Open gap {o - pc:+.0f}  (open {o:,.1f}, {where.lower()} the range)",
             ha="center", va="center", fontsize=13.5, fontweight="bold", color=INK)
    fig.text(0.5, 0.943, f"Next day: high {nd['high'].max():,.1f}, low {nd['low'].min():,.1f}, close {c:,.1f} ({c - pc:+.0f}); "
             f"broke match {', '.join(broke) if broke else 'neither side'}   |   match close {pc:,.1f}",
             ha="center", va="center", fontsize=11, color="#555")
    return fig


@st.cache_data(ttl=24 * 3600, show_spinner=False, max_entries=60)
def match_png(today: date, t: pd.Timestamp, anchor: pd.Timestamp):
    """Chart one match as PNG bytes (cached: one render per match per day, shared by every visitor)."""
    import io
    import matplotlib.pyplot as plt
    fig = match_figure(load_daily(today), t, anchor)
    if fig is None:
        return None
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=72, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# ----------------------------------------------------------------------------- view
def _shape(f: pd.Series) -> str:
    return f"body {100 * f['body_0']:+.0f}%, upper wick {100 * f['uw_0']:.0f}%, lower wick {100 * f['lw_0']:.0f}%"


def render_pattern_twins():
    st.subheader("🔁 Pattern Twins — past days that looked like this one")
    st.caption("Pick a day. Finds 2022+ days whose daily and weekly candle (body / wick % within a tolerance, same "
               "colour) and trend (vs 20/50/200-DMA and 10/20/40-week averages, RSI, momentum) match it, then charts each "
               "match day and the session after it. Research: `Observe_Pattern/NIFTY_PatternTwins`. Context only — not a signal.")
    today = datetime.now(IST).date()
    with st.spinner("Loading NIFTY daily data from Upstox…"):
        d = load_daily(today)
    c1, c2, c3, c4 = st.columns([1.2, 1.2, 1, 1])
    anchor_in = c1.date_input("Day to match", value=d.index[-1].date(), min_value=date(2022, 6, 1),
                              max_value=d.index[-1].date(), key="pt_day")
    mode = c2.selectbox("Match on", ["Day + week", "Day only", "Week only"], key="pt_mode")
    n = c3.number_input("Matches", 1, 10, 5, key="pt_n")
    tol0 = c4.select_slider("Candle tolerance (± pts)", [10, 15, 20, 25], value=15, key="pt_tol") / 100
    anchor = pd.Timestamp(anchor_in)
    if anchor not in d.index:
        prev = d.index[d.index <= anchor]
        if not len(prev):
            st.warning("No trading day on or before that date.")
            return
        anchor = prev[-1]
        st.info(f"{anchor_in} was not a trading day — using {anchor:%a %d %b %Y}.")
    with st.spinner("Finding twins…"):
        tbl, tol, info = find_twins(d, anchor, mode, int(n), tol0)
    a = d.loc[anchor]
    st.markdown(f"**{anchor:%a %d %b %Y}** — O {a.open:,.1f} · H {a.high:,.1f} · L {a.low:,.1f} · C {a.close:,.1f} "
                f"(range {a.high - a.low:,.0f} pt)")
    st.markdown(f"- **Daily candle:** {_shape(info['day'])}\n"
                f"- **Weekly candle** ({'completed week' if info['week_done'] else 'week-to-date, day ' + str(info['day_no'])}):"
                f" {_shape(info['week'])}")
    if tbl.empty:
        st.warning("No 2022+ day passes the candle gate even at ±40 pts. Try 'Day only'.")
        return
    if tol > tol0 + 1e-9:
        st.info(f"Fewer than {n} days since 2022 matched at ±{tol0 * 100:.0f} pts, so the candle tolerance was widened to "
                f"**±{tol * 100:.0f} pts** (see the body / wick % columns).")
    st.markdown(f"**{len(tbl)} matches since 2022 (latest first)**")
    st.dataframe(tbl, hide_index=True, width="stretch")
    up = (tbl["next close Δ pt"] > 0).sum()
    st.caption(f"Next day closed up {up} of {len(tbl)}; opened inside the match candle {int((tbl['open vs match range'] == 'inside').sum())} "
               f"of {len(tbl)}. A handful of analogs is an anecdote, not an edge.")
    labels = {f"{pd.Timestamp(r['match day']):%a %d %b %Y} → {pd.Timestamp(r['next day']):%a %d %b}   ·   next close "
              f"{r['next close Δ pt']:+d} pt": pd.Timestamp(r["match day"]) for _, r in tbl.iterrows()}
    pick = st.selectbox("Chart (1m / 5m / 15m / 1h / 1d)", list(labels), index=0, key="pt_pick")
    try:
        with st.spinner("Pulling 1-min bars and drawing…"):
            png = match_png(today, labels[pick], anchor)
        if png is None:
            st.warning("No 1-min data for this pair of days.")
        else:
            st.image(png, width="stretch")
    except Exception as e:  # noqa: BLE001
        st.error(f"Chart unavailable: {e}")
