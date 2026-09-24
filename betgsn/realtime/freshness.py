"""BETGSN :: realtime.freshness — idade real da quote, sem maquiagem.

Uma quote so e "FRESH" quando foi observada ha pouco. Estados:

  FRESH    idade <= fresh_seconds
  RECENT   idade <= recent_seconds
  STALE    idade <= stale_seconds (ou qualquer idade conhecida acima)
  UNKNOWN  sem timestamp observado — nunca 0, nunca "agora"

A idade e sempre a diferenca entre o AGORA da consulta e o TIMESTAMP
DA OBSERVACAO (nao o momento da captura): e o horario em que a
informacao nasceu no mundo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from ..timeutil import KickoffError, parse_kickoff


class FreshnessState(str, Enum):
    FRESH = "FRESH"
    RECENT = "RECENT"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class FreshnessThresholds:
    fresh_seconds: float = 300.0
    recent_seconds: float = 900.0
    stale_seconds: float = 3600.0

    def validate(self) -> None:
        if not (0 < self.fresh_seconds <= self.recent_seconds <= self.stale_seconds):
            raise ValueError(
                "thresholds de freshness precisam ser crescentes: "
                f"fresh={self.fresh_seconds} recent={self.recent_seconds} "
                f"stale={self.stale_seconds}"
            )


def parse_stamp(stamp: str) -> datetime | None:
    """Datetime UTC do carimbo observado, ou None quando ilegivel."""
    try:
        return parse_kickoff(stamp)
    except (KickoffError, TypeError, ValueError):
        return None


def age_seconds(stamp: str, now: datetime | None = None) -> float | None:
    """Idade da observacao em segundos; None quando nao ha carimbo valido."""
    moment = parse_stamp(stamp)
    if moment is None:
        return None
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (reference - moment).total_seconds()


def freshness_state(
    stamp: str,
    thresholds: FreshnessThresholds,
    now: datetime | None = None,
) -> tuple[FreshnessState, float | None]:
    """(estado, idade em segundos). Sem carimbo: (UNKNOWN, None)."""
    seconds = age_seconds(stamp, now)
    if seconds is None:
        return FreshnessState.UNKNOWN, None
    if seconds < 0.0:
        #: carimbo no futuro = dado suspeito, nunca "fresco"
        return FreshnessState.UNKNOWN, seconds
    if seconds <= thresholds.fresh_seconds:
        return FreshnessState.FRESH, seconds
    if seconds <= thresholds.recent_seconds:
        return FreshnessState.RECENT, seconds
    return FreshnessState.STALE, seconds
