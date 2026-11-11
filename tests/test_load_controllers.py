import datetime as dt

import numpy as np
import pandas as pd
import pytest

from peak_shaving import controllers as C
from peak_shaving.battery import Battery
from peak_shaving.config import BatterySpec, Tariff
from peak_shaving.load import easter, holidays, shutdown_days, simulate_load, year_slice


@pytest.fixture(scope="module")
def sim():
    return simulate_load(2025, 2, seed=3)


def test_easter_known_dates():
    assert easter(2025) == dt.date(2025, 4, 20)
    assert easter(2026) == dt.date(2026, 4, 5)
    assert easter(2024) == dt.date(2024, 3, 31)


def test_holidays_include_national_day_and_good_friday():
    h = holidays(2026)
    assert dt.date(2026, 8, 1) in h
    assert dt.date(2026, 4, 3) in h  # Good Friday
    assert dt.date(2026, 5, 14) in h  # Ascension


def test_summer_shutdown_two_weeks_from_monday():
    s = shutdown_days(2026)
    summer = sorted(d for d in s if d.month in (7, 8))
    assert len(summer) == 14
    assert summer[0].weekday() == 0


def test_simulation_shape_and_seed(sim):
    assert len(sim) == 730 * 96
    assert sim.index.freq is None or sim.index.freqstr in ("15min", "15T")
    again = simulate_load(2025, 2, seed=3)
    assert np.array_equal(sim["load_kw"].to_numpy(), again["load_kw"].to_numpy())
    other = simulate_load(2025, 2, seed=4)
    assert not np.array_equal(sim["load_kw"].to_numpy(), other["load_kw"].to_numpy())


def test_simulation_physical_ranges(sim):
    x = sim["load_kw"]
    assert x.min() >= 50.0
    assert 400 < x.mean() < 700
    assert 1100 < x.max() < 2200


def test_weekday_peaks_exceed_sundays(sim):
    y = year_slice(sim, 2026)
    by_day = y["load_kw"].groupby(y.index.date).max()
    wd = pd.Series([d.weekday() for d in by_day.index], index=by_day.index)
    assert by_day[wd < 5].median() > by_day[wd == 6].median() + 300


def test_winter_load_above_summer(sim):
    y = year_slice(sim, 2026)
    m = y["load_kw"].groupby(y.index.month).mean()
    assert m[1] > m[5]


def test_simulate_rejects_zero_years():
    with pytest.raises(ValueError):
        simulate_load(2025, 0)


def _toy(n_days=60, seed=0):
    idx = pd.date_range("2026-01-01", periods=n_days * 96, freq="15min")
    rng = np.random.default_rng(seed)
    hour = idx.hour.to_numpy()
    load = 300 + 400 * ((hour >= 6) & (hour < 20)) + rng.normal(0, 10, len(idx))
    load[rng.integers(0, len(idx), 20)] += 300
    return idx, load


def test_inlined_threshold_rule_matches_battery_model():
    idx, load = _toy(10)
    spec = BatterySpec.sized(300)
    r = C.run_threshold(idx, load, spec, 800.0)
    b = Battery(spec)
    for i in range(len(load)):
        t = 800.0
        p = b.step(load[i] - t) if load[i] > t else b.step(-(t - load[i]))
        assert r.battery_kw[i] == pytest.approx(p, abs=1e-9)
        assert r.soc[i] == pytest.approx(b.soc, abs=1e-12)


def test_threshold_rule_never_charges_above_threshold():
    idx, load = _toy()
    r = C.run_threshold(idx, load, BatterySpec.sized(300), 750.0)
    charging = r.battery_kw < 0
    assert np.all(r.grid_kw[charging] <= 750.0 + 1e-9)


def test_threshold_rule_soc_bounds():
    idx, load = _toy()
    spec = BatterySpec.sized(200)
    r = C.run_threshold(idx, load, spec, 650.0)
    assert r.soc.min() >= spec.soc_min - 1e-12
    assert r.soc.max() <= spec.soc_max + 1e-12


def test_ratchet_never_raises_peak():
    idx, load = _toy()
    spec = BatterySpec.sized(200)
    a = C.run_threshold(idx, load, spec, 700.0, ratchet=False)
    b = C.run_threshold(idx, load, spec, 700.0, ratchet=True)
    assert C.monthly_peaks(b).sum() <= C.monthly_peaks(a).sum() + 1e-6
    assert np.clip(b.battery_kw, 0, None).sum() <= np.clip(a.battery_kw, 0, None).sum() + 1e-6


def test_no_battery_summary_is_zero():
    idx, load = _toy()
    base = C.run_none(idx, load, BatterySpec())
    s = C.summarize(base, base, Tariff())
    assert s["peak_cut_pct"] == 0
    assert s["net_saving_chf"] == 0
    assert s["missed_months"] is None


def test_hindsight_beats_or_equals_rules():
    idx, load = _toy()
    spec = BatterySpec.sized(200)
    t = Tariff()
    base = C.run_none(idx, load, spec)
    h = C.summarize(C.run_hindsight(idx, load, spec, t), base, t)
    for thr in (650.0, 700.0, 800.0):
        f = C.summarize(C.run_threshold(idx, load, spec, thr), base, t)
        assert h["demand_saving_chf"] >= f["demand_saving_chf"] - 1e-6


def test_misses_flag_breached_months():
    idx = pd.date_range("2026-01-01", periods=96 * 59, freq="15min")  # Jan + Feb
    load = np.full(len(idx), 500.0)
    load[96 * 40] = 2000.0  # huge spike in February
    spec = BatterySpec.sized(100)
    r = C.run_threshold(idx, load, spec, 600.0)
    m = C.misses(r)
    assert list(m.values) == [False, True]


def test_forecast_month_repeats_last_28_days_weekday_aligned():
    idx = pd.date_range("2026-01-01", periods=96 * 90, freq="15min")
    load = np.arange(len(idx), dtype=float)
    ms, me = pd.Timestamp("2026-03-01"), pd.Timestamp("2026-04-01")
    fc = C.forecast_month(idx, load, ms, me)
    assert len(fc) == 31 * 96
    i0 = idx.searchsorted(ms)
    assert fc[0] == load[i0 - 28 * 96]
    assert fc[28 * 96] == fc[0]  # repeats after 4 weeks
    assert idx[i0 - 28 * 96].weekday() == ms.weekday()


def test_forecast_needs_history():
    idx = pd.date_range("2026-01-01", periods=96 * 40, freq="15min")
    with pytest.raises(ValueError):
        C.forecast_month(idx, np.ones(len(idx)), pd.Timestamp("2026-01-10"), pd.Timestamp("2026-02-01"))


def test_cycles_per_year_known_value():
    idx = pd.date_range("2026-01-01", periods=96 * 365, freq="15min")
    load = np.full(len(idx), 100.0)
    spec = BatterySpec(capacity_kwh=100, power_kw=100, round_trip_eff=1.0, soc_min=0.0, soc_max=1.0, soc_init=1.0)
    bat = np.zeros(len(idx))
    bat[::96] = 100.0  # 100 kW for 15 min = 25 kWh a day -> 365*25/100 = 91.25 cycles
    run = C.Run("x", idx, load, load - bat, bat, np.zeros(len(idx)), np.full(len(idx), np.nan), spec)
    s = C.summarize(run, C.run_none(idx, load, spec), Tariff())
    assert s["cycles_per_year"] == pytest.approx(91.25)
