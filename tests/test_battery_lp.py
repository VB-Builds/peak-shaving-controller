import numpy as np
import pandas as pd
import pytest

from peak_shaving.battery import Battery
from peak_shaving.config import BatterySpec, CostAssumption, Tariff
from peak_shaving.lp import solve_peak_lp


def spec(**kw):
    base = dict(capacity_kwh=100.0, power_kw=50.0, round_trip_eff=0.81, soc_min=0.1, soc_max=0.9, soc_init=0.9)
    base.update(kw)
    return BatterySpec(**base)


def test_spec_validation():
    with pytest.raises(ValueError):
        BatterySpec(round_trip_eff=0)
    with pytest.raises(ValueError):
        BatterySpec(soc_min=0.9, soc_max=0.5, soc_init=0.5)
    with pytest.raises(ValueError):
        BatterySpec(soc_init=0.95)
    with pytest.raises(ValueError):
        BatterySpec(capacity_kwh=-1)


def test_efficiency_split_and_usable():
    s = spec()
    assert s.eta_charge * s.eta_discharge == pytest.approx(0.81)
    assert s.eta_charge == pytest.approx(0.9)
    assert s.usable_kwh == pytest.approx(80.0)
    assert BatterySpec.sized(400, 0.5).power_kw == 200


def test_discharge_respects_power_limit():
    b = Battery(spec())
    assert b.step(500.0) == pytest.approx(50.0)
    # 50 kW for 15 min at eta_d 0.9 -> 12.5/0.9 kWh from the cells
    assert b.soc_kwh == pytest.approx(90 - 12.5 / 0.9)


def test_discharge_stops_at_soc_min():
    b = Battery(spec(), soc_kwh=12.0)  # 2 kWh above the 10 kWh floor
    p = b.step(50.0)
    assert p == pytest.approx(2.0 * 0.9 / 0.25)
    assert b.soc_kwh == pytest.approx(10.0)
    assert b.step(50.0) == 0.0


def test_charge_stops_at_soc_max():
    b = Battery(spec(), soc_kwh=89.0)
    p = b.step(-50.0)
    assert p == pytest.approx(-1.0 / (0.9 * 0.25))
    assert b.soc_kwh == pytest.approx(90.0)
    assert b.step(-10) == 0.0


def test_round_trip_loses_energy():
    b = Battery(spec(), soc_kwh=50.0)
    out = 0.0
    for _ in range(4):
        out += b.step(20.0) * 0.25
    taken = 50.0 - b.soc_kwh
    put = 0.0
    while b.soc_kwh < 50.0 - 1e-9:
        put += -b.step(-20.0) * 0.25
    assert out / put == pytest.approx(0.81, rel=0.02)
    assert taken > out


def test_zero_battery_does_nothing():
    b = Battery(BatterySpec(capacity_kwh=0, power_kw=0, soc_init=0.9))
    assert b.step(100) == 0.0
    assert b.step(-100) == 0.0
    assert b.soc == 0.0


def test_lp_flat_load_needs_no_battery():
    res = solve_peak_lp(np.full(96, 300.0), spec(), demand_price=15, energy_price=0.2)
    assert res.peaks_kw[0] == pytest.approx(300.0, abs=1e-6)
    assert np.abs(res.battery_kw).max() < 1e-6


def test_lp_single_spike_is_cut_by_power():
    load = np.full(96, 200.0)
    load[40] = 400.0  # 200 kW over for one interval; battery has 50 kW
    res = solve_peak_lp(load, spec(), demand_price=15, energy_price=0.2)
    assert res.peaks_kw[0] == pytest.approx(350.0, abs=1e-4)


def test_lp_energy_limited_block():
    # 2 h block 100 kW above base; usable 80 kWh * eta_d 0.9 = 72 kWh AC -> shave 36 kW over 2 h (power 50 ok)
    load = np.full(96, 200.0)
    load[40:48] = 300.0
    res = solve_peak_lp(load, spec(), demand_price=15, energy_price=0.2)
    assert res.peaks_kw[0] == pytest.approx(300 - 72 / 2, abs=1e-3)
    assert res.soc_kwh.min() >= 10 - 1e-6
    assert res.soc_kwh.max() <= 90 + 1e-6


def test_lp_recharge_between_peaks_counts_losses():
    # two 1-h blocks separated by a long valley: battery can refill and shave both
    load = np.full(192, 200.0)
    load[20:24] = 300.0
    load[150:154] = 300.0
    res = solve_peak_lp(load, spec(), demand_price=15, energy_price=0.2)
    assert res.peaks_kw[0] == pytest.approx(250.0, abs=1e-3)
    assert res.grid_kw.min() >= -1e-6


def test_lp_one_peak_per_month_group():
    load = np.concatenate([np.full(10, 100.0), np.full(10, 300.0)])
    load[15] = 500.0
    ids = np.r_[np.zeros(10, int), np.ones(10, int)]
    res = solve_peak_lp(load, spec(), month_ids=ids, demand_price=15, energy_price=0.2)
    assert len(res.peaks_kw) == 2
    assert res.peaks_kw[0] <= 100.0 + 1e-6  # may even drop: it recharges in the cheap second month
    assert res.peaks_kw[1] == pytest.approx(450.0, abs=1e-4)  # power-limited


def test_lp_never_exports():
    load = np.full(20, 10.0)  # load below battery power
    load[5] = 80.0
    res = solve_peak_lp(load, spec(), demand_price=15, energy_price=0.2)
    assert res.grid_kw.min() >= -1e-6


def test_lp_empty_load_raises():
    with pytest.raises(ValueError):
        solve_peak_lp(np.array([]), spec())


def test_tariff_demand_charge():
    t = Tariff()
    assert t.demand_chf_per_kw_month == 15.0
    assert t.demand_charge([1000] * 12) == pytest.approx(180000.0)
    assert t.energy_chf_per_kwh == pytest.approx(0.0205 + 0.1340 + 0.0027 + 0.0041 + 0.0005 + 0.0140 + 0.0230)


def test_annualized_cost_known_value():
    c = CostAssumption(installed_chf_per_kwh=1000, life_years=10, interest=0.0)
    assert c.annualized_chf_per_kwh == pytest.approx(100.0)
    c2 = CostAssumption(installed_chf_per_kwh=450, life_years=15, interest=0.03)
    assert c2.annualized_chf_per_kwh == pytest.approx(37.70, abs=0.01)


def test_month_ids_series_helper():
    from peak_shaving.controllers import month_ids
    idx = pd.date_range("2026-01-31 23:45", periods=2, freq="15min")
    ids = month_ids(idx)
    assert ids[1] == ids[0] + 1
