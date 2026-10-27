"""macro_events.py — data + logic for the NIFTY Teller "🌍 Macro & Events" tab (v2).

Four kinds of catalyst, all driven off `today` so the tab advances by itself every morning:
  • SCHEDULED  — recurring prints generated from a few anchor facts in `schedule` (RBI MPC, FOMC +
                 minutes, India CPI/WPI/IIP/GDP, US CPI/NFP, GST, auto sales, PMI, results deadlines,
                 monthly expiry) + one-off dated events in `calendar` (budget, elections, MSCI, FTAs).
  • PENDING    — undated catalysts that can land any day in the next ~month (`pending`), each with a
                 subjective P(30d); the Iran item takes its P from the time-to-peace survival model.
  • EARNINGS   — NIFTY 50 / Next 50 / Bank Nifty results dates auto-pulled from the NSE event calendar.
  • SECTOR     — curated government/regulator stance per sector (`sector_policy`) + auto-tagged policy
                 headlines (`sector_news`) + the RBI release wire (`rbi_releases`).

All *facts* live in macro_events.json (the 7AM-IST refresh rewrites it); all *logic* + date math lives
here and recomputes on every app load. Pure stdlib. No personal names of officials anywhere.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "macro_events.json")
IST = timezone(timedelta(hours=5, minutes=30))

IMPORTANCE_RANK = {"high": 0, "med": 1, "low": 2}
INDEX_RANK = {"NIFTY50": 0, "BANK": 1, "NIFTYNEXT50": 2}


def today_ist() -> date:
    """Today's date in IST (the market the app serves)."""
    return datetime.now(IST).date()


def load_data(path: str = DATA_PATH) -> dict:
    with open(path) as f:
        return json.load(f)


