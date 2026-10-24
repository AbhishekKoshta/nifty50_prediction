"""Scaffold a new validated strategy into the 🎯 Strategy Book.

    python -m strategy_book.new_strategy 63_MyEdge "My edge — one-line name" \\
        --verdict GO --source Algo_Nifty50/strategies/63_MyEdge \\
        --trades ../../strategies/63_MyEdge/trades.csv \\
        --map date=entry_date side=direction net_rs=net_rs_lot65

Creates strategy_book/strategies/s<id>.py from _template.py and (optionally) a normalised
trade log in strategy_book/trades/<id>.csv with columns
date, side, entry_ts, exit_ts, exit_reason, R, net_rs (net ₹ per lot of 65).
If the source log is in POINTS instead of ₹, pass --points (net_rs = pts × 65).
Only GO / MARGINAL-GO strategies belong here — it is the shipped book, not the lab.
"""
import argparse
import os
import re
import sys

import pandas as pd

from .core import LOT, TRADE_COLS, TRADES_DIR

HERE = os.path.dirname(os.path.abspath(__file__))
STRAT_DIR = os.path.join(HERE, "strategies")


def normalise(src: str, mapping: dict, points: bool) -> pd.DataFrame:
    raw = pd.read_csv(src)
    out = pd.DataFrame()
    for col in TRADE_COLS:
        s = mapping.get(col, col)
        out[col] = raw[s] if s in raw.columns else None
    if out["net_rs"].isna().all():
        sys.exit(f"--trades: no net column found (looked for '{mapping.get('net_rs', 'net_rs')}'); use --map net_rs=<col>")
    if points:
        out["net_rs"] = out["net_rs"].astype(float) * LOT
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out["side"] = out["side"].astype(str).str.upper().replace({"L": "LONG", "S": "SHORT", "1": "LONG", "-1": "SHORT"})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id", help="stable id, e.g. 63_MyEdge")
    ap.add_argument("name")
    ap.add_argument("--verdict", default="GO", choices=["GO", "MARGINAL-GO", "MARGINAL", "PAPER"])
    ap.add_argument("--source", default="TODO research folder")
    ap.add_argument("--trades", help="backtest trade-log CSV to bundle")
    ap.add_argument("--map", nargs="*", default=[], help="target=sourcecol pairs for the trade log")
    ap.add_argument("--points", action="store_true", help="the net column is in index points, not ₹/lot")
    ap.add_argument("--force", action="store_true", help="overwrite an existing strategy file")
    a = ap.parse_args()

    mod = "s" + re.sub(r"[^0-9a-zA-Z_]", "_", a.id).lower()
    path = os.path.join(STRAT_DIR, mod + ".py")
    if os.path.exists(path) and not a.force:
        sys.exit(f"{path} exists (use --force)")

    log = None
    if a.trades:
        mapping = dict(kv.split("=", 1) for kv in a.map)
        t = normalise(a.trades, mapping, a.points)
        os.makedirs(TRADES_DIR, exist_ok=True)
        log = f"{a.id}.csv"
        t.to_csv(os.path.join(TRADES_DIR, log), index=False)
        print(f"trade log → strategy_book/trades/{log} ({len(t)} trades, net ₹{t.net_rs.sum():,.0f}/lot)")

    tpl = open(os.path.join(STRAT_DIR, "_template.py")).read()
    tpl = (tpl.replace("__ID__", a.id).replace("__NAME__", a.name).replace("__SOURCE__", a.source)
              .replace("__VERDICT__", a.verdict).replace("__TRADE_LOG__", repr(log)))
    with open(path, "w") as f:
        f.write(tpl)
    print(f"strategy  → {os.path.relpath(path)}  — fill the TODOs, then run:  python -m strategy_book.check")


if __name__ == "__main__":
    main()
