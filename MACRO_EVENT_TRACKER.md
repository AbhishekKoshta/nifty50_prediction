# NIFTY-50 Macro Event Tracker

**Purpose:** a running tab of geopolitical / policy events that can move NIFTY-50, each scored
with two probabilities a trader actually needs:
1. **P(announcement)** — will a concrete announcement land in the next window? Estimated from the
   *historical gaps between announcements* (base-rate / hazard method, not vibes).
2. **P(implementation | announcement)** — if announced, will it actually get done and *stick*?
   Discounted for how fickle the source is (US-administration policy-reversal / "chicken-out" base rate).

**Combined edge = P(announcement) × P(implementation | announcement)** = probability the market
gets a *real, durable* outcome (not just a headline that fades).

> These are subjective base-rate estimates. The point is the *method* and showing the work, so you
> can re-price as facts change. Ranges given; point estimate in **bold**.

**Last updated:** 2026-07-20 · **Next scheduled review:** on any deadline hit, else weekly (Sun).

---

## How the two probabilities are built

### P(announcement) — the "gap" method
For a recurring headline, list every *concrete* announcement/deadline in its history and measure the
gaps. Two readouts:
- **Hit rate** = (concrete positive announcements) ÷ (deadlines or "it's imminent" promises). Low hit
  rate ⇒ discount the next "imminent."
- **Hazard** = if concrete steps historically land every ~G weeks and a hard deadline is D days out,
  a step near the deadline is more likely (deadlines are forcing functions) but a *full* resolution
  usually slips to the next gap.

### P(implementation | announcement) — the fickleness discount
Direction matters more than a single "flips X%" number:
- **Escalation threat → implemented AND sustained: ~30%.** ~28 tariff flip-flops since Apr-2025
  "Liberation Day"; the administration typically stands down after markets drop (the policy-reversal pattern). **Fade escalation
  threats.**
- **Negotiated de-escalation / signed deal → holds: ~75%.** Once a deal is actually *signed* with a
  partner it has generally stuck; the reversal risk is on threats, not on concluded deals.

So a positive trade headline is worth more than a negative one of equal "loudness."

---

## LIVE TRACKER (as of 2026-07-20)

| # | Event | Status | Next catalyst | If it happens → NIFTY | P(announce) | P(implement \| announce) | Combined |
|---|-------|--------|---------------|----------------------|:-----------:|:-----------------------:|:--------:|
| 1 | **India-US trade deal** (1st tranche signed) | "99% / last 1%" done; partial relief already live (reciprocal 25→18%, +25% Russia tariff removed) | **Jul 24, 2026** temp-tariff expiry | Full pact = **↑ risk-on** (IT/pharma/auto); snap-back = **↓** | full pact **0.33**; *some* relief/extension **0.65** | **0.78** (signed deals stick) | full pact **~0.26**; avoid-snapback **~0.55** |
| 2 | **US reciprocal tariff on India** (18% baseline) | Cut 25→18% already implemented via EO | Rides on #1 at Jul 24 | Further cut **↑**; snap-back to 25%+ **↓↓** | further cut **0.30**; snap-back **0.18** | 0.75 / 0.35 | cut **~0.23**; snap-back **~0.06** |
| 3 | **Iran–US war / Strait of Hormuz** | Active, escalating — 9th night of US strikes (Jul 19); MOU "over" (Jul 8) | Rolling; watch Hormuz + Brent | Ceasefire **↑**; Hormuz closure/oil spike **↓↓ (crude channel)** | ceasefire (2wk) **0.28**; Hormuz closure **~0.12 tail** | ceasefire holds **0.40** | durable ceasefire **~0.11** |
| 4 | **US Fed policy path** | On hold; mkt prices possible **Sept hike** | Fed testimony / Sept FOMC | Hike/hawkish **↓ (FII, INR)**; dovish **↑** | Sept hike priced-in, not a surprise | n/a (delivered = implemented) | data-dependent |
| 5 | **RBI MPC** | Repo 5.25%, neutral; hikes called "premature" | Next MPC | Cut **↑ banks**; hawkish surprise **↓** | status-quo base case | high (RBI ≠ fickle) | low surprise risk |
| 6 | **Q1 FY27 earnings** | Season underway (TCS reported Jul 9) | Rolling through Aug | Beats **↑**; misses **↓**, stock-specific | n/a (bottom-up) | n/a | n/a |
| 7 | **Russia-oil / secondary sanctions** | +25% India tariff already *removed* on oil-stop pledge | Tied to #1 & #3 | Re-imposition **↓**; stays off **↑/neutral** | re-impose **0.20** | 0.35 (threat, fade) | **~0.07** |

