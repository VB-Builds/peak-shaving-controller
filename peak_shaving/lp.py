"""Linear programme: battery dispatch that minimises demand charge (sum of monthly peaks x price) plus the energy
cost of the battery's losses, for a known load. Used two ways:
- on the real load of a whole year = perfect hindsight (upper bound on what any controller can save);
- on a forecast of the coming month = the forecast controller's monthly threshold.
Solved with scipy's HiGHS."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp
from scipy.optimize import linprog

from .config import BatterySpec
from .load import STEP_H


@dataclass
class LPResult:
    battery_kw: np.ndarray  # + discharge / - charge (AC)
    grid_kw: np.ndarray
    soc_kwh: np.ndarray  # after each interval
    peaks_kw: np.ndarray  # one per month group


def solve_peak_lp(load_kw, spec: BatterySpec, month_ids=None, soc0_kwh: float | None = None,
                  demand_price: float = 15.0, energy_price: float = 0.2, dt_h: float = STEP_H,
                  cyclic: bool = True) -> LPResult:
    """Minimise sum_m demand_price * P_m + energy_price * sum_t grid_t * dt subject to battery limits.

    month_ids: integer group per interval (0..M-1); one peak variable per group. None = single group.
    cyclic: the battery must end at least as full as it started (no free energy from the initial charge).
    """
    load = np.asarray(load_kw, float)
    n = len(load)
    if n == 0:
        raise ValueError("empty load")
    groups = np.zeros(n, int) if month_ids is None else np.asarray(month_ids, int)
    _, groups = np.unique(groups, return_inverse=True)
    m = int(groups.max()) + 1
    cap = spec.capacity_kwh
    if cap <= 0 or spec.power_kw <= 0:
        peaks = np.array([load[groups == g].max() for g in range(m)])
        return LPResult(np.zeros(n), load.copy(), np.zeros(n), peaks)
    soc0 = spec.soc_init * cap if soc0_kwh is None else soc0_kwh
    ec, ed = spec.eta_charge, spec.eta_discharge
    # variables: c[0:n], d[n:2n], s[2n:3n], P[3n:3n+m]
    nv = 3 * n + m
    cost = np.zeros(nv)
    cost[:n] = energy_price * dt_h
    cost[n:2 * n] = -energy_price * dt_h
    cost[3 * n:] = demand_price
    t = np.arange(n)
    # SoC dynamics: s_t - s_{t-1} - ec*dt*c_t + dt/ed*d_t = 0 ; s_{-1} = soc0
    rows = np.concatenate([t, t, t, t[1:]])
    cols = np.concatenate([2 * n + t, t, n + t, 2 * n + t[1:] - 1])
    vals = np.concatenate([np.ones(n), np.full(n, -ec * dt_h), np.full(n, dt_h / ed), -np.ones(n - 1)])
    a_eq = sp.csr_matrix((vals, (rows, cols)), shape=(n, nv))
    b_eq = np.zeros(n)
    b_eq[0] = soc0
    # grid_t = load + c - d <= P_g  ->  c - d - P <= -load ;  grid >= 0 -> -c + d <= load
    rows = np.concatenate([t, t, t, n + t, n + t])
    cols = np.concatenate([t, n + t, 3 * n + groups, t, n + t])
    vals = np.concatenate([np.ones(n), -np.ones(n), -np.ones(n), -np.ones(n), np.ones(n)])
    a_ub = sp.csr_matrix((vals, (rows, cols)), shape=(2 * n, nv))
    b_ub = np.concatenate([-load, load])
    if cyclic:
        end = sp.csr_matrix(([-1.0], ([0], [3 * n - 1])), shape=(1, nv))
        a_ub = sp.vstack([a_ub, end]).tocsr()
        b_ub = np.concatenate([b_ub, [-soc0]])
    bounds = [(0, spec.power_kw)] * (2 * n) + [(spec.soc_min * cap, spec.soc_max * cap)] * n + [(0, None)] * m
    res = linprog(cost, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if res.status != 0:
        raise RuntimeError(f"LP failed: {res.message}")
    x = res.x
    c, d = x[:n], x[n:2 * n]
    bat = d - c
    grid = load - bat
    peaks = np.array([grid[groups == g].max() for g in range(m)])
    return LPResult(bat, grid, x[2 * n:3 * n], peaks)
