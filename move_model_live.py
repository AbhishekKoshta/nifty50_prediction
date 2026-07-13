#!/usr/bin/env python3
"""
move_model_live.py — NIFTY next-DAY MOVE distribution, computed LIVE off the
dashboard's daily feed (Nifty_Features.csv), so the tab refreshes with new data.

Faithful port of candle_probability/move_model.py:
  1. VOLATILITY (the real edge): causal EWMA of squared returns (RiskMetrics λ=0.94)
     → forecasts tomorrow's RANGE (sets the distribution WIDTH).
  2. DRIFT (weak edge): ridge regression of next-day return on mean-reversion/trend
     state (RSI2, dist-from-200DMA, today's & 3-day return, 60-day range pctile,
     above/below 200DMA). Small by construction — the only place a directional tilt lives.
  3. SHAPE: empirical z = ret/σ̂ reused as the template (keeps NIFTY's fat tails/skew).

Everything causal. Direction is NOT better than 50/50 (NIFTY is memoryless day-ahead);
the value is the calibrated RANGE/risk read. The heavy walk-forward validation is
included so the tab can show live coverage/PIT numbers, but it's cached upstream.
"""
import math
import os

import numpy as np
import pandas as pd

LAM = 0.94          # EWMA decay (RiskMetrics daily)
BURN = 500          # walk-forward burn-in (days) before scoring
BUCKETS = [(-100, -1.5), (-1.5, -0.7), (-0.7, -0.25), (-0.25, 0.25),
           (0.25, 0.7), (0.7, 1.5), (1.5, 100)]
BUCKET_LBL = ["down >1.5%", "down 0.7-1.5%", "down 0.25-0.7%", "flat ±0.25%",
              "up 0.25-0.7%", "up 0.7-1.5%", "up >1.5%"]
FEATS = ["rsi2_c", "dist200", "ret", "ret3", "rng_pct60", "above200"]


def rsi(series, n=2):
    d = series.diff()
    up = d.clip(lower=0.0); dn = (-d).clip(lower=0.0)
    ru = up.ewm(alpha=1 / n, adjust=False).mean()
    rd = dn.ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + ru / rd.replace(0, np.nan))


def build(df):
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    d["ret"] = d["close"].pct_change()
    var = np.full(len(d), np.nan)
    r = d["ret"].values
    seed = np.nanvar(r[1:BURN]) if len(r) > BURN else np.nanvar(r[1:])
    v = seed
    for i in range(len(d)):
        if i == 0 or np.isnan(r[i]):
            var[i] = v
        else:
            v = LAM * v + (1 - LAM) * r[i] ** 2
            var[i] = v
    d["sigma"] = np.sqrt(var)
    d["rsi2"] = rsi(d["close"], 2)
    d["ma200"] = d["close"].rolling(200).mean()
    d["ma50"] = d["close"].rolling(50).mean()
    d["dist200"] = (d["close"] - d["ma200"]) / d["ma200"]
    d["ret3"] = d["close"].pct_change(3)
    d["above200"] = (d["close"] > d["ma200"]).astype(float)
    d["rng_pct60"] = ((d["high"] - d["low"]) / d["close"]).rolling(60).apply(
        lambda w: (w.iloc[-1] > w).mean(), raw=False)
    d["fwd_ret"] = d["close"].shift(-1) / d["close"] - 1
    d["z"] = d["ret"] / d["sigma"].shift(1)
    return d


def _design(d):
    x = pd.DataFrame(index=d.index)
    x["rsi2_c"] = (d["rsi2"] - 50) / 50.0
    x["dist200"] = d["dist200"]
    x["ret"] = d["ret"]
    x["ret3"] = d["ret3"]
    x["rng_pct60"] = d["rng_pct60"] - 0.5
    x["above200"] = d["above200"] - 0.5
    return x[FEATS]


def ridge_fit(X, y, lam=10.0):
    mu = X.mean(axis=0); sd = X.std(axis=0).replace(0, 1)
    Xs = ((X - mu) / sd).values
    Xs = np.column_stack([np.ones(len(Xs)), Xs])
    A = Xs.T @ Xs + lam * np.eye(Xs.shape[1]); A[0, 0] -= lam
    beta = np.linalg.solve(A, Xs.T @ y.values)
    return mu, sd, beta


def ridge_pred(row, mu, sd, beta):
    xs = np.concatenate([[1.0], ((row - mu) / sd).values])
    return float(xs @ beta)


def predict_dist(mu_hat, sigma, z_template):
    sims = mu_hat + sigma * z_template
    probs = [float(((sims > lo / 100) & (sims <= hi / 100)).mean()) for lo, hi in BUCKETS]
    p_up = float((sims > 0).mean())
    q = {k: float(np.quantile(sims, k)) for k in (0.05, 0.16, 0.25, 0.5, 0.75, 0.84, 0.95)}
    return np.array(probs), p_up, q, float(sims.mean())