**Reading the combined column:** anything <0.15 = treat as tail/headline noise, don't position for it.
0.15–0.40 = live risk, hedge don't bet. >0.50 = base case, can lean into it.

---

## Per-event base-rate work

### 1 · India-US trade deal (the main event)
**Timeline of concrete steps (the "gaps"):**
- Feb 13, 2025 — BTA negotiations formally launched.
- "Fall 2025" — original first-tranche target → **missed**.
- Feb 2026 — framework/"contours" announced (White House "historic trade deal" fact sheet) → *concrete*.
- ~Jul 2026 — EO removes extra +25% (Russia-oil); reciprocal cut 25→18% → *concrete partial implementation*.
- Jul 24, 2026 — temp 10% tariff expiry = current forcing deadline.
- Repeated "99% / very close" statements in between → *soft, non-concrete* (officials: "very close", "last 1%").

**Gap read:** concrete positive actions ~every 4-5 months (Feb-26 framework → ~Jul-26 tariff cut). "Imminent/99%"
has fired **~5-7 times with 0 fully-signed pacts** → hit-rate for "*full* tranche on this deadline" is low
(**~0.33**). But partial de-escalation is genuinely on a glide-path, so "*some* relief or a clean extension that
avoids snap-back" is much more likely (**~0.65**). Worst case — punitive snap-back to ≥25% — is the fade case (~0.15-0.20).

**Fickleness:** this is a *de-escalation/deal*, not a threat → high stick rate (**~0.78**). The risk here is Indian-side
pullback (happened once after a negotiation round), not a US reversal.

**NIFTY posture:** binary event risk into Jul 24 for IT / pharma / auto. Base case = relief or soft extension (mildly ↑);
real tail = snap-back (sharp ↓, low prob). Size for a gap, not a drift.

### 3 · Iran–US war — TIME-TO-PEACE model (the "when do they talk?" question)
Not a "will he announce" play — it's a live conflict. Model it as a **survival curve**: across comparable
market-moving conflicts, how many DAYS from the start of a fighting phase to the first ceasefire / serious
peace-talk announcement, and where is the current phase on that curve? *(reproducible: `macro_peace_model.py`)*

**This war's own history (Feb 28 start):** phase-1 ceasefire at **day 39** (Apr 8) → collapsed; Jun **MOU→ceasefire
in 11 days** (Jun 17→28) → collapsed Jul 8. **Both ceasefires held only ~10-14 days.** Current phase = **Jul 8 start,
day 12 today.**

**Comparable conflicts — days from phase start to ceasefire/serious talks:**

| Conflict | Days |
|---|---:|
| Twelve-Day War (Iran-Israel-US, Jun 2025) — *closest analog* | 12 |
| Yom Kippur (1973) | 19 |
| Iraq / Baghdad fall (2003) | 21 |
| Gaza 'Cast Lead' (2008-09) | 22 |
| Lebanon (2006) | 34 |
| **2026 Iran war — phase 1 (this war)** | 39 |
| Gulf War / Desert Storm (1991) | 42 |
| Israel-Hamas first truce (2023) | 48 |
| Kosovo (1999) | 78 |
| Russia-Ukraine (2022– ) | *never (censored)* |

**Median = 34 days · Mean = 35 days.** The current phase already **outlived the 12-day-war analog** (we're on day 12
with no ceasefire) → we're in the longer-war branch.

**Forward probability of a ceasefire/peace-talk announcement (conditional on survival to today):**

| Date | Phase day | P(announced by date) |
|---|---:|:---:|
| Jul 30 | 22 | 0.33 |
| **Aug 11** | 34 | **0.44** |
| Aug 16 | 39 | 0.56 |
| **Aug 19** | 42 | **0.67** |
| **Aug 25** | 48 | **0.78 ← crosses 0.70** |
| Sep 24 | 78 | 0.89 (ceiling) |

**Punchline:** median de-escalation ≈ **34 days → ~Aug 11**; **P>0.70 lands ~Aug 19–25, 2026** (phase day 42–48).
A *peace-talk announcement* (lower bar than a full ceasefire) typically leads the ceasefire by ~1 week → nudge
that into **early–mid August**. But **P(a ceasefire actually HOLDS) ≈ 0.35–0.40** — both prior ones collapsed in
10–14 days, so treat any announcement as a *tradeable relief pop, not a durable peace*.