def _d(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


# ---- trading-day helpers ------------------------------------------------------------
def holidays_set(data: dict) -> set[date]:
    return {h for h in (_d(x) for x in data.get("nse_holidays", [])) if h}


def is_trading_day(d: date, holidays=frozenset()) -> bool:
    return d.weekday() < 5 and d not in holidays


def next_trading_day(d: date, holidays=frozenset(), inclusive: bool = True) -> date:
    if not inclusive:
        d += timedelta(days=1)
    while not is_trading_day(d, holidays):
        d += timedelta(days=1)
    return d


def nth_trading_day(year: int, month: int, n: int, holidays=frozenset()) -> date:
    d, k = date(year, month, 1), 0
    while True:
        if is_trading_day(d, holidays):
            k += 1
            if k == n:
                return d
        d += timedelta(days=1)


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


# ---- US Eastern → IST ---------------------------------------------------------------
def _nth_sunday(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    while d.weekday() != 6:
        d += timedelta(days=1)
    return d + timedelta(days=7 * (n - 1))


def us_dst(d: date) -> bool:
    """US daylight time: 2nd Sunday of March → 1st Sunday of November."""
    return _nth_sunday(d.year, 3, 2) <= d < _nth_sunday(d.year, 11, 1)


def us_to_ist(d: date, hhmm: str) -> tuple[date, str]:
    """(IST date, 'HH:MM') for a US-Eastern wall-clock time on US date `d`."""
    h, m = (int(x) for x in hhmm.split(":"))
    et = datetime(d.year, d.month, d.day, h, m)
    ist = et + timedelta(hours=9, minutes=30) + (timedelta() if us_dst(d) else timedelta(hours=1))
    return ist.date(), ist.strftime("%H:%M")


def _session(ist_hhmm: str) -> str:
    """Which NSE session reacts to a print at this IST clock time."""
    return "next day" if ist_hhmm >= "15:30" else "same day"


def _flag(days_until: int) -> str:
    return ("TODAY" if days_until == 0 else
            "TOMORROW" if days_until == 1 else
            "THIS WEEK" if days_until <= 6 else "UPCOMING")


# ---- recurring events, generated from `schedule` anchors ---------------------------
def _months(today: date, window_days: int):
    end = today + timedelta(days=window_days)
    y, m = today.year, today.month
    while date(y, m, 1) <= end:
        yield y, m
        m, y = (1, y + 1) if m == 12 else (m + 1, y)


def _prev_month_name(y: int, m: int) -> str:
    pm = date(y, m, 1) - timedelta(days=1)
    return pm.strftime("%b")


def recurring_events(today: date, data: dict, window_days: int = 120) -> list[dict]:
    sch = data.get("schedule", {})
    hol = holidays_set(data)
    rows: list[dict] = []

    def add(d, name, cat, imp, ist_time, session, impact, **kw):
        if d is None:
            return
        rows.append({"date": d, "name": name, "category": cat, "importance": imp, "ist_time": ist_time,
                     "nifty_session": session, "impact": impact, "recurring": True, **kw})

    # India monthly prints (MOSPI/DPIIT: on the calendar day, next working day if weekend/holiday)
    for y, m in _months(today, window_days):
        ref = _prev_month_name(y, m)
        add(next_trading_day(date(y, m, sch.get("india_cpi_dom", 12)), hol), f"India CPI ({ref}) — 16:00",
            "Data-IN", "high", "16:00", "next day",
            "Post-hike regime: a >5.5% print keeps the next RBI hike live (rate-sensitives ↓); sub-5% = relief in banks/realty/autos")
        add(next_trading_day(date(y, m, sch.get("india_wpi_dom", 14)), hol), f"India WPI ({ref}) — 12:00",
            "Data-IN", "low", "12:00", "same day", "Input-cost pulse for metals/chemicals/FMCG margins")
        add(next_trading_day(date(y, m, sch.get("india_iip_dom", 28)), hol), f"India IIP ({ref}) — 16:00",
            "Data-IN", "low", "16:00", "next day", "Capex/industrial pulse; moves capital goods & PSU names at the margin")
        add(date(y, m, 1), f"GST collections + auto monthly sales ({ref})", "Data-IN", "low", "through the day",
            "same day", "GST run-rate = fiscal cushion; dispatch numbers move Maruti/M&M/Tata Motors/2-wheelers")
        add(nth_trading_day(y, m, 1, hol), f"India PMI manufacturing ({ref}) — 10:30", "Data-IN", "low", "10:30",
            "same day", "Rarely index-moving; watch the new-export-orders line for the trade-deal narrative")
        add(nth_trading_day(y, m, 3, hol), f"India PMI services ({ref}) — 10:30", "Data-IN", "low", "10:30",
            "same day", "Rarely index-moving")

    # RBI MPC decisions + minutes
    for s in sch.get("rbi_mpc_decisions", []):
        d = _d(s)
        add(d, "RBI MPC decision — 10:00 (repo, stance, CPI/GDP projections)", "CenBank", "high", "10:00", "same day",
            "Hike → banks ↑ NIM / NBFC-realty-auto ↓; pause with dovish tilt → broad ↑. Rupee guidance matters this cycle",
            source=sch.get("sources", {}).get("rbi_mpc", ""))
        add(d + timedelta(days=sch.get("rbi_minutes_lag_days", 14)) if d else None,
            "RBI MPC minutes — 17:00 (vote split, hike bias)", "CenBank", "low", "17:00", "next day",
            "Dissent count = odds of the next hike")

    # FOMC decisions + minutes (US Eastern → IST)
    seps = set(sch.get("fomc_sep", []))
    for s in sch.get("fomc_decisions", []):
        d = _d(s)
        if not d:
            continue
        idt, itime = us_to_ist(d, "14:00")
        sep = " + dot plot/SEP" if s in seps else ""
        add(idt, f"FOMC decision{sep} — {itime} IST (US {d:%d %b})", "CenBank", "high", itime, _session(itime),
            "Hawkish → DXY/UST yields ↑ → FII outflow, INR ↓, NIFTY ↓ (IT mixed); dovish → risk-on",
            source=sch.get("sources", {}).get("fomc", ""))
        md = d + timedelta(days=sch.get("fomc_minutes_lag_days", 21))
        mdt, mtime = us_to_ist(md, "14:00")
        add(mdt, f"FOMC minutes ({d:%b} meeting) — {mtime} IST", "CenBank", "med", mtime, _session(mtime),
            "Tone on further hikes / balance sheet; moves US yields → FII flows")

    # US data (08:30 ET)
    for key, label, imp, impact in (
            ("us_cpi", "US CPI", "high", "Hot print → Fed hike odds ↑ → DXY ↑, EM outflows; cool → relief"),
            ("us_nfp", "US jobs report (NFP)", "med", "Strong jobs + wages = hawkish Fed; weak = yields ↓")):
        for s in sch.get(key, []):
            d = _d(s)
            if not d:
                continue
            idt, itime = us_to_ist(d, "08:30")
            add(idt, f"{label} — {itime} IST (US {d:%d %b})", "Data-US", imp, itime, _session(itime), impact,
                source=sch.get("sources", {}).get("bls", ""))

    # India GDP (list) + Union Budget
    for g in sch.get("india_gdp", []):
        d = _d(g.get("date"))
        add(d, f"India {g.get('label', 'GDP')} — 16:00" + (" (approx. date)" if g.get("approx") else ""),
            "Data-IN", "high", "16:00", "next day", "Growth vs RBI projection; a miss revives cut talk, a beat cements tightening",
            approx=bool(g.get("approx")), source=sch.get("sources", {}).get("mospi", ""))
    ub = _d(sch.get("union_budget"))
    if ub:
        add(ub, "Union Budget — 11:00 (market open)", "Policy", "high", "11:00", "same day",
            "Capex, fiscal-deficit glide path, capital-gains/STT, sector schemes; the year's biggest single-day policy vol")

    # Quarterly results filing deadlines (SEBI: 45 days; Q4 audited: 60 days)
    for qe, lag, label in ((date(today.year - 1, 12, 31), 45, "Q3"), (date(today.year, 3, 31), 60, "Q4"),
                           (date(today.year, 6, 30), 45, "Q1"), (date(today.year, 9, 30), 45, "Q2"),
                           (date(today.year, 12, 31), 45, "Q3"), (date(today.year + 1, 3, 31), 60, "Q4")):
        fy = qe.year + 1 if qe.month > 3 else qe.year
        add(qe + timedelta(days=lag), f"{label} FY{str(fy)[2:]} results filing deadline (SEBI {lag}-day) — season ends",
            "Earnings", "med", "—", "—", "Last cluster of mid/small-cap prints; index heavyweights report in the first 3 weeks")

    # Monthly expiry
    for e in next_monthly_expiries(today, 4, hol):
        add(e, "NIFTY monthly F&O expiry", "Expiry", "med", "15:30", "same day",
            "Elevated intraday vol; pin/unwind risk in the last 30 min")
    return rows


def build_calendar(today: date, data: dict, window_days: int = 45) -> list[dict]:
    """Merge one-off JSON events + generated recurring events; tag with days_until/flag; de-dupe."""
    rows: list[dict] = []
    for ev in data.get("calendar", []):
        d = _d(ev.get("date"))
        if d:
            rows.append({**ev, "date": d, "recurring": False})
    rows += recurring_events(today, data, window_days)
    out, seen = [], set()
    for r in rows:
        du = (r["date"] - today).days
        key = (r["date"], r.get("name", "").lower())
        if 0 <= du <= window_days and key not in seen:
            seen.add(key)
            out.append({**r, "days_until": du, "flag": _flag(du)})
    out.sort(key=lambda r: (r["date"], IMPORTANCE_RANK.get(r.get("importance", "low"), 2), r.get("name", "")))
    return out


# ---- earnings (auto-filled by update_macro.py from the NSE event calendar) ----------
def earnings_window(today: date, data: dict, days: int = 14) -> list[dict]:
    out = []
    for e in (data.get("earnings") or {}).get("upcoming", []):
        d = _d(e.get("date"))
        if not d:
            continue
        du = (d - today).days
        if 0 <= du <= days:
            out.append({**e, "date": d, "days_until": du, "flag": _flag(du)})
    out.sort(key=lambda e: (e["date"], INDEX_RANK.get(e.get("index", ""), 9), e.get("symbol", "")))
    return out


def alerts(today: date, data: dict) -> dict:
    """The D-0 / D-1 lists that drive the 'know today & 1 day before' banner (high/med events +
    NIFTY 50 heavyweights reporting)."""
    cal = [r for r in build_calendar(today, data, window_days=2) if r.get("importance") in ("high", "med")]
    out = {"today": [r for r in cal if r["days_until"] == 0],
           "tomorrow": [r for r in cal if r["days_until"] == 1]}
    for du, key in ((0, "today"), (1, "tomorrow")):
        syms = [e["symbol"] for e in earnings_window(today, data, 1)
                if e["days_until"] == du and e.get("index") == "NIFTY50"]
        if syms:
            label = ", ".join(syms[:6]) + (f" +{len(syms) - 6} more" if len(syms) > 6 else "")
            out[key].append({"name": f"NIFTY 50 results: {label}", "category": "Earnings", "importance": "med",
                             "date": today + timedelta(days=du), "days_until": du})
    return out


# ---- pending (undated, any-day catalysts) --------------------------------------------
def peace_p_within(pm: dict, today: date, days: int = 30) -> float | None:
    """P(ceasefire/talks announced) by today+days from the live survival curve."""
    rows = pm.get("rows") or []
    if not rows:
        return None
    target = today + timedelta(days=days)
    p = rows[0]["p"]
    for r in rows:
        if r["date"] <= target:
            p = r["p"]
    return float(p)


def build_pending(today: date, data: dict, pm: dict | None = None) -> list[dict]:
    out = []
    for it in data.get("pending", []):
        p = it.get("p_30d")
        if it.get("p_source") == "peace_model" and pm:
            p = peace_p_within(pm, today, 30)
        upd = _d(it.get("updated"))
        out.append({**it, "p": (None if p is None else float(p)),
                    "age_days": (today - upd).days if upd else None})
    out.sort(key=lambda x: (-(x["p"] or 0), x.get("name", "")))
    return out


def sector_news(today: date, data: dict, max_age_days: int = 7) -> list[dict]:
    out = []
    for it in (data.get("sector_news") or {}).get("items", []):
        d = _d(it.get("date"))
        age = (today - d).days if d else None
        if age is None or 0 <= age <= max_age_days:
            out.append({**it, "age": age})
    out.sort(key=lambda x: (x.get("date") or ""), reverse=True)
    return out


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
        d = _d(s.get("date"))
        if not d:
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

    # Tail regime: the phase has outlasted every resolved comparable, so the empirical curve is
    # exhausted (it would read 0 forever). Extrapolate with a constant hazard = the sample's
    # resolution rate (resolved share / mean duration), flagged as `tail=True` for the UI.
    tail = not at_risk
    lam = ((len(durs) / (len(durs) + n_censored)) / mean) if (tail and mean) else 0.0

    rows, crossed = [], False
    grid = sorted(set(durs) | set(range(elapsed, max(max(durs), elapsed) + 92, 7)))
    for d in grid:
        if d < elapsed:
            continue
        dt = phase_start + timedelta(days=d)
        if tail:
            import math
            p_base = 1.0 - math.exp(-lam * (d - elapsed))
        else:
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
            "n_resolved": len(durs), "n_censored": n_censored, "tail": tail,
            "tail_hazard": round(lam, 4),
            "signal_mult": mult, "signals": signals}


if __name__ == "__main__":  # quick CLI sanity check
    d = load_data()
    t = today_ist()
    print(f"today (IST): {t}\n")
    print("ALERTS:", {k: [e['name'] for e in v] for k, v in alerts(t, d).items()}, "\n")
    print("CALENDAR (next 60d):")
    for r in build_calendar(t, d, 60):
        print(f"  {r['date']}  D-{r['days_until']:<3} [{r['flag']:<9}] {r.get('importance','?'):<4} "
              f"{r['category']:<8} {r['name']}")
    pm = peace_model(t, d["peace_model"])
    print(f"\nPEACE MODEL: phase day {pm['elapsed']}, median {pm['median']}d, P(hold)={pm['hold_prob']}, "
          f"signal_mult={pm['signal_mult']}x ({len(pm['signals'])} active); P(30d)={peace_p_within(pm, t)}")
    print("\nPENDING (sorted by P):")
    for p in build_pending(t, d, pm):
        ps = "n/a" if p["p"] is None else f"{p['p']:.0%}"
        print(f"  {ps:>4}  {p['name']}")
    ew = earnings_window(t, d, 14)
    print(f"\nEARNINGS next 14d: {len(ew)} index names; sector_news items: "
          f"{len(sector_news(t, d))}; holidays: {len(d.get('nse_holidays', []))}")