def walkforward(d):
    valid = d.dropna(subset=["fwd_ret", "sigma", "dist200", "ret3", "rng_pct60", "z"]).copy()
    if len(valid) <= BURN + 120:
        return None
    ll_model, ll_base, brier_m, brier_b, pit = [], [], [], [], []
    hits68 = hits90 = hits50 = n = 0
    vol_pred, vol_real = [], []
    beta_cache = None; last_fit_year = None
    Xall = _design(valid)
    for pos in range(BURN, len(valid)):
        i = valid.index[pos]
        yr = valid.loc[i, "date"].year
        if beta_cache is None or yr != last_fit_year:
            train = valid.iloc[:pos]
            beta_cache = ridge_fit(_design(train), train["fwd_ret"], lam=10.0)
            last_fit_year = yr
        mu, sd, beta = beta_cache
        mu_hat = ridge_pred(Xall.loc[i], mu, sd, beta)
        sigma = valid.loc[i, "sigma"]
        ztmpl = valid.iloc[:pos]["z"].dropna().values
        if len(ztmpl) < 100:
            continue
        r = valid.loc[i, "fwd_ret"]
        sims = mu_hat + sigma * ztmpl
        p_up = min(max(float((sims > 0).mean()), 1e-4), 1 - 1e-4)
        y_up = 1.0 if r > 0 else 0.0
        ll_model.append(-(y_up * math.log(p_up) + (1 - y_up) * math.log(1 - p_up)))
        ll_base.append(-(y_up * math.log(0.5) + (1 - y_up) * math.log(0.5)))
        brier_m.append((p_up - y_up) ** 2); brier_b.append((0.5 - y_up) ** 2)
        pit.append(float((sims <= r).mean()))
        lo50, hi50 = np.quantile(sims, [0.25, 0.75])
        lo68, hi68 = np.quantile(sims, [0.16, 0.84])
        lo90, hi90 = np.quantile(sims, [0.05, 0.95])
        hits50 += (lo50 <= r <= hi50); hits68 += (lo68 <= r <= hi68); hits90 += (lo90 <= r <= hi90)
        vol_pred.append(sigma); vol_real.append(abs(r)); n += 1
    pit = np.array(pit)
    return dict(
        n=n, ll_model=float(np.mean(ll_model)), ll_base=float(np.mean(ll_base)),
        brier_model=float(np.mean(brier_m)), brier_base=float(np.mean(brier_b)),
        cover50=hits50 / n, cover68=hits68 / n, cover90=hits90 / n,
        vol_corr=float(np.corrcoef(vol_pred, vol_real)[0, 1]),
        pit_mean=float(pit.mean()), pit_std=float(pit.std()),
    )


def compute(data_file: str, validate: bool = True) -> dict:
    """Full next-day move prediction from a daily OHLC CSV (date,open,high,low,close)."""
    df = pd.read_csv(data_file, usecols=["date", "open", "high", "low", "close"])
    d = build(df)

    fr = d["fwd_ret"].dropna()
    base_probs = [float(((fr > lo / 100) & (fr <= hi / 100)).mean()) for lo, hi in BUCKETS]

    valid = d.dropna(subset=["sigma", "dist200", "ret3", "rng_pct60", "z"])
    fit_rows = valid.dropna(subset=["fwd_ret"])
    mu, sd, beta = ridge_fit(_design(fit_rows), fit_rows["fwd_ret"], lam=10.0)
    last = d.iloc[-1]
    mu_hat = ridge_pred(_design(d).iloc[-1], mu, sd, beta)
    sigma = float(last["sigma"])
    ztmpl = valid["z"].dropna().values
    probs, p_up, q, exp = predict_dist(mu_hat, sigma, ztmpl)

    val = walkforward(d) if validate else None
    return dict(
        date=str(pd.Timestamp(last["date"]).date()), close=float(last["close"]),
        rsi2=float(last["rsi2"]), above200=bool(last["close"] > last["ma200"]),
        sigma=sigma, mu_hat=float(mu_hat), p_up=float(p_up), exp=float(exp),
        probs=[float(x) for x in probs], base=base_probs,
        q={str(k): float(v) for k, v in q.items()},
        bucket_lbl=BUCKET_LBL, n_days=int(len(d)),
        span=f"{d['date'].min().date()} → {d['date'].max().date()}", val=val,
    )


if __name__ == "__main__":
    import json
    here = os.path.dirname(os.path.abspath(__file__))
    out = compute(os.path.join(here, "Nifty_Features.csv"))
    print(json.dumps({k: v for k, v in out.items() if k not in ("probs", "base")}, indent=2, default=float))
    print("P(up) %.1f%%  E[move] %+.2f%%  sigma %.2f%%" % (out["p_up"] * 100, out["exp"] * 100, out["sigma"] * 100))
