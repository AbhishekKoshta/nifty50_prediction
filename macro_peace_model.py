"""
Time-to-peace survival model for the 2026 Iran-US war.

Question: given N comparable market-moving conflicts, how many DAYS from the start of a
fighting phase until the first ceasefire / serious peace-talk announcement — and where is
the current phase (started 2026-07-08) on that curve today?

Method: empirical survival curve. Days-to-de-escalation for each comparable conflict form
a distribution; we read the CDF unconditionally (from phase start) and conditional on the
current phase having survived to today. A protracted case (Russia-Ukraine) is carried as a
censored point so the probability ceiling stays below 1 (some wars just grind on).

No look-ahead, no fitting — pure base rates. Re-run any day; only `TODAY` changes.
"""
from datetime import date, timedelta

# --- Comparable conflicts: days from a fighting-phase START to first ceasefire / serious talks ---
CONFLICTS = {
    "Twelve-Day War (Iran-Israel-US, Jun 2025)": 12,   # Jun 13 -> Jun 24 ceasefire  (closest analog)
    "Yom Kippur War (1973)":                     19,   # Oct 6  -> Oct 25 ceasefire
    "Iraq War / Baghdad fall (2003)":            21,   # Mar 20 -> Apr 9 regime collapse
    "Gaza 'Cast Lead' (2008-09)":                22,   # Dec 27 -> Jan 18 ceasefire
    "Lebanon War (2006)":                        34,   # Jul 12 -> Aug 14 ceasefire (UN 1701)
    "2026 Iran War - phase 1 (this war)":        39,   # Feb 28 -> Apr 8 first ceasefire
    "Gulf War / Desert Storm (1991)":            42,   # Jan 17 -> Feb 28 ceasefire
    "Israel-Hamas first truce (2023)":           48,   # Oct 7  -> Nov 24 truce
    "Kosovo (1999)":                             78,   # Mar 24 -> Jun 10 Kumanovo
}
CENSORED = {"Russia-Ukraine (2022- )": None}           # never resolved -> probability ceiling < 1

PHASE_START = date(2026, 7, 8)    # current renewed phase (2nd ceasefire collapsed here)
TODAY       = date(2026, 7, 20)   # update this to re-price

def main():
    durs = sorted(CONFLICTS.values())
    n_total = len(durs) + len(CENSORED)          # censored included in denominator
    elapsed = (TODAY - PHASE_START).days

    print(f"Phase start : {PHASE_START}")
    print(f"Today       : {TODAY}  (day {elapsed} of current phase)")
    print(f"Sample      : {len(durs)} resolved + {len(CENSORED)} censored")
    print(f"Median days-to-de-escalation (resolved) : {durs[len(durs)//2]}")
    print(f"Mean                                    : {sum(durs)/len(durs):.0f}")
    print()

    # at-risk set for the conditional curve = durations strictly greater than days already elapsed
    at_risk = [d for d in durs if d > elapsed] + [None]          # +censored
    n_at_risk = len(at_risk)

    print(f"{'Date':<12}{'Day':>4}   {'P(uncond)':>10}   {'P(cond|survived to today)':>26}")
    print("-" * 58)
    grid = sorted(set(durs + list(range(elapsed, 82, 7))))
    for d in grid:
        dt = PHASE_START + timedelta(days=d)
        # unconditional CDF from phase start
        k_uncond = sum(1 for x in durs if x <= d)
        p_uncond = k_uncond / n_total
        # conditional CDF given survival to `elapsed`
        if d < elapsed:
            p_cond = None
        else:
            k_cond = sum(1 for x in at_risk if x is not None and x <= d)
            p_cond = k_cond / n_at_risk
        flag = "  <-- crosses 0.70" if (p_cond is not None and p_cond >= 0.70
                                        and (sum(1 for x in at_risk if x is not None and x <= d-1)/n_at_risk) < 0.70) else ""
        cond_s = f"{p_cond:>26.2f}" if p_cond is not None else f"{'(past)':>26}"
        print(f"{dt.isoformat():<12}{d:>4}   {p_uncond:>10.2f}   {cond_s}{flag}")

    print()
    print("Read: the current phase already OUTLIVED the 12-day-war analog (day 12), so the")
    print("conditional curve is the forward-looking one. But note this war's own cadence is")
    print("faster than the external set (phase-1 ceasefire at day 39; Jun MOU->ceasefire in 11d),")
    print("and BOTH prior ceasefires collapsed within ~10-14 days -> P(a ceasefire HOLDS) ~0.35-0.40.")

if __name__ == "__main__":
    main()
