"""macro_events.py — data + logic for the NIFTY Teller "Macro & Events" tab.

Two kinds of catalyst:
  • CALENDAR — known-date scheduled events (FOMC, RBI MPC, CPI, expiry, deadlines).
    Date math is LIVE, so the D-1 / D-0 flags advance every morning by themselves.
  • DYNAMIC  — geopolitical trackers with probabilities (India-US deal, Iran war),
    plus the Iran time-to-peace survival model (also computed live for `today`).

All *facts* live in macro_events.json (the 7AM-IST refresh agent rewrites it); all
*logic* + date math lives here and recomputes on every app load. Pure stdlib.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "macro_events.json")
IST = timezone(timedelta(hours=5, minutes=30))


def today_ist() -> date:
    """Today's date in IST (the market the app serves)."""
    return datetime.now(IST).date()


def load_data(path: str = DATA_PATH) -> dict:
    with open(path) as f:
        return json.load(f)


# ---- NSE monthly expiry: last Tuesday of the month, holiday-adjusted --------------
def last_tuesday(year: int, month: int) -> date:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    while d.weekday() != 1:            # Monday=0 … Tuesday=1
        d -= timedelta(days=1)
    return d


def next_monthly_expiries(today: date, n: int = 3, holidays=frozenset()) -> list[date]:
    out, y, m = [], today.year, today.month
    while len(out) < n:
        e = last_tuesday(y, m)
        while e in holidays:          # holiday → preceding trading day
            e -= timedelta(days=1)
        if e >= today:
            out.append(e)
        m, y = (1, y + 1) if m == 12 else (m + 1, y)
    return out


def _flag(days_until: int) -> str:
    return ("TODAY" if days_until == 0 else
            "TOMORROW" if days_until == 1 else
            "THIS WEEK" if days_until <= 6 else "UPCOMING")


def build_calendar(today: date, data: dict, window_days: int = 120) -> list[dict]:
    """Merge JSON dated events + computed monthly expiries; tag with days_until/flag."""
    holidays = {date.fromisoformat(h) for h in data.get("nse_holidays", [])}
    rows: list[dict] = []
    for ev in data.get("calendar", []):
        rows.append({**ev, "date": date.fromisoformat(ev["date"])})
    for e in next_monthly_expiries(today, 3, holidays):
        rows.append({"date": e, "name": "NIFTY monthly F&O expiry",
                     "category": "Expiry", "importance": "med",
                     "impact": "Elevated intraday vol; pin/unwind risk (last 30-min settle)",
                     "nifty_session": "same day"})
    out = []
    for r in rows:
        du = (r["date"] - today).days
        if 0 <= du <= window_days:
            out.append({**r, "days_until": du, "flag": _flag(du)})
    out.sort(key=lambda r: (r["date"], r.get("name", "")))
    return out


def alerts(today: date, data: dict) -> dict:
    """The D-0 / D-1 lists that drive the 'know today & 1 day before' banner."""
    cal = build_calendar(today, data, window_days=2)
    return {"today":    [r for r in cal if r["days_until"] == 0],
            "tomorrow": [r for r in cal if r["days_until"] == 1]}


# ---- Iran time-to-peace survival model (live for `today`) -------------------------
_SIGNAL_BASE = {"weak": 1.15, "medium": 1.4, "strong": 1.8}


def signal_multiplier(signals, today: date, halflife: int = 10):
    """Net proportional-hazards multiplier from dated diplomatic signals.

    Each signal nudges the survival curve: kind='dovish' → M>1 (peace/talks sooner),
    kind='hawkish' → M<1 (further out). A signal's deviation from 1 DECAYS with age
    (half-life `halflife` days) so stale headlines fade unless reinforced. Net M is the
    product of active signals, clamped to [0.4, 2.5], applied as S_adj = S_base ** M.
    Signals carry NO personal names — describe the actor by institution/role only.
    """
    M = 1.0
    applied = []
    for s in signals or []:
        try:
            d = date.fromisoformat(s["date"])
        except (KeyError, ValueError, TypeError):
            continue
        age = (today - d).days
        if age < 0:
            continue
        m = _SIGNAL_BASE.get(s.get("weight", "medium"), 1.4)
        if s.get("kind") == "hawkish":
            m = 1.0 / m
        decayed = 1.0 + (m - 1.0) * (0.5 ** (age / halflife))   # fade toward 1 with age
        M *= decayed
        applied.append({"date": d, "kind": s.get("kind", "dovish"),
                        "weight": s.get("weight", "medium"), "note": s.get("note", ""),
                        "age": age, "mult": round(decayed, 3)})
    return max(0.4, min(2.5, round(M, 3))), applied


def peace_model(today: date, cfg: dict) -> dict:
    """Empirical survival curve: P(ceasefire/peace-talk announced by date), conditional
    on the current fighting phase having survived to `today`, then adjusted by any dated
    diplomatic signals (see signal_multiplier). Censored cases (never resolved) sit in the
    denominator so the probability ceiling stays < 1."""
    phase_start = date.fromisoformat(cfg["phase_start"])
    durs = sorted(cfg["conflicts"].values())
    n_censored = len(cfg.get("censored", []))
    elapsed = (today - phase_start).days

    at_risk = [d for d in durs if d > elapsed]         # eventually-resolved, still pending
    n_at_risk = len(at_risk) + n_censored              # + never-resolved tail

    median = durs[len(durs) // 2]
    mean = sum(durs) / len(durs)
    mult, signals = signal_multiplier(cfg.get("signals", []), today)

    rows, crossed = [], False
    grid = sorted(set(durs) | set(range(elapsed, max(durs) + 1, 7)))
    for d in grid:
        if d < elapsed:
            continue
        dt = phase_start + timedelta(days=d)
        k = sum(1 for x in at_risk if x <= d)
        p_base = (k / n_at_risk) if n_at_risk else 0.0
        p_adj = 1.0 - (1.0 - p_base) ** mult           # proportional-hazards shift
        cross = (not crossed) and p_adj >= 0.70
        crossed = crossed or cross
        rows.append({"date": dt, "phase_day": d, "p_base": round(p_base, 2),
                     "p": round(p_adj, 2), "crosses_70": cross})

    return {"phase_start": phase_start, "elapsed": elapsed, "median": median,
            "mean": round(mean), "hold_prob": cfg.get("hold_prob"),
            "phase_note": cfg.get("phase_note", ""), "rows": rows,
            "n_resolved": len(durs), "n_censored": n_censored,
            "signal_mult": mult, "signals": signals}


if __name__ == "__main__":  # quick CLI sanity check
    d = load_data()
    t = today_ist()
    print(f"today (IST): {t}\n")
    print("ALERTS:", {k: [e['name'] for e in v] for k, v in alerts(t, d).items()}, "\n")
    print("CALENDAR (next 120d):")
    for r in build_calendar(t, d):
        print(f"  {r['date']}  D-{r['days_until']:<3} [{r['flag']:<9}] {r['name']}")
    pm = peace_model(t, d["peace_model"])
    print(f"\nPEACE MODEL: phase day {pm['elapsed']}, median {pm['median']}d, "
          f"P(hold)={pm['hold_prob']}, signal_mult={pm['signal_mult']}x "
          f"({len(pm['signals'])} active)")
    for r in pm["rows"]:
        mark = "  <-- crosses 0.70" if r["crosses_70"] else ""
        base = f" (base {r['p_base']:.2f})" if pm["signal_mult"] != 1.0 else ""
        print(f"  {r['date']}  day {r['phase_day']:<3} P={r['p']:.2f}{base}{mark}")
