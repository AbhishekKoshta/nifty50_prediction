import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

import os

from teller import (build_plan, load_daily, load_today_open,
                    DATA_FILE, OPEN_FILE, GAPFADE_GMIN, GAPHALF_GMIN)

# ----------------------------------------------------------------------------
# Page setup
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="Nifty50 Probability Dashboard",
    page_icon="📊",
    layout="wide",
)

# Optional visitor tracking (Google Analytics 4). No-op unless a GA4 Measurement
# ID is set via st.secrets["ga_measurement_id"] or the GA_MEASUREMENT_ID env var.
from analytics import inject_ga  # noqa: E402
inject_ga()

# ----------------------------------------------------------------------------
# TELLER — the morning plan, pinned to the top.
# Given the latest CLOSE it prints what to do at the NEXT open and the exact
# gap-up LEVEL above which the validated GO shorts trigger. Surfaces the GO edges
# plus two MARGINAL satellites (BearRallyFade short, MarubozuGapReclaim long).
# ----------------------------------------------------------------------------
st.markdown("""
<style>
  .teller-card {border:1px solid rgba(128,128,128,.25); border-radius:14px;
                padding:12px 16px; margin-bottom:10px; background:rgba(128,128,128,.05);}
  .active {border-left:5px solid #22c55e;
           background:linear-gradient(90deg, rgba(34,197,94,.16), rgba(16,185,129,.06));
           box-shadow:0 0 0 1px #22c55e55, 0 3px 14px rgba(34,197,94,.28);}
  .armed  {border-left:5px solid #16a34a;}
  .ifgap  {border-left:5px solid #f59e0b;}
  .passed {border-left:5px solid rgba(128,128,128,.35); opacity:.6;}
  .idle   {border-left:5px solid rgba(128,128,128,.35); opacity:.72;}
  .lvl    {font-size:1.7rem; font-weight:700; letter-spacing:-.5px;}
  .pill   {display:inline-block; padding:2px 10px; border-radius:999px;
           font-size:.72rem; font-weight:700; letter-spacing:.3px; vertical-align:middle;}
  .p-act  {background:#22c55e33; color:#15803d;}
  .p-arm  {background:#16a34a22; color:#16a34a;}
  .p-gap  {background:#f59e0b22; color:#d97706;}
  .p-pass {background:#8080801a; color:#888;}
  .p-idle {background:#8080801a; color:#888;}
  .p-long {background:#2563eb22; color:#2563eb;}
  .p-short{background:#dc262622; color:#dc2626;}
  .kv     {color:#888; font-size:.82rem;}
  .headline {font-size:1.0rem; font-weight:600; margin:.3rem 0;}
</style>
""", unsafe_allow_html=True)


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


@st.cache_data(ttl=3600)
def _cached_daily(data_mtime: float):
    # mtime is a cache key only — when the 16:00 CI run rewrites the daily feed
    # the cache invalidates and the indicators rebuild. build_plan() itself is
    # cheap (last-row read) so it runs live, letting a manual open resolve instantly.
    return load_daily()


def get_daily():
    return _cached_daily(_mtime(DATA_FILE))


def _side_pill(side: str) -> str:
    cls = "p-long" if side == "LONG" else "p-short"
    return f'<span class="pill {cls}">{side}</span>'


def _status_pill(status: str) -> str:
    m = {"ACTIVATED": ("p-act", "🎉 ACTIVE NOW"),
         "ARMED": ("p-arm", "🟢 ARMED"),
         "CONDITIONAL": ("p-gap", "🟡 IF GAP-UP"),
         "PASSED": ("p-pass", "⚫ PASSED"),
         "IDLE": ("p-idle", "⚪ IDLE")}
    cls, label = m[status]
    return f'<span class="pill {cls}">{label}</span>'


