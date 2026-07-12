"""
build_features.py
-----------------
Takes a raw daily OHLC DataFrame for the Nifty 50 (columns: date, open, high,
low, close) and returns the full feature table used by the dashboard
(Nifty_Features.csv schema).

This is a cleaned-up, complete version of the original Nifty50_Features.py. It
reproduces every dashboard-critical column exactly and documents the few legacy
columns whose original logic was inferred from the data.

Usage:
    from build_features import build_features
    feat = build_features(ohlc_df)
"""
import pandas as pd
import numpy as np

# Column order of Nifty_Features.csv (kept stable so appends never shift columns)
FEATURE_COLUMNS = [
    "date", "close", "high", "low", "open", "day_name", "market_move", "direction",
    "pattern", "candle_color", "abs_directional_move", "abs_directional_move_pct",
    "move_category", "prev_2_day_seq", "prev_3_day_seq", "prev_4_day_seq",
    "prev_5_day_seq", "abs_day_move", "pct_abs_day_move", "day_move_category",
    "pct_move_1d", "pct_move_3d", "pct_move_5d", "flag_same_closing_2_days",
    "flag_same_closing_3_days", "flag_close_range", "flag_above_5EMA",
    "flag_above_8EMA", "flag_above_13EMA", "flag_above_50EMA", "flag_above_100EMA",
    "flag_above_200EMA", "opening_category", "3m_high", "3m_low", "pts_from_3m_high",
    "pts_from_3m_low", "pct_from_3m_high", "pct_from_3m_low",
    "pct_away_from_all_time_high", "flag_all_time_high", "next_close",
    "next_move_category", "next_abs_directional_move_pct", "next_direction",
    "next_candle_color", "next_pattern", "next_opening_category", "target",
    "flag_prev_at_all_time_high", "inside_1_day", "inside_for_consec_2_days",
    "inside_for_consec_3_days", "inside_for_consec_4_days", "inside_for_consec_5_days",
    "inside_for_consec_6_days", "inside_for_consec_7_days",
]


def classify_candle(row):
    o, h, l, c = row["open"], row["high"], row["low"], row["close"]
    body = abs(c - o)
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l

    if body == 0 or (body / o < 0.002):
        base, color = "Doji", "Gray"
    elif c > o:
        base, color = "Bullish", "Green"
    else:
        base, color = "Bearish", "Red"

    if body > 0:
        if upper_shadow / body < 0.05 and lower_shadow / body < 0.05:
            return ("Bullish Marubozu", "Green") if c > o else ("Bearish Marubozu", "Red")
    if body > 0:
        if lower_shadow >= 2 * body and upper_shadow < 0.5 * body:
            return ("Hammer", "Green") if base == "Bullish" else ("Hanging Man", "Red")
    if body > 0:
        if upper_shadow >= 2 * body and lower_shadow < 0.5 * body:
            return ("Inverted Hammer", "Green") if base == "Bullish" else ("Shooting Star", "Red")
    return base, color


def categorize_move(pct):
    a = abs(pct)
    if a < 0.5:
        return "Low"
    elif a < 1.0:
        return "Moderate"
    elif a < 1.5:
        return "High"
    else:
        return "Exceptional"


def classify_market_trend(change):
    return "Down" if change < 0 else "Up"


def opening_category(row):
    if pd.isna(row["prev_close"]):
        return "N/A"
    gap = (row["open"] - row["prev_close"]) / row["prev_close"] * 100
    if gap > 0.95:
        return "Large Gap Up"
    elif gap > 0.44:
        return "Gap Up"
    elif gap < -0.95:
        return "Large Gap Down"
    elif gap < -0.44:
        return "Gap Down"
    return "Flat"


