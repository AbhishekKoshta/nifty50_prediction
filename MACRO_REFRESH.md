# Macro tab — daily refresh playbook

This is the exact procedure the **7 AM IST refresh agent** follows (and how to run it by hand).
It updates **facts only** in `macro_events.json`. It must **never** edit the logic in
`macro_events.py` or `streamlit_app.py`. All date math (calendar D-1/D-0 flags, the peace-model
probabilities) is computed live by the app off *today's date* — so those advance on their own; this
job only keeps the **narrative + event list** current.

## What to update (in `macro_events.json`)
1. **`dynamic[]` trackers** — re-research and refresh `status`, `prob`, `combined`, `watch` for:
   - **India-US trade deal**: signed? new deadline? tariff level changed? Indian-side pullback?
   - **Iran-US war**: new strikes / ceasefire / peace talks? If a **new fighting phase started**
     (a ceasefire collapsed), set `peace_model.phase_start` to the new phase-start date and note it
     in `phase_note`. If a ceasefire is announced, update `prob.p_ceasefire_hold_if_announced`.
2. **`calendar[]`** — add any newly-announced dated, high-impact events (off-cycle RBI, new tariff
   deadline, budget, major Fed speak, India CPI/GDP prints, big scheduled geopolitical dates).
   Use ISO `YYYY-MM-DD`. Past events can stay — the app filters them out. Keep FOMC/RBI/CPI dates
   accurate (correct them if the schedule changes).
3. **`peace_model.signals[]`** — the news→curve overlay. On any Iran diplomatic headline, append a
   dated signal (see schema below). This is how a *statement* (not yet a ceasefire) moves the model:
   dovish pulls the peace date earlier, hawkish pushes it out; the effect decays with age.
4. **`peace_model`** (rest) — only `phase_start`, `phase_note`, `hold_prob`, and (rarely) the
   `conflicts` sample change. Reset `phase_start` only when a ceasefire actually collapses.
5. Always bump **`last_updated`** (today, ISO) and set **`updated_by": "daily-refresh"`**.

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
  of signals; prune older ones (they've decayed to ~nil anyway).
- **Worked example:** *"Iranian officials say talks should continue even at 10% odds"* → a **weak
  dovish** statement of principle → `{"kind":"dovish","weight":"weak"}` → M≈1.15 → pulls the 0.70
  date ~1 week earlier, **hold_prob unchanged** (the "10%" framing confirms low durability).

## Rules
- **No personal names anywhere in the data.** Refer to the actor by institution/role
  ("Iranian officials", "the US administration", "RBI", "the Fed") — never an individual's name.
- **Facts only, no fabrication.** If a search is inconclusive, leave the field and add a short
  `"note"`. Cite sources in the commit body.
- **Keep the JSON schema intact** — same keys/types. The app reads these exact fields.
- **Probabilities**: P(announcement) from historical gaps between announcements; P(implementation)
  discounted for fickleness (escalation threats revert ~70%, signed deals hold ~75%). See
  `MACRO_EVENT_TRACKER.md` for the method — keep the two consistent.

## Validate before commit (MANDATORY)
```bash
python3 -c "import json; json.load(open('macro_events.json'))"   # valid JSON
python3 macro_events.py                                          # prints calendar + peace table, no error
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
Ask me ("refresh the macro tab") any time, or run the research yourself and edit the JSON following
the steps above. The app also reflects any manual JSON edit on its next load.
