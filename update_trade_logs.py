#!/usr/bin/env python3
"""
Auto-update the intraday trade logs with the LATEST completed trigger day.

The dashboard's "Last recorded trade" is read from static bundled logs in
trade_logs/ that the daily Action never regenerated, so the cards froze. This
script keeps the frequently-firing INTRADAY edges current: after the afternoon
data update it looks at the latest completed session, checks each edge's entry
condition from the daily feed (prior-day values, all known at the open), and if
the edge would have traded it pulls that day's 5-minute NIFTY bars from yfinance
and simulates the same-day trade with rules ported 1:1 from the validated
generators in strategies/*. Completed trades are appended to their logs
(idempotent — latest day only, never a duplicate date), each in that log's own
schema, so every covered card reflects the most recent trigger.

Edges covered (intraday, same-day EOD square-off):
  C SqueezeORB    squeeze armed -> break of the 30m OR-high      (38_SqueezeORB)
  A DownDayBounce prior day < -0.9% -> buy 09:15 open, 1xATR SL/tgt (23_DownDayBounce)
  B GapFade       |gap| >= 0.30% -> fade toward prior close, 1x-gap SL (14_GapFade, L+S)
  F GapHalfFill   gap-up >= 0.35% -> short to the half-gap fill, +50 SL (26_GapHalfFill)

NOT covered (yet): the swing edges (RSI2, Donchian, BearRallyFade, the momentum
books, the bear satellites). Their bundled logs carry pre-2023 history built from
a 10-year daily dataset this repo doesn't ship, so they can't be regenerated in
CI; each needs its own append-latest port. They also fire rarely, so their cards
stay accurate far longer. See the registry note at the bottom.

Self-contained: pandas / numpy / yfinance only (already in the workflow).
No look-ahead: conditions use prior-day values; entries fill at/after the open;
exits are checked intrabar. Costs (FEE) are applied by the dashboard; the stored
`pnl` is informational (teller recomputes net from entry/exit/side).

Usage:
  python update_trade_logs.py                 # append the latest completed day
  python update_trade_logs.py --day 2026-07-14 --dry   # verify a port, no write
"""
import argparse
import os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DAILY_FILE = os.path.join(HERE, "Nifty_Features.csv")
LOG_DIR = os.path.join(HERE, "trade_logs")
TICKER = "^NSEI"
IST = "Asia/Kolkata"
FEE = 10.0
SQUEEZE_Q = 0.25


# ----------------------------------------------------------------------------- data
def load_daily():
    d = pd.read_csv(DAILY_FILE, usecols=["date", "open", "high", "low", "close"])
    d["date"] = pd.to_datetime(d["date"])
    return d.sort_values("date").reset_index(drop=True)


def daily_context(d):
    """Prior-day fields the intraday entry conditions key off (all .shift(1) so
    the decision only sees data up to the prior close — no look-ahead)."""
    d = d.copy()
    ret_oc = (d["close"] - d["open"]) / d["open"] * 100.0        # intraday open->close %
    atr14 = (d["high"] - d["low"]).rolling(14).mean()
    span5 = (d["high"].rolling(5).max() - d["low"].rolling(5).min()) / d["close"] * 100.0
    d["pret"] = ret_oc.shift(1)                                  # DownDayBounce
    d["patr"] = atr14.shift(1)
    d["prev_close"] = d["close"].shift(1)                        # GapFade / GapHalfFill
    d["span5_prev"] = span5.shift(1)                             # SqueezeORB
    d["span5_thr"] = span5.shift(1).rolling(60, min_periods=20).quantile(SQUEEZE_Q)
    return d


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
    if df.index.tz is None:                       # robust on a UTC CI runner
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(IST)
    df = df[[t.date() == day for t in df.index]]
    return df if len(df) else None


def arrays(bars):
    return (bars["Open"].values, bars["High"].values, bars["Low"].values,
            bars["Close"].values, bars.index.strftime("%H:%M"), bars.index)


