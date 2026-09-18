"""Power Usage Effectiveness: PUE = total facility energy / IT equipment energy.

WATTS reports four views of PUE (current, historical, estimated, anomalous) and refuses
to report a value it cannot justify: if facility metering is missing, the returned
snapshot is ESTIMATED and says which input was substituted.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Sequence

from .model import Provenance


@dataclass(frozen=True)
class PUESnapshot:
    pue: float
    it_energy_wh: float
    facility_energy_wh: float
    provenance: Provenance
    window_s: float
    basis: str = ""

    def as_dict(self) -> dict:
        return {
            "pue": self.pue,
            "it_energy_wh": self.it_energy_wh,
            "facility_energy_wh": self.facility_energy_wh,
            "provenance": self.provenance.value,
            "window_s": self.window_s,
            "basis": self.basis,
        }


def compute_pue(
    it_energy_wh: float,
    facility_energy_wh: float | None = None,
    *,
    cooling_energy_wh: float | None = None,
    other_overhead_wh: float = 0.0,
    window_s: float = 0.0,
    facility_meter_available: bool = True,
    source_provenance: Provenance | None = None,
) -> PUESnapshot:
    """Compute PUE and say what it rests on.

    ``source_provenance`` overrides the label when the inputs did not come from a meter -
    a simulated window produces a simulated PUE, and calling it "facility meter" because
    the arithmetic is the same would be exactly the relabelling WATTS exists to prevent.
    """
    if it_energy_wh <= 0:
        raise ValueError("IT energy must be positive to compute PUE")

    if facility_energy_wh is not None and facility_meter_available:
        total = facility_energy_wh
        prov = Provenance.DERIVED
        basis = "facility meter / PDU totals"
        if source_provenance is not None and source_provenance is not Provenance.MEASURED:
            prov = source_provenance
            basis = (f"facility and IT totals from a {source_provenance.value} source, "
                     f"divided")
    elif cooling_energy_wh is not None:
        total = it_energy_wh + cooling_energy_wh + other_overhead_wh
        prov = Provenance.ESTIMATED
        basis = "IT + modelled cooling load (no facility meter)"
    else:
        raise ValueError("need either facility energy or a cooling estimate")

    if total < it_energy_wh:
        raise ValueError("facility energy below IT energy: check meter boundaries")

    return PUESnapshot(total / it_energy_wh, it_energy_wh, total, prov, window_s, basis)


def pue_anomaly(history: Sequence[float], current: float, k: float = 3.5) -> dict:
    """Robust (median/MAD) outlier test on a PUE history.

    Returns a dict with ``anomalous``, the robust z-score and the baseline, so the
    caller can show the evidence instead of a bare boolean.
    """
    vals = [v for v in history if v and v >= 1.0]
    if len(vals) < 8:
        return {"anomalous": False, "reason": "insufficient history", "samples": len(vals)}
    med = median(vals)
    mad = median([abs(v - med) for v in vals]) or 1e-9
    z = 0.6745 * (current - med) / mad
    return {
        "anomalous": abs(z) >= k,
        "robust_z": z,
        "baseline_pue": med,
        "current_pue": current,
        "direction": "worse" if current > med else "better",
        "samples": len(vals),
    }
