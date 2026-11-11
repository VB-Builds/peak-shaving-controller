"""docs/figure.png (1600x900) and docs/post-figure.png (960x700, dark LinkedIn palette)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager  # noqa: E402

BG, GRID, TEXT, MUTED = "#16213A", "#2A3750", "#F8FAFC", "#A9B6CB"
ACCENT, GREEN, AMBER, RED, GREY, VIOLET = "#38BDF8", "#34D399", "#FBBF24", "#F87171", "#64748B", "#A78BFA"
COLORS = {"none": GREY, "fixed": AMBER, "forecast": ACCENT, "hindsight": GREEN}
LABELS = {"none": "no battery", "fixed": "fixed threshold", "forecast": "forecast + LP", "hindsight": "perfect hindsight"}

_names = {f.name for f in font_manager.fontManager.ttflist}
FONT = "Segoe UI" if "Segoe UI" in _names else "DejaVu Sans"
plt.rcParams.update({"font.family": FONT, "text.color": TEXT, "axes.labelcolor": MUTED, "xtick.color": MUTED,
                     "ytick.color": MUTED, "axes.facecolor": BG, "figure.facecolor": BG, "axes.edgecolor": GRID,
                     "savefig.facecolor": BG, "axes.titlecolor": TEXT, "legend.facecolor": BG, "legend.edgecolor": GRID})

MONTHS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]


def _style(ax, size=11):
    ax.grid(True, color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(labelsize=size)


def _title(ax, text, size=13):
    ax.set_title(text, loc="left", fontsize=size, fontweight="bold", color=TEXT, pad=8)


def _monthly(ax, tab, size=11, lw=2.4, annotate=True):
    x = np.arange(len(tab))
    for k in ("none", "fixed", "forecast", "hindsight"):
        ax.plot(x, tab[k].to_numpy(), color=COLORS[k], lw=lw if k != "none" else lw + 0.6, marker="o", ms=5,
                label=LABELS[k], zorder=3 if k == "forecast" else 2)
    miss = tab["forecast_missed"].to_numpy().astype(bool)
    ax.scatter(x[miss], tab["forecast"].to_numpy()[miss], s=150, facecolors="none", edgecolors=RED, lw=2, zorder=4,
               label="forecast missed (new peak set)")
    ax.set_xticks(x)
    ax.set_xticklabels(MONTHS[:len(tab)], fontsize=size)
    ax.set_ylabel("monthly 15-min peak (kW)", fontsize=size)


def _sweep(ax, sweep, ann_cost, size=11, lw=2.4):
    for k in ("fixed", "forecast", "hindsight"):
        g = sweep[sweep["controller"] == k].sort_values("capacity_kwh")
        ax.plot(g["capacity_kwh"], g["net_saving_chf"] / 1000, color=COLORS[k], lw=lw, marker="o", ms=5, label=LABELS[k])
    kwh = np.linspace(0, sweep["capacity_kwh"].max(), 50)
    ax.plot(kwh, kwh * ann_cost / 1000, color=RED, lw=1.8, ls="--", label=f"battery cost (assumed, CHF {ann_cost:.0f}/kWh/yr)")
    ax.set_xlabel("battery capacity (kWh), power = 0.5 x capacity", fontsize=size)
    ax.set_ylabel("net saving (kCHF per year)", fontsize=size)


def main_figure(r: dict, path: Path):
    tab, sweep = r["monthly"], r["sweep"]
    ann = r["config"]["annualized_chf_per_kwh"]
    s = r["default"]["summary"]
    fig = plt.figure(figsize=(16, 9), dpi=100)
    fig.text(0.03, 0.955, "Battery peak shaving against a monthly demand charge: forecast controller vs perfect hindsight",
             fontsize=19, fontweight="bold", color=TEXT)
    fig.text(0.03, 0.918, f"SIMULATED 15-min factory load, evaluation year {r['config']['eval_year']} (seed {r['config']['seed']}); "
             f"demand charge CHF {r['config']['demand_chf_per_kw_month']:.2f}/kW/month (published 2026 grid tariff, see README)",
             fontsize=12, color=MUTED)
    gs = fig.add_gridspec(2, 2, left=0.055, right=0.985, top=0.86, bottom=0.08, hspace=0.42, wspace=0.17)

    # A: peak day of the month where the forecast missed by the most kW (or the largest gap)
    ax = fig.add_subplot(gs[0, 0])
    runs = r["runs"]
    fc, hs = runs["forecast"], runs["hindsight"]
    gap = tab["gap_to_hindsight_kw"]
    month = gap.idxmax()
    y, m = map(int, month.split("-"))
    sel = (fc.index.year == y) & (fc.index.month == m)
    i_peak = np.flatnonzero(sel)[int(np.argmax(fc.grid_kw[sel]))]
    day = fc.index[i_peak].normalize()
    w = (fc.index >= day) & (fc.index < day + np.timedelta64(1, "D"))
    hrs = (fc.index[w] - day).total_seconds() / 3600
    ax.plot(hrs, fc.load_kw[w], color=GREY, lw=5.0, label="load")
    ax.plot(hrs, hs.grid_kw[w], color=GREEN, lw=2.0, label="grid, hindsight")
    ax.plot(hrs, fc.grid_kw[w], color=ACCENT, lw=2.0, label="grid, forecast")
    ax.axhline(fc.planned_kw[i_peak], color=AMBER, lw=1.6, ls="--", label="forecast's planned threshold")
    ax.set_xlim(0, 24)
    ax.set_xticks(range(0, 25, 3))
    ax.set_xlabel(f"hour of {day:%a %d %b %Y}", fontsize=11)
    ax.set_ylabel("kW (15-min mean)", fontsize=11)
    _style(ax)
    _title(ax, f"Worst month for the forecast ({month}): {gap.max():.0f} kW above hindsight")
    ax.legend(loc="lower center", bbox_to_anchor=(0.6, 0.02), fontsize=10, frameon=False, labelcolor=TEXT, ncol=2)

    ax = fig.add_subplot(gs[0, 1])
    _monthly(ax, tab)
    _style(ax)
    _title(ax, "Monthly billed peak per strategy (500 kWh / 250 kW)")
    ax.legend(loc="upper center", fontsize=10, frameon=False, labelcolor=TEXT, ncol=3)
    lo, hi = ax.get_ylim()
    ax.set_ylim(lo, hi + 0.25 * (hi - lo))

    ax = fig.add_subplot(gs[1, 0])
    _sweep(ax, sweep, ann)
    _style(ax)
    _title(ax, "Net saving per year vs battery size (demand charge minus extra energy for losses)")
    ax.legend(loc="upper left", fontsize=10, frameon=False, labelcolor=TEXT)

    ax = fig.add_subplot(gs[1, 1])
    names = ["fixed", "forecast", "hindsight"]
    vals = [s[k]["net_saving_chf"] / 1000 for k in names]
    bars = ax.barh(range(3), vals, color=[COLORS[k] for k in names], height=0.6)
    for b, k in zip(bars, names, strict=True):
        ss = s[k]
        extra = "" if ss["miss_share"] is None else f", missed {ss['missed_months']}/12 months"
        ax.text(b.get_width() + 0.5, b.get_y() + b.get_height() / 2,
                f"{b.get_width():.1f} kCHF, peaks -{ss['peak_cut_pct']:.1f} %, {ss['cycles_per_year']:.0f} cycles{extra}",
                va="center", fontsize=11, color=TEXT)
    ax.set_yticks(range(3))
    ax.set_yticklabels([LABELS[k] for k in names], fontsize=11)
    ax.invert_yaxis()
    ax.set_xlim(0, max(vals) * 2.1)
    ax.set_xlabel("net saving, kCHF per year (500 kWh / 250 kW)", fontsize=11)
    _style(ax)
    _title(ax, "Default battery: what each strategy earns")
    fig.text(0.985, 0.012, "simulated load; tariff: City of Bern 2026 tariff sheet; battery cost is an assumption",
             fontsize=10, color=MUTED, ha="right")
    fig.savefig(path, dpi=100, facecolor=BG)
    plt.close(fig)


def post_figure(r: dict, path: Path):
    tab, sweep = r["monthly"], r["sweep"]
    ann = r["config"]["annualized_chf_per_kwh"]
    fig = plt.figure(figsize=(9.6, 7.0), dpi=100, facecolor=BG)
    fig.text(0.035, 0.95, "Battery peak shaving: forecast controller vs hindsight", fontsize=14, fontweight="bold",
             color=TEXT)
    fig.text(0.035, 0.912, f"simulated 15-min factory load, {r['config']['eval_year']}; demand charge CHF "
             f"{r['config']['demand_chf_per_kw_month']:.0f}/kW/month (published tariff)", fontsize=11, color=MUTED)
    gs = fig.add_gridspec(2, 1, left=0.10, right=0.975, top=0.83, bottom=0.085, hspace=0.55)
    ax = fig.add_subplot(gs[0])
    _monthly(ax, tab, size=11, lw=2.6)
    _style(ax)
    _title(ax, "Monthly billed peak, 500 kWh battery", size=12)
    ax.legend(loc="upper center", fontsize=11, frameon=False, labelcolor=TEXT, ncol=3, bbox_to_anchor=(0.5, 1.02))
    lo, hi = ax.get_ylim()
    ax.set_ylim(lo, hi + 0.42 * (hi - lo))
    ax.set_ylabel("peak (kW)", fontsize=11)
    ax = fig.add_subplot(gs[1])
    _sweep(ax, sweep, ann, size=11, lw=2.6)
    _style(ax)
    _title(ax, "Net saving per year vs battery size", size=12)
    ax.legend(loc="upper left", fontsize=11, frameon=False, labelcolor=TEXT)
    ax.set_ylabel("kCHF / year", fontsize=11)
    fig.savefig(path, dpi=100, facecolor=BG)
    plt.close(fig)


def make_all(r: dict, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    main_figure(r, out / "figure.png")
    post_figure(r, out / "post-figure.png")
