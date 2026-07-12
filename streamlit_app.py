import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

# ----------------------------------------------------------------------------
# Page setup
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="Nifty50 Probability Dashboard",
    page_icon="📊",
    layout="wide",
)

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
