"""macro_view.py — renders the 🌍 Macro & Events tab (v2).

Self-contained: needs only macro_events.py (data + logic) + streamlit + pandas, so it drops into any
Streamlit app as one tab. No personal names of officials anywhere — actors are institutions/roles.

Sections, top to bottom:
  backdrop strip (policy rates, CPI, crude/INR/VIX/DXY/UST) → TODAY/TOMORROW banner →
  🗓️ scheduled (filterable calendar) → ⏳ pending any-day catalysts → 📈 big-company results →
  🏭 sector policy radar (curated stance + auto-tagged headlines) → 🏦 RBI wire →
  🌍 geopolitical trackers + headlines → 🕊️ Iran time-to-peace model → sources.
"""
from datetime import timedelta

import pandas as pd
import streamlit as st

from macro_events import (alerts as _alerts, build_calendar, build_pending, earnings_window,
                          peace_model, sector_news)

_FLAG = {"TODAY": "🔴 TODAY", "TOMORROW": "🟠 TOMORROW", "THIS WEEK": "🟡 this wk"}
_CAT = {"Trade": "🤝", "CenBank": "🏦", "Data-IN": "📊", "Data-US": "🇺🇸", "Expiry": "⏳", "Geo": "🌍",
        "Politics": "🗳️", "Flows": "🔁", "Earnings": "📈", "Policy": "🏛️", "Rupee": "💵", "Sector": "🏭",
        "Markets": "📉", "Macro": "🌐"}
_IMP = {"high": "🔺 high", "med": "▫️ med", "low": "· low"}
_IDX = {"NIFTY50": "NIFTY 50", "NIFTYNEXT50": "Next 50", "BANK": "Bank Nifty"}
_STANCE = {"+": "🟢 supportive", "-": "🔴 adverse", "−": "🔴 adverse", "±": "🟡 mixed"}

_CSS = """<style>
  .macro-card {border:1px solid rgba(128,128,128,.25); border-radius:14px;
               padding:12px 16px; margin-bottom:10px; background:rgba(128,128,128,.05);}
  .macro-kv {color:#888; font-size:.82rem;}
  .macro-p {display:inline-block; min-width:64px; text-align:center; border-radius:8px;
            padding:2px 8px; font-weight:600; font-size:.85rem; color:#fff;}
  .macro-strip {font-size:.9rem; line-height:1.9;}
</style>"""


def _when(r):
    return _FLAG.get(r["flag"]) or f"⚪ D-{r['days_until']}"


def _pbadge(p):
    if p is None:
        return '<span class="macro-p" style="background:#777">n/a</span>'
    col = "#c62828" if p >= 0.6 else "#ef6c00" if p >= 0.3 else "#607d8b"
    return f'<span class="macro-p" style="background:{col}">{p:.0%}</span>'


def _table(df: pd.DataFrame, cfg=None):
    """Full-width dataframe across Streamlit versions (width= is new; use_container_width is legacy)."""
    try:
        st.dataframe(df, hide_index=True, width="stretch", column_config=cfg or {})
    except TypeError:
        try:
            st.dataframe(df, hide_index=True, use_container_width=True, column_config=cfg or {})
        except TypeError:                     # very old streamlit: no column_config
            st.dataframe(df, hide_index=True, use_container_width=True)


def _link_cfg(label="🔗"):
    try:
        return st.column_config.LinkColumn(label, display_text="open", width="small")
    except Exception:  # noqa: BLE001
        return None


