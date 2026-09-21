"""BETGSN :: datalayer.coverage — o que cada fonte cobre (e o que não cobre).

Cobertura é diferente de saúde. Uma fonte saudável pode simplesmente não
ter odds nem xG. Sem explicitar isso, a ausência vira "dado faltando" e
alguém tenta preencher com estimativa — exatamente o que não se quer.

`build_coverage` monta um retrato: por fonte (disponível, capacidades,
estado de saúde) e por recurso (quais fontes oferecem). Assim dá para
responder "quem fornece xG?" e "quais buracos ficam sem fonte?".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .health import HealthRegistry, ProviderStatus

__all__ = ["SourceCoverage", "CoverageReport", "build_coverage"]


@dataclass(frozen=True)
class SourceCoverage:
    name: str
    available: bool
    capabilities: tuple[str, ...]
    status: str
    reliability_pct: float
    no_coverage: tuple[str, ...] = ()
    note: str = ""

    @property
    def healthy(self) -> bool:
        return self.status in (ProviderStatus.HEALTHY.value, ProviderStatus.UNKNOWN.value)


@dataclass(frozen=True)
class CoverageReport:
    sources: tuple[SourceCoverage, ...]
    features: tuple[str, ...]

    def for_feature(self, feature: str) -> tuple[str, ...]:
        """Fontes que declaram cobrir `feature`."""
        return tuple(
            s.name for s in self.sources
            if feature in s.capabilities and feature not in s.no_coverage
        )

    def gaps(self, required: Iterable[str] | None = None) -> dict[str, tuple[str, ...]]:
        """Recursos sem nenhuma fonte disponível."""
        wanted = tuple(required) if required is not None else self.features
        return {
            feature: ()
            for feature in wanted
            if not self.for_feature(feature)
        }

    def summary(self) -> dict[str, Any]:
        return {
            "n_sources": len(self.sources),
            "available": sum(1 for s in self.sources if s.available),
            "features": list(self.features),
            "providers_by_feature": {
                feature: list(self.for_feature(feature)) for feature in self.features
            },
            "gaps": {k: list(v) for k, v in self.gaps().items()},
            "sources": [
                {
                    "name": s.name,
                    "available": s.available,
                    "status": s.status,
                    "capabilities": list(s.capabilities),
                    "reliability_pct": s.reliability_pct,
                    "no_coverage": list(s.no_coverage),
                }
                for s in self.sources
            ],
        }


def build_coverage(
    registrations: Iterable[Any],
    health: HealthRegistry | None = None,
) -> CoverageReport:
    """Retrato de cobertura a partir das fontes registradas na camada."""
    entries: list[SourceCoverage] = []
    features: set[str] = set()
    for registration in registrations:
        source = registration.source
        name = getattr(source, "name", type(source).__name__)
        capabilities = tuple(sorted(registration.capabilities))
        features.update(capabilities)
        state = health.health(name) if health is not None else None
        entries.append(
            SourceCoverage(
                name=name,
                available=_safe_available(source),
                capabilities=capabilities,
                status=state.status.value if state else ProviderStatus.UNKNOWN.value,
                reliability_pct=state.reliability_pct if state else 100.0,
                no_coverage=tuple(sorted(state.no_coverage)) if state else (),
                note=state.status_reason if state else "",
            )
        )
    return CoverageReport(
        sources=tuple(sorted(entries, key=lambda e: e.name)),
        features=tuple(sorted(features)),
    )


def _safe_available(source: Any) -> bool:
    checker = getattr(source, "available", None)
    if checker is None:
        return True
    try:
        return bool(checker())
    except Exception:  # noqa: BLE001
        return False
