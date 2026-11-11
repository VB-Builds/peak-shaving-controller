"""The four strategies compared.

1. none        - no battery.
2. fixed       - one grid-power threshold for the whole year, picked as the best single value on LAST year's load.
                 Discharge whenever load > threshold, recharge whenever load < threshold (never above it).
3. forecast    - at the start of each month: forecast the month as a repeat of the last 28 days (weekdays aligned),
                 solve the peak LP on that forecast, threshold = LP peak + margin. During the month: same rule as
                 `fixed`, but once a peak above the threshold has been set it raises the threshold to that peak
                 (that peak is billed anyway, so shaving below it is wasted energy). The margin is tuned on last year.
4. hindsight   - the LP on the real load of the whole year: the upper bound, not a controller.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .battery import Battery
from .config import BatterySpec, Tariff
from .load import STEP_H
from .lp import solve_peak_lp


@dataclass
class Run:
    name: str
    index: pd.DatetimeIndex
    load_kw: np.ndarray
    grid_kw: np.ndarray
    battery_kw: np.ndarray
    soc: np.ndarray  # fraction of capacity after each interval
    planned_kw: np.ndarray  # planned threshold per interval (nan for none/hindsight)
    spec: BatterySpec


def month_ids(index: pd.DatetimeIndex) -> np.ndarray:
    return (index.year * 12 + index.month - 1).to_numpy()


def run_none(index, load_kw, spec: BatterySpec) -> Run:
    load = np.asarray(load_kw, float)
    n = len(load)
    return Run("none", index, load, load.copy(), np.zeros(n), np.zeros(n), np.full(n, np.nan), spec)


def run_threshold(index, load_kw, spec: BatterySpec, threshold_kw, ratchet: bool = False, name: str = "fixed",
                  soc0_kwh: float | None = None) -> Run:
    """Threshold rule. threshold_kw: scalar or one value per interval. ratchet: within a calendar month, the
    threshold never stays below the highest grid power already reached in that month."""
    load = np.asarray(load_kw, float)
    n = len(load)
    thr = np.broadcast_to(np.asarray(threshold_kw, float), (n,)).copy()
    # Same physics as battery.Battery.step, inlined because this loop runs millions of times in the study
    # (tests check both give identical results).
    cap = spec.capacity_kwh
    e = Battery(spec) if soc0_kwh is None else Battery(spec, soc0_kwh)
    s_kwh = e.soc_kwh
    lo, hi = spec.soc_min * cap, spec.soc_max * cap
    pmax, ec, ed, dt = spec.power_kw, spec.eta_charge, spec.eta_discharge, STEP_H
    mid = month_ids(index)
    new_month = np.r_[True, mid[1:] != mid[:-1]]
    grid = np.empty(n)
    p_out = np.empty(n)
    soc = np.empty(n)
    month_peak = -np.inf
    lt, tt, nm = load.tolist(), thr.tolist(), new_month.tolist()
    for i in range(n):
        if nm[i]:
            month_peak = -np.inf
        t = tt[i]
        if ratchet and month_peak > t:
            t = month_peak
        x = lt[i]
        if cap <= 0:
            p = 0.0
        elif x > t:
            p = min(x - t, pmax, max(0.0, s_kwh - lo) * ed / dt)
            s_kwh = max(lo, s_kwh - p * dt / ed)
        else:
            c = min(t - x, pmax, max(0.0, hi - s_kwh) / (ec * dt))
            p = -c
            s_kwh = min(hi, s_kwh + c * dt * ec)
        g = x - p
        grid[i] = g
        p_out[i] = p
        soc[i] = s_kwh / cap if cap > 0 else 0.0
        if g > month_peak:
            month_peak = g
    return Run(name, index, load, grid, p_out, soc, thr, spec)


def best_fixed_threshold(index, load_kw, spec: BatterySpec, tariff: Tariff, n_grid: int = 41) -> float:
    """Single threshold that maximises the net saving on the given (past) load."""
    load = np.asarray(load_kw, float)
    if spec.capacity_kwh <= 0 or spec.power_kw <= 0:
        return float(load.max())
    top = float(load.max())
    cands = np.linspace(top - 1.2 * spec.power_kw, top, n_grid)
    base = annual_cost(run_none(index, load, spec), tariff)["total_chf"]
    best, best_save = top, -np.inf
    for c in cands:
        r = run_threshold(index, load, spec, c)
        save = base - annual_cost(r, tariff)["total_chf"]
        if save > best_save:
            best, best_save = float(c), save
    return best


def forecast_month(full_index: pd.DatetimeIndex, full_load: np.ndarray, month_start: pd.Timestamp,
                   month_end: pd.Timestamp) -> np.ndarray:
    """Forecast = the last 28 days before the month, repeated with weekdays aligned (28 days = 4 whole weeks)."""
    steps_28 = 28 * 96
    i0 = int(full_index.searchsorted(month_start))
    if i0 < steps_28:
        raise ValueError("need 28 days of history before the month")
    hist = np.asarray(full_load[i0 - steps_28:i0], float)
    i1 = int(full_index.searchsorted(month_end))
    n = i1 - i0
    return np.resize(hist, n)


def run_forecast(full_index, full_load, eval_mask, spec: BatterySpec, tariff: Tariff, margin_frac: float,
                 lp_cache: dict | None = None) -> Run:
    """Forecast controller on the intervals selected by eval_mask (must start on a month boundary and be preceded
    by at least 28 days of history in full_load)."""
    full_load = np.asarray(full_load, float)
    idx = full_index[eval_mask]
    load = full_load[eval_mask]
    mid = month_ids(idx)
    planned = np.empty(len(idx))
    for m in np.unique(mid):
        sel = mid == m
        ms = idx[sel][0]
        me = idx[sel][-1] + pd.Timedelta(minutes=15)
        key = (int(m), spec.capacity_kwh, spec.power_kw)
        if lp_cache is not None and key in lp_cache:
            t_lp = lp_cache[key]
        else:
            fc = forecast_month(full_index, full_load, ms, me)
            t_lp = float(solve_peak_lp(fc, spec, demand_price=tariff.demand_chf_per_kw_month,
                                       energy_price=tariff.energy_chf_per_kwh).peaks_kw[0])
            if lp_cache is not None:
                lp_cache[key] = t_lp
        planned[sel] = t_lp + margin_frac * spec.power_kw
    return run_threshold(idx, load, spec, planned, ratchet=True, name="forecast")


def run_hindsight(index, load_kw, spec: BatterySpec, tariff: Tariff) -> Run:
    load = np.asarray(load_kw, float)
    res = solve_peak_lp(load, spec, month_ids(index), demand_price=tariff.demand_chf_per_kw_month,
                        energy_price=tariff.energy_chf_per_kwh)
    cap = spec.capacity_kwh
    soc = res.soc_kwh / cap if cap > 0 else np.zeros(len(load))
    return Run("hindsight", index, load, res.grid_kw, res.battery_kw, soc, np.full(len(load), np.nan), spec)


# ---------------------------------------------------------------- metrics

def monthly_peaks(run: Run, which: str = "grid") -> pd.Series:
    arr = run.grid_kw if which == "grid" else run.load_kw
    return pd.Series(arr, index=run.index).groupby([run.index.year, run.index.month]).max()


def annual_cost(run: Run, tariff: Tariff) -> dict:
    peaks = monthly_peaks(run)
    demand = tariff.demand_charge(peaks.values)
    energy = float(run.grid_kw.sum() * STEP_H * tariff.energy_chf_per_kwh)
    return {"demand_chf": demand, "energy_chf": energy, "total_chf": demand + energy}


def misses(run: Run, tol_kw: float = 0.5) -> pd.Series:
    """Per month: True if the grid peak went above the threshold planned for that month (a new peak set anyway)."""
    if np.all(np.isnan(run.planned_kw)):
        return pd.Series(dtype=bool)
    df = pd.DataFrame({"g": run.grid_kw, "p": run.planned_kw}, index=run.index)
    gb = df.groupby([run.index.year, run.index.month])
    return gb["g"].max() > gb["p"].first() + tol_kw


def summarize(run: Run, base: Run, tariff: Tariff) -> dict:
    pk, pk0 = monthly_peaks(run), monthly_peaks(base)
    c, c0 = annual_cost(run, tariff), annual_cost(base, tariff)
    usable = run.spec.usable_kwh
    discharged = float(np.clip(run.battery_kw, 0, None).sum() * STEP_H)
    ms = misses(run)
    return {
        "controller": run.name,
        "capacity_kwh": run.spec.capacity_kwh,
        "power_kw": run.spec.power_kw,
        "sum_monthly_peaks_kw": float(pk.sum()),
        "peak_cut_pct": float(100 * (1 - pk.sum() / pk0.sum())),
        "annual_max_kw": float(run.grid_kw.max()),
        "annual_max_cut_pct": float(100 * (1 - run.grid_kw.max() / base.grid_kw.max())),
        "demand_saving_chf": float(c0["demand_chf"] - c["demand_chf"]),
        "loss_cost_chf": float(c["energy_chf"] - c0["energy_chf"]),
        "net_saving_chf": float(c0["total_chf"] - c["total_chf"]),
        "cycles_per_year": float(discharged / usable * (8760 / (len(run.grid_kw) * STEP_H))) if usable > 0 else 0.0,
        "months": int(len(pk)),
        "missed_months": int(ms.sum()) if len(ms) else None,
        "miss_share": float(ms.mean()) if len(ms) else None,
    }
