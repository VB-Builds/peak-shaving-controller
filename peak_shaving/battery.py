"""Battery model: energy capacity, AC power limit, round-trip efficiency split evenly between charge and discharge,
SoC limits. Sign convention: positive power = discharge (battery feeds the site), negative = charge."""

from __future__ import annotations

from dataclasses import dataclass

from .config import BatterySpec
from .load import STEP_H


@dataclass
class Battery:
    spec: BatterySpec
    soc_kwh: float = -1.0

    def __post_init__(self):
        if self.soc_kwh < 0:
            self.soc_kwh = self.spec.soc_init * self.spec.capacity_kwh

    @property
    def soc(self) -> float:
        """State of charge as a fraction of capacity (0 for a zero-size battery)."""
        return self.soc_kwh / self.spec.capacity_kwh if self.spec.capacity_kwh > 0 else 0.0

    def max_discharge_kw(self, dt_h: float = STEP_H) -> float:
        s = self.spec
        avail = max(0.0, self.soc_kwh - s.soc_min * s.capacity_kwh)
        return min(s.power_kw, avail * s.eta_discharge / dt_h)

    def max_charge_kw(self, dt_h: float = STEP_H) -> float:
        s = self.spec
        room = max(0.0, s.soc_max * s.capacity_kwh - self.soc_kwh)
        return min(s.power_kw, room / (s.eta_charge * dt_h)) if s.eta_charge > 0 else 0.0

    def step(self, p_req_kw: float, dt_h: float = STEP_H) -> float:
        """Apply a power request for one interval; return the AC power actually delivered (clipped to the limits)."""
        if p_req_kw >= 0:
            p = min(p_req_kw, self.max_discharge_kw(dt_h))
            self.soc_kwh -= p * dt_h / self.spec.eta_discharge
        else:
            p = -min(-p_req_kw, self.max_charge_kw(dt_h))
            self.soc_kwh -= p * dt_h * self.spec.eta_charge
        # guard against float drift
        lo, hi = self.spec.soc_min * self.spec.capacity_kwh, self.spec.soc_max * self.spec.capacity_kwh
        self.soc_kwh = min(max(self.soc_kwh, lo), hi) if self.spec.capacity_kwh > 0 else 0.0
        return p