- **Tail: Strait of Hormuz closure → Brent spike.** India imports ~85% crude → CAD/INR/inflation hit ⇒ NIFTY down.
  Low prob (~0.10-0.12) but high impact — *hedge* this, don't predict it.
- **Channel into NIFTY = Brent.** A ceasefire headline ≈ crude down ≈ **NIFTY relief rally** (in Jun-2025 the
  12-day-war ceasefire crashed oil ~-12% in a day). Position for the relief pop into the mid-Aug window; keep the Hormuz hedge on.

### Fickleness reference (why the discount is what it is)
~28 documented tariff flip-flops Apr-2025 → mid-2025 (the policy-reversal pattern), continued into 2026 (Jan-2026 Greenland/EU threat
walked back). Pattern: loud threat → stand-down after equities fall. ⇒ **escalation threats: implement-and-sustain ~30%;
concluded deals: ~75%.** Apply per-direction, above.

---

## NIFTY impact cheat-sheet (which way to lean)
- **Crude up (Hormuz)** → NIFTY **down** (India = oil importer). Fastest, cleanest channel. Hedge here.
- **Tariff relief / deal signed** → **up**, led by IT / pharma / auto (export-facing).
- **Tariff snap-back** → **down**, same sectors. Low prob but real gap risk on Jul 24.
- **Fed hawkish / INR weak** → **down** via FII outflows; banks sensitive.
- **RBI dovish** → **up**, banks lead. RBI is the *non-fickle* actor — trust its guidance.
- **Rule:** fade *escalation* headlines (they revert); respect *signed* outcomes and *live-conflict* oil moves.

---

## Maintenance
- **Shipped into the app** — this tracker now drives the **🌍 Macro & Events** tab of NIFTY Teller
  (`streamlit_app.py`). Live facts sit in `macro_events.json`; logic + date math in `macro_events.py`
  (calendar D-1/D-0 flags and the peace model advance automatically off today's date).
- **Daily refresh** — the 7 AM IST job follows `MACRO_REFRESH.md` (facts-only edits to
  `macro_events.json`, validate, commit, push → Streamlit Cloud redeploys).
- This markdown stays the **method/reference**; keep its probabilities consistent with the JSON.
- Update the **Status** and **Last updated** on any deadline hit or major headline; log changes below.

### Update log
- **2026-07-20** — Initial build. India-US deal at "last 1%" into Jul 24 deadline; Iran war on 9th night of US
  strikes; reciprocal tariff already cut 25→18% + Russia +25% removed.

---
### Sources (2026-07-20)
- India-US deal: [The Diplomat](https://thediplomat.com/2026/07/why-is-the-india-us-bilateral-trade-agreement-still-on-hold/) · [WH fact sheet](https://www.whitehouse.gov/fact-sheets/2026/02/fact-sheet-the-united-states-and-india-announce-historic-trade-deal/) · [Angel One (Jul 24 deadline)](https://www.angelone.in/news/economy/india-us-trade-deal-talks-focus-on-tariff-concessions-ahead-of-july-24-deadline) · [ThePrint](https://theprint.in/economy/india-us-review-trade-pact-progress-as-tariff-deadline-nears-no-clarity-on-interim-deal/2968701/)
- Iran war: [CNN live (Jul 19)](https://www.cnn.com/2026/07/19/world/live-news/iran-war-trump) · [Al Jazeera](https://www.aljazeera.com/news/liveblog/2026/7/19/iran-war-live-us-launches-new-strikes-trump-mourns-killed-soldiers) · [Wikipedia: 2026 Iran war](https://en.wikipedia.org/wiki/2026_Iran_war)
- US policy-reversal record: [Forbes reversal tracker (28 flip-flops)](https://www.forbes.com/sites/alisondurkee/2025/07/08/trump-claims-no-extension-to-new-tariff-deadline-here-are-the-28-times-hes-flip-flopped-since-liberation-day/) · [Wikipedia: policy-reversal pattern](https://en.wikipedia.org/wiki/Trump_Always_Chickens_Out)
- NIFTY outlook / triggers: [Goodreturns](https://www.goodreturns.in/news/stock-market-outlook-today-july-14-2026-sensex-nifty-likely-to-stay-resilient-despite-global-unc-1522005.html) · [NiftyTrader (Fed/RBI on hold)](https://www.niftytrader.in/markets/us-fed-and-rbi-set-to-stay-on-hold/)
