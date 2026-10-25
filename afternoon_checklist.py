"""afternoon_checklist.py — renders the 🕛 Afternoon Move tab.

Will NIFTY move >= 0.5% after 12:00 today, and which way? Research: `Algo_Nifty50/strategies/68_AfternoonMove`
(Upstox NIFTY 5m 2022-01 -> 2026-10, 1,186 sessions; walk-forward split 2024-06-01).
  * SIZE score = +1 each: wide morning (09:15-12:00 range >= 1%), India VIX >= 16, |gap| >= 0.5%, ATR% top tercile,
    previous day big, expiry day;  -1 each: ascending triangle, quiet morning (<= 0.4%), rectangle/box, 5m BB squeeze.
    P(>= 0.5% one-way move after 12:00) goes 9% (score -2) -> 70% (score 4+), monotonic in both halves.
  * Compression patterns (triangles, box, squeeze, inside candles) precede QUIET afternoons, not breakouts.
  * Direction only after a TREND MORNING (>= 0.5% from the open, efficiency >= 0.35): with score >= 2 the afternoon goes
    >= 0.5% with the morning 43% vs 22% against. Futures rule (go with the morning at 12:00, stop 0.25-0.35%, hold to
    15:20): 74 trades, PF ~1.8, both halves +, 5/5 years — MARGINAL-GO, paper first.
  * Expiry options checklist from `67_ExpiryOTMLottery` (MARGINAL-GO, paper first).

Self-contained: pulls the free Upstox v3 feed itself (NIFTY 5m + daily, India VIX daily). The detectors below are a copy
of `68_AfternoonMove/a_event_study.py::morning_patterns` and the daily features there — keep them identical.
Every auto-detected item can be overridden by hand; the probability updates from the overridden boxes.
"""
import json
import os
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import streamlit as st

IST = timezone(timedelta(hours=5, minutes=30))
NIFTY = urllib.parse.quote("NSE_INDEX|Nifty 50", safe="")
VIX = urllib.parse.quote("NSE_INDEX|India VIX", safe="")
STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "afternoon_checklist_stats.json")
TUESDAY_FROM = date(2025, 9, 1)          # NIFTY weekly expiry: Thursday before, Tuesday from 2025-09-01
_CACHE: dict = {}

POS = ["wide_morning", "vix_ge16", "gap_ge_0p5", "atr_top_tercile", "prev_day_big", "expiry_day"]
NEG = ["asc_triangle", "quiet_morning", "rectangle_box", "squeeze_bbw_5m"]
LABELS = {
    "wide_morning": "Wide morning — 09:15–12:00 range ≥ 1.0% of price",
    "vix_ge16": "India VIX (previous close) ≥ 16",
    "gap_ge_0p5": "Opening gap ≥ 0.5% (either way)",
    "atr_top_tercile": "ATR% (14 d) in the top third of the last year",
    "prev_day_big": "Yesterday's range in the top third of the last 60 days",
    "expiry_day": "Weekly expiry today (auto = Tuesday; tick by hand on a holiday-shifted expiry)",
    "asc_triangle": "Ascending triangle on the 5m morning (flat top + rising lows)",
    "quiet_morning": "Quiet morning — 09:15–12:00 range ≤ 0.4%",
    "rectangle_box": "Rectangle / box on the 5m morning (flat top + flat bottom)",
    "squeeze_bbw_5m": "5m Bollinger squeeze at 11:55 (tightest 20% of the last 60 days)",
}


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
    return d.drop_duplicates("t").set_index("t").sort_index()[["open", "high", "low", "close"]].astype(float)


