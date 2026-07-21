"""macro_view.py — renders the 🌍 Macro & Events tab.

Self-contained: needs only macro_events.py (data + logic) + streamlit + pandas, so it
drops into any Streamlit app as one tab. No personal names of officials anywhere — actors
are referred to by institution/role only.
"""
import pandas as pd
import streamlit as st

from macro_events import build_calendar, peace_model

_FLAG_EMOJI = {"TODAY": "🔴 TODAY", "TOMORROW": "🟠 TOMORROW",
               "THIS WEEK": "🟡 this wk", "UPCOMING": "⚪"}
_CAT_EMOJI = {"Trade": "🤝", "CenBank": "🏦", "Data": "📊", "Expiry": "⏳", "Geo": "🌍"}

_CSS = """<style>
  .macro-card {border:1px solid rgba(128,128,128,.25); border-radius:14px;
               padding:14px 18px; margin-bottom:12px; background:rgba(128,128,128,.05);}
  .macro-kv {color:#888; font-size:.82rem;}
</style>"""


def render_macro(data, today, alerts_dict=None):
    """Render the macro tab. `data` = macro_events.load_data(); `today` = a date."""
    st.markdown(_CSS, unsafe_allow_html=True)
    st.caption(f"Macro catalysts that move NIFTY · **{today:%a %d %b %Y}** (IST) · "
               f"data as of **{data.get('last_updated', '?')}** ({data.get('updated_by', '?')}) · "
               "refreshes daily 7 AM IST.")

    # --- live market strip (free, yfinance) ---
    mk = data.get("market") or {}
    _row = []
    if mk.get("brent"):
        _row.append(f"🛢 Brent ${mk['brent']['last']:,.2f} ({mk['brent']['chg_pct']:+.2f}%)")
    if mk.get("usdinr"):
        _row.append(f"💵 USD/INR {mk['usdinr']['last']:,.2f} ({mk['usdinr']['chg_pct']:+.2f}%)")
    if mk.get("indiavix"):
        _row.append(f"📉 India VIX {mk['indiavix']['last']:,.2f} ({mk['indiavix']['chg_pct']:+.2f}%)")
    if _row:
        st.caption("&nbsp;&nbsp;·&nbsp;&nbsp;".join(_row) + f"&nbsp;&nbsp;· as of {mk.get('fetched', '')}")

    # --- TODAY / TOMORROW banner ---
    if alerts_dict is None:
        from macro_events import alerts as _alerts
        alerts_dict = _alerts(today, data)
    if alerts_dict["today"]:
        st.error("🔴 **TODAY:** " + " · ".join(e["name"] for e in alerts_dict["today"]))
    if alerts_dict["tomorrow"]:
        st.warning("🟠 **TOMORROW:** " + " · ".join(e["name"] for e in alerts_dict["tomorrow"]))
    if not alerts_dict["today"] and not alerts_dict["tomorrow"]:
        st.success("🟢 No high-impact scheduled event today or tomorrow.")

    # --- Event calendar ---
    st.subheader("🗓️ Event calendar")
    rows = []
    for r in build_calendar(today, data):
        when = _FLAG_EMOJI.get(r["flag"], r["flag"])
        if r["flag"] == "UPCOMING":
            when = f"⚪ D-{r['days_until']}"
        rows.append({
            "When": when,
            "Date": r["date"].strftime("%a %d %b"),
            "Event": f"{_CAT_EMOJI.get(r['category'], '')} {r['name']}".strip(),
            "Impact on NIFTY": r.get("impact", ""),
            "❗": "🔺" if r.get("importance") == "high" else "",
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    eb = data.get("earnings_band", {})
    if eb.get("active"):
        st.caption(f"📈 **{eb.get('label', 'Earnings')}** — {eb.get('note', '')}")

    # --- Geopolitical trackers ---
    st.divider()
    st.subheader("🌍 Geopolitical trackers")
    st.caption("P(announcement) from historical gaps × P(implementation) discounted for "
               "policy-reversal fickleness. See MACRO_EVENT_TRACKER.md for the full method.")
    for ev in data.get("dynamic", []):
        p = ev.get("prob", {})
        prob_line = " · ".join(f"{k.replace('_', ' ')}: **{v:.0%}**"
                               for k, v in p.items() if isinstance(v, (int, float)))
        html = (f'<div class="macro-card">'
                f'<b>{ev["name"]}</b><br>'
                f'<span class="macro-kv">{ev.get("status", "")}</span><br>'
                f'<span class="macro-kv"><b>Direction:</b> {ev.get("direction", "")}</span><br>'
                f'<span class="macro-kv"><b>Probabilities:</b> {prob_line}</span><br>'
                f'<span class="macro-kv"><b>Combined:</b> {ev.get("combined", "")} · '
                f'<b>Watch:</b> {ev.get("watch", "")}</span></div>')
        st.markdown(html, unsafe_allow_html=True)

    # --- latest headlines (free, GDELT) ---
    hl = data.get("headlines") or {}
    if hl.get("iran") or hl.get("india_us"):
        st.divider()
        st.subheader("📰 Latest headlines")
        st.caption(f"Auto-fetched from GDELT (free, no key) · as of {hl.get('fetched', '')} "
                   "· personal names scrubbed to roles")
        for label, key in (("🌍 Iran-US war", "iran"), ("🤝 India-US trade deal", "india_us")):
            arts = hl.get(key) or []
            if arts:
                st.markdown(f"**{label}**")
                for a in arts[:5]:
                    meta = (f" <span class='macro-kv'>· {a.get('domain', '')} · "
                            f"{a.get('date', '')}</span>")
                    line = (f"- [{a.get('title', '')}]({a['url']})" if a.get("url")
                            else f"- {a.get('title', '')}")
                    st.markdown(line + meta, unsafe_allow_html=True)

    # --- Iran time-to-peace model ---
    st.divider()
    st.subheader("🕊️ Iran time-to-peace model")
    pm = peace_model(today, data["peace_model"])
    cross = next((r for r in pm["rows"] if r["crosses_70"]), None)
    adjusted = pm["signal_mult"] != 1.0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Phase day", pm["elapsed"])
    c2.metric("Median (days)", pm["median"])
    c3.metric("P>0.70 by", cross["date"].strftime("%d %b") if cross else "—")
    c4.metric("Signal adj.", f"{pm['signal_mult']:g}×",
              delta=(None if not adjusted else
                     ("dovish → sooner" if pm["signal_mult"] > 1 else "hawkish → later")))
    st.caption(f"Survival curve over {pm['n_resolved']} comparable conflicts "
               f"(+{pm['n_censored']} never-resolved). Phase start "
               f"{pm['phase_start']:%d %b} — {pm['phase_note']}. "
               f"**P(ceasefire holds if announced) = {pm['hold_prob']:.0%}** — "
               "a ceasefire announcement ≠ durable peace (both prior 2026 ceasefires collapsed in ~10-14d).")

    if pm["signals"]:
        st.markdown("**📡 Active signals** — news currently bending the curve:")
        for s in pm["signals"]:
            tag = "🕊️ dovish" if s["kind"] == "dovish" else "⚔️ hawkish"
            st.caption(f"• {s['date']:%d %b} · {tag} ({s['weight']}, ×{s['mult']:g}, age {s['age']}d) — {s['note']}")
    else:
        st.caption("📡 No active diplomatic signals — base-rate curve unadjusted. "
                   "The daily refresh appends dovish/hawkish signals from the news (institutions/roles, no personal names).")

    pm_rows = []
    for r in pm["rows"]:
        row = {"Date": r["date"].strftime("%a %d %b"), "Phase day": r["phase_day"]}
        if adjusted:
            row["Base P"] = f"{r['p_base']:.0%}"
        row["P(ceasefire/talks announced)"] = f"{r['p']:.0%}"
        row[""] = "← crosses 0.70" if r["crosses_70"] else ""
        pm_rows.append(row)
    st.dataframe(pd.DataFrame(pm_rows), hide_index=True, use_container_width=True)

    with st.expander("📎 Sources & method"):
        st.markdown(
            "- **Method:** P(announcement) from historical gaps between announcements; "
            "P(implementation) discounted for policy-reversal fickleness (escalation threats "
            "revert ~70%, signed deals hold ~75%). Peace model = empirical survival curve "
            "conditional on the current fighting phase surviving to today.\n"
            "- **Signals overlay:** dated dovish/hawkish news shifts the curve via a "
            "proportional-hazards multiplier (S_adj = S_base^M) that decays with age.\n"
            "- Full write-up: `MACRO_EVENT_TRACKER.md`; code: `macro_events.py`; refresh: `MACRO_REFRESH.md`.")
