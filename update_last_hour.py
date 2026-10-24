"""
update_last_hour.py
-------------------
Writes `last_hour.json` = the 14:30–15:30 IST candle of the most recent completed NIFTY
session, aggregated from 5-minute ^NSEI bars (yfinance). The 🎯 Strategy Book's
GOLD 09:15 strategy reads it for its next-day bias.

Same design rules as mark_open.py: never touches the daily history, self-healing (if
Yahoo has nothing usable it leaves the old file alone and exits 0), and the dashboard
only uses the file when `session` equals the last daily bar's date.

Run locally:   python update_last_hour.py
Run in CI:     .github/workflows/update-data.yml, afternoon run (after the close).
"""
import json
import sys
from datetime import datetime, timezone

import pandas as pd

OUT_PATH = "last_hour.json"
TICKER = "^NSEI"


def last_hour(df5: pd.DataFrame) -> dict | None:
    """df5: 5m OHLC indexed by IST timestamps. Returns the last session's 14:30–15:25 bars."""
    if df5 is None or df5.empty:
        return None
    ix = df5.index.tz_convert("Asia/Kolkata") if df5.index.tz is not None else df5.index.tz_localize("Asia/Kolkata")
    df5 = df5.set_axis(ix)
    day = df5.index[-1].date()
    lh = df5[(df5.index.date == day)].between_time("14:30", "15:25")
    if len(lh) < 10:                      # the backtest skipped days with < 10 of 12 bars
        return None
    return {"session": str(day), "open": round(float(lh["open"].iloc[0]), 2),
            "high": round(float(lh["high"].max()), 2), "low": round(float(lh["low"].min()), 2),
            "close": round(float(lh["close"].iloc[-1]), 2), "bars": int(len(lh)),
            "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}


def main() -> int:
    try:
        import yfinance as yf
        df = yf.download(TICKER, period="5d", interval="5m", auto_adjust=False, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)[["open", "high", "low", "close"]].dropna()
        rec = last_hour(df)
    except Exception as e:  # noqa: BLE001
        print(f"last-hour fetch failed: {e}")
        return 0
    if rec is None:
        print("no usable last-hour candle — leaving last_hour.json unchanged")
        return 0
    with open(OUT_PATH, "w") as f:
        json.dump(rec, f, indent=2)
    print("wrote", OUT_PATH, rec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
