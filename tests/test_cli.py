import numpy as np
import pandas as pd
import pytest

from peak_shaving import cli, study
from peak_shaving.config import StudyConfig


def test_first_eval_start():
    assert cli.first_eval_start(pd.Timestamp("2026-01-01")) == pd.Timestamp("2026-02-01")
    assert cli.first_eval_start(pd.Timestamp("2026-01-04")) == pd.Timestamp("2026-02-01")
    assert cli.first_eval_start(pd.Timestamp("2026-01-05")) == pd.Timestamp("2026-03-01")


def _write(tmp_path, load, start="2026-01-01"):
    idx = pd.date_range(start, periods=len(load), freq="15min")
    p = tmp_path / "load.csv"
    pd.DataFrame({"time": idx, "load_kw": load}).to_csv(p, index=False)
    return p


def test_read_csv_rejects_gaps(tmp_path):
    idx = pd.date_range("2026-01-01", periods=10, freq="15min").delete(4)
    p = tmp_path / "gap.csv"
    pd.DataFrame({"time": idx, "load_kw": 1.0}).to_csv(p, index=False)
    with pytest.raises(ValueError, match="missing"):
        cli.read_load_csv(p)


def test_read_csv_rejects_bad_values(tmp_path):
    p = _write(tmp_path, ["5", "x", "7"])
    with pytest.raises(ValueError):
        cli.read_load_csv(p)
    p2 = _write(tmp_path, [5.0, -1.0, 7.0])
    with pytest.raises(ValueError):
        cli.read_load_csv(p2)


def test_read_csv_needs_load_column(tmp_path):
    p = tmp_path / "x.csv"
    pd.DataFrame({"time": ["2026-01-01 00:00"], "kw": [1]}).to_csv(p, index=False)
    with pytest.raises(ValueError, match="load_kw"):
        cli.read_load_csv(p)


def test_evaluate_command_runs(tmp_path, capsys):
    n = 70 * 96
    hour = (np.arange(n) % 96) / 4
    load = 300 + 500 * ((hour >= 6) & (hour < 20))
    load = load + np.where(np.arange(n) % 700 == 30, 300.0, 0.0)
    p = _write(tmp_path, load)
    cli.main(["evaluate", str(p), "--kwh", "200", "--threshold", "750"])
    out = capsys.readouterr().out
    assert "hindsight" in out and "forecast" in out and "fixed" in out


def test_tariff_command_prints_source(capsys):
    cli.main(["tariff"])
    out = capsys.readouterr().out
    assert "15.00" in out and "bern.ch" in out


def test_marginal_and_stops_paying():
    sw = pd.DataFrame({"controller": ["hindsight"] * 3, "capacity_kwh": [100, 200, 400],
                       "net_saving_chf": [10000.0, 15000.0, 20000.0]})
    m = study.marginal(sw, StudyConfig())
    assert list(m["chf_per_extra_kwh_year"].round(1)) == [100.0, 50.0, 25.0]
    assert study.stops_paying(m, "hindsight") == 200.0  # 100->200 earns 50 > 37.7, 200->400 earns 25 < 37.7
    kwh, val = study.best_size(sw, "hindsight", 37.7)
    assert kwh == 200 and val == pytest.approx(15000 - 7540)


def test_stops_paying_edge_cases():
    sw = pd.DataFrame({"controller": ["a", "a"], "capacity_kwh": [100, 200], "net_saving_chf": [1000.0, 1500.0]})
    assert study.stops_paying(study.marginal(sw, StudyConfig()), "a") == 0.0
    sw2 = pd.DataFrame({"controller": ["a", "a"], "capacity_kwh": [100, 200], "net_saving_chf": [9000.0, 18000.0]})
    assert study.stops_paying(study.marginal(sw2, StudyConfig()), "a") is None
