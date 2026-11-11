"""Tariff, battery and study parameters. Every value says where it comes from.

status: `published` (read from a public tariff sheet), `assumption` (chosen for this study, change freely).
"""

from __future__ import annotations

from dataclasses import dataclass, field

TARIFF_SOURCE_URL = (
    "https://www.bern.ch/mediencenter/medienmitteilungen/aktuell_ptk/anpassung-der-stromtarife-fuer-2026/"
    "dokumente/tabelle-stromtarife-2026.pdf/download"
)
TARIFF_SOURCE = "City of Bern, Gemeinderat: 'Stromtarife 2026 im Ueberblick' (PDF of 27 Aug 2025, press release of 28 Aug 2025)"


@dataclass(frozen=True)
class Tariff:
    """Demand-charge tariff: CHF per kW of the monthly 15-min peak, plus a flat energy price for battery losses."""

    demand_chf_per_kw_month: float = 15.00  # published: grid-use 'Professional' Leistungstarif 2026, CHF/kW/month
    # Energy price used only to charge the battery's round-trip losses (CHF/kWh). Sum of the 2026 rows of the same
    # sheet for a medium-voltage 'Professional' customer at the normal (high) tariff, Rp./kWh:
    # grid 2.05 + energy (basic product, normal) 13.40 + system services 0.27 + federal reserve 0.41
    # + solidarity 0.05 + municipal levy 1.40 + federal grid surcharge 2.30 = 19.88 Rp./kWh.
    energy_chf_per_kwh: float = 0.1988  # published (sum of rows, see above)
    peak_interval_min: int = 15  # assumption: the sheet does not state the metering basis; 15-min monthly max assumed

    def demand_charge(self, monthly_peaks_kw) -> float:
        return float(sum(monthly_peaks_kw)) * self.demand_chf_per_kw_month


@dataclass(frozen=True)
class BatterySpec:
    capacity_kwh: float = 500.0  # assumption (default study size)
    power_kw: float = 250.0  # assumption (0.5C)
    round_trip_eff: float = 0.90  # assumption, AC-to-AC, typical for a Li-ion system incl. inverter
    soc_min: float = 0.10  # assumption
    soc_max: float = 0.90  # assumption
    soc_init: float = 0.90  # assumption: starts full

    def __post_init__(self):
        if self.capacity_kwh < 0 or self.power_kw < 0:
            raise ValueError("capacity and power must be >= 0")
        if not 0 < self.round_trip_eff <= 1:
            raise ValueError("round_trip_eff must be in (0, 1]")
        if not 0 <= self.soc_min < self.soc_max <= 1:
            raise ValueError("need 0 <= soc_min < soc_max <= 1")
        if not self.soc_min <= self.soc_init <= self.soc_max:
            raise ValueError("soc_init must lie within [soc_min, soc_max]")

    @property
    def eta_charge(self) -> float:
        return self.round_trip_eff ** 0.5

    @property
    def eta_discharge(self) -> float:
        return self.round_trip_eff ** 0.5

    @property
    def usable_kwh(self) -> float:
        return self.capacity_kwh * (self.soc_max - self.soc_min)

    @classmethod
    def sized(cls, capacity_kwh: float, c_rate: float = 0.5, **kw) -> BatterySpec:
        return cls(capacity_kwh=capacity_kwh, power_kw=capacity_kwh * c_rate, **kw)


@dataclass(frozen=True)
class CostAssumption:
    """Only used to draw the 'stops paying' line in the size sweep. Not a quote."""

    installed_chf_per_kwh: float = 450.0  # assumption: installed cost of a commercial battery incl. inverter
    life_years: int = 15  # assumption
    interest: float = 0.03  # assumption

    @property
    def annualized_chf_per_kwh(self) -> float:
        r, n = self.interest, self.life_years
        crf = r * (1 + r) ** n / ((1 + r) ** n - 1) if r > 0 else 1 / n
        return self.installed_chf_per_kwh * crf


@dataclass(frozen=True)
class StudyConfig:
    seed: int = 7
    history_year: int = 2025  # used only to set the fixed threshold and tune the forecast margin
    eval_year: int = 2026
    sweep_kwh: tuple = (100, 200, 300, 400, 600, 800, 1000, 1300, 1600, 2000)
    c_rate: float = 0.5
    margins: tuple = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40)  # forecast margin candidates, fraction of battery power
    robustness_seeds: tuple = (1, 2, 3, 4, 5)
    tariff: Tariff = field(default_factory=Tariff)
    cost: CostAssumption = field(default_factory=CostAssumption)
