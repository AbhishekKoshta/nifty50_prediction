"""
update_data.py
--------------
Fetches new Nifty 50 daily OHLC (from the day after the last stored date up to
today), appends it to the raw file, and refreshes the feature file the dashboard
reads.

Design principle: HISTORY IS FROZEN. Every row that already exists in
Nifty_Features.csv is preserved exactly. Only genuinely new dates are computed
with build_features(), and the previously-last row simply gets its (formerly
empty) next-day columns filled in. Nothing else in history changes.

Run locally:      python update_data.py
Run in CI:        called by .github/workflows/update-data.yml on a daily schedule

Data source: Yahoo Finance ticker ^NSEI via yfinance.
"""
import sys
import pandas as pd

from build_features import build_features

RAW_PATH = "Nifty50_till2025.csv"
FEAT_PATH = "Nifty_Features.csv"
TICKER = "^NSEI"

NEXT_COLS = [
    ("next_close", "close"),
    ("next_move_category", "move_category"),
    ("next_abs_directional_move_pct", "abs_directional_move_pct"),
    ("next_direction", "direction"),
    ("next_candle_color", "candle_color"),
    ("next_pattern", "pattern"),
    ("next_opening_category", "opening_category"),
]


def fetch_new_ohlc(last_date: str) -> pd.DataFrame:
    """Download Nifty 50 daily OHLC after `last_date` (exclusive) up to today.

    Returns a DataFrame with columns date, open, high, low, close (date as
    'YYYY-MM-DD' strings). Empty DataFrame if nothing new / on failure.
    """
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance not installed. Run: pip install yfinance", file=sys.stderr)
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])

    start = (pd.to_datetime(last_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Fetching {TICKER} from {start} to today ...")
    df = yf.download(TICKER, start=start, auto_adjust=False, progress=False)

    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])

    # yfinance may return a MultiIndex column frame for a single ticker
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.reset_index()[["Date", "Open", "High", "Low", "Close"]]
    df.columns = ["date", "open", "high", "low", "close"]
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df[df["date"] > last_date].reset_index(drop=True)
    return df


def _refresh_next_columns(feat: pd.DataFrame) -> pd.DataFrame:
    """Recompute the pure-lookahead columns across the whole frame.

    These are exact shifts, so recomputing them never alters any 'today' value —
    it only fills the boundary row whose next-day was previously unknown.
    """
    feat = feat.sort_values("date").reset_index(drop=True)
    for out_col, src_col in NEXT_COLS:
        feat[out_col] = feat[src_col].shift(-1)
    feat["target"] = feat["next_move_category"].isin(["High", "Exceptional"]).astype(int)
    return feat


def update(fetcher=fetch_new_ohlc, raw_path=RAW_PATH, feat_path=FEAT_PATH) -> int:
    existing_feat = pd.read_csv(feat_path)
    raw = pd.read_csv(raw_path)
    raw["date"] = pd.to_datetime(raw["date"]).dt.strftime("%Y-%m-%d")

    last_date = raw["date"].max()
    print(f"Last stored date: {last_date}")

    new_ohlc = fetcher(last_date)
    if new_ohlc.empty:
        print("Already up to date — no new trading days to add.")
        return 0

    print(f"Adding {len(new_ohlc)} new day(s): {new_ohlc['date'].min()} -> {new_ohlc['date'].max()}")

    # 1) Grow the raw OHLC file (existing rows win on any date overlap)
    full_ohlc = (
        pd.concat([raw, new_ohlc], ignore_index=True)
        .drop_duplicates(subset="date", keep="first")
        .sort_values("date")
        .reset_index(drop=True)
    )

    # 2) Build features on the FULL series (correct EMAs / rolling windows),
    #    then keep only the brand-new dated rows.
    built_full = build_features(full_ohlc)
    new_feat_rows = built_full[built_full["date"] > last_date].copy()

    # 3) Freeze history: append new rows to the untouched existing features,
    #    then refresh only the lookahead columns (fills the old last row).
    result = pd.concat([existing_feat, new_feat_rows], ignore_index=True)
    result = _refresh_next_columns(result)
    result = result[list(existing_feat.columns)]  # keep exact column order

    # 4) Persist
    full_ohlc.to_csv(raw_path, index=False)
    result.to_csv(feat_path, index=False)
    print(f"Done. Features now span {result['date'].min()} -> {result['date'].max()} "
          f"({len(result)} rows).")
    return len(new_feat_rows)


if __name__ == "__main__":
    update()
