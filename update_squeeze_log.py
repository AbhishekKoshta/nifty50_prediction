#!/usr/bin/env python3
"""
Auto-update the Squeeze-ORB trade log with the LATEST completed trigger day.

Runs in the afternoon (after update_data.py has appended today's completed daily
bar). It looks at the latest completed trading day in the daily feed, checks
whether the volatility squeeze was armed for it (prior 5-day span <= 25th pctile
of the trailing 60d — the same filter as strategies/38_SqueezeORB), and if so
pulls that day's 5-minute NIFTY bars from yfinance and simulates the 30-min
opening-range breakout long:

    Opening range = first 30 min (six 5-min bars, 09:15-09:45).
    First break of OR-high after 09:45 (no new entry after 15:00).
    Fill = worse-of {OR-high, bar open} (buy-stop).  Stop = OR-low.
    No target; ride to the 15:25 square-off (or OR-low stop first).  One trade/day.

If a completed trade results it is appended to trade_logs/SqueezeORB.csv
(idempotent — never duplicates a date), so the dashboard's "Last recorded trade"
on the Squeeze card always reflects the most recent trigger. Only the latest
completed day is processed each run (no back-fill): the daily workflow keeps it
current going forward, and missed history isn't wanted.

Self-contained (pandas / numpy / yfinance only — all already in the workflow).
No look-ahead: squeeze from the prior-day span; entry via buy-stop; exit checked
intrabar. Costs (FEE_PTS) are applied by the dashboard, not stored here.
"""
import os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DAILY_FILE = os.path.join(HERE, "Nifty_Features.csv")
LOG_FILE = os.path.join(HERE, "trade_logs", "SqueezeORB.csv")
LOG_COLS = ["date", "side", "entry_dt", "entry", "exit_dt", "exit",
            "sl", "target", "pnl", "bars_held", "exit_reason"]
TICKER = "^NSEI"
IST = "Asia/Kolkata"
SQUEEZE_Q = 0.25          # 5d-span percentile (bottom 25%) over trailing 60d


def latest_armed_day():
    """(date, armed) for the latest completed session in the daily feed.

    armed == squeeze on = prior 5-day span (as % of close) <= 25th pctile of the
    trailing 60 days. Uses .shift(1) so the decision only sees data up to the
    PRIOR close (no look-ahead) — identical to strategies/38_SqueezeORB.
    """
    d = pd.read_csv(DAILY_FILE, usecols=["date", "high", "low", "close"])
    d["date"] = pd.to_datetime(d["date"])
    d = d.sort_values("date").reset_index(drop=True)
    span5 = (d["high"].rolling(5).max() - d["low"].rolling(5).min()) / d["close"] * 100
    span5_prev = span5.shift(1)
    span5_thr = span5.shift(1).rolling(60, min_periods=20).quantile(SQUEEZE_Q)
    i = len(d) - 1
    day = d["date"].iloc[i].date()
    sp, th = span5_prev.iloc[i], span5_thr.iloc[i]
    armed = bool(pd.notna(sp) and pd.notna(th) and sp <= th)
    return day, armed


def fetch_5m(day):
    """That day's 5-minute NIFTY bars from yfinance, IST-indexed, or None."""
    import yfinance as yf
    start = pd.Timestamp(day)
    end = start + pd.Timedelta(days=1)
    df = yf.download(TICKER, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
                     interval="5m", auto_adjust=False, progress=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df[["Open", "High", "Low", "Close"]].copy()
    if df.index.tz is None:                       # be robust on a UTC CI runner
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(IST)
    df = df[[t.date() == day for t in df.index]]
    return df if len(df) else None


def orb_long_trade(day, bars):
    """Simulate the squeeze ORB long for `day`; return a trade dict or None."""
    idx = bars.index
    hm = idx.strftime("%H:%M")
    o = bars["Open"].values; hi = bars["High"].values
    lo = bars["Low"].values; c = bars["Close"].values
    n = len(bars)
    if n < 9:
        return None
    or_hi = float(hi[:6].max()); or_lo = float(lo[:6].min())   # first 30 min (09:15-09:45)
    for i in range(6, n):
        if hm[i] > "15:00":              # no new entry after 15:00
            break
        if hi[i] < or_hi:                # wait for the OR-high break (long)
            continue
        fill = max(or_hi, o[i]) if o[i] > or_hi else or_hi      # buy-stop, worse-of fill
        stop = or_lo; ei = i; xi = i; exit_px = None; reason = None
        for j in range(i, n):
            if hm[j] >= "15:25":         # EOD square-off
                exit_px = c[j]; reason = "EOD"; xi = j; break
            if lo[j] <= stop:            # OR-low stop
                exit_px = min(stop, o[j]); reason = "SL"; xi = j; break
        if exit_px is None:
            exit_px = c[-1]; reason = "EOD"; xi = n - 1
        pnl = exit_px - fill
        return dict(date=str(day), side="long",
                    entry_dt=str(idx[ei]), entry=round(float(fill), 2),
                    exit_dt=str(idx[xi]), exit=round(float(exit_px), 2),
                    sl=round(or_lo, 2), target="", pnl=round(float(pnl), 2),
                    bars_held=int(xi - ei + 1), exit_reason=reason)
    return None


def append_trade(trade):
    log = pd.read_csv(LOG_FILE) if os.path.exists(LOG_FILE) else pd.DataFrame(columns=LOG_COLS)
    if (log["date"].astype(str).str[:10] == trade["date"][:10]).any():
        print(f"[squeeze-log] {trade['date']} already logged — no change.")
        return False
    log = pd.concat([log, pd.DataFrame([trade])[LOG_COLS]], ignore_index=True)
    log = log.sort_values("date").reset_index(drop=True)
    log.to_csv(LOG_FILE, index=False)
    print(f"[squeeze-log] appended {trade['date']}: {trade['side'].upper()} "
          f"{trade['entry']} -> {trade['exit']} = {trade['pnl']:+} pt ({trade['exit_reason']})")
    return True


def main():
    day, armed = latest_armed_day()
    print(f"[squeeze-log] latest completed session {day} | squeeze armed: {armed}")
    if not armed:
        print("[squeeze-log] no squeeze on the latest session — nothing to log.")
        return
    bars = fetch_5m(day)
    if bars is None:
        print("[squeeze-log] no 5m data for the day yet — skipping.")
        return
    trade = orb_long_trade(day, bars)
    if trade is None:
        print("[squeeze-log] squeeze armed but OR-high never broke — no trade.")
        return
    append_trade(trade)


if __name__ == "__main__":
    main()
