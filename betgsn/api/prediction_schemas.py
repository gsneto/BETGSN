"""Contratos de auditoria de previsões e avaliação estatística."""
from typing import Literal
from pydantic import BaseModel, Field


class Provenance(BaseModel):
    model_version: str = "BASELINE_V1"
    data_version: str | None = None
    prediction_timestamp: str | None = None
    odds_timestamp: str | None = None
    calibration_version: str | None = None
    calibration_status: str = "UNCALIBRATED"
    xg_status: Literal["REAL", "ESTIMATED", "UNAVAILABLE"] = "UNAVAILABLE"
    xg_source: str | None = None
    source: str = "real"


class FeatureSnapshot(BaseModel):
    kickoff: str
    prediction_timestamp: str
    feature_time: str | None
    values: dict[str, float | None]
    version: str


class Prediction(Provenance):
    match_id: str
    kickoff: str
    features: FeatureSnapshot
    raw_model_probability: dict[str, float]
    calibrated_probability: dict[str, float] | None = None
    probability: dict[str, float]


class CLVMetrics(BaseModel):
    entry_odd: float = Field(gt=1)
    closing_odd: float | None = None
    prediction_timestamp: str
    bet_timestamp: str
    closing_timestamp: str | None = None
    absolute: float | None = None
    percentage: float | None = None


class CalibrationMetrics(BaseModel):
    n: int
    brier: float | None = None
    logloss: float | None = None
    rps: float | None = None
    ece: float | None = None
    mce: float | None = None
    reliability_curve: list[dict[str, float]] = Field(default_factory=list)