def _render_signal(s: dict):
    css = {"ACTIVATED": "active", "ARMED": "armed", "CONDITIONAL": "ifgap",
           "PASSED": "passed", "IDLE": "idle"}[s["status"]]
    parts = [f'<div class="teller-card {css}">']
    parts.append(
        f'{_status_pill(s["status"])} {_side_pill(s["side"])} '
        f'<b>{s["name"]}</b> <span class="kv">· {s["horizon"]} · {s["stats"]}</span>')
    parts.append(f'<div class="headline">{s["headline"]}</div>')
    if s["status"] == "CONDITIONAL" and s.get("level"):
        parts.append(f'<div class="lvl">▸ {s["level"]:,.0f}</div>'
                     f'<div class="kv">trigger: {s["trigger"]}</div>')
    if s["status"] in ("ACTIVATED", "ARMED", "CONDITIONAL"):
        for lbl, key in (("Entry", "entry"), ("Stop", "stop"), ("Target", "target")):
            if s.get(key):
                parts.append(f'<div class="kv"><b>{lbl}:</b> {s[key]}</div>')
        if s.get("note"):
            parts.append(f'<div class="kv"><i>{s["note"]}</i></div>')
    else:  # PASSED / IDLE
        parts.append(f'<div class="kv">{s["trigger"]}</div>')
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def render_teller():
    try:
        daily = get_daily()
    except Exception as e:  # noqa: BLE001 — never let the teller break the dashboard
        st.warning(f"Morning teller unavailable: {e}")
        return

    captured = load_today_open()                       # from the 09:10 CI run, if any
    anchor = build_plan(daily)["context"]              # unresolved — gives anchor date/close/low
    anchor_date, anchor_close = anchor["date"], anchor["close"]
    captured_valid = bool(captured and str(captured.get("anchor_date")) == anchor_date)

    st.title("📈 NIFTY Teller — the morning plan")
    st.caption(f"What to do at the open **after {anchor_date}** · anchor close "
               f"**{anchor_close:,.1f}** · rebuilds each market close, then resolves at ~09:10 "
               f"once the open prints. Validated **GO** edges plus two **MARGINAL** satellites "
               f"(BearRallyFade, MarubozuGapReclaim) — take those small.")

    # ---- what-if: type an open price and see which edges activate ---------------
    with st.expander("🔬 Check an open price — see which edges activate",
                     expanded=not captured_valid):
        col1, col2 = st.columns([3, 2])
        default_open = float(captured["open"]) if captured_valid else round(anchor_close, 1)
        manual_open = col1.number_input(
            "NIFTY open price", min_value=0.0, value=default_open, step=5.0, format="%.1f",
            help="Enter the 09:15 open (or any hypothetical) to resolve the plan against it.")
        use_manual = col2.toggle("Use this open", value=False,
                                 help="On = resolve against the number above. "
                                      "Off = use the auto-captured open (or the pre-open plan).")

    is_manual = bool(use_manual)
    if is_manual:
        today_open = {"date": "manual", "open": float(manual_open),
                      "anchor_date": anchor_date, "anchor_close": anchor_close}
    else:
        today_open = captured if captured_valid else None

    plan = build_plan(daily, today_open)
    c = plan["context"]
    o = c.get("today_open")

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Close", f"{c['close']:,.0f}")
    m2.metric("Today o→c", f"{c['ret']:+.2f}%")
    m3.metric("ATR14", f"{c['atr14']:,.0f}")
    m4.metric("RSI(2)", f"{c['rsi2']:.1f}")
    m5.metric("vs 20-DMA", "above" if c["uptrend20"] else "below")
    m6.metric("vs 200-DMA", "above" if c["above200"] else "below")

    if o:
        gap = o["open"] - c["close"]
        arrow = "GAP-UP" if o["gap_pct"] >= 0 else "GAP-DOWN"
        active_n = sum(1 for s in plan["signals"] if s["status"] == "ACTIVATED")
        lead = "**What-if open" if is_manual else "**Open printed"
        banner = (f"{lead}: {o['open']:,.0f}  ({o['gap_pct']:+.2f}%, {arrow} {gap:+,.0f} pt "
                  f"vs {c['close']:,.0f}).**  Plan RESOLVED — "
                  f"**{active_n} edge{'s' if active_n != 1 else ''} active"
                  f"{' (hypothetical)' if is_manual else ' now'}.**")
        (st.success if active_n else st.info)(banner)
    else:
        gf = c["gap_up_levels"]["gapfade_0.30pct"]
        gh = c["gap_up_levels"]["gaphalf_0.35pct"]
        st.info(
            f"**If NIFTY opens GAP-UP → go SHORT.**  "
            f"GapFade-short triggers above **{gf:,.0f}** (≥{GAPFADE_GMIN:.2f}% gap, needs close>20DMA "
            f"— currently {'✔ ON' if c['uptrend20'] else '✘ off'}).  "
            f"GapHalfFill-short triggers above **{gh:,.0f}** (≥{GAPHALF_GMIN:.2f}% gap, half-gap target)."
        )

    order = {"ACTIVATED": 0, "ARMED": 1, "CONDITIONAL": 2, "PASSED": 3, "IDLE": 4}
    sigs = sorted(plan["signals"], key=lambda x: order[x["status"]])
    active = [s for s in sigs if s["status"] == "ACTIVATED"]
    armed = [s for s in sigs if s["status"] == "ARMED"]
    cond = [s for s in sigs if s["status"] == "CONDITIONAL"]
    stood_down = [s for s in sigs if s["status"] in ("PASSED", "IDLE")]

    if active:
        st.subheader("🎉 Active now — trade at the open")
        for s in active:
            _render_signal(s)
    if armed:
        st.subheader("🟢 Armed at the open")
        for s in armed:
            _render_signal(s)
    if cond:
        st.subheader("🟡 Conditional on the open (gap)")
        for s in cond:
            _render_signal(s)
    if stood_down:
        label = "Passed / idle" if o else "Idle edges"
        with st.expander(f"⚪ {label} ({len(stood_down)}) — no trade for these today"):
            for s in stood_down:
                _render_signal(s)
    st.caption("⚠️ Gap shorts are fill-sensitive — use a limit at/just below the open; the edge lives "
               "in the first minute. Costs ~10 pt/round-trip (Zerodha futures, lot 75), net figures. "
               "Educational use only — not financial advice.")


