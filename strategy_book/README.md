# 🎯 Strategy Book

The app tab that shows every **validated** strategy (GO / MARGINAL-GO) and what each one
says for the next session. Each strategy is one file in `strategies/`, found automatically.
You never edit `streamlit_app.py` to add one.

## Add a winning strategy (3 steps)

```bash
cd nifty50_prediction
# 1. scaffold the file and bundle its backtest trade log
python -m strategy_book.new_strategy 63_MyEdge "My edge: one-line name" \
    --verdict GO --source Algo_Nifty50/strategies/63_MyEdge \
    --trades ../../strategies/63_MyEdge/trades.csv \
    --map date=entry_date side=direction net_rs=net_rs_lot65      # add --points if net is in index pts
# 2. open strategy_book/strategies/s63_myedge.py and fill the TODOs:
#    rules / stats / caveats, plus signal(ctx) = the EXACT backtest rule, causal
# 3. verify, then push to main (Streamlit Cloud redeploys)
python -m strategy_book.check
```

`check` imports every strategy and validates each trade log. It then **replays the signal on
every backtest trade date** and fails if fewer than 90% re-signal on the same side. That
catches a live rule that has drifted from the backtest.

## The contract (`core.py`)
- `Strategy(...)` is the static card: id, name, source, verdict, horizon, instrument,
  summary, rules, stats, caveats, `trade_log`, `signal`.
- `signal(ctx) -> TodaySignal(status, headline, side, levels, instrument, steps, why)`.
  The status is ARMED, CONDITIONAL, NO TRADE or NEEDS DATA.
- `ctx` holds:
  - `daily`: completed daily OHLC
  - `last`: the last row
  - `next_session`
  - `open_px`: the next session's open, from the 09:10 IST capture or typed in
  - `last_hour`: the last session's 14:30–15:30 candle, from `last_hour.json`
- Helpers: `level_beyond`, `itm_strike`, `next_weekly`, `weekly_expiries`, `fmt`.
- Trade log schema (`trades/<id>.csv`): `date, side, entry_ts, exit_ts, exit_reason, R, net_rs`,
  where `net_rs` is net ₹ per lot of 65.
- A broken strategy file appears as a load error in the tab. It never breaks the other cards
  or the app.

## Data feeds
- Daily OHLC and the 09:10 open come from the existing `update-data.yml` workflow.
- `last_hour.json` is written by `update_last_hour.py` in the 16:00 IST run.
- If a feed is missing, the tab's **✍️ Inputs** box takes manual values.