def build_features(ohlc: pd.DataFrame) -> pd.DataFrame:
    """Build the full 57-column feature frame from raw OHLC."""
    d = ohlc.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.sort_values("date").reset_index(drop=True)

    d["day_name"] = d["date"].dt.day_name()
    d["prev_close"] = d["close"].shift(1)
    d["market_move"] = d["close"] - d["prev_close"]
    d["direction"] = d["market_move"].apply(classify_market_trend)
    d[["pattern", "candle_color"]] = d.apply(lambda r: pd.Series(classify_candle(r)), axis=1)

    d["abs_directional_move"] = (d["open"] - d["close"]).abs()
    d["abs_directional_move_pct"] = (d["open"] - d["close"]).abs() / d["open"] * 100
    d["move_category"] = d["abs_directional_move_pct"].apply(categorize_move)

    d["abs_day_move"] = d["high"] - d["low"]
    d["pct_abs_day_move"] = d["abs_day_move"] / d["open"] * 100
    d["day_move_category"] = d["pct_abs_day_move"].apply(categorize_move)

    # Previous-days move-category sequences (most recent first)
    mc = d["move_category"]
    for n in [2, 3, 4, 5]:
        seq = mc.shift(1)
        for k in range(2, n + 1):
            seq = seq + " " + mc.shift(k)
        d[f"prev_{n}_day_seq"] = seq

    d["pct_move_1d"] = d["close"].pct_change(1) * 100
    d["pct_move_3d"] = d["close"].pct_change(3) * 100
    d["pct_move_5d"] = d["close"].pct_change(5) * 100

    # flag_same_closing_N_days: the last N closes sit within 0.25% of each other
    for n in [2, 3]:
        w = d["close"].rolling(n)
        d[f"flag_same_closing_{n}_days"] = (
            (w.max() - w.min()) / w.min() * 100 <= 0.25
        ).astype(int)

    # flag_close_range: both of the previous 2 days had (high-low)/close <= 0.2%
    def close_in_range(idx, df, threshold=0.2):
        if idx < 2:
            return 0
        prev = df.iloc[idx - 2:idx]
        ok = all(((r["high"] - r["low"]) / r["close"] * 100) <= threshold
                 for _, r in prev.iterrows())
        return 1 if ok else 0
    d["flag_close_range"] = [close_in_range(i, d) for i in range(len(d))]

    # EMAs and above/below flags
    for span, tag in [(5, "5"), (8, "8"), (13, "13"), (50, "50"), (100, "100"), (200, "200")]:
        ema = d["close"].ewm(span=span, adjust=False).mean()
        d[f"flag_above_{tag}EMA"] = (d["close"] > ema).astype(int)

    d["opening_category"] = d.apply(opening_category, axis=1)

    # 3-month (~63 trading day) rolling high/low
    win = 63
    d["3m_high"] = d["high"].rolling(win, min_periods=1).max()
    d["3m_low"] = d["low"].rolling(win, min_periods=1).min()
    d["pts_from_3m_high"] = d["3m_high"] - d["close"]
    d["pts_from_3m_low"] = d["close"] - d["3m_low"]
    d["pct_from_3m_high"] = d["pts_from_3m_high"] / d["3m_high"] * 100
    d["pct_from_3m_low"] = d["pts_from_3m_low"] / d["3m_low"] * 100

    # All-time-high tracking
    cummax = d["close"].cummax()
    d["pct_away_from_all_time_high"] = (cummax - d["close"]) / d["close"] * 100
    d["flag_all_time_high"] = (d["close"] == cummax).astype(int)
    d["flag_prev_at_all_time_high"] = (d["prev_close"] == cummax.shift(1)).astype(int)

    # Inside-bar detection (today's range engulfed by yesterday's)
    inside = ((d["high"] < d["high"].shift(1)) & (d["low"] > d["low"].shift(1))).astype(int)
    d["inside_1_day"] = inside
    for n in range(2, 8):
        d[f"inside_for_consec_{n}_days"] = (inside.rolling(n).sum() == n).astype(int)

    # Next-day (lookahead) columns
    d["next_close"] = d["close"].shift(-1)
    d["next_move_category"] = d["move_category"].shift(-1)
    d["next_abs_directional_move_pct"] = d["abs_directional_move_pct"].shift(-1)
    d["next_direction"] = d["direction"].shift(-1)
    d["next_candle_color"] = d["candle_color"].shift(-1)
    d["next_pattern"] = d["pattern"].shift(-1)
    d["next_opening_category"] = d["opening_category"].shift(-1)

    # target: is the NEXT day a big move (High or Exceptional)?
    d["target"] = d["next_move_category"].isin(["High", "Exceptional"]).astype(int)

    # Emit date as plain YYYY-MM-DD string to match the existing file
    d["date"] = d["date"].dt.strftime("%Y-%m-%d")
    d = d.drop(columns=["prev_close"])
    return d[FEATURE_COLUMNS]