render_teller()
st.divider()

# ----------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------
@st.cache_data
def load_data():
    tmp = pd.read_csv("Nifty_Features.csv")
    tmp = tmp.sort_values(by="date")
    tmp["date"] = pd.to_datetime(tmp["date"])
    return tmp


data = load_data()

# ----------------------------------------------------------------------------
# Human-friendly labels for the raw category codes in the data
# ----------------------------------------------------------------------------
COLOR_MEANING = {
    "Green": "an UP day (closed higher than it opened)",
    "Red": "a DOWN day (closed lower than it opened)",
    "Gray": "a FLAT day (closed within 0.2% of the open)",
}

OPENING_MEANING = {
    "Flat": "opens roughly flat (near yesterday's close)",
    "Gap Up": "opens higher than yesterday's close (gap up)",
    "Gap Down": "opens lower than yesterday's close (gap down)",
    "Large Gap Up": "opens with a BIG jump up",
    "Large Gap Down": "opens with a BIG drop down",
}

MOVE_MEANING = {
    "Low": "a quiet day (moves less than 0.5%)",
    "Moderate": "a normal day (moves 0.5% to 1.0%)",
    "High": "a big-move day (moves 1.0% to 1.5%)",
    "Exceptional": "a wild day (moves 1.5% or more)",
}

# A consistent, readable order for each category type
COLOR_ORDER = ["Green", "Red", "Gray"]
MOVE_ORDER = ["Low", "Moderate", "High", "Exceptional"]
OPENING_ORDER = ["Flat", "Gap Up", "Gap Down", "Large Gap Up", "Large Gap Down"]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def counts_out_of_n(proportions: dict, n: int = 10) -> dict:
    """Turn probabilities into whole numbers out of n using the largest-remainder
    method, so the numbers always add up to exactly n."""
    raw = {k: v * n for k, v in proportions.items()}
    floors = {k: int(np.floor(x)) for k, x in raw.items()}
    remaining = n - sum(floors.values())
    fracs = sorted(raw.items(), key=lambda kv: kv[1] - np.floor(kv[1]), reverse=True)
    for i in range(remaining):
        floors[fracs[i % len(fracs)][0]] += 1
    return floors


def order_props(props: pd.Series, order: list) -> pd.Series:
    """Reindex a proportion series into a friendly fixed order, dropping missing."""
    keep = [c for c in order if c in props.index]
    extra = [c for c in props.index if c not in order]
    return props.reindex(keep + extra)


def plain_english_line(props: pd.Series, noun: str = "days") -> str:
    """Build the headline 'Out of every 10 days, expect ...' sentence."""
    if props.empty:
        return "Not enough data in this selection to estimate."
    n10 = counts_out_of_n(props.to_dict(), 10)
    parts = []
    for cat in props.index:
        c = n10[cat]
        if c == 0:
            parts.append(f"fewer than 1 **{cat}**")
        else:
            parts.append(f"**{c} {cat}**")
    if len(parts) == 1:
        joined = parts[0]
    else:
        joined = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    return f"Out of every 10 {noun}, you can expect roughly {joined}."


def detail_bullets(props: pd.Series, meaning: dict) -> str:
    """Per-category plain-English bullets with the exact percentage."""
    lines = []
    for cat, p in props.items():
        pct = round(p * 100)
        desc = meaning.get(cat, cat)
        lines.append(f"- **{cat}** — {desc}: about **{pct}%** of the time")
    return "\n".join(lines)