def render_macro(data, today, alerts_dict=None):
    """Render the macro tab. `data` = macro_events.load_data(); `today` = a date."""
    st.markdown(_CSS, unsafe_allow_html=True)
    st.caption(f"Everything that can move NIFTY in the next month · **{today:%a %d %b %Y}** (IST) · "
               f"data as of **{data.get('last_updated', '?')}** ({data.get('updated_by', '?')}) · "
               "refreshes daily 7 AM IST · no personal names of officials (institutions/roles only).")

    # --- backdrop strip: policy rates + market channels ---
    rt, mk = data.get("rates") or {}, data.get("market") or {}
    s1 = []
    if rt.get("rbi_repo") is not None:
        s1.append(f"🏦 **RBI repo {rt['rbi_repo']:.2f}%** — {rt.get('rbi_bias', '')}")
    if rt.get("fed_funds"):
        s1.append(f"🇺🇸 **Fed {rt['fed_funds']}** — {rt.get('fed_bias', '')}")
    ci = rt.get("india_cpi") or {}
    if ci.get("value") is not None:
        s1.append(f"📊 **India CPI {ci['value']:.2f}%** ({ci.get('period', '')}; prev {ci.get('prev', '?')}%)")
    s2 = []
    for key, lab, fmt in (("brent", "🛢 Brent", "${:,.2f}"), ("usdinr", "💵 USD/INR", "{:,.2f}"),
                          ("indiavix", "📉 India VIX", "{:,.2f}"), ("dxy", "💲 DXY", "{:,.2f}"),
                          ("us10y", "🇺🇸 US 10Y", "{:,.2f}%")):
        q = mk.get(key)
        if q:
            s2.append(f"{lab} {fmt.format(q['last'])} ({q['chg_pct']:+.2f}%)")
    if s1 or s2:
        html = '<div class="macro-strip">' + " &nbsp;·&nbsp; ".join(s1)
        if s2:
            html += "<br>" + " &nbsp;·&nbsp; ".join(s2) + f' <span class="macro-kv">· as of {mk.get("fetched", "")}</span>'
        st.markdown(html + "</div>", unsafe_allow_html=True)
    if rt.get("rbi_last_move") or rt.get("fed_last_move"):
        st.caption(f"RBI: {rt.get('rbi_last_move', '')} · Fed: {rt.get('fed_last_move', '')}")

    # --- TODAY / TOMORROW banner ---
    if alerts_dict is None:
        alerts_dict = _alerts(today, data)
    if alerts_dict["today"]:
        st.error("🔴 **TODAY:** " + " · ".join(e["name"] for e in alerts_dict["today"]))
    if alerts_dict["tomorrow"]:
        st.warning("🟠 **TOMORROW:** " + " · ".join(e["name"] for e in alerts_dict["tomorrow"]))
    if not alerts_dict["today"] and not alerts_dict["tomorrow"]:
        st.success("🟢 No high-impact scheduled event today or tomorrow.")

    # ================= 1. SCHEDULED =================
    st.subheader("🗓️ Scheduled — dates we know")
    c1, c2 = st.columns([1, 3])
    with c1:
        window = st.radio("Window", [14, 30, 60, 120], index=1, horizontal=True, key="macro_window",
                          format_func=lambda d: f"{d}d")
    cal_all = build_calendar(today, data, window_days=120)
    cats = sorted({r["category"] for r in cal_all})
    with c2:
        pick = st.multiselect("Categories", cats, default=cats, key="macro_cats",
                              format_func=lambda c: f"{_CAT.get(c, '')} {c}")
    show_low = st.checkbox("Show low-impact prints (WPI, IIP, PMI, GST, auto sales, minutes)", value=False,
                           key="macro_low")
    rows = []
    for r in cal_all:
        if r["days_until"] > window or r["category"] not in pick:
            continue
        if not show_low and r.get("importance") == "low":
            continue
        rows.append({"When": _when(r), "Date": r["date"].strftime("%a %d %b"),
                     "Event": f"{_CAT.get(r['category'], '')} {r['name']}".strip(),
                     "Impact": _IMP.get(r.get("importance", "low"), ""),
                     "NIFTY session": r.get("nifty_session", ""),
                     "What it does to NIFTY": r.get("impact", ""),
                     "🔗": r.get("source") or None})
    if rows:
        cfg = {"🔗": _link_cfg()} if _link_cfg() else None
        _table(pd.DataFrame(rows), cfg)
    else:
        st.info("Nothing scheduled in this window for the chosen categories.")
    st.caption("Recurring prints are generated from official calendars (RBI MPC FY27, Fed FOMC 2026-27, MOSPI ARC "
               "2026-27, BLS); weekend/holiday prints roll to the next working day. US times converted to IST "
               "(EDT/EST aware); 'next day' = the NSE session that reacts.")

    # ================= 2. PENDING =================
    st.divider()
    st.subheader("⏳ Pending — can land any day (next ~30 days)")
    st.caption("Undated catalysts. **P(30d)** = subjective base-rate estimate (gap method, see "
               "MACRO_EVENT_TRACKER.md); the Iran item takes its P from the live time-to-peace model. "
               "Sorted by probability.")
    pm = peace_model(today, data["peace_model"])
    pend = build_pending(today, data, pm)
    for it in pend:
        secs = " · ".join(it.get("sectors", []))
        src = (f' · <a href="{it["source"]}" target="_blank">source</a>' if it.get("source") else "")
        age = f" · updated {it['age_days']}d ago" if it.get("age_days") is not None else ""
        html = (f'<div class="macro-card">{_pbadge(it["p"])} &nbsp; '
                f'<b>{_CAT.get(it.get("category", ""), "")} {it["name"]}</b>'
                f'<span class="macro-kv"> · {it.get("category", "")}{age}{src}</span><br>'
                f'<span class="macro-kv">{it.get("status", "")}</span><br>'
                f'<span class="macro-kv"><b>If it lands:</b> {it.get("direction", "")}</span><br>'
                f'<span class="macro-kv"><b>Sectors:</b> {secs} &nbsp; <b>Watch:</b> {it.get("watch", "")}</span>'
                f'</div>')
        st.markdown(html, unsafe_allow_html=True)
    if not pend:
        st.info("No pending items — the refresh agent fills this list.")

    # ================= 3. EARNINGS =================
    st.divider()
    st.subheader("📈 Big-company results (NIFTY 50 · Next 50 · Bank Nifty)")
    earn = data.get("earnings") or {}
    ew = earnings_window(today, data, days=window)
    load = earn.get("load") or {}
    if ew:
        st.caption(f"Board meetings to approve results, from the NSE event calendar (as of {earn.get('fetched', '?')}). "
                   f"**{len(ew)}** index names in the next {window} days. 'All NSE' = total companies reporting that day "
                   "(a crowded day dilutes single-name moves).")
        rows = [{"When": _when(e), "Date": e["date"].strftime("%a %d %b"), "Symbol": e["symbol"],
                 "Company": e["company"], "Industry": e.get("industry", ""), "Index": _IDX.get(e.get("index"), ""),
                 "All NSE": load.get(e["date"].isoformat(), "")} for e in ew]
        _table(pd.DataFrame(rows))
    else:
        st.info("No index-member results dates in this window" +
                (" — results calendar not fetched yet (run update_macro.py)." if not earn.get("fetched") else "."))
    rep = earn.get("reported") or []
    if rep:
        syms = ", ".join(f"{r['symbol']} ({r['date'][5:]})" for r in rep[:25])
        st.caption(f"**Reported in the last 10 days:** {syms}" + (" …" if len(rep) > 25 else ""))

    # ================= 4. SECTOR POLICY RADAR =================
    st.divider()
    st.subheader("🏭 Sector policy radar — what the government / regulator is doing to each sector")
    sp = data.get("sector_policy") or []
    if sp:
        rows = [{"Sector": s["sector"], "Stance": _STANCE.get(s.get("stance", "±"), s.get("stance", "")),
                 "Driver": s.get("driver", ""), "Names": s.get("names", ""), "Since": s.get("date", ""),
                 "🔗": s.get("source") or None} for s in sp]
        cfg = {"🔗": _link_cfg()} if _link_cfg() else None
        _table(pd.DataFrame(rows), cfg)
    sn = sector_news(today, data, max_age_days=7)
    if sn:
        st.markdown("**📰 Policy headlines by sector (last 7 days, auto-tagged — verify before acting)**")
        by = {}
        for it in sn:
            for sec in it.get("sectors", []):
                by.setdefault(sec, []).append(it)
        order = [s["sector"] for s in sp] + sorted(k for k in by if k not in {s["sector"] for s in sp})
        for sec in [s for s in order if s in by]:
            items = by[sec]
            tones = "".join("🟢" if i["tone"] == "+" else "🔴" if i["tone"] == "−" else "🟡" if i["tone"] == "±" else "·"
                            for i in items[:8])
            with st.expander(f"{sec} — {len(items)} headlines {tones}"):
                for i in items[:12]:
                    t = {"+": "🟢", "−": "🔴", "±": "🟡"}.get(i["tone"], "·")
                    line = f"{t} {i['date'][5:] if i.get('date') else ''} · "
                    line += f"[{i['title']}]({i['url']})" if i.get("url") else i["title"]
                    st.markdown(f"- {line} <span class='macro-kv'>· {i.get('source', '')}</span>",
                                unsafe_allow_html=True)
        st.caption(f"Sources: ET industry/economy/markets, BusinessLine, Mint, PIB via GDELT · as of "
                   f"{(data.get('sector_news') or {}).get('fetched', '?')} · tone = keyword heuristic.")

    # ================= 5. RBI WIRE =================
    rb = data.get("rbi_releases") or {}
    if rb.get("press") or rb.get("notifications"):
        st.divider()
        st.subheader("🏦 RBI wire — rupee, liquidity, regulation")
        st.caption(f"Latest from the RBI RSS feeds (as of {rb.get('fetched', '?')}). Rupee-defence steps, OMO/VRR "
                   "calendars and FEMA changes land here first, usually after 17:00 IST.")
        c1, c2 = st.columns(2)
        for col, key, lab in ((c1, "press", "Press releases"), (c2, "notifications", "Notifications")):
            with col:
                st.markdown(f"**{lab}**")
                for i in (rb.get(key) or [])[:8]:
                    line = f"[{i['title']}]({i['url']})" if i.get("url") else i["title"]
                    st.markdown(f"- {i.get('date', '')[5:]} · {line}")

    # ================= 6. GEOPOLITICAL TRACKERS =================
    st.divider()
    st.subheader("🌍 Geopolitical trackers")
    st.caption("P(announcement) from historical gaps × P(implementation) discounted for policy-reversal "
               "fickleness. See MACRO_EVENT_TRACKER.md for the method.")
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

    hl = data.get("headlines") or {}
    if hl.get("iran") or hl.get("india_us"):
        st.markdown("**📰 Latest geopolitical headlines** "
                    f"<span class='macro-kv'>· GDELT · as of {hl.get('fetched', '')} · names scrubbed to roles</span>",
                    unsafe_allow_html=True)
        for label, key in (("🌍 Iran-US war", "iran"), ("🤝 India-US trade deal", "india_us")):
            arts = hl.get(key) or []
            if arts:
                st.markdown(f"*{label}*")
                for a in arts[:5]:
                    meta = f" <span class='macro-kv'>· {a.get('domain', '')} · {a.get('date', '')}</span>"
                    line = (f"- [{a.get('title', '')}]({a['url']})" if a.get("url") else f"- {a.get('title', '')}")
                    st.markdown(line + meta, unsafe_allow_html=True)

    # ================= 7. IRAN TIME-TO-PEACE MODEL =================
    st.divider()
    st.subheader("🕊️ Iran time-to-peace model")
    cross = next((r for r in pm["rows"] if r["crosses_70"]), None)
    adjusted = pm["signal_mult"] != 1.0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Phase day", pm["elapsed"])
    c2.metric("Median (days)", pm["median"])
    c3.metric("P>0.70 by", cross["date"].strftime("%d %b") if cross else "—")
    c4.metric("Signal adj.", f"{pm['signal_mult']:g}×",
              delta=(None if not adjusted else
                     ("dovish → sooner" if pm["signal_mult"] > 1 else "hawkish → later")))
    tail_note = ("" if not pm.get("tail") else
                 f" **This phase has outlasted all {pm['n_resolved']} comparables**, so the curve is a "
                 f"constant-hazard extrapolation ({pm['tail_hazard']:.3f}/day = the sample's resolution rate).")
    st.caption(f"Survival curve over {pm['n_resolved']} comparable conflicts (+{pm['n_censored']} never-resolved). "
               f"Phase start {pm['phase_start']:%d %b} — {pm['phase_note']}.{tail_note} "
               f"**P(ceasefire holds if announced) = {pm['hold_prob']:.0%}** — an announcement ≠ durable peace "
               "(both prior 2026 ceasefires collapsed in ~10-14d).")
    if pm["signals"]:
        st.markdown("**📡 Active signals** — news currently bending the curve:")
        for s in pm["signals"]:
            tag = "🕊️ dovish" if s["kind"] == "dovish" else "⚔️ hawkish"
            st.caption(f"• {s['date']:%d %b} · {tag} ({s['weight']}, ×{s['mult']:g}, age {s['age']}d) — {s['note']}")
    else:
        st.caption("📡 No active diplomatic signals — base-rate curve unadjusted.")
    with st.expander("Survival table"):
        pm_rows = []
        for r in pm["rows"][:16]:
            row = {"Date": r["date"].strftime("%a %d %b"), "Phase day": r["phase_day"]}
            if adjusted:
                row["Base P"] = f"{r['p_base']:.0%}"
            row["P(ceasefire/talks announced)"] = f"{r['p']:.0%}"
            row[""] = "← crosses 0.70" if r["crosses_70"] else ""
            pm_rows.append(row)
        _table(pd.DataFrame(pm_rows))

    with st.expander("📎 Sources & method"):
        sch = (data.get("schedule") or {}).get("sources") or {}
        st.markdown(
            "- **Scheduled:** RBI MPC FY27 calendar, Fed FOMC 2026-27 calendar (minutes = +3 weeks), MOSPI Advance "
            "Release Calendar 2026-27 (CPI 12th, IIP 28th, GDP dates), BLS schedule (US CPI/NFP), NSE holiday "
            "master (expiry/holiday rolls), SEBI 45/60-day results deadlines.\n"
            + "".join(f"  - [{k}]({v})\n" for k, v in sch.items() if v) +
            "- **Pending:** P(announcement) from historical gaps between announcements; P(implementation) "
            "discounted for fickleness (escalation threats revert ~70%, signed deals hold ~75%).\n"
            "- **Earnings:** NSE corporate event calendar (board meetings for financial results) filtered to "
            "NIFTY 50 / Next 50 / Bank Nifty members (NSE index CSVs); 'reported' from NSE announcements.\n"
            "- **Sector radar:** curated stance (refresh agent) + RSS headlines auto-tagged by keyword with a "
            "tone heuristic — a prompt to read, not a signal.\n"
            "- **Peace model:** empirical survival curve conditional on the phase surviving to today; dated "
            "dovish/hawkish signals shift it via a proportional-hazards multiplier (S_adj = S_base^M) that "
            "decays with age; constant-hazard tail once the phase outlasts every comparable.\n"
            "- Code: `macro_events.py` (logic) · `update_macro.py` (refresh) · `MACRO_REFRESH.md` (agent playbook).")