def load_data(day: date, ttl: int = 240):
    """5m bars (~100 calendar days up to `day`, incl. today's live bars), daily NIFTY (~420 d), daily VIX."""
    key = ("d", day)
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time() - ttl:
        return hit[1]
    parts, hi = [], day
    lo_all = day - timedelta(days=100)
    while hi > lo_all:
        lo = max(lo_all, hi - timedelta(days=27))
        parts.append(_frame(_get(f"https://api.upstox.com/v3/historical-candle/{NIFTY}/minutes/5/{hi}/{lo}")))
        hi = lo - timedelta(days=1)
    m5 = pd.concat(parts)
    if day == datetime.now(IST).date():
        try:
            m5 = pd.concat([m5, _frame(_get(f"https://api.upstox.com/v3/historical-candle/intraday/{NIFTY}/minutes/5"))])
        except Exception:  # noqa: BLE001  (empty / closed outside market hours)
            pass
    m5 = m5[~m5.index.duplicated(keep="last")].sort_index()
    m5 = m5[(m5.index.strftime("%H:%M") >= "09:15") & (m5.index.strftime("%H:%M") <= "15:25")]
    d1 = _frame(_get(f"https://api.upstox.com/v3/historical-candle/{NIFTY}/days/1/{day}/{day - timedelta(days=620)}"))
    v1 = _frame(_get(f"https://api.upstox.com/v3/historical-candle/{VIX}/days/1/{day}/{day - timedelta(days=30)}"))
    for x in (d1, v1):
        x.index = x.index.normalize()
    d1 = d1[(d1.index.dayofweek < 5) & ((d1.high - d1.low) > 0)]
    out = (m5, d1, v1)
    _CACHE[key] = (time.time(), out)
    return out


# ----------------------------------------------------------------------------- detectors (= 68 a_event_study.py)
def _pivots(h, l, k=2):
    n = len(h); last_ok = n - 1 - k
    ph = [i for i in range(k, last_ok + 1) if h[i] == h[i - k:i + k + 1].max()]
    pl = [i for i in range(k, last_ok + 1) if l[i] == l[i - k:i + k + 1].min()]
    return ph, pl


def morning_patterns(m, S):
    o, h, l, c = (m[k].values for k in ("open", "high", "low", "close"))
    n = len(c); tol = 0.0010 * S
    rng = h.max() - l.min(); last = c[-1]
    ph, pl = _pivots(h, l)
    P = {}
    hh = [h[i] for i in ph[-4:]]; ll = [l[i] for i in pl[-4:]]
    flat_top = len(hh) >= 2 and (max(hh) - min(hh)) <= tol
    flat_bot = len(ll) >= 2 and (max(ll) - min(ll)) <= tol
    rising_lows = len(ll) >= 2 and ll[-1] - ll[0] > tol and all(np.diff(ll) > -tol / 2)
    falling_highs = len(hh) >= 2 and hh[0] - hh[-1] > tol and all(np.diff(hh) < tol / 2)
    inside_now = len(hh) >= 2 and len(ll) >= 2 and last <= max(hh) + tol / 2 and last >= min(ll) - tol / 2
    P["asc_triangle"] = (flat_top and rising_lows and inside_now, +1)
    P["desc_triangle"] = (flat_bot and falling_highs and inside_now, -1)
    P["rectangle_box"] = (flat_top and flat_bot and inside_now and rng <= 0.006 * S, 0)
    P["quiet_morning"] = (rng <= 0.004 * S, 0)
    P["wide_morning"] = (rng >= 0.010 * S, 0)
    mv = (last - o[0]) / o[0]
    er = abs(last - o[0]) / max(1e-9, np.abs(np.diff(c)).sum() + abs(c[0] - o[0]))
    P["trend_morning"] = (abs(mv) >= 0.005 and er >= 0.35, int(np.sign(mv)))
    last60 = slice(max(0, n - 12), n)
    f = m.iloc[:18]; mast_up = f.high.max() - o[0]; mast_dn = o[0] - f.low.min()
    cons = h[last60].max() - l[last60].min()
    P["bull_flag"] = (mast_up >= 0.005 * S and mast_up > mast_dn and cons <= 0.4 * mast_up and last >= o[0] + 0.5 * mast_up, +1)
    P["bear_flag"] = (mast_dn >= 0.005 * S and mast_dn > mast_up and cons <= 0.4 * mast_dn and last <= o[0] - 0.5 * mast_dn, -1)
    pos = (last - l.min()) / max(1e-9, rng)
    P["at_morning_high"] = (pos >= 0.9, +1)
    P["at_morning_low"] = (pos <= 0.1, -1)
    return P, mv * 100, rng / S * 100, er


