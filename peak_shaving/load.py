"""Seeded generator for a SIMULATED 15-min factory load (kW, mean power per 15-min interval).

Components (all values are assumptions, not measurements of a real site):
- base load (lighting, IT, compressed-air leaks, standby): ~180 kW around the clock
- production: weekdays two shifts 06-14 and 14-22, ~650 / ~550 kW, breaks at 09:00 and 12:00, shift-change dip;
  some Saturdays a morning shift at 60 %; nothing on Sundays, public holidays, a 2-week summer and a Christmas shutdown
- start-up spikes when a shift starts (motors, compressors, furnaces heating up), bigger on Monday mornings
- random coincident events (several large loads running together), ~3 per month, 15-90 min: the peaks no
  calendar-based forecast can see
- heating (heat pumps) below 15 C outdoor and cooling (chillers) above 20 C, from a synthetic outdoor temperature with
  a seasonal cycle, a daily cycle and multi-day warm/cold spells
- AR(1) noise
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

STEP_H = 0.25
STEPS_PER_DAY = 96


def easter(year: int) -> dt.date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def holidays(year: int) -> set[dt.date]:
    """Public holidays most Swiss cantons observe (simplified list; cantonal ones vary)."""
    e = easter(year)
    days = {dt.date(year, 1, 1), dt.date(year, 1, 2), dt.date(year, 8, 1), dt.date(year, 12, 25), dt.date(year, 12, 26)}
    days |= {e - dt.timedelta(days=2), e + dt.timedelta(days=1), e + dt.timedelta(days=39), e + dt.timedelta(days=50)}
    return days


def shutdown_days(year: int) -> set[dt.date]:
    """Two-week summer shutdown (from the last Monday of July) and Christmas to New Year."""
    d = dt.date(year, 7, 31)
    while d.weekday() != 0:
        d -= dt.timedelta(days=1)
    summer = {d + dt.timedelta(days=i) for i in range(14)}
    xmas = {dt.date(year, 12, 24) + dt.timedelta(days=i) for i in range(8)}
    return summer | xmas


def outdoor_temperature(index: pd.DatetimeIndex, rng: np.random.Generator) -> np.ndarray:
    """Synthetic outdoor temperature (C): ~0 C mean in mid-January, ~19 C mid-July, +-4 K daily cycle, warm/cold spells."""
    doy = index.day_of_year.to_numpy()
    hour = index.hour.to_numpy() + index.minute.to_numpy() / 60
    seasonal = 9.5 + 9.5 * np.sin(2 * np.pi * (doy - 105) / 365.25)
    daily = 4.0 * np.cos(2 * np.pi * (hour - 15) / 24)
    days = index.normalize()
    uniq, inv = np.unique(days, return_inverse=True)
    spell = np.zeros(len(uniq))
    for i in range(1, len(uniq)):
        spell[i] = 0.8 * spell[i - 1] + rng.normal(0, 2.1)  # stationary std ~3.5 K
    # smooth the daily anomaly over the day so there are no jumps at midnight
    s = np.interp(np.arange(len(index)) / STEPS_PER_DAY, np.arange(len(uniq)) + 0.5, spell)
    return seasonal + daily + s + rng.normal(0, 0.3, len(index))


def simulate_load(start_year: int = 2025, years: int = 2, seed: int = 7, events_per_month: float = 3.0) -> pd.DataFrame:
    """Return a DataFrame indexed by 15-min interval start with columns load_kw, temp_c, production (0/1), event_kw."""
    if years < 1:
        raise ValueError("years must be >= 1")
    rng = np.random.default_rng(seed)
    idx = pd.date_range(f"{start_year}-01-01", f"{start_year + years}-01-01", freq="15min", inclusive="left")
    n = len(idx)
    temp = outdoor_temperature(idx, rng)
    hour = idx.hour.to_numpy() + idx.minute.to_numpy() / 60
    dates = idx.date
    wd = idx.day_of_week.to_numpy()

    off = set()
    for y in range(start_year, start_year + years):
        off |= holidays(y) | shutdown_days(y)
    is_off = np.fromiter((d in off for d in dates), bool, n)
    weekday = (wd < 5) & ~is_off

    # Saturday morning shifts on ~30 % of Saturdays
    sat_days = sorted({d for d, w, o in zip(dates, wd, is_off, strict=True) if w == 5 and not o})
    sat_on = {d for d in sat_days if rng.random() < 0.3}
    saturday = np.fromiter((d in sat_on for d in dates), bool, n)

    # daily production level
    uniq_days = sorted(set(dates))
    level = {d: rng.normal(1.0, 0.06) for d in uniq_days}
    lvl = np.fromiter((level[d] for d in dates), float, n)

    prod = np.zeros(n)
    s1 = weekday & (hour >= 6) & (hour < 14)
    s2 = weekday & (hour >= 14) & (hour < 22)
    prod[s1] = 650.0
    prod[s2] = 550.0
    prod[weekday & (hour >= 5.75) & (hour < 6)] = 300.0  # ramp-up
    prod[saturday & (hour >= 6) & (hour < 12)] = 390.0
    breaks = (hour >= 9) & (hour < 9.5) | (hour >= 12) & (hour < 12.5) | (hour >= 18) & (hour < 18.5)
    prod[breaks & (prod > 0)] *= 0.85
    prod[weekday & (hour >= 13.75) & (hour < 14.25)] *= 0.8  # shift change
    prod *= lvl
    production = (prod > 0).astype(int)

    # start-up spikes (one 15-min interval)
    spikes = np.zeros(n)
    start1 = weekday & (hour == 6)
    start2 = weekday & (hour == 14)
    spikes[start1] = rng.uniform(150, 350, start1.sum())
    spikes[start1 & (wd == 0)] += 100.0  # cold machines on Monday
    spikes[start2] = rng.uniform(80, 200, start2.sum())
    spikes[saturday & (hour == 6)] = rng.uniform(80, 200, (saturday & (hour == 6)).sum())

    # random coincident events during production
    event = np.zeros(n)
    months = pd.PeriodIndex(idx, freq="M")
    prod_pos = np.flatnonzero(prod > 0)
    for m in months.unique():
        cand = prod_pos[months[prod_pos] == m]
        if len(cand) == 0:
            continue
        for _ in range(rng.poisson(events_per_month)):
            start = int(rng.choice(cand))
            dur = int(rng.integers(1, 7))
            event[start:start + dur] += rng.uniform(100, 300)

    # HVAC
    occupied_heat = weekday & (hour >= 5) & (hour < 22)
    heat = 14.0 * np.clip(15.0 - temp, 0, None) * np.where(occupied_heat, 1.0, 0.5)
    heat[occupied_heat & (wd == 0) & (hour < 8)] *= 1.5  # Monday morning reheat after the weekend
    occupied_cool = weekday & (hour >= 8) & (hour < 20)
    cool = 22.0 * np.clip(temp - 20.0, 0, None) * np.where(occupied_cool, 1.0, 0.4)

    noise = np.zeros(n)
    eps = rng.normal(0, 20 * np.sqrt(1 - 0.9 ** 2), n)
    for i in range(1, n):
        noise[i] = 0.9 * noise[i - 1] + eps[i]

    load = 180.0 + prod + spikes + event + heat + cool + noise
    load = np.clip(load, 50.0, None)
    return pd.DataFrame({"load_kw": load, "temp_c": temp, "production": production, "event_kw": event}, index=idx)


def year_slice(df: pd.DataFrame, year: int) -> pd.DataFrame:
    return df.loc[f"{year}-01-01":f"{year}-12-31 23:45"]