def sample_note(n_rows: int) -> None:
    """Warn the reader when the sample behind a probability is small."""
    if n_rows == 0:
        st.info("No matching days in the selected range — try widening the dates.")
    elif n_rows < 30:
        st.warning(
            f"⚠️ Only **{n_rows}** matching days here. That's a small sample, so treat "
            "these numbers as a rough hint, not a reliable probability."
        )
    else:
        st.caption(f"Based on {n_rows:,} matching trading days.")


def probability_view(subset: pd.DataFrame, column: str, order: list,
                     meaning: dict, noun: str = "days"):
    """Render one full probability block: headline + chart + bullets + sample note."""
    n_rows = int(subset[column].notna().sum())
    if n_rows == 0:
        sample_note(0)
        return
    props = order_props(subset[column].value_counts(normalize=True), order)
    st.success(plain_english_line(props, noun=noun))
    left, right = st.columns([1, 1])
    with left:
        st.bar_chart(props)
    with right:
        st.markdown(detail_bullets(props, meaning))
    sample_note(n_rows)


# ----------------------------------------------------------------------------
# Sidebar controls
# ----------------------------------------------------------------------------
st.sidebar.header("⚙️ Filters")

start_date = st.sidebar.date_input("Start date", data["date"].min())
end_date = st.sidebar.date_input("End date", data["date"].max())

weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
chosen_days = st.sidebar.multiselect(
    "Weekdays to include", weekdays, default=weekdays,
    help="Look only at certain days of the week — e.g. is Friday more bullish?",
)

filtered_data = data[
    (data["date"] >= pd.to_datetime(start_date))
    & (data["date"] <= pd.to_datetime(end_date))
]
if chosen_days:
    filtered_data = filtered_data[filtered_data["day_name"].isin(chosen_days)]

# ----------------------------------------------------------------------------
# Header
# ----------------------------------------------------------------------------
st.title("📊 Nifty50 Probability Dashboard")
st.markdown(
    "A plain-English look at how the Nifty 50 has behaved historically. "
    "Every probability below is also written as **\"out of 10 days\"** so you don't "
    "need a stats background to read it."
)

with st.expander("❓ How to read this dashboard (start here)"):
    st.markdown(
        """
These numbers come from **history** — thousands of past Nifty 50 trading days. They tell
you how *often* something happened before, which is a useful base rate. They are **not a
prediction** of any single day, and past patterns can break.

**The colour code for a day:**
- 🟢 **Green** = the market closed *higher* than it opened (an up day)
- 🔴 **Red** = the market closed *lower* than it opened (a down day)
- ⚪ **Gray** = the market barely moved — closed within **0.2%** of the open

**How big the day was (the "move"):**
- **Low** = moved less than 0.5% (quiet)
- **Moderate** = moved 0.5%-1.0% (normal)
- **High** = moved 1.0%-1.5% (big)
- **Exceptional** = moved 1.5% or more (wild)

Use the sidebar to narrow the date range or pick specific weekdays.
        """
    )

# ----------------------------------------------------------------------------
# Snapshot metrics
# ----------------------------------------------------------------------------
st.subheader("Snapshot of the selected period")
total_days = len(filtered_data)
if total_days > 0:
    color_share = filtered_data["candle_color"].value_counts(normalize=True)
    up_pct = round(color_share.get("Green", 0) * 100)
    down_pct = round(color_share.get("Red", 0) * 100)
    flat_pct = round(color_share.get("Gray", 0) * 100)
    avg_move = filtered_data["abs_directional_move_pct"].mean()

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Trading days", f"{total_days:,}")
    m2.metric("🟢 Up days", f"{up_pct}%")
    m3.metric("🔴 Down days", f"{down_pct}%")
    m4.metric("⚪ Flat days", f"{flat_pct}%")
    m5.metric("Avg daily move", f"{avg_move:.2f}%")
else:
    st.info("No days match your current filters. Widen the date range or add weekdays.")

st.divider()

# ----------------------------------------------------------------------------
# View 1 - How does a day usually close?
# ----------------------------------------------------------------------------
st.header("1) How does a day usually close - green, red or gray?")
probability_view(filtered_data, "candle_color", COLOR_ORDER, COLOR_MEANING)

st.divider()

# ----------------------------------------------------------------------------
# View 2 - How does the market usually open?
# ----------------------------------------------------------------------------
st.header("2) How does the market usually open?")
probability_view(filtered_data, "opening_category", OPENING_ORDER, OPENING_MEANING)

st.divider()

# ----------------------------------------------------------------------------
# View 3 - How big is a typical day?
# ----------------------------------------------------------------------------
st.header("3) How big is a typical day's move?")
probability_view(filtered_data, "move_category", MOVE_ORDER, MOVE_MEANING)