def compute(day: date):
    m5, d1, v1 = load_data(day)
    D = pd.Timestamp(day)
    today = m5[m5.index.normalize() == D]
    if today.empty:
        return {"ok": False, "why": f"No NIFTY 5-min bars for {day} yet (holiday, weekend, or before 09:15)."}
    hist = d1[d1.index < D]
    if len(hist) < 260:
        return {"ok": False, "why": "Not enough daily history from Upstox."}
    mor = today[today.index.strftime("%H:%M") < "12:00"]
    aft = today[today.index.strftime("%H:%M") >= "12:00"]
    complete = len(mor) >= 33
    now = datetime.now(IST).replace(tzinfo=None)
    S = aft.open.iloc[0] if len(aft) else mor.close.iloc[-1]
    P, mv, mrng, er = morning_patterns(mor, S) if len(mor) >= 6 else ({}, 0.0, 0.0, 0.0)
    # daily features (known before the open)
    rngd = hist.high - hist.low
    pc = hist.close.iloc[-1]; ph_, pl_ = hist.high.iloc[-1], hist.low.iloc[-1]
    rp = rngd / hist.close.shift()
    prev_rank = float((rp.iloc[-1] > rp.iloc[-61:-1]).mean())
    atrp = rp.rolling(14).mean()
    w = atrp.iloc[-252:]
    atr_rank = float((atrp.iloc[-1] >= w).mean())
    vprev = v1[v1.index < D].close
    vix = float(vprev.iloc[-1]) if len(vprev) else np.nan
    op = today.open.iloc[0]
    gap = (op / pc - 1) * 100
    expiry = (day.weekday() == 1) if day >= TUESDAY_FROM else (day.weekday() == 3)
    # squeeze: BBW(20) on continuous 5m at 11:55 vs the last 60 sessions' 11:55 values (1 = tightest)
    c = m5.close
    bbw = 4 * c.rolling(20).std() / c.rolling(20).mean()
    at = bbw[bbw.index.strftime("%H:%M") == "11:55"]
    at = at[at.index.normalize() <= D].iloc[-60:]
    squeeze = bool(complete and len(at) >= 30 and at.index[-1].normalize() == D and (at.iloc[-1] <= at).mean() >= 0.8)
    flags = dict(
        wide_morning=bool(P.get("wide_morning", (False,))[0]), vix_ge16=bool(vix >= 16), gap_ge_0p5=bool(abs(gap) >= 0.5),
        atr_top_tercile=bool(atr_rank >= 2 / 3), prev_day_big=bool(prev_rank >= 2 / 3), expiry_day=bool(expiry),
        asc_triangle=bool(P.get("asc_triangle", (False,))[0]), quiet_morning=bool(P.get("quiet_morning", (False,))[0]),
        rectangle_box=bool(P.get("rectangle_box", (False,))[0]), squeeze_bbw_5m=squeeze)
    after = None
    if len(aft):
        a = aft[aft.index.strftime("%H:%M") <= "15:25"]
        after = dict(up_pct=(a.high.max() - S) / S * 100, dn_pct=(S - a.low.min()) / S * 100, last=a.close.iloc[-1],
                     last_t=a.index[-1].strftime("%H:%M"), now_pct=(a.close.iloc[-1] - S) / S * 100)
    # 67 expiry options trigger: first 5m close beyond the 09:15-12:00 range after 12:00 (before 14:00)
    brk = None
    if complete and len(aft):
        mh, ml = mor.high.max(), mor.low.min()
        for t, row in aft.iterrows():
            if t.strftime("%H:%M") >= "14:00":
                break
            if row.close > mh or row.close < ml:
                brk = dict(t=(t + pd.Timedelta(minutes=5)).strftime("%H:%M"), side=1 if row.close > mh else -1, px=row.close)
                break
    return dict(ok=True, day=day, complete=complete, n_mor=len(mor), S=S, open=op, prev_close=pc, prev_high=ph_, prev_low=pl_,
                gap=gap, vix=vix, atr_rank=atr_rank, prev_rank=prev_rank, mor_move=mv, mor_rng=mrng, er=er, P=P, flags=flags,
                after=after, brk=brk, as_of=today.index[-1].strftime("%H:%M"), live=(day == now.date()))


# ----------------------------------------------------------------------------- UI
def _stats():
    with open(STATS_FILE) as f:
        return json.load(f)


def _pct(x):
    return f"{x * 100:.0f}%"