# ----------------------------------------------------------------------------- sims
def sim_open_entry(a, side, entry, stop, target, sq, net):
    """Enter at the 09:15 open (bar 0), manage from bar 1. Stop checked before
    target (conservative), EOD square-off at `sq`. Shared by DownDayBounce /
    GapFade / GapHalfFill. Returns a trade-row dict (or None if <2 bars)."""
    o, hi, lo, c, hm, idx = a
    n = len(hm)
    if n < 2:
        return None
    exit_px = reason = None; xi = n - 1
    for j in range(1, n):
        if hm[j] >= sq:
            exit_px, reason, xi = c[j], "eod", j; break
        if side == "long":
            if lo[j] <= stop:
                exit_px, reason, xi = min(stop, o[j]), "sl", j; break
            if target is not None and hi[j] >= target:
                exit_px, reason, xi = max(target, o[j]), "tgt", j; break
        else:
            if hi[j] >= stop:
                exit_px, reason, xi = max(stop, o[j]), "sl", j; break
            if target is not None and lo[j] <= target:
                exit_px, reason, xi = min(target, o[j]), "tgt", j; break
    if exit_px is None:
        exit_px, reason, xi = c[-1], "eod", n - 1
    gross = (exit_px - entry) if side == "long" else (entry - exit_px)
    pnl = gross - FEE if net else gross
    return dict(side=side, entry_dt=str(idx[0]), exit_dt=str(idx[xi]),
                entry=round(float(entry), 2), exit=round(float(exit_px), 2),
                sl=round(float(stop), 2), target=("" if target is None else round(float(target), 2)),
                pnl=round(float(pnl), 2), bars_held=int(xi), _reason=reason)


def sig_squeeze(row):
    sp, th = row["span5_prev"], row["span5_thr"]
    return {} if (pd.notna(sp) and pd.notna(th) and sp <= th) else None


def sim_squeeze(day, a, params):
    """Squeeze-ORB long: OR = first 30 min; first break of OR-high after 09:45,
    fill worse-of {OR-high, open}, stop OR-low, ride to 15:25 (38_SqueezeORB)."""
    o, hi, lo, c, hm, idx = a
    n = len(hm)
    if n < 9:
        return None
    or_hi = float(hi[:6].max()); or_lo = float(lo[:6].min())
    for i in range(6, n):
        if hm[i] > "15:00":
            break
        if hi[i] < or_hi:
            continue
        fill = max(or_hi, o[i]) if o[i] > or_hi else or_hi
        stop = or_lo; ei = i; xi = i; exit_px = reason = None
        for j in range(i, n):
            if hm[j] >= "15:25":
                exit_px, reason, xi = c[j], "eod", j; break
            if lo[j] <= stop:
                exit_px, reason, xi = min(stop, o[j]), "sl", j; break
        if exit_px is None:
            exit_px, reason, xi = c[-1], "eod", n - 1
        return dict(side="long", entry_dt=str(idx[ei]), exit_dt=str(idx[xi]),
                    entry=round(float(fill), 2), exit=round(float(exit_px), 2),
                    sl=round(or_lo, 2), target="", pnl=round(float(exit_px - fill), 2),
                    bars_held=int(xi - ei + 1), _reason=reason)
    return None


def sig_downday(row):
    if pd.isna(row["pret"]) or pd.isna(row["patr"]):
        return None
    return {"atr": float(row["patr"])} if row["pret"] < -0.9 else None


def sim_downday(day, a, p):
    entry = float(a[0][0]); atr = p["atr"]
    return sim_open_entry(a, "long", entry, entry - atr, entry + atr, "15:20", net=True)


def sig_gap(row):
    return {"pc": float(row["prev_close"])} if pd.notna(row["prev_close"]) else None


def sim_gapfade(day, a, p):
    entry = float(a[0][0]); pc = p["pc"]; gap = entry - pc
    if abs(100 * gap / pc) < 0.30:
        return None
    if gap > 0:
        r = sim_open_entry(a, "short", entry, entry + abs(gap), pc, "15:20", net=False)
    else:
        r = sim_open_entry(a, "long", entry, entry - abs(gap), pc, "15:20", net=False)
    if r:
        r["gap_pct"] = round(100 * gap / pc, 2)
    return r


