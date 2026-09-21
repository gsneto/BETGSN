"""Contrato intercambiável para xG observado/estimado, com proveniência.

Regra de ouro: xG NUNCA é fabricado. Ausência é um estado explícito
(`XGStatus.UNAVAILABLE`), não um zero silencioso. Quando a fonte entrega
xG real, `source` e `available_at` são obrigatórios para permitir o corte
point-in-time.
"""
from dataclasses import dataclass, replace
from enum import Enum
from math import isfinite
from typing import Iterable, Protocol


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
    #: Motivo da ausência ou observação de proveniência. Nunca inventa valor.
    note: str = ""

    def __post_init__(self):
        vals = (self.home_xg, self.away_xg, self.home_xg_against, self.away_xg_against)
        if any(v is not None and (not isfinite(v) or v < 0) for v in vals):
            raise ValueError("xG deve ser finito e não negativo")
        if self.status != XGStatus.UNAVAILABLE and not self.source:
            raise ValueError("xG requer fonte explícita")
        if self.status == XGStatus.UNAVAILABLE and any(v is not None for v in vals):
            raise ValueError("xG indisponível deve ser null")

    @property
    def available(self) -> bool:
        """True apenas quando há valor observado com proveniência."""
        return self.status != XGStatus.UNAVAILABLE and (
            self.home_xg is not None or self.away_xg is not None
        )


class XGSource(Protocol):
    def fetch(self, match_id: str) -> XGObservation: ...


def unavailable(reason: str = "", source: str | None = None) -> XGObservation:
    """Observação explícita de ausência de xG (nunca zero)."""
    return XGObservation(status=XGStatus.UNAVAILABLE, source=source, note=reason)


class UnavailableXGSource:
    """Fonte que sempre declara ausência — default honesto do sistema."""

    def __init__(self, reason: str = "nenhuma fonte de xG configurada") -> None:
        self.reason = reason

    def fetch(self, match_id: str) -> XGObservation:
        return unavailable(self.reason)


class ChainXGSource:
    """Tenta fontes em ordem e devolve a primeira observação real.

    Nunca mistura fontes nem fabrica valor: se nenhuma tiver xG, devolve
    ausência com o motivo agregado (para auditoria). Erro de uma fonte não
    derruba a cadeia — apenas a registra e segue para a próxima.
    """

    def __init__(self, sources: Iterable[XGSource]) -> None:
        self.sources = tuple(sources)

    def fetch(self, match_id: str) -> XGObservation:
        notes: list[str] = []
        for source in self.sources:
            name = getattr(source, "name", type(source).__name__)
            try:
                obs = source.fetch(match_id)
            except Exception as exc:  # fonte quebrada não invalida as demais
                notes.append(f"{name}: erro ({type(exc).__name__})")
                continue
            if obs.available:
                return obs
            notes.append(f"{name}: {obs.note or 'sem xG'}")
        return unavailable("; ".join(notes) or "nenhuma fonte forneceu xG")


def attach_xg(match, observation: XGObservation):
    return replace(match, home_xg=observation.home_xg, away_xg=observation.away_xg,
                   home_xg_against=observation.home_xg_against,
                   away_xg_against=observation.away_xg_against,
                   xg_status=observation.status.value, xg_source=observation.source,
                   xg_available_at=observation.available_at)
