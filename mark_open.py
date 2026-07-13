"""
mark_open.py
------------
Morning open-capture for the Teller resolver.

Runs at ~09:10 IST (CI), AFTER the 09:15 NSE open has printed, but BEFORE the
16:00 IST run that appends the completed daily bar. Its only job is to fetch
TODAY's opening price and drop it into `today_open.json` so the dashboard can
mark which strategy is ACTIVATED for the session.

Design principles (mirrors update_data.py's "history is frozen" rule):
  * It NEVER touches Nifty50_till2025.csv or Nifty_Features.csv — those stay
    frozen and are only grown by the afternoon run with a COMPLETED bar. A
    forming intraday bar must never be appended (it would corrupt history).
  * Self-healing: if Yahoo hasn't published today's bar yet (open unavailable),
    it writes nothing and exits 0 — the dashboard simply shows the pre-open
    CONDITIONAL plan, exactly as before. No stale file is left behind.
  * The JSON is validated against the teller anchor by `anchor_date`: the teller
    only applies today_open when its anchor_date matches the last completed day.

Run locally:   python mark_open.py
Run in CI:     called by .github/workflows/update-data.yml on the morning cron.

Data source: Yahoo Finance ticker ^NSEI via yfinance.
"""
import json
import sys
from datetime import datetime, timezone

import pandas as pd

FEAT_PATH = "Nifty_Features.csv"
OPEN_PATH = "today_open.json"
TICKER = "^NSEI"


def _anchor():
    """Last completed daily bar the teller will anchor on (date, close, low)."""
    feat = pd.read_csv(FEAT_PATH)
    feat["date"] = pd.to_datetime(feat["date"]).dt.strftime("%Y-%m-%d")
    row = feat.sort_values("date").iloc[-1]
    return row["date"], float(row["close"]), float(row["low"])


def fetch_today_open(after_date: str):
    """Return (date, open) for the most recent ^NSEI bar strictly after
    `after_date`, or None if Yahoo has no newer bar yet."""
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance not installed. Run: pip install yfinance", file=sys.stderr)
        return None

    df = yf.download(TICKER, period="5d", interval="1d",
                     auto_adjust=False, progress=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.reset_index()[["Date", "Open"]].dropna(subset=["Open"])
    df["date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    newer = df[df["date"] > after_date]
    if newer.empty:
        return None
    last = newer.sort_values("date").iloc[-1]
    return last["date"], float(last["Open"])


def mark(fetcher=fetch_today_open, feat_path=FEAT_PATH, open_path=OPEN_PATH) -> int:
    global FEAT_PATH
    FEAT_PATH = feat_path
    anchor_date, anchor_close, anchor_low = _anchor()
    print(f"Anchor (last completed day): {anchor_date}  close={anchor_close:,.2f}  low={anchor_low:,.2f}")

    got = fetcher(anchor_date)
    if got is None:
        print("Today's open not available from Yahoo yet — leaving plan pre-open (no file written).")
        return 0

    today, open_px = got
    payload = {
        "date": today,
        "open": round(open_px, 2),
        "anchor_date": anchor_date,
        "anchor_close": round(anchor_close, 2),
        "anchor_low": round(anchor_low, 2),
        "gap_pct": round((open_px - anchor_close) / anchor_close * 100, 3),
        "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(open_path, "w") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    print(f"Wrote {open_path}: {today} open={open_px:,.2f}  gap {payload['gap_pct']:+.2f}% vs {anchor_date}")
    return 1


if __name__ == "__main__":
    mark()
