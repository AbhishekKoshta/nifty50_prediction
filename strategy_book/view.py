"""strategy_book.view — renders the 🎯 Strategy Book tab from whatever strategies are registered.

Nothing here is strategy-specific: every card, signal and equity curve comes from the
`STRATEGY` objects discovered in strategy_book/strategies/.
"""
import pandas as pd
import streamlit as st

from .core import (ACTIVE, ARMED, CONDITIONAL, IDLE, NEEDS_DATA, build_context, discover, fmt,
                   load_trades, run_signal, trade_stats)

_BADGE = {ACTIVE: ("🟢", "#14b8a6"), ARMED: ("🟢", "#14b8a6"), CONDITIONAL: ("🟠", "#D97757"),
          IDLE: ("⚪", "#9ca3af"), NEEDS_DATA: ("🟡", "#eab308")}
_VERDICT = {"GO": "🟩 GO", "MARGINAL-GO": "🟨 MARGINAL-GO", "MARGINAL": "🟨 MARGINAL", "PAPER": "📝 PAPER"}


def _rs(x: float) -> str:
    sign = "+" if x >= 0 else "−"
    x = abs(x)
    return f"{sign}₹{x / 1e5:.2f} L" if x >= 1e5 else f"{sign}₹{x / 1e3:.1f}k"


def _manual_inputs(ctx_probe) -> dict:
    """Optional override: today's open (before the 09:10 capture lands). 0 = use the feed."""
    man = {}
    with st.expander("✍️ Inputs"):
        op = st.number_input(f"Open {ctx_probe.next_session}", min_value=0.0, value=0.0, step=1.0,
                             help="Leave 0 to use the auto-captured open (09:10 IST run).", key="sb_open")
        if op > 0:
            man["open"] = op
        op_txt = f"✅ {fmt(ctx_probe.today_open)}" if ctx_probe.today_open else "⏳ not yet captured"
        st.caption(f"Today's open: {op_txt}")
    return man


def _signal_block(sig):
    icon, colour = _BADGE.get(sig.status, ("⚪", "#9ca3af"))
    st.markdown(f"<div style='border-left:5px solid {colour};padding:6px 12px;margin:4px 0 8px 0;"
                f"background:rgba(128,128,128,.06);border-radius:8px'><b>{icon} {sig.status}</b> — {sig.headline}"
                + (f"<br><small>{sig.instrument}</small>" if sig.instrument else "") + "</div>",
                unsafe_allow_html=True)
    if sig.levels:
        cols = st.columns(len(sig.levels))
        for c, lv in zip(cols, sig.levels):
            c.metric(lv.label, fmt(lv.value) if lv.value is not None else "live", help=lv.note or None)
            if lv.note and lv.value is not None:
                c.caption(lv.note)
            elif lv.value is None:
                c.caption(lv.note)
    if sig.steps:
        st.markdown("\n".join(f"{i}. {s}" for i, s in enumerate(sig.steps, 1)))
    if sig.why:
        st.caption("Inputs → " + sig.why)


_TRADE_COL_NAMES = {"signal_ts": "Signal candle", "entry_ts": "Entry time", "exit_ts": "Exit time", "side": "Side",
                    "entry": "Entry", "stop": "Initial stop", "exit": "Exit", "exit_reason": "Exit reason",
                    "net_pts": "Net pts", "R": "R", "net_rs": "Net ₹/lot", "lots": "Lots", "gap_pct": "Day gap %"}


def _recent_trades(s, sig):
    """Previous 5 closed trades: live (from the signal engine) when available, else the bundled backtest log."""
    t = getattr(sig, "recent_trades", None) if sig is not None else None
    src = "live replay"
    if t is None or len(t) == 0:
        log = load_trades(s.trade_log)
        if log is None or not len(log):
            return
        t, src = log.tail(5).iloc[::-1], "backtest log"
    cols = [c for c in _TRADE_COL_NAMES if c in t.columns] or list(t.columns)
    st.markdown(f"**Previous 5 trades** <small>({src})</small>", unsafe_allow_html=True)
    st.dataframe(t.head(5)[cols].rename(columns=_TRADE_COL_NAMES), hide_index=True, width="stretch")


def _card(s, sig):
    st.markdown(f"#### {s.name}")
    st.caption(f"{_VERDICT.get(s.verdict, s.verdict)} · {s.horizon} · {s.instrument} · `{s.id}`")
    st.write(s.summary)
    if sig is not None:
        _signal_block(sig)
    _recent_trades(s, sig)
    t1, t2, t3 = st.tabs(["📜 Rules", "📈 Backtest", "⚠️ Caveats"])
    with t1:
        st.markdown("\n".join(f"{i}. {r}" for i, r in enumerate(s.rules, 1)))
        st.caption(f"Source: `{s.source}`")
    with t2:
        st.markdown(" · ".join(f"**{k}** {v}" for k, v in s.stats.items()))
        t = load_trades(s.trade_log)
        if t is not None and len(t):
            m = trade_stats(t)
            c = st.columns(6)
            c[0].metric("Trades", f"{m['trades']}", f"{m['per_year']:.0f}/yr", delta_color="off")
            c[1].metric("Win", f"{m['win']:.0%}")
            c[2].metric("PF", f"{m['pf']:.2f}")
            c[3].metric("Net / lot", _rs(m["net"]))
            c[4].metric("Max DD", _rs(m["dd"]))
            c[5].metric("H1 / H2", f"{_rs(m['h1'])} / {_rs(m['h2'])}")
            st.line_chart(t.set_index("date").net_rs.cumsum().rename("cum net ₹ / lot"), height=180)
            st.caption("By year: " + " · ".join(f"{y} {_rs(v)}" for y, v in m["by_year"].items())
                       + f" · worst losing streak {m['max_loss_streak']}")
            with st.expander("Last 15 trades"):
                st.dataframe(t.tail(15).iloc[::-1], hide_index=True, width="stretch")
    with t3:
        st.markdown("\n".join(f"- {c}" for c in s.caveats) or "—")


def render_strategy_book(daily: pd.DataFrame, today_open: dict | None):
    strategies, errors = discover()
    st.markdown("### 🎯 Strategy Book — validated setups and what each one says for the next session")
    st.caption("Every card is auto-loaded from `strategy_book/strategies/`. Option P&L in the 62 studies is "
               "MODEL-priced — paper-trade first. ₹ per lot of 65, net of Zerodha costs.")
    probe = build_context(daily, today_open)
    ctx = build_context(daily, today_open, _manual_inputs(probe))
    st.markdown(f"**Session:** {ctx.next_session:%a %d %b %Y} · last close {ctx.last_date} = {fmt(ctx.last.close)}")

    sigs = {s.id: run_signal(s, ctx) for s in strategies}
    rows = [dict(Strategy=s.name, Verdict=s.verdict, Status=(sigs[s.id].status if sigs[s.id] else "—"),
                 Today=(sigs[s.id].headline if sigs[s.id] else "static card — no live signal"))
            for s in strategies]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    for s in strategies:
        st.divider()
        _card(s, sigs[s.id])
    if errors:
        with st.expander(f"⚠️ {len(errors)} strategy file(s) failed to load"):
            for e in errors:
                st.code(e)
