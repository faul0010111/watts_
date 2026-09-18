"""Token economics: what 1,000 tokens cost in energy, money, GPU time and carbon.

Carbon is kept strictly downstream of energy: WATTS multiplies kWh by a grid intensity
signal supplied by the operator. It never treats a carbon number as a measurement of
energy, and it never reports carbon without saying which intensity source produced it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EconomicsInputs:
    energy_price_per_kwh: float
    gpu_cost_per_hour: float = 0.0
    carbon_intensity_g_per_kwh: float | None = None
    carbon_source: str = "not configured"
    price_currency: str = "USD"


@dataclass(frozen=True)
class TokenEconomics:
    tokens: int
    energy_wh: float
    gpu_seconds: float
    inputs: EconomicsInputs

    def _per_1k(self, value: float) -> float:
        return value / (self.tokens / 1000.0) if self.tokens else 0.0

    @property
    def energy_cost(self) -> float:
        return self.energy_wh / 1000.0 * self.inputs.energy_price_per_kwh

    @property
    def gpu_cost(self) -> float:
        return self.gpu_seconds / 3600.0 * self.inputs.gpu_cost_per_hour

    @property
    def total_cost(self) -> float:
        return self.energy_cost + self.gpu_cost

    @property
    def carbon_g(self) -> float | None:
        ci = self.inputs.carbon_intensity_g_per_kwh
        if ci is None:
            return None
        return self.energy_wh / 1000.0 * ci

    def as_dict(self) -> dict:
        carbon = self.carbon_g
        return {
            "tokens": self.tokens,
            "energy_wh": self.energy_wh,
            "gpu_seconds": self.gpu_seconds,
            "per_1k_tokens": {
                "energy_wh": self._per_1k(self.energy_wh),
                "gpu_seconds": self._per_1k(self.gpu_seconds),
                "cost": self._per_1k(self.total_cost),
                "carbon_g": self._per_1k(carbon) if carbon is not None else None,
            },
            "cost": {
                "energy": self.energy_cost,
                "gpu": self.gpu_cost,
                "total": self.total_cost,
                "currency": self.inputs.price_currency,
            },
            "carbon": {
                "total_g_co2e": carbon,
                "intensity_g_per_kwh": self.inputs.carbon_intensity_g_per_kwh,
                "source": self.inputs.carbon_source,
                "note": "carbon is derived from energy and a grid intensity signal; it is not measured",
            },
        }
