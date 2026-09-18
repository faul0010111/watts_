"""Calibration: the difference between a plausible number and a defensible one.

    Digital twin → prediction → hardware measurement → comparison → calibration →
    updated coefficients

Nothing in WATTS is described as calibrated without a ``MeasurementSession`` recorded here.
"""
from .session import CalibrationSample, MeasurementSession
from .fitting import (
    Coefficients, FittingError, ValidationResult, compare_prediction, fit_coefficients, validate,
)
from .registry import CalibrationRecord, CalibrationRegistry, build_record
from .report import calibration_report

__all__ = [
    "CalibrationSample", "MeasurementSession",
    "Coefficients", "ValidationResult", "FittingError",
    "fit_coefficients", "validate", "compare_prediction",
    "CalibrationRecord", "CalibrationRegistry", "build_record",
    "calibration_report",
]
