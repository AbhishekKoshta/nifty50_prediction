"""fake_breakout_signal.py — renders the 🪤 Fake Breakout Watch tab.

Live rule from `54_ORB_HighVolBreakout` (market-analysis research repo): fading a
FALSE 60-minute opening-range breakout (price breaks one side, then reverses and
breaks the other) works on days whose PREDECESSOR was a QUIET day — not a volatile
one. PF 1.50 net, every year 2023-2026 positive, both walk-forward halves positive.
MARGINAL-GO (~21 trades/yr — real and robust, but thin; paper-trade before sizing
up). Full backtest + look-ahead sign-off:
`Algo_Nifty50/market-analysis/strategies/54_ORB_HighVolBreakout/VERDICT.md`.

Self-contained: takes the daily OHLC frame the app already loads (`teller.load_daily()`),
no new data pipeline. Tells you which DAYS the setup is armed for — the actual
break/reversal is watched live on your broker chart during the 09:15-10:15 opening
range and beyond, per the playbook below (no live intraday feed in this app).
"""
import pandas as pd
import streamlit as st

RANK_WINDOW = 100
RANK_MINP = 60


def _true_range(d: pd.DataFrame) -> pd.Series:
    prev_close = d["close"].shift(1)
    return pd.concat([
        d["high"] - d["low"],
        (d["high"] - prev_close).abs(),
        (d["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)


def _trailing_pct_rank(s: pd.Series, window: int = RANK_WINDOW, minp: int = RANK_MINP):
    """Percentile rank of the LAST value vs the `window` values strictly before it."""
    vals = s.dropna().values
    if len(vals) < minp + 1:
        return None
    hist = vals[-(window + 1):-1]
    if len(hist) < minp:
        return None
    return float((hist < vals[-1]).mean())


def compute_regime(daily: pd.DataFrame) -> dict:
    """daily: DataFrame indexed by date with open/high/low/close, sorted ascending
    (exactly what `teller.load_daily()` returns). Returns the fake-breakout-fade
    ARMED/IDLE call for the NEXT session, using the same causal ATR14-true-range,
    trailing-100-day percentile rank, bottom-tercile definition validated in
    `53_VolatilityClustering` / `54_ORB_HighVolBreakout` (the most recent row here
    IS "yesterday" relative to the session being classified — no further shift
    needed)."""
    d = daily[["open", "high", "low", "close"]].dropna().copy()
    d["atr14"] = _true_range(d).rolling(14).mean()
    pr = _trailing_pct_rank(d["atr14"])
    if pr is None:
        return {"available": False}
    if pr < 1 / 3:
        regime, armed = "Low", True
    elif pr < 2 / 3:
        regime, armed = "Mid", False
    else:
        regime, armed = "High", False
    return {
        "available": True,
        "armed": armed,
        "regime": regime,
        "pct_rank": pr,
        "as_of_date": d.index[-1],
    }


_CSS = """<style>
  .fb-card {border:1px solid rgba(128,128,128,.25); border-radius:14px;
            padding:16px 18px; margin-bottom:14px; background:rgba(128,128,128,.05);}
  .fb-armed {border-left:5px solid #16a34a;}
  .fb-idle  {border-left:5px solid rgba(128,128,128,.35); opacity:.75;}
  .fb-pill  {display:inline-block; padding:2px 10px; border-radius:999px;
             font-size:.72rem; font-weight:700; letter-spacing:.3px; vertical-align:middle;}
  .fb-p-arm {background:#16a34a22; color:#16a34a;}
  .fb-p-idle{background:#8080801a; color:#888;}
  .fb-kv    {color:#888; font-size:.85rem; margin-top:4px;}
  .fb-step  {margin:.25rem 0 .25rem 1.1rem;}
</style>"""


def render_fake_breakout(daily: pd.DataFrame):
    st.markdown(_CSS, unsafe_allow_html=True)
    st.caption(
        "Watches for **false opening-range breakouts** — the range breaks one side, then "
        "reverses and breaks the other. Research: 60-min opening range, fading the SECOND "
        "break works best after a QUIET predecessor day (not a volatile one) — PF 1.50 net, "
        "every year 2023-2026 positive, both walk-forward halves positive. **MARGINAL-GO** "
        "(~21 trades/yr — paper-trade before sizing up)."
    )

    info = compute_regime(daily)
    if not info["available"]:
        st.info("Not enough daily history yet to classify today's regime.")
        return

    as_of = info["as_of_date"]
    as_of_str = as_of.strftime("%d %b %Y") if hasattr(as_of, "strftime") else str(as_of)

    if info["armed"]:
        st.markdown(f"""
        <div class="fb-card fb-armed">
          <span class="fb-pill fb-p-arm">🟢 ARMED</span>
          <b>Today is a fake-breakout-watch day</b>
          <div class="fb-kv">Yesterday ({as_of_str}) was a QUIET day — realised range in the
          bottom third of its trailing-100-day distribution (percentile {info['pct_rank']:.0%}).
          Quiet-day predecessors are when opening-range breaks are most likely to be fakeouts.</div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("**Playbook for today:**")
        st.markdown(f"""
        <div class="fb-step">1. Mark the <b>09:15-10:15</b> opening range (high &amp; low).</div>
        <div class="fb-step">2. Wait for a break of ONE side. That's not the trade yet.</div>
        <div class="fb-step">3. If price reverses and breaks the <b>OTHER</b> side too, before
        3:00pm — that's the fade. Enter in the direction of the SECOND break.</div>
        <div class="fb-step">4. Stop = the extreme reached during the failed first move (the
        swing high/low of the break that got reversed).</div>
        <div class="fb-step">5. No fixed target — ride to the 3:25pm square-off.</div>
        <div class="fb-step">6. If the first break never reverses, there's no trade today — the
        breakout held.</div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div class="fb-card fb-idle">
          <span class="fb-pill fb-p-idle">⚪ IDLE</span>
          <b>Not a fake-breakout-watch day</b>
          <div class="fb-kv">Yesterday ({as_of_str}) was a <b>{info['regime']}</b>-vol day
          (percentile {info['pct_rank']:.0%} of its trailing-100-day range distribution) — the
          edge is validated on QUIET-predecessor days only. Sit this setup out today.</div>
        </div>
        """, unsafe_allow_html=True)

    with st.expander("Why this works / full research"):
        st.markdown(
            "Volatility clusters in **magnitude, not direction** — a quiet day tends to stay "
            "relatively quiet, so an opening-range break on a day that follows a quiet one is "
            "more often noise than the start of a real trend, and price snaps back through the "
            "other side. Trusting the breakout because *yesterday* was volatile does **not** "
            "work (fails walk-forward — `54_ORB_HighVolBreakout` Result 1) — only fading the "
            "reversal after a QUIET predecessor does. Full backtest, look-ahead sign-off and "
            "trade snapshots: `Algo_Nifty50/market-analysis/strategies/54_ORB_HighVolBreakout/"
            "VERDICT.md` in the research repo."
        )