st.divider()

# ----------------------------------------------------------------------------
# View 4 - What happens after 3 quiet, sideways days?
# ----------------------------------------------------------------------------
st.header("4) After the market sits flat for 3 days, what comes next?")
st.caption(
    "Looking only at days where the market closed within ~0.25% of itself for 3 days in a "
    "row - then measuring the *next* day's move size."
)
flat3 = filtered_data[filtered_data["flag_same_closing_3_days"] == 1]
probability_view(flat3, "next_move_category", MOVE_ORDER, MOVE_MEANING)

st.divider()

# ----------------------------------------------------------------------------
# View 5 - Flat for 3 days AND very little movement
# ----------------------------------------------------------------------------
st.header("5) After 3 flat days with barely any movement, what comes next?")
st.caption(
    "The quietest possible setup: 3 days of sideways closing *and* a low-movement day. "
    "Does a big move tend to follow the calm?"
)
coiled = filtered_data[
    (filtered_data["flag_same_closing_3_days"] == 1)
    & (filtered_data["prev_2_day_seq"] == "Low Low")
    & (filtered_data["move_category"] == "Low")
]
probability_view(coiled, "next_move_category", MOVE_ORDER, MOVE_MEANING)

st.divider()

# ----------------------------------------------------------------------------
# View 6 - What follows a big-move day?
# ----------------------------------------------------------------------------
st.header("6) After a big-move (High) day, how big is the next day?")
after_high = filtered_data[filtered_data["move_category"] == "High"]
probability_view(after_high, "next_move_category", MOVE_ORDER, MOVE_MEANING)

st.divider()

# ----------------------------------------------------------------------------
# View 7 - How does the market open after a big-move day?
# ----------------------------------------------------------------------------
st.header("7) After a big-move (High) day, how does the next day open?")
probability_view(after_high, "opening_category", OPENING_ORDER, OPENING_MEANING, noun="such days")

st.divider()

# ----------------------------------------------------------------------------
# View 8 - Day-of-week personality (new)
# ----------------------------------------------------------------------------
st.header("8) Does each weekday have its own personality?")
st.caption("Share of green (up) days for each weekday in the selected period.")
if total_days > 0:
    dow = (
        filtered_data.assign(is_green=(filtered_data["candle_color"] == "Green"))
        .groupby("day_name")["is_green"]
        .mean()
        .reindex(weekdays)
        .dropna()
    )
    if not dow.empty:
        dow_pct = (dow * 100).round(0)
        st.bar_chart(dow_pct.rename("% up days"))
        best = dow_pct.idxmax()
        worst = dow_pct.idxmin()
        st.markdown(
            f"In this period, **{best}** has been the most bullish weekday "
            f"(**{int(dow_pct[best])}%** green days) and **{worst}** the least "
            f"(**{int(dow_pct[worst])}%** green days). "
            "Out of 10 such weekdays, that's about "
            f"**{round(dow_pct[best] / 10)} green** on {best} vs "
            f"**{round(dow_pct[worst] / 10)} green** on {worst}."
        )
else:
    st.info("No data to show for the weekday view.")

st.divider()

# ----------------------------------------------------------------------------
# Distribution charts
# ----------------------------------------------------------------------------
st.header("9) The spread of daily & multi-day moves")
st.caption("How percentage moves are distributed. Fatter tails = more surprise days.")

if total_days > 0:
    c1, c2, c3 = st.columns(3)
    for col, title, container in [
        ("abs_directional_move_pct", "1-day move %", c1),
        ("pct_move_3d", "3-day move %", c2),
        ("pct_move_5d", "5-day move %", c3),
    ]:
        with container:
            fig, ax = plt.subplots()
            sns.histplot(filtered_data[col].dropna(), kde=True, ax=ax, color="#4C78A8")
            ax.set_xlabel(title)
            ax.set_ylabel("Number of days")
            ax.set_title(title)
            st.pyplot(fig)
            plt.close(fig)

st.divider()

# ----------------------------------------------------------------------------
# Raw data
# ----------------------------------------------------------------------------
with st.expander("🔎 See the underlying data"):
    st.dataframe(filtered_data)

# ----------------------------------------------------------------------------
# Disclaimer
# ----------------------------------------------------------------------------
st.divider()
st.caption(
    "⚠️ **Educational use only - not financial advice.** Every figure here is a historical "
    "base rate, not a forecast of any specific day. Markets change and past patterns can "
    "and do break. Do your own research before trading."
)
