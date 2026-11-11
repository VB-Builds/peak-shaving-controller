"""The study behind the README numbers: 4 strategies on one simulated evaluation year, a battery size sweep and a
seed-robustness check. Every tuned value (fixed threshold, forecast margin) is chosen on the PREVIOUS simulated
year only, never on the evaluation year."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from . import controllers as C
from .config import BatterySpec, StudyConfig, Tariff
from .load import simulate_load


def tune_margin(df: pd.DataFrame, hist_mask, spec: BatterySpec, tariff: Tariff, margins, lp_cache) -> float:
    """Margin with the best net saving on the history months that have 28 days of history (Feb-Dec)."""
    idx = df.index
    load = df["load_kw"].to_numpy()
    mask = hist_mask & (idx >= idx[0] + pd.Timedelta(days=31))
    mask &= idx >= pd.Timestamp(year=idx[mask][0].year, month=idx[mask][0].month, day=1)
    base = C.run_none(idx[mask], load[mask], spec)
    best, best_save = margins[0], -np.inf
    for m in margins:
        r = C.run_forecast(idx, load, mask, spec, tariff, m, lp_cache)
        save = C.summarize(r, base, tariff)["net_saving_chf"]
        if save > best_save + 1e-6:
            best, best_save = m, save
    return float(best)


def evaluate_size(df: pd.DataFrame, cfg: StudyConfig, spec: BatterySpec, keep_runs: bool = False) -> dict:
    idx = df.index
    load = df["load_kw"].to_numpy()
    hist = (idx.year == cfg.history_year)
    ev = (idx.year == cfg.eval_year)
    t = cfg.tariff
    cache: dict = {}
    thr = C.best_fixed_threshold(idx[hist], load[hist], spec, t)
    margin = tune_margin(df, hist, spec, t, cfg.margins, cache)
    base = C.run_none(idx[ev], load[ev], spec)
    runs = {
        "none": base,
        "fixed": C.run_threshold(idx[ev], load[ev], spec, thr),
        "forecast": C.run_forecast(idx, load, ev, spec, t, margin, cache),
        "hindsight": C.run_hindsight(idx[ev], load[ev], spec, t),
    }
    out = {"capacity_kwh": spec.capacity_kwh, "power_kw": spec.power_kw, "fixed_threshold_kw": thr,
           "forecast_margin_frac": margin,
           "summary": {k: C.summarize(r, base, t) for k, r in runs.items()}}
    if keep_runs:
        out["runs"] = runs
    return out


def monthly_table(df: pd.DataFrame, runs: dict, tariff: Tariff) -> pd.DataFrame:
    """Per month: peaks of each strategy, the forecast's planned threshold, miss flag, and the reason for a miss."""
    rows = {k: C.monthly_peaks(r) for k, r in runs.items()}
    tab = pd.DataFrame(rows)
    fc = runs["forecast"]
    planned = pd.Series(fc.planned_kw, index=fc.index).groupby([fc.index.year, fc.index.month]).first()
    tab["forecast_planned"] = planned
    tab["forecast_missed"] = C.misses(fc)
    tab["fixed_missed"] = C.misses(runs["fixed"])
    ev = df.loc[fc.index]
    reasons = []
    for (y, m), row in tab.iterrows():
        if not row["forecast_missed"]:
            reasons.append("")
            continue
        sel = (fc.index.year == y) & (fc.index.month == m)
        g = fc.grid_kw[sel]
        i = int(np.argmax(g))
        w = slice(max(0, i - 8), i + 1)  # the peak interval and the 2 h before it
        ev_kw = ev["event_kw"].to_numpy()[sel][w]
        reasons.append("random event" if ev_kw.max() > 0 else "load above last 28 days")
    tab["miss_reason"] = reasons
    tab["gap_to_hindsight_kw"] = tab["forecast"] - tab["hindsight"]
    tab["gap_chf"] = tab["gap_to_hindsight_kw"] * tariff.demand_chf_per_kw_month
    tab.index = [f"{y}-{m:02d}" for y, m in tab.index]
    return tab


def gap_breakdown(tab: pd.DataFrame) -> dict:
    miss = tab["forecast_missed"].astype(bool)
    out = {"total_gap_chf": float(tab["gap_chf"].sum()),
           "missed_months_gap_chf": float(tab.loc[miss, "gap_chf"].sum()),
           "other_months_gap_chf": float(tab.loc[~miss, "gap_chf"].sum())}
    for reason in ("random event", "load above last 28 days"):
        sel = tab["miss_reason"] == reason
        out[f"months_{reason.replace(' ', '_')}"] = int(sel.sum())
        out[f"gap_chf_{reason.replace(' ', '_')}"] = float(tab.loc[sel, "gap_chf"].sum())
    return out


