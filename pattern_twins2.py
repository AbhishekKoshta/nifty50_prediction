"""pattern_twins2.py — renders the 🔁 Twins · 2 candles tab (sits beside the single-candle 🔁 Pattern Twins tab).

Pick any day; find the past days whose LAST TWO daily candles (the day and the day before it) look like it, in shape
AND in how the two bars sit together, then chart each twin's two pattern days + the session after them on
1m / 5m / 15m / 1h / 1d, plus a candle grid of the anchor pair against its twins.
Research copy: `~/Trading/Observe_Pattern/NIFTY_PatternTwins/twins.py` (+ `latest_match_chart.py`) — keep the rules identical.

Two-candle similarity
  * Shape gate (hard) on BOTH bars: same colour, body %, upper-wick % and lower-wick % of each bar's range within ±tol.
  * Overlay gate (hard): both bars drawn on one 0–1 scale spanning the pair's high–low; each of the 8
    open/high/low/close positions within ±tol of the anchor's. Pins relative size, the gap and where day 2 closed
    inside day 1 (engulfing / inside / harami / hammer-then-confirmation all become distinct).
  * If fewer than N days pass, tol widens in 5-pt steps (max ±40) and the tab says so.
  * Ranking = the single-candle tab's trend + candle distance, with the 8 overlay positions added to the candle part.
  * Optional weekly part: 1 weekly candle (the current week-to-date bar) or 2 (last completed week + week-to-date).
Reuses the data loaders and base features of `pattern_twins.py`; that module is untouched.
"""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import streamlit as st

import pattern_twins as pt
from pattern_twins import (CANDLE_F, DN, INK, INTRADAY_FROM, IST, LAG_W, LEVEL, MATCH_C, NEXT_C, TREND_F, UP,
                           load_1m, load_daily)

PREV_C = "#7a9cc6"            # lighter slate = day 1 of the pair
RANGE_BAND = "#f8f9fa"        # very light grey band = pattern range (user: lighter, 2026-10-28)
OVERLAY_F = [f"ov_{x}{k}" for k in (0, 1) for x in "ohlc"]
MODES = ["2 daily candles", "2 daily + 1 weekly", "2 daily + 2 weekly", "2 weekly candles"]
ALL_FROM = pd.Timestamp("2016-01-04")   # the app's daily feed starts 2015 → one year of warm-up