def render_afternoon_checklist():
    J = _stats()
    st.subheader("🕛 Afternoon Move Checklist — will NIFTY move ≥ 0.5% after 12:00?")
    st.caption("Backtest: Upstox NIFTY 5m 2022-01 → 2026-10 (1,186 sessions), `68_AfternoonMove`. Base rate: a ≥ 0.5% "
               f"one-way move after 12:00 on {_pct(J['base']['p_big'])} of days; the close stays ≥ 0.5% away from 12:00 on "
               f"{_pct(J['base']['p_net'])}. Probabilities are calibrated **at 12:00** — before noon the morning items are provisional.")
    c1, c2 = st.columns([1, 3])
    day = c1.date_input("Session", value=datetime.now(IST).date(), max_value=datetime.now(IST).date(), key="aft_day",
                        help="Today = live. Pick a past date to replay how the checklist looked that day.")
    if c2.button("🔄 Refresh live data", key="aft_refresh"):
        _CACHE.clear()
    try:
        R = compute(day)
    except Exception as e:  # noqa: BLE001
        R = {"ok": False, "why": f"Upstox fetch failed: {e}"}
    if not R["ok"]:
        st.info(R["why"] + " You can still tick the boxes by hand below.")
        R = dict(flags={k: False for k in POS + NEG}, P={}, complete=False, mor_move=0.0, er=0.0, after=None, brk=None, live=False)
    else:
        status = "✅ morning complete (12:00 snapshot)" if R["complete"] else f"⏳ provisional — {R['n_mor']}/33 morning bars (as of {R['as_of']})"
        st.markdown(f"**{R['day']:%a %d %b %Y}** · {status} · open {R['open']:,.1f} · gap {R['gap']:+.2f}% · "
                    f"VIX(prev) {R['vix']:.2f} · morning range {R['mor_rng']:.2f}% · open→now {R['mor_move']:+.2f}%")

    st.markdown("##### 1 · Size checklist (auto-filled — untick/tick to override)")
    a, b = st.columns(2)
    vals = {}
    with a:
        st.markdown("**➕ Raise the odds**")
        for k in POS:
            vals[k] = st.checkbox(LABELS[k], value=R["flags"][k], key=f"aft_{k}_{day}")
    with b:
        st.markdown("**➖ Compression — predicts a QUIET afternoon**")
        for k in NEG:
            vals[k] = st.checkbox(LABELS[k], value=R["flags"][k], key=f"aft_{k}_{day}")
    score = sum(vals[k] for k in POS) - sum(vals[k] for k in NEG)
    sb = str(int(np.clip(score, -2, 4)))
    s = J["score"][sb]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Score", f"{score:+d}", help="Each ➕ = +1, each ➖ = −1 (backtest buckets: ≤ −2 … ≥ 4)")
    m2.metric("P(≥ 0.5% move after 12:00)", _pct(s["p_big"]), f"{(s['p_big'] - J['base']['p_big']) * 100:+.0f} pts vs base")
    m3.metric("P(quiet < 0.25%)", _pct(s["p_quiet"]))
    m4.metric("P(close ≥ 0.5% from 12:00)", _pct(s["p_net"]))
    st.caption(f"Score bucket {sb}: {s['n']} historical days. Score is a SIZE forecast, not direction — at score ≥ 4 the move "
               f"went with the morning {_pct(s['p_with'])} vs against {_pct(s['p_against'])}.")

    st.markdown("##### 2 · Direction")
    tm_auto = bool(R["P"].get("trend_morning", (False, 0))[0])
    tm = st.checkbox("Trend morning — ≥ 0.5% from the open by 12:00 and a clean move (efficiency ≥ 0.35)", value=tm_auto,
                     key=f"aft_tm_{day}", help=f"auto: open→12:00 {R['mor_move']:+.2f}%, efficiency {R['er']:.2f}")
    side = "UP 📈" if R["mor_move"] > 0 else "DOWN 📉"
    if tm and score >= 2:
        t = J["trend_and_score2"]
        st.success(f"**ARMED — go with the morning ({side}).** Historically ({t['n']} days): ≥ 0.5% move **with** the morning "
                   f"{_pct(t['p_with'])} vs against {_pct(t['p_against'])}; closed ≥ 0.5% its way {_pct(t['p_net_with'])}.  \n"
                   f"Futures rule: {J['trade']['rule']} → {J['trade']['n']} trades, PF {J['trade']['pf']}, +{J['trade']['net_pts']:,} pt "
                   f"(H1 +{J['trade']['h1']} / H2 +{J['trade']['h2']}), {J['trade']['yrs']} yrs. **{J['trade']['verdict']}**")
    elif tm:
        t = J["trend_not_score2"]
        st.warning(f"Trend morning but score < 2 — weak: with the morning {_pct(t['p_with'])} vs against {_pct(t['p_against'])} "
                   f"({t['n']} days). Sit out or size down.")
    else:
        st.info("No trend morning → no direction edge. W / M / cup & handle are coin flips for direction (68 Part A); "
                "use the score only to size stops/targets.")

    with st.expander("Other morning patterns detected (information only — no reliable edge)"):
        rows = []
        for k, lab in [("desc_triangle", "Descending triangle"), ("bull_flag", "Bull flag"), ("bear_flag", "Bear flag"),
                       ("at_morning_high", "At the morning high"), ("at_morning_low", "At the morning low")]:
            p = J["patterns"].get(k, {})
            rows.append({"pattern": lab, "today": "✔" if R["P"].get(k, (False,))[0] else "",
                         "P(big after 12)": _pct(p["p_big"]) if p else "—", "lift": p.get("lift", "—"),
                         "big days its way / against": f"{_pct(p['its_way'])} / {_pct(p['against'])}" if p.get("its_way") is not None else "—"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    if vals["expiry_day"]:
        st.markdown("##### 3 · Expiry options checklist (`67_ExpiryOTMLottery`)")
        e = J["expiry_options"]
        beyond = R.get("S") is not None and R.get("prev_high") is not None and (
            (R["mor_move"] > 0 and R["S"] > R["prev_high"]) or (R["mor_move"] < 0 and R["S"] < R["prev_low"]))
        c_atr = st.checkbox("ATR% top third of the last year", value=vals["atr_top_tercile"], key=f"aft_e1_{day}")
        c_mv = st.checkbox("≥ 0.5% from the open by 12:00", value=abs(R["mor_move"]) >= 0.5, key=f"aft_e2_{day}")
        c_pd = st.checkbox("Price beyond yesterday's high (up) / low (down), same direction", value=bool(beyond), key=f"aft_e3_{day}")
        trig = R.get("brk")
        if c_atr and c_mv and c_pd:
            if trig and np.sign(trig["side"]) == np.sign(R["mor_move"]):
                st.success(f"**TRIGGERED {trig['t']}** — 5m close {trig['px']:,.1f} broke the morning "
                           f"{'high' if trig['side'] > 0 else 'low'} → buy the ~₹30–50 OTM {'CE' if trig['side'] > 0 else 'PE'}, "
                           f"limit-sell at 4×, else exit 15:25. Backtest: {e['n']} trades, {_pct(e['win'])} win, "
                           f"+₹{e['pnl_per_lot']:,}/lot, ROI {_pct(e['roi'])}. **{e['verdict']}**")
                st.caption("Trigger here uses 5m closes (the bar closing at the time shown); the backtest used 1m closes, "
                           "so live it can fire a few minutes earlier on your 1m chart.")
            else:
                st.warning("All 3 conditions met — WAIT for a 5m close beyond the 09:15–12:00 range in the same direction "
                           "(12:00–14:00). Avoid the ₹10–20 strikes (they lose even on these days).")
        else:
            st.info("Not all 3 conditions met — no expiry option trade.")

    if R.get("after"):
        a = R["after"]
        st.markdown(f"##### What happened after 12:00 (to {a['last_t']})")
        st.markdown(f"High {a['up_pct']:+.2f}% / low −{a['dn_pct']:.2f}% vs the 12:00 price · now {a['now_pct']:+.2f}% · "
                    f"{'✅ ≥ 0.5% move' if max(a['up_pct'], a['dn_pct']) >= 0.5 else 'no 0.5% move yet'}")
    st.caption("Paper-trade first. Not investment advice. Full tables: `68_AfternoonMove/VERDICT.md`, `67_ExpiryOTMLottery/VERDICT.md`.")
