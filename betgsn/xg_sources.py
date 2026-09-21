"""Contrato intercambiável para xG observado/estimado, com proveniência."""
from dataclasses import dataclass, replace
from enum import Enum
from math import isfinite
from typing import Protocol


class XGStatus(str, Enum):
    REAL = "REAL"
    ESTIMATED = "ESTIMATED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class XGObservation:
    home_xg: float | None = None
    away_xg: float | None = None
    home_xg_against: float | None = None
    away_xg_against: float | None = None
    status: XGStatus = XGStatus.UNAVAILABLE
    source: str | None = None
    available_at: str | None = None

    def __post_init__(self):
        vals = (self.home_xg, self.away_xg, self.home_xg_against, self.away_xg_against)
        if any(v is not None and (not isfinite(v) or v < 0) for v in vals):
            raise ValueError("xG deve ser finito e não negativo")
        if self.status != XGStatus.UNAVAILABLE and not self.source:
            raise ValueError("xG requer fonte explícita")
        if self.status == XGStatus.UNAVAILABLE and any(v is not None for v in vals):
            raise ValueError("xG indisponível deve ser null")


class XGSource(Protocol):
    def fetch(self, match_id: str) -> XGObservation: ...


class UnavailableXGSource:
    def fetch(self, match_id: str) -> XGObservation:
        return XGObservation()


def attach_xg(match, observation: XGObservation):
    return replace(match, home_xg=observation.home_xg, away_xg=observation.away_xg,
                   home_xg_against=observation.home_xg_against,
                   away_xg_against=observation.away_xg_against,
                   xg_status=observation.status.value, xg_source=observation.source,
                   xg_available_at=observation.available_at)
