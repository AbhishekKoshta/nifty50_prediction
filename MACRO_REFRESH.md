# Macro tab — daily refresh playbook

This is the exact procedure the **7 AM IST refresh agent** follows (and how to run it by hand).
It updates **facts only** in `macro_events.json`. It must **never** edit the logic in
`macro_events.py` or `streamlit_app.py`. All date math (calendar D-1/D-0 flags, the peace-model
probabilities) is computed live by the app off *today's date* — so those advance on their own; this
job only keeps the **narrative + event list** current.

## What to update (in `macro_events.json`) — v2 schema
The file has two layers. **Hand-maintained** keys (you edit these): `dynamic`, `pending`, `sector_policy`,
`rates`, `calendar`, `schedule`, `peace_model`. **Auto-filled** keys (never edit — `update_macro.py` refreshes
them from NSE/RSS/yfinance and merges them back over whatever you output): `earnings`, `sector_news`,
`rbi_releases`, `index_members`, `nse_holidays`, `market`, `headlines`.

1. **`dynamic[]` trackers** — re-research and refresh `status`, `prob`, `combined`, `watch` for:
   - **India-US trade deal**: signed? new deadline? tariff level changed? Indian-side pullback?
   - **Iran-US war**: new strikes / ceasefire / peace talks? If a **new fighting phase started**
     (a ceasefire collapsed), set `peace_model.phase_start` to the new phase-start date and note it
     in `phase_note`. If a ceasefire is announced, update `prob.p_ceasefire_hold_if_announced`.
2. **`pending[]` — undated catalysts that can land ANY DAY in the next ~30 days.** This is the list a
   trader checks every morning. Keep it to the things that would actually move NIFTY or a sector:
   rupee/RBI measures (FCNR, FX-derivative curbs, oil windows), RBI liquidity ops or an off-cycle move,
   cabinet/CCEA sector schemes (PLI, EV, defence DAC, rail, power), GST Council, SEBI board decisions
   (F&O rules, levies), US tariff actions (232/301), trade-deal/FTA milestones (US tranche, EU signing),
   OPEC+ output decisions, Parliament session + bill list, sovereign rating actions, sector policy
   (sugar/ethanol, telecom AGR/spectrum, pharma). For each item update `status` (facts, dated),
   `p_30d` (subjective P of landing within 30 days, gap method), `direction` (what it does to NIFTY and
   which names), `sectors`, `watch`, `source`, `updated` (today). Add new items (unique `id`), drop
   ones that happened (move the fact into `sector_policy`/`calendar`) or died. Optional `expires`
   (ISO) auto-prunes an item. The Iran item keeps `"p_source": "peace_model"` (its P is computed live).
3. **`sector_policy[]` — the government/regulator stance per sector**, one row per sector with a dated
   driver: `sector`, `stance` (`+` supportive / `-` adverse / `±` mixed), `driver` (the concrete
   decision: cabinet approval, DAC clearance, DGFT notification, RBI circular, US tariff order…),
   `names` (listed beneficiaries/losers), `date`, `source`. Replace a row when a newer decision
   supersedes it; drop rows older than ~90 days unless still the live driver.
4. **`rates{}`** — `rbi_repo`, `rbi_last_move`, `rbi_bias`, `fed_funds`, `fed_last_move`, `fed_bias`,
   `india_cpi{period,value,prev,note}`, `as_of`. Update on every RBI/Fed decision and CPI print.
5. **`calendar[]`** — ONE-OFF dated events only (elections, budget, off-cycle meetings, FTA signings,
   MSCI review dates, special summits). Recurring prints (RBI MPC, FOMC + minutes, India CPI/WPI/IIP/GDP,
   US CPI/NFP, GST, PMI, expiry, results deadlines) are GENERATED from `schedule` — do not add them here.
   Fields: `date` (ISO), `name`, `category` (Trade|CenBank|Data-IN|Data-US|Expiry|Geo|Politics|Flows|
   Earnings|Policy|Rupee|Sector|Markets|Macro), `importance` (high|med|low), `ist_time`, `nifty_session`,
   `impact`, `source`.
6. **`schedule{}`** — the anchor facts the generator uses: `rbi_mpc_decisions[]` (decision days),
   `fomc_decisions[]` + `fomc_sep[]` (US dates), `us_cpi[]`, `us_nfp[]`, `india_gdp[]`
   (`{date,label,approx}`), `union_budget`, day-of-month for CPI/WPI/IIP. Only touch these when an
   official calendar changes or a new year's calendar is published (RBI: ~Mar; Fed: ~Jun; MOSPI: ~Feb;
   BLS: ~Dec). Extend the US lists each month so the window never runs dry.
7. **`peace_model.signals[]`** — the news→curve overlay. On any Iran diplomatic headline, append a
   dated signal (schema below). `phase_start`, `phase_note`, `hold_prob` change only on a real phase change.
8. Always bump **`last_updated`** (today, ISO) and set **`updated_by": "daily-refresh"`**.

### `signals[]` schema (the news overlay)
```json
{"date": "YYYY-MM-DD", "kind": "dovish|hawkish", "weight": "weak|medium|strong",
 "note": "<what was said — institution/role only, NO personal names>", "source": "<url>"}
```
- `kind` — dovish = de-escalation/talks/willingness; hawkish = escalation/threat/strike vow.
- `weight` — `weak` = rhetoric/statement of principle (×1.15); `medium` = concrete step, e.g. a
  scheduled meeting or mediator named (×1.4); `strong` = ceasefire framework / major escalation (×1.8).
- The model multiplies active signals (hawkish = 1/mult), decays each toward 1 with a ~10-day
  half-life, clamps net to [0.4, 2.5], and applies `S_adj = S_base ** M`. Keep only the last ~3 weeks
  of signals. Once the phase outlasts every comparable conflict the curve switches to a constant-hazard
  tail (flagged in the UI) — that is expected, not an error.

## Rules
- **No personal names anywhere in the data.** Refer to the actor by institution/role
  ("Iranian officials", "the US administration", "the commerce minister", "RBI", "the Fed") — never an
  individual's name. This applies inside `status`, `driver`, `watch`, `note` text too. URLs may contain names.
- **Facts only, no fabrication.** If a search is inconclusive, leave the field and add a short
  `"note"`. Cite sources in the commit body and in each item's `source`.
- **Keep the JSON schema intact** — same keys/types. The app reads these exact fields.
- **Probabilities**: P(announcement) from historical gaps between announcements; P(implementation)
  discounted for fickleness (escalation threats revert ~70%, signed deals hold ~75%). See
  `MACRO_EVENT_TRACKER.md` for the method — keep the two consistent.

## Validate before commit (MANDATORY)
```bash
python3 -c "import json; json.load(open('macro_events.json'))"   # valid JSON
python3 macro_events.py                                          # prints calendar, pending, peace table — no error
```
Both must succeed. If either fails, fix the JSON — do not commit broken data.

## Commit
```bash
git add macro_events.json
git commit -m "macro refresh <YYYY-MM-DD>: <one-line what changed>"
git push origin main
```
Streamlit Cloud auto-redeploys from `main`. If nothing material changed, just bump `last_updated`
(or skip the commit).

## Run it by hand
`python3 update_macro.py` runs the free layer (NSE results calendar + holidays, sector RSS, RBI wire,
market quotes) with no key; with `ANTHROPIC_API_KEY` set it also runs the research layer above. Or ask me
("refresh the macro tab") any time. The app reflects any manual JSON edit on its next load.
