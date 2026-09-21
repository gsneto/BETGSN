"""BETGSN :: datalayer.envelope — dado + proveniência + status.

Todo dado que sai da camada de dados vem dentro de um `DataEnvelope`. Ele
carrega a resposta e a META-RESPOSTA: de onde veio, quando foi observado,
quantos segundos tem, se veio de cache, se está stale, qual a qualidade e
o que falhou antes de chegar ali.

Sem esse envelope, "carregar um arquivo" e "ter um dado confiável" viram a
mesma coisa — e um dado de uma semana atrás passa a alimentar uma decisão
como se fosse de agora. O envelope impede isso.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..data_quality import QualityGrade
from .times import age_seconds, humanize_age

__all__ = [
    "DataStatus",
    "Provenance",
    "DataEnvelope",
    "DataUnavailable",
    "build_provenance",
]


class DataStatus(str, Enum):
    """Resultado da tentativa de obter o dado."""

    OK = "OK"                    # dado fresco da melhor fonte disponível
    STALE = "STALE"              # dado antigo, entregue explicitamente como antigo
    DEGRADED = "DEGRADED"        # obtido de fonte secundária após falha da primária
    NO_COVERAGE = "NO_COVERAGE"  # nenhuma fonte cobre esse dado
    MISSING = "MISSING"          # todas as fontes falharam
    ERROR = "ERROR"              # erro de programação/uso (ex.: parâmetro inválido)


class DataUnavailable(RuntimeError):
    """Pedido explícito de dado que não está disponível."""


@dataclass(frozen=True)
class Provenance:
    """Rastro de uma observação: quem, quando, com que idade, com que qualidade."""

    source: str
    fetched_at: str
    source_timestamp: str = ""
    data_age_seconds: float | None = None
    from_cache: bool = False
    quality: QualityGrade = QualityGrade.UNKNOWN
    coverage: tuple[str, ...] = ()
    note: str = ""

    @property
    def age_human(self) -> str:
        return humanize_age(self.data_age_seconds)

    def is_stale(self, max_age_seconds: float | None) -> bool:
        """True quando a idade excede o limite. Sem carimbo, nunca é "fresco"."""
        if self.data_age_seconds is None:
            return False
        if max_age_seconds is None:
            return False
        return self.data_age_seconds > max_age_seconds

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "fetched_at": self.fetched_at,
            "source_timestamp": self.source_timestamp,
            "data_age_seconds": self.data_age_seconds,
            "data_age_human": self.age_human,
            "from_cache": self.from_cache,
            "quality": self.quality.value if isinstance(self.quality, QualityGrade) else str(self.quality),
            "coverage": list(self.coverage),
            "note": self.note,
        }


@dataclass(frozen=True)
class DataEnvelope:
    """Resultado tipado de uma consulta à camada de dados."""

    value: Any = None
    provenance: Provenance | None = None
    status: DataStatus = DataStatus.MISSING
    kind: str = ""
    errors: tuple[str, ...] = ()
    note: str = ""
    alternatives: tuple[Provenance, ...] = ()

    @property
    def available(self) -> bool:
        return self.status in (DataStatus.OK, DataStatus.STALE, DataStatus.DEGRADED)

    @property
    def is_stale(self) -> bool:
        return self.status == DataStatus.STALE

    @property
    def source(self) -> str:
        return self.provenance.source if self.provenance else ""

    @property
    def age_seconds(self) -> float | None:
        return self.provenance.data_age_seconds if self.provenance else None

    @property
    def empty(self) -> bool:
        if self.value is None:
            return True
        try:
            return len(self.value) == 0  # type: ignore[arg-type]
        except TypeError:
            return False

    def require(self) -> Any:
        """Devolve o valor ou levanta `DataUnavailable` explicando o porquê."""
        if not self.available or self.value is None:
            reason = self.note or "; ".join(self.errors) or self.status.value
            raise DataUnavailable(f"{self.kind or 'dado'} indisponível: {reason}")
        return self.value

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "status": self.status.value,
            "available": self.available,
            "empty": self.empty,
            "note": self.note,
            "errors": list(self.errors),
        }
        if self.provenance:
            payload["provenance"] = self.provenance.as_dict()
        if self.alternatives:
            payload["alternatives"] = [p.as_dict() for p in self.alternatives]
        return payload


def build_provenance(
    *,
    source: str,
    source_timestamp: str = "",
    fetched_at: str,
    from_cache: bool = False,
    quality: QualityGrade = QualityGrade.UNKNOWN,
    coverage: tuple[str, ...] = (),
    note: str = "",
    now: str | None = None,
) -> Provenance:
    """Monta a proveniência calculando a idade a partir do carimbo da fonte."""
    return Provenance(
        source=source,
        fetched_at=fetched_at,
        source_timestamp=source_timestamp,
        data_age_seconds=age_seconds(source_timestamp, now) if source_timestamp else None,
        from_cache=from_cache,
        quality=quality,
        coverage=coverage,
        note=note,
    )