def sim_gaphalffill(day, a, p):
    entry = float(a[0][0]); pc = p["pc"]; gap = entry - pc; gp = 100 * gap / pc
    if gp < 0.35:                                   # gap-up only, short
        return None
    r = sim_open_entry(a, "short", entry, entry + 50.0, entry - 0.5 * gap, "15:20", net=True)
    if r:
        r["gap_pct"] = round(gp, 2)
    return r


# ----------------------------------------------------------------------------- registry
# key, log filename, human name, signal(ctx_row)->params|None, simulate(day, arrays, params)->row|None
EDGES = [
    ("C", "SqueezeORB.csv",   "Squeeze-ORB",    sig_squeeze, sim_squeeze),
    ("A", "DownDayBounce.csv", "DownDayBounce",  sig_downday, sim_downday),
    ("B", "GapFade.csv",       "GapFade",        sig_gap,     sim_gapfade),
    ("F", "GapHalfFill.csv",   "GapHalfFill",    sig_gap,     sim_gaphalffill),
]


def append_row(fname, row, dry=False):
    path = os.path.join(LOG_DIR, fname)
    log = pd.read_csv(path)
    cols = list(log.columns)
    reason = row.get("_reason", "")
    row = dict(row)
    row["date"] = row["date"][:10]
    if "exit_reason" in cols:
        row["exit_reason"] = reason
    if "tag" in cols:
        row["tag"] = reason
    exists = (log["date"].astype(str).str[:10] == row["date"]).any()
    if dry:
        print(f"  {fname}: [computed] {row['date']} {row['side'].upper()} "
              f"{row['entry']} -> {row['exit']} sl {row.get('sl','')} "
              f"tgt {row.get('target','')} ({reason})")
        if exists:
            b = log[log["date"].astype(str).str[:10] == row["date"]].iloc[-1]
            print(f"  {fname}: [bundled ] {row['date']} {str(b['side']).upper()} "
                  f"{b['entry']} -> {b['exit']} sl {b.get('sl','')} "
                  f"tgt {b.get('target','')} ({b.get('exit_reason', b.get('tag',''))})")
        return False
    if exists:
        print(f"  {fname}: {row['date']} already logged — no change.")
        return False
    newrow = {c: row.get(c, "") for c in cols}
    log = pd.concat([log, pd.DataFrame([newrow])[cols]], ignore_index=True)
    log = log.sort_values("date", key=lambda s: pd.to_datetime(s, errors="coerce")).reset_index(drop=True)
    log.to_csv(path, index=False)
    print(f"  {fname}: appended {row['date']} {row['side'].upper()} "
          f"{row['entry']} -> {row['exit']} ({reason})")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="process this YYYY-MM-DD instead of the latest completed session")
    ap.add_argument("--dry", action="store_true", help="compute & print, but do not write the logs")
    a = ap.parse_args()

    daily = load_daily()
    ctx = daily_context(daily)
    if a.day:
        want = pd.Timestamp(a.day).date()
        m = ctx[ctx["date"].dt.date == want]
        if m.empty:
            print(f"[trade-logs] {want} not in the daily feed."); return
        row = m.iloc[-1]; day = want
    else:
        row = ctx.iloc[-1]; day = row["date"].date()

    print(f"[trade-logs] session {day}{' (dry-run)' if a.dry else ''}")
    fired = [(k, f, nm, sim) for k, f, nm, sig, sim in EDGES if sig(row) is not None]
    if not fired:
        print("[trade-logs] no intraday edge armed on this session — nothing to log.")
        return

    bars = fetch_5m(day)
    if bars is None:
        print("[trade-logs] no 5m data for the day yet — skipping."); return

    a5 = arrays(bars)
    for k, f, nm, sig, sim in EDGES:
        params = sig(row)
        if params is None:
            continue
        trade = sim(day, a5, params)
        if trade is None:
            print(f"  {f}: {nm} armed but no trade (level never triggered).")
            continue
        trade["date"] = str(day)
        append_row(f, trade, dry=a.dry)


if __name__ == "__main__":
    main()