# ----------------------------------------------------------------------------- features (= research twins.py)
def _overlay(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    h2, l2 = np.maximum(h, h.shift(1)), np.minimum(l, l.shift(1))
    r2 = (h2 - l2).replace(0, np.nan)
    out = pd.DataFrame(index=df.index)
    for k, (oo, hh, ll, cc) in enumerate([(o, h, l, c), (o.shift(1), h.shift(1), l.shift(1), c.shift(1))]):
        out[f"ov_o{k}"], out[f"ov_h{k}"], out[f"ov_l{k}"], out[f"ov_c{k}"] = \
            (oo - l2) / r2, (hh - l2) / r2, (ll - l2) / r2, (cc - l2) / r2
    return out


def features2(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    return pt.features(df, tf).join(_overlay(df))


def _weekly_features2_at(d: pd.DataFrame, n: int, since: pd.Timestamp) -> pd.DataFrame:
    """Week-to-date features: every week (since `since`) cut at its n-th trading day; history = completed weeks."""
    w = pt.weekly(d)
    sub = d[d.index >= since - timedelta(days=7)]
    rows = {}
    for _, x in sub.groupby(sub.index.to_period("W-FRI")):
        if len(x) < n:
            continue
        x = x.iloc[:n]
        bar = pd.DataFrame({"open": [x["open"].iloc[0]], "high": [x["high"].max()], "low": [x["low"].min()],
                            "close": [x["close"].iloc[-1]]}, index=[x.index[-1]])
        hist = w[w.index < x.index[0]].iloc[-300:]
        if len(hist) >= 60:
            rows[x.index[-1]] = features2(pd.concat([hist, bar]), "week").iloc[-1]
    return pd.DataFrame(rows).T


def _dist2(f: pd.DataFrame, anchor: pd.Series, n_candles: int) -> pd.Series:
    cols_c = [f"{c}_{k}" for k in range(len(LAG_W)) for c in CANDLE_F]
    w_c = [LAG_W[k] for k in range(len(LAG_W)) for _ in CANDLE_F]
    if n_candles >= 2:
        cols_c, w_c = cols_c + OVERLAY_F, w_c + [1.0] * len(OVERLAY_F)

    def rms(cols, w):
        w = np.array(w)
        x = f[cols].astype(float)
        spread = 1.4826 * (x - x.median()).abs().median()
        spread = spread.where(spread > 1e-9, x.std())
        z = (x - anchor[cols].astype(float)) / spread
        return np.sqrt((z.pow(2) * w).sum(axis=1) / w.sum())
    return np.sqrt(0.5 * rms(cols_c, w_c) ** 2 + 0.5 * rms(TREND_F, np.ones(len(TREND_F))) ** 2)


def _shape(f: pd.DataFrame, a: pd.Series, k: int, tol: float) -> pd.Series:
    b, ba = f[f"body_{k}"], a[f"body_{k}"]
    colour = (b.abs() < 0.35) if abs(ba) < 0.15 else (np.sign(b) == np.sign(ba))
    return colour & ((b - ba).abs() <= tol) & ((f[f"uw_{k}"] - a[f"uw_{k}"]).abs() <= tol) & \
        ((f[f"lw_{k}"] - a[f"lw_{k}"]).abs() <= tol)


def _gate2(f: pd.DataFrame, a: pd.Series, n_candles: int, tol: float) -> pd.Series:
    g = _shape(f, a, 0, tol)
    if n_candles >= 2:
        g = g & _shape(f, a, 1, tol)
        for col in OVERLAY_F:
            g &= (f[col] - a[col]).abs() <= tol
    return g


def pair_label(r: pd.Series) -> str:
    """size ratio / where bar 2 opened / closed inside bar 1 (0% = its low)."""
    rng1, rng0 = r["ov_h1"] - r["ov_l1"], r["ov_h0"] - r["ov_l0"]
    if not rng1 > 0:
        return "n/a"
    return (f"{rng0 / rng1:.1f}× / o {100 * (r['ov_o0'] - r['ov_l1']) / rng1:.0f}% / "
            f"c {100 * (r['ov_c0'] - r['ov_l1']) / rng1:.0f}%")


def _shape_txt(f: pd.Series, k: int = 0) -> str:
    return f"{100 * f[f'body_{k}']:+.0f} / {100 * f[f'uw_{k}']:.0f} / {100 * f[f'lw_{k}']:.0f}"


# ----------------------------------------------------------------------------- matching
@st.cache_data(ttl=3 * 3600, show_spinner=False)
def find_twins2(d: pd.DataFrame, anchor: pd.Timestamp, mode: str, n: int, tol0: float, since: pd.Timestamp):
    """Returns (matches DataFrame latest-first, tolerance used, anchor info dict, {tol: n passed})."""
    n_d = 0 if mode == "2 weekly candles" else 2
    n_w = {"2 daily candles": 0, "2 daily + 1 weekly": 1, "2 daily + 2 weekly": 2, "2 weekly candles": 2}[mode]
    fd = features2(d, "day")
    per = d.index.to_period("W-FRI")
    i = d.index.get_loc(anchor)
    day_no = int((per[:i + 1] == per[i]).sum())
    later_same_week = i + 1 < len(d) and per[i + 1] == per[i]
    week_done = (not later_same_week) and (i + 1 < len(d) or anchor.day_of_week == 4)
    fw = None
    if n_w:
        if week_done:
            fw = features2(pt.weekly(d), "week")
        else:
            fw = _weekly_features2_at(d, day_no, since)
            own = _weekly_features2_at(d[d.index <= anchor], day_no, anchor - timedelta(days=6))
            fw = pd.concat([fw, own]).groupby(level=0).last()
    last_ok = d.index[-2] if len(d) > 1 else d.index[-1]
    cand_d = fd[(fd.index >= since) & (fd.index < anchor - timedelta(days=30)) & (fd.index <= last_ok)].dropna()
    dist_d = _dist2(cand_d, fd.loc[anchor], n_d)
    gap = 7
    if n_w == 0:
        dist = dist_d
        gate_fn = lambda tol: _gate2(cand_d, fd.loc[anchor], n_d, tol)
    else:
        cand_w = fw[fw.index.isin(cand_d.index)].dropna()
        dist_w = _dist2(cand_w, fw.loc[anchor], n_w)
        gap = 21
        if n_d == 0:
            dist = dist_w
            gate_fn = lambda tol: _gate2(cand_w, fw.loc[anchor], n_w, tol)
        else:
            common = dist_w.index.intersection(dist_d.index)
            dist = np.sqrt(0.5 * dist_d[common] ** 2 + 0.5 * dist_w[common] ** 2)
            gate_fn = lambda tol: (_gate2(cand_d.loc[common], fd.loc[anchor], n_d, tol)
                                   & _gate2(cand_w.loc[common], fw.loc[anchor], n_w, tol))
    score = (100 * (1 - dist.rank(pct=True)) + 100 / len(dist)).clip(upper=100)
    tol, best, n_at = tol0, pd.Series(dtype=float), {}
    while True:
        g = gate_fn(tol).reindex(score.index).fillna(False)
        best = pt._dedupe(score[g], gap)
        n_at[round(tol, 2)] = len(best)
        if len(best) >= n or tol >= 0.40 - 1e-9:
            break
        tol = round(tol + 0.05, 2)
    best = best.iloc[:n]
    rows = []
    for t, s in best.items():
        j = d.index.get_loc(t)
        prev, nx = d.index[j - 1], d.index[j + 1]
        p_hi, p_lo = d.high.iloc[j - 1:j + 1].max(), d.low.iloc[j - 1:j + 1].min()
        pc, o, c = d.close.iloc[j], d.open.iloc[j + 1], d.close.iloc[j + 1]
        r = {"day 1": prev.date(), "day 2 (match)": t.date(), "next day": nx.date(), "score": round(s, 1)}
        if n_d:
            r["D1 body/uw/lw %"] = _shape_txt(fd.loc[t], 1)
            r["D2 body/uw/lw %"] = _shape_txt(fd.loc[t], 0)
            r["pair (size / open / close)"] = pair_label(fd.loc[t])
        if n_w:
            if n_w >= 2:
                r["W1 body/uw/lw %"] = _shape_txt(fw.loc[t], 1)
            r["W body/uw/lw %"] = _shape_txt(fw.loc[t], 0)
            if n_w >= 2:
                r["W pair"] = pair_label(fw.loc[t])
        r["next open gap"] = round(o - pc)
        r["open vs pattern range"] = "inside" if p_lo <= o <= p_hi else ("above" if o > p_hi else "below")
        r["next close Δ pt"] = round(c - pc)
        r["next close Δ %"] = round(100 * (c / pc - 1), 2)
        r["+5d %"] = round(100 * (d.close.iloc[j + 5] / pc - 1), 2) if j + 5 < len(d) else None
        rows.append(r)
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("day 2 (match)", ascending=False).reset_index(drop=True)
    info = {"day": fd.loc[anchor], "week": None if fw is None else fw.loc[anchor], "week_done": week_done,
            "day_no": day_no, "n_d": n_d, "n_w": n_w, "candidates": int(len(dist))}
    return out, tol, info, n_at


# ----------------------------------------------------------------------------- charts
def _candles(ax, bars, width=0.62, thin=False):
    from matplotlib.patches import Rectangle
    for i, r in enumerate(bars.itertuples()):
        c = UP if r.close >= r.open else DN
        ax.vlines(i, r.low, r.high, color=c, lw=0.6 if thin else 1.1, zorder=2)
        ax.add_patch(Rectangle((i - width / 2, min(r.open, r.close)), width, max(abs(r.close - r.open), 1e-9),
                               color=c, lw=0, zorder=3))


def _panel2(ax, bars, title, hi, lo, days, fmt, step, next_open, band_labels=True, wide=False):
    """Levels, band, day stripes and the open marker only — all text annotations live in the figure header."""
    from matplotlib.patches import Rectangle
    n = len(bars)
    n_pat = len(days) - 1
    tr = ax.get_xaxis_transform()
    first = {}
    for k, dd in enumerate(days):
        idx = [i for i, t in enumerate(bars.index) if t.normalize() == dd]
        if not idx:
            continue
        first[dd] = idx[0]
        if k == n_pat:
            ax.axvline(idx[0] - 0.5, color="#6c757d", lw=1.4, zorder=4)
        elif k > 0:
            ax.axvline(idx[0] - 0.5, color="#adb5bd", lw=1.0, ls=(0, (3, 3)), zorder=4)
        col = NEXT_C if k == n_pat else (MATCH_C if k == n_pat - 1 else PREV_C)
        ax.add_patch(Rectangle((idx[0] - 0.5, 1.0), len(idx), 0.045, transform=tr, color=col, clip_on=False, lw=0, zorder=5))
        if band_labels:
            lab = "NEXT DAY" if k == n_pat else f"MATCH DAY {k + 1} of {n_pat}"
            if not wide:
                lab = "NEXT" if k == n_pat else f"DAY {k + 1}"
            dtxt = f"{dd:%a %d %b %Y}" if wide else f"{dd:%a %d %b}"
            ax.text((idx[0] + idx[-1]) / 2, 1.0225, f"{dtxt}  ·  {lab}", transform=tr, ha="center", va="center",
                    fontsize=9.5, fontweight="bold", color="white", zorder=6, clip_on=False)
    ax.fill_between([-1, n - 0.5], lo, hi, color=RANGE_BAND, zorder=1, lw=0)
    _candles(ax, bars, width=0.7 if n > 300 else 0.62, thin=n > 300)
    for y in (hi, lo):
        ax.hlines(y, -1, n - 0.5, color=LEVEL, ls=(0, (5, 3)), lw=1.3, zorder=4)
    if days[-1] in first:
        ax.plot(first[days[-1]] - 0.6, next_open, marker=">", color=NEXT_C, ms=9, zorder=6)
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


def match2_figure(d: pd.DataFrame, t: pd.Timestamp, anchor: pd.Timestamp):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    j = d.index.get_loc(t)
    prev, nxt = d.index[j - 1], d.index[j + 1]
    m = load_1m(prev.date(), nxt.date())
    sess = [prev, t, nxt]
    if any(not (m.index.normalize() == s).any() for s in sess):
        return None
    m = m[m.index.normalize().isin(sess)]
    md, nd = m[m.index.normalize().isin(sess[:2])], m[m.index.normalize() == nxt]
    hi, lo = md["high"].max(), md["low"].min()
    pc, c = d.close.iloc[j], d.close.iloc[j + 1]          # official NSE closes
    o = nd["open"].iloc[0]
    fig = plt.figure(figsize=(22, 20), facecolor="white")
    gs = fig.add_gridspec(3, 2, height_ratios=[1.1, 1, 1], hspace=0.28, wspace=0.12)
    _panel2(fig.add_subplot(gs[0, :]), m, "1 minute", hi, lo, sess, "%H:%M", 45, o, wide=True)
    _panel2(fig.add_subplot(gs[1, 0]), pt._rs(m, "5min"), "5 minute", hi, lo, sess, "%H:%M", 12, o)
    _panel2(fig.add_subplot(gs[1, 1]), pt._rs(m, "15min"), "15 minute", hi, lo, sess, "%H:%M", 4, o)
    _panel2(fig.add_subplot(gs[2, 0]), pt._rs(m, "60min"), "1 hour", hi, lo, sess, "%H:%M", 1, o)
    _panel2(fig.add_subplot(gs[2, 1]), d.iloc[max(0, j - 24): j + 2], "1 day (last 25 sessions)", hi, lo, sess,
            "%d %b", 4, o, band_labels=False)
    where = "inside" if lo <= o <= hi else ("above" if o > hi else "below")
    broke = [s for s, ok in [(f"high +{nd['high'].max() - hi:.0f}", nd["high"].max() > hi),
                             (f"low −{lo - nd['low'].min():.0f}", nd["low"].min() < lo)] if ok]
    fig.text(0.5, 0.975, f"NIFTY — twin of {anchor:%a %d %b %Y}:  {prev:%a %d %b} + {t:%a %d %b %Y} (2-candle pattern)"
             f"  →  {nxt:%a %d %b %Y} (next day)", ha="center", va="center", fontsize=13, color=INK)
    fig.text(0.5, 0.958, f"Pt High {hi:,.1f}     ·     Pt Low {lo:,.1f}     ·     Pt range {hi - lo:,.0f} pt     ·     "
             f"Open gap {o - pc:+.0f}  (open {o:,.1f}, {where} the range)",
             ha="center", va="center", fontsize=13.5, fontweight="bold", color=INK)
    fig.text(0.5, 0.943, f"Next day: high {nd['high'].max():,.1f}, low {nd['low'].min():,.1f}, close {c:,.1f} ({c - pc:+.0f}); "
             f"broke pattern {', '.join(broke) if broke else 'neither side'}   |   pattern close {pc:,.1f}",
             ha="center", va="center", fontsize=11, color="#555")
    return fig


def pair_grid_figure(d: pd.DataFrame, anchor: pd.Timestamp, matches: list, back=10, fwd=5, cols=4):
    """Anchor pair + its twins as small daily-candle panels: the two pattern bars shaded blue, what came next grey."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    items = [anchor] + [pd.Timestamp(x) for x in matches][:cols * 3 - 1]
    rows = int(np.ceil(len(items) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(4.6 * cols, 3.3 * rows), facecolor="white")
    axes = np.atleast_1d(axes).ravel()
    for ax, t in zip(axes, items):
        i = d.index.get_loc(t)
        seg = d.iloc[max(0, i - back + 1): i + 1 + fwd]
        k0 = min(back, i + 1) - 1
        ax.axvspan(k0 - 1.5, k0 + 0.5, color="#dbe7f5", lw=0, zorder=0)
        if len(seg) > k0 + 1:
            ax.axvspan(k0 + 0.5, len(seg) - 0.5, color="#ededed", lw=0, zorder=0)
        _candles(ax, seg)
        pair = seg.iloc[k0 - 1: k0 + 1]
        ax.hlines([pair["high"].max(), pair["low"].min()], -0.5, len(seg) - 0.5, color=LEVEL, ls=(0, (4, 3)), lw=0.9, zorder=1)
        ttl = f"{t:%d %b %Y}  ANCHOR" if t == anchor else f"{t:%d %b %Y}"
        if len(seg) > k0 + 1:
            ttl += f"   next {min(fwd, len(seg) - k0 - 1)}: {100 * (seg['close'].iloc[-1] / seg['close'].iloc[k0] - 1):+.1f}%"
        ax.set_title(ttl, fontsize=9.5, loc="left", fontweight="bold" if t == anchor else "normal", color=INK)
        ax.set_xticks([])
        ax.tick_params(axis="y", labelsize=7, colors="#555")
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        ax.set_xlim(-0.7, len(seg) - 0.3)
    for ax in axes[len(items):]:
        ax.axis("off")
    fig.suptitle(f"The 2-candle pattern of {anchor:%a %d %b %Y} (blue) vs its twins — grey = what came next, "
                 f"dashed = pattern high / low", fontsize=12, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    return fig


def _png(fig, dpi=72):
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


@st.cache_data(ttl=24 * 3600, show_spinner=False, max_entries=60)
def match2_png(today: date, t: pd.Timestamp, anchor: pd.Timestamp):
    fig = match2_figure(load_daily(today), t, anchor)
    return None if fig is None else _png(fig)


@st.cache_data(ttl=24 * 3600, show_spinner=False, max_entries=30)
def pair_grid_png(today: date, anchor: pd.Timestamp, matches: tuple):
    return _png(pair_grid_figure(load_daily(today), anchor, list(matches)), dpi=90)


# ----------------------------------------------------------------------------- view
def render_pattern_twins2():
    st.subheader("🔁 Twins · 2 candles — past days whose last TWO candles looked like this")
    st.caption("Pick a day. Both the day and the day before it must match in shape (body / wick %, same colour) AND in how "
               "the two bars sit together (size, gap, where day 2 closed inside day 1), then trend ranks the survivors. "
               "Charts show both pattern days + the next session. Research: `Observe_Pattern/NIFTY_PatternTwins`. "
               "Context only — not a signal.")
    today = datetime.now(IST).date()
    with st.spinner("Loading NIFTY daily data from Upstox…"):
        d = load_daily(today)
    c1, c2, c3, c4, c5 = st.columns([1.2, 1.3, 0.8, 1, 1.1])
    anchor_in = c1.date_input("Day to match (= day 2)", value=d.index[-1].date(), min_value=date(2022, 6, 1),
                              max_value=d.index[-1].date(), key="pt2_day")
    mode = c2.selectbox("Match on", MODES, key="pt2_mode")
    n = c3.number_input("Matches", 1, 12, 6, key="pt2_n")
    tol0 = c4.select_slider("Candle tolerance (± pts)", [10, 15, 20, 25, 30], value=15, key="pt2_tol") / 100
    all_hist = c5.toggle("Search since 2016 (no intraday chart before 2022)", value=False, key="pt2_all")
    since = ALL_FROM if all_hist else INTRADAY_FROM
    anchor = pd.Timestamp(anchor_in)
    if anchor not in d.index:
        prev = d.index[d.index <= anchor]
        if not len(prev):
            st.warning("No trading day on or before that date.")
            return
        anchor = prev[-1]
        st.info(f"{anchor_in} was not a trading day — using {anchor:%a %d %b %Y}.")
    with st.spinner("Finding two-candle twins…"):
        tbl, tol, info, n_at = find_twins2(d, anchor, mode, int(n), tol0, since)
    j = d.index.get_loc(anchor)
    a1, a2 = d.iloc[j - 1], d.loc[anchor]
    fa = info["day"]
    st.markdown(f"**Day 1 · {d.index[j - 1]:%a %d %b %Y}** — O {a1.open:,.1f} · H {a1.high:,.1f} · L {a1.low:,.1f} · C {a1.close:,.1f}"
                f" (range {a1.high - a1.low:,.0f} pt) · body / upper wick / lower wick **{_shape_txt(fa, 1)} %**")
    st.markdown(f"**Day 2 · {anchor:%a %d %b %Y}** — O {a2.open:,.1f} · H {a2.high:,.1f} · L {a2.low:,.1f} · C {a2.close:,.1f}"
                f" (range {a2.high - a2.low:,.0f} pt) · body / upper wick / lower wick **{_shape_txt(fa, 0)} %**")
    st.markdown(f"**Pair:** {pair_label(fa)} — day 2's range ÷ day 1's / where day 2 opened / closed inside day 1's bar "
                f"(0% = its low). Pattern range {min(a1.low, a2.low):,.1f} – {max(a1.high, a2.high):,.1f}.")
    if info["week"] is not None:
        wk = "completed week" if info["week_done"] else f"week-to-date, day {info['day_no']}"
        line = f"**Weekly bar** ({wk}): body / upper wick / lower wick {_shape_txt(info['week'], 0)} %"
        if info["n_w"] >= 2:
            line += f" · previous week {_shape_txt(info['week'], 1)} % · pair {pair_label(info['week'])}"
        st.markdown(line)
    if tbl.empty:
        st.warning(f"No day since {since.year} passes the two-candle gate even at ±40 pts. Try '2 daily candles' or the "
                   "since-2016 toggle.")
        return
    if tol > tol0 + 1e-9:
        steps = ", ".join(f"±{100 * k:.0f}: {v}" for k, v in n_at.items())
        st.info(f"Fewer than {n} days matched at ±{tol0 * 100:.0f} pts, so the tolerance was widened to **±{tol * 100:.0f} pts** "
                f"({steps}). Anything that needed ±30 or more is 'roughly similar', not a twin.")
    st.markdown(f"**{len(tbl)} twins since {since.year} (latest first)** — {info['candidates']} candidate days searched")
    st.dataframe(tbl, hide_index=True, width="stretch")
    up = int((tbl["next close Δ pt"] > 0).sum())
    up5 = int((tbl["+5d %"].dropna() > 0).sum())
    st.caption(f"Next day closed up {up} of {len(tbl)}; 5 days later up {up5} of {int(tbl['+5d %'].notna().sum())}; "
               f"opened inside the pattern range {int((tbl['open vs pattern range'] == 'inside').sum())} of {len(tbl)}. "
               "A handful of analogs is an anecdote, not an edge.")
    try:
        with st.spinner("Drawing the candle grid…"):
            st.image(pair_grid_png(today, anchor, tuple(str(x) for x in tbl["day 2 (match)"])), width="stretch")
    except Exception as e:  # noqa: BLE001
        st.warning(f"Candle grid unavailable: {e}")
    labels = {f"{pd.Timestamp(r['day 1']):%a %d %b} + {pd.Timestamp(r['day 2 (match)']):%a %d %b %Y} → "
              f"{pd.Timestamp(r['next day']):%a %d %b}   ·   next close {r['next close Δ pt']:+d} pt": pd.Timestamp(r["day 2 (match)"])
              for _, r in tbl.iterrows()}
    pick = st.selectbox("Chart (1m / 5m / 15m / 1h / 1d — both pattern days + the next day)", list(labels), index=0, key="pt2_pick")
    if labels[pick] < INTRADAY_FROM + timedelta(days=1):
        st.info("No intraday data before 2022 for this twin — the candle grid above is its picture.")
        return
    try:
        with st.spinner("Pulling 1-min bars and drawing…"):
            png = match2_png(today, labels[pick], anchor)
        if png is None:
            st.warning("No 1-min data for these sessions.")
        else:
            st.image(png, width="stretch")
    except Exception as e:  # noqa: BLE001
        st.error(f"Chart unavailable: {e}")