def run_study(cfg: StudyConfig | None = None, quick: bool = False, log=print) -> dict:
    cfg = cfg or StudyConfig()
    t0 = time.time()
    df = simulate_load(cfg.history_year, cfg.eval_year - cfg.history_year + 1, cfg.seed)
    ev = df.loc[str(cfg.eval_year)]
    spec = BatterySpec.sized(500.0, cfg.c_rate)
    main = evaluate_size(df, cfg, spec, keep_runs=True)
    tab = monthly_table(df, main["runs"], cfg.tariff)
    log(f"default size done ({time.time() - t0:.0f} s)")

    sizes = (200, 500) if quick else cfg.sweep_kwh
    sweep = []
    for kwh in sizes:
        r = evaluate_size(df, cfg, BatterySpec.sized(float(kwh), cfg.c_rate))
        for k, s in r["summary"].items():
            if k != "none":
                sweep.append(s | {"fixed_threshold_kw": r["fixed_threshold_kw"],
                                  "forecast_margin_frac": r["forecast_margin_frac"]})
        log(f"  sweep {kwh} kWh done ({time.time() - t0:.0f} s)")
    sweep_df = pd.DataFrame(sweep)
    marg = marginal(sweep_df, cfg)

    robust = []
    if not quick:
        for seed in cfg.robustness_seeds:
            d = simulate_load(cfg.history_year, cfg.eval_year - cfg.history_year + 1, seed)
            r = evaluate_size(d, StudyConfig(seed=seed), spec)
            for k, s in r["summary"].items():
                if k != "none":
                    robust.append(s | {"seed": seed})
            log(f"  seed {seed} done ({time.time() - t0:.0f} s)")

    return {
        "config": {"seed": cfg.seed, "history_year": cfg.history_year, "eval_year": cfg.eval_year,
                   "c_rate": cfg.c_rate, "demand_chf_per_kw_month": cfg.tariff.demand_chf_per_kw_month,
                   "energy_chf_per_kwh": cfg.tariff.energy_chf_per_kwh,
                   "battery": {"round_trip_eff": spec.round_trip_eff, "soc_min": spec.soc_min, "soc_max": spec.soc_max},
                   "annualized_chf_per_kwh": cfg.cost.annualized_chf_per_kwh,
                   "installed_chf_per_kwh": cfg.cost.installed_chf_per_kwh, "life_years": cfg.cost.life_years,
                   "interest": cfg.cost.interest, "quick": quick},
        "load": {"intervals": int(len(ev)), "energy_mwh": float(ev["load_kw"].sum() * 0.25 / 1000),
                 "annual_peak_kw": float(ev["load_kw"].max()), "mean_kw": float(ev["load_kw"].mean()),
                 "min_monthly_peak_kw": float(tab["none"].min()), "max_monthly_peak_kw": float(tab["none"].max())},
        "default": {k: v for k, v in main.items() if k != "runs"},
        "monthly": tab,
        "gap": gap_breakdown(tab),
        "sweep": sweep_df,
        "marginal": marg,
        "best_size": {k: best_size(sweep_df, k, cfg.cost.annualized_chf_per_kwh) for k in ("fixed", "forecast", "hindsight")},
        "stops_paying": {k: stops_paying(marg, k) for k in ("fixed", "forecast", "hindsight")},
        "robustness": pd.DataFrame(robust),
        "runs": main["runs"],
        "df": df,
        "seconds": time.time() - t0,
    }


def marginal(sweep_df: pd.DataFrame, cfg: StudyConfig) -> pd.DataFrame:
    """Extra net saving per extra kWh between neighbouring sizes, vs the annualized cost per kWh (assumption)."""
    rows = []
    ann = cfg.cost.annualized_chf_per_kwh
    for name, g in sweep_df.groupby("controller"):
        g = g.sort_values("capacity_kwh")
        kwh = np.concatenate([[0.0], g["capacity_kwh"].to_numpy()])
        sav = np.concatenate([[0.0], g["net_saving_chf"].to_numpy()])
        for i in range(1, len(kwh)):
            per = (sav[i] - sav[i - 1]) / (kwh[i] - kwh[i - 1])
            rows.append({"controller": name, "from_kwh": kwh[i - 1], "to_kwh": kwh[i],
                         "chf_per_extra_kwh_year": per, "pays": bool(per >= ann)})
    return pd.DataFrame(rows)


def stops_paying(marg: pd.DataFrame, controller: str) -> float | None:
    """Size above which no further step in the sweep earns its annualized cost: the end of the last step that still
    pays. 0 if no step pays, None if even the last step pays."""
    g = marg[marg["controller"] == controller].sort_values("to_kwh")
    if g["pays"].iloc[-1]:
        return None
    good = g[g["pays"]]
    return float(good["to_kwh"].iloc[-1]) if len(good) else 0.0


def best_size(sweep_df: pd.DataFrame, controller: str, annualized_chf_per_kwh: float) -> tuple[float, float]:
    """Swept size with the highest net saving minus annualized battery cost; returns (kWh, CHF per year)."""
    g = sweep_df[sweep_df["controller"] == controller]
    margin = g["net_saving_chf"] - annualized_chf_per_kwh * g["capacity_kwh"]
    i = margin.idxmax()
    return float(g.loc[i, "capacity_kwh"]), float(margin.loc[i])
