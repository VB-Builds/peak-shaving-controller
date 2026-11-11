"""Command line: demo (reproduces the README numbers), simulate, evaluate (your own 15-min CSV), tariff, modbus-sim."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import controllers as C
from . import study
from .config import TARIFF_SOURCE, TARIFF_SOURCE_URL, BatterySpec, StudyConfig, Tariff
from .load import simulate_load

ROOT = Path(__file__).resolve().parent.parent
COLS = ["peak_cut_pct", "demand_saving_chf", "loss_cost_chf", "net_saving_chf", "cycles_per_year", "missed_months"]


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, list | tuple):
        return [_clean(v) for v in o]
    if isinstance(o, pd.DataFrame):
        return _clean(o.reset_index().to_dict(orient="records"))
    if isinstance(o, float | np.floating):
        return None if np.isnan(o) else round(float(o), 3)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def _fmt(v, nd=1):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    return f"{v:,.{nd}f}".replace(",", " ")


def results_md(r: dict) -> str:
    c, ld, d = r["config"], r["load"], r["default"]
    L = ["# Measured results (simulated load)", "",
         f"Produced by `python -m peak_shaving demo`{' --quick' if c['quick'] else ''}. Simulated factory load, seed {c['seed']}; "
         f"fixed threshold and forecast margin tuned on {c['history_year']}, everything below measured on {c['eval_year']}.",
         f"Load {c['eval_year']}: {_fmt(ld['energy_mwh'], 0)} MWh, mean {_fmt(ld['mean_kw'], 0)} kW, annual peak "
         f"{_fmt(ld['annual_peak_kw'], 0)} kW, monthly peaks {_fmt(ld['min_monthly_peak_kw'], 0)}-{_fmt(ld['max_monthly_peak_kw'], 0)} kW.",
         f"Tariff: CHF {c['demand_chf_per_kw_month']:.2f}/kW/month on the monthly 15-min peak; losses charged at CHF {c['energy_chf_per_kwh']:.4f}/kWh.",
         "", f"## Default battery: {d['capacity_kwh']:.0f} kWh / {d['power_kw']:.0f} kW", "",
         f"Fixed threshold (best on {c['history_year']}): {d['fixed_threshold_kw']:.0f} kW. Forecast margin (best on {c['history_year']}): "
         f"{d['forecast_margin_frac']:.2f} x battery power.", "",
         "| strategy | sum of monthly peaks (kW) | peak cut | annual max cut | demand saving CHF/yr | extra energy CHF/yr | net CHF/yr | cycles/yr | missed months |",
         "|---|---|---|---|---|---|---|---|---|"]
    for k, s in d["summary"].items():
        L.append(f"| {k} | {_fmt(s['sum_monthly_peaks_kw'], 0)} | {_fmt(s['peak_cut_pct'])} % | {_fmt(s['annual_max_cut_pct'])} % | "
                 f"{_fmt(s['demand_saving_chf'], 0)} | {_fmt(s['loss_cost_chf'], 0)} | {_fmt(s['net_saving_chf'], 0)} | "
                 f"{_fmt(s['cycles_per_year'])} | {'-' if s['missed_months'] is None else str(s['missed_months']) + '/' + str(s['months'])} |")
    L += ["", "## Per month (kW)", "",
          "| month | no battery | fixed | forecast | hindsight | forecast planned | forecast missed | reason | gap to hindsight CHF |",
          "|---|---|---|---|---|---|---|---|---|"]
    for mth, row in r["monthly"].iterrows():
        L.append(f"| {mth} | {row['none']:.0f} | {row['fixed']:.0f} | {row['forecast']:.0f} | {row['hindsight']:.0f} | "
                 f"{row['forecast_planned']:.0f} | {'yes' if row['forecast_missed'] else ''} | {row['miss_reason']} | {row['gap_chf']:.0f} |")
    g = r["gap"]
    L += ["", f"Forecast vs hindsight: CHF {g['total_gap_chf']:.0f}/yr in total; CHF {g['missed_months_gap_chf']:.0f} in missed months "
          f"({g['months_random_event']} with a random event at the peak: CHF {g['gap_chf_random_event']:.0f}; "
          f"{g['months_load_above_last_28_days']} where the load simply ran above the last 28 days: "
          f"CHF {g['gap_chf_load_above_last_28_days']:.0f}), CHF {g['other_months_gap_chf']:.0f} in months it held its threshold "
          "(threshold planned higher than needed).", "",
          f"## Battery size sweep (power = {c['c_rate']} x capacity)", "",
          "| kWh | kW | fixed net CHF/yr | forecast net CHF/yr | hindsight net CHF/yr | forecast peak cut | forecast cycles/yr | forecast missed |",
          "|---|---|---|---|---|---|---|---|"]
    sw = r["sweep"]
    for kwh, g2 in sw.groupby("capacity_kwh"):
        x = {row["controller"]: row for _, row in g2.iterrows()}
        L.append(f"| {kwh:.0f} | {x['forecast']['power_kw']:.0f} | {_fmt(x['fixed']['net_saving_chf'], 0)} | "
                 f"{_fmt(x['forecast']['net_saving_chf'], 0)} | {_fmt(x['hindsight']['net_saving_chf'], 0)} | "
                 f"{_fmt(x['forecast']['peak_cut_pct'])} % | {_fmt(x['forecast']['cycles_per_year'])} | {int(x['forecast']['missed_months'])}/12 |")
    L += ["", f"Extra net saving per extra kWh per year vs the assumed annualized cost CHF {c['annualized_chf_per_kwh']:.1f}/kWh/yr "
          f"(CHF {c['installed_chf_per_kwh']:.0f}/kWh installed, {c['life_years']} years, {100 * c['interest']:.0f} %):", "",
          "| step (kWh) | fixed | forecast | hindsight |", "|---|---|---|---|"]
    mg = r["marginal"]
    for (a, b), g3 in mg.groupby(["from_kwh", "to_kwh"]):
        x = {row["controller"]: row["chf_per_extra_kwh_year"] for _, row in g3.iterrows()}
        L.append(f"| {a:.0f} -> {b:.0f} | {x['fixed']:.1f} | {x['forecast']:.1f} | {x['hindsight']:.1f} |")
    L.append("")
    for k in ("fixed", "forecast", "hindsight"):
        sp = r["stops_paying"][k]
        kwh, val = r["best_size"][k]
        txt = f"extra kWh stops paying above {sp:.0f} kWh" if sp is not None else "extra kWh still pays at the largest size"
        L.append(f"- {k}: {txt}; best swept size {kwh:.0f} kWh (net saving minus annualized cost CHF {val:,.0f}/yr)".replace(",", " "))
    rb = r["robustness"]
    if len(rb):
        L += ["", f"## Robustness: {rb['seed'].nunique()} other seeds, 500 kWh / 250 kW", "",
              "| strategy | net CHF/yr mean | min | max | peak cut mean | missed months mean |", "|---|---|---|---|---|---|"]
        for k, g4 in rb.groupby("controller", sort=False):
            mm = g4["missed_months"].dropna()
            L.append(f"| {k} | {_fmt(g4['net_saving_chf'].mean(), 0)} | {_fmt(g4['net_saving_chf'].min(), 0)} | "
                     f"{_fmt(g4['net_saving_chf'].max(), 0)} | {_fmt(g4['peak_cut_pct'].mean())} % | "
                     f"{_fmt(mm.mean()) if len(mm) else '-'} |")
    L += ["", f"Run time: {r['seconds']:.0f} s.", ""]
    return "\n".join(L)


def cmd_demo(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    r = study.run_study(StudyConfig(seed=a.seed), quick=a.quick)
    js = {k: v for k, v in r.items() if k not in ("runs", "df")}
    js["monthly"] = r["monthly"].rename_axis("month")
    (out / "results.json").write_text(json.dumps(_clean(js), indent=1), encoding="utf-8")
    (out / "results.md").write_text(results_md(r), encoding="utf-8")
    if not a.no_figures:
        from .figures import make_all
        make_all(r, out)
    s = r["default"]["summary"]
    for k in ("fixed", "forecast", "hindsight"):
        print(f"{k:10s} " + "  ".join(f"{c}={s[k][c]:.1f}" if s[k][c] is not None else f"{c}=-" for c in COLS))
    print(f"wrote {out}")


def cmd_simulate(a):
    df = simulate_load(a.start_year, a.years, a.seed)
    df.round(2).to_csv(a.out, index_label="time")
    print(f"wrote {len(df)} intervals to {a.out}")


def read_load_csv(path) -> pd.Series:
    """CSV with a time column (interval start) and a load_kw column (15-min mean kW). Rejects gaps and bad values."""
    df = pd.read_csv(path)
    tcol = "time" if "time" in df.columns else df.columns[0]
    if "load_kw" not in df.columns:
        raise ValueError("CSV needs a 'load_kw' column")
    s = pd.Series(pd.to_numeric(df["load_kw"], errors="coerce").to_numpy(), index=pd.to_datetime(df[tcol]))
    s = s[~s.index.duplicated()].sort_index()
    if s.isna().any() or (s < 0).any():
        raise ValueError("load_kw has missing, non-numeric or negative values")
    full = pd.date_range(s.index[0], s.index[-1], freq="15min")
    if len(full) != len(s):
        raise ValueError(f"{len(full) - len(s)} missing 15-min intervals; fill or cut them first")
    return s


def first_eval_start(t0: pd.Timestamp) -> pd.Timestamp:
    """First month start with at least 28 days of data before it."""
    cand = t0 + pd.Timedelta(days=28)
    return (cand - pd.Timedelta(minutes=1)).to_period("M").to_timestamp() + pd.offsets.MonthBegin(1)


def cmd_evaluate(a):
    s = read_load_csv(a.csv)
    t = Tariff(demand_chf_per_kw_month=a.price) if a.price else Tariff()
    spec = BatterySpec(capacity_kwh=a.kwh, power_kw=a.kw or a.kwh * 0.5)
    idx, load = s.index, s.to_numpy()
    ev = np.asarray(idx >= first_eval_start(idx[0]))
    if not ev.any():
        raise SystemExit("need more than 28 days of data before the first whole month")
    base = C.run_none(idx[ev], load[ev], spec)
    runs = {"none": base, "forecast": C.run_forecast(idx, load, ev, spec, t, a.margin),
            "hindsight": C.run_hindsight(idx[ev], load[ev], spec, t)}
    if a.threshold:
        runs["fixed"] = C.run_threshold(idx[ev], load[ev], spec, a.threshold)
    print(f"evaluated {idx[ev][0]:%Y-%m-%d} to {idx[ev][-1]:%Y-%m-%d} (first 28+ days used as forecast history)")
    for k, r in runs.items():
        sm = C.summarize(r, base, t)
        print(f"{k:10s} " + "  ".join(f"{c}={sm[c]:.1f}" if sm[c] is not None else f"{c}=-" for c in COLS))


def cmd_tariff(_a):
    t = Tariff()
    print(f"demand charge: CHF {t.demand_chf_per_kw_month:.2f} per kW of the monthly {t.peak_interval_min}-min peak (published)")
    print(f"energy price for battery losses: CHF {t.energy_chf_per_kwh:.4f}/kWh (published rows summed)")
    print(f"source: {TARIFF_SOURCE}\n  {TARIFF_SOURCE_URL}")
    print("metering basis (15-min monthly maximum): assumption, not stated on the sheet")


def cmd_modbus_sim(a):
    from .modbus import run_server_forever
    run_server_forever(a.host, a.port)


def main(argv=None):
    p = argparse.ArgumentParser(prog="peak_shaving")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="run the study and write results + figures")
    d.add_argument("--quick", action="store_true", help="2 battery sizes, no robustness seeds (CI)")
    d.add_argument("--no-figures", action="store_true")
    d.add_argument("--seed", type=int, default=StudyConfig().seed)
    d.add_argument("--out", default=str(ROOT / "docs"))
    d.set_defaults(fn=cmd_demo)
    s = sub.add_parser("simulate", help="write a simulated load CSV")
    s.add_argument("--start-year", type=int, default=2025)
    s.add_argument("--years", type=int, default=2)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--out", default="load.csv")
    s.set_defaults(fn=cmd_simulate)
    e = sub.add_parser("evaluate", help="run the strategies on your own 15-min load CSV (time, load_kw)")
    e.add_argument("csv")
    e.add_argument("--kwh", type=float, default=500.0)
    e.add_argument("--kw", type=float, default=None)
    e.add_argument("--margin", type=float, default=0.1, help="forecast margin as a fraction of battery power")
    e.add_argument("--threshold", type=float, default=None, help="also run a fixed threshold (kW)")
    e.add_argument("--price", type=float, default=None, help="demand charge CHF/kW/month")
    e.set_defaults(fn=cmd_evaluate)
    t = sub.add_parser("tariff", help="print the tariff values and their source")
    t.set_defaults(fn=cmd_tariff)
    m = sub.add_parser("modbus-sim", help="run a simulated battery inverter as a Modbus TCP server")
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=5020)
    m.set_defaults(fn=cmd_modbus_sim)
    a = p.parse_args(argv)
    a.fn(a)
