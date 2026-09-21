"""BETGSN :: datalayer.health — saúde e cobertura das fontes de dados.

Uma fonte não é apenas "ok" ou "quebrada". Ela pode estar degradada (já
falhou algumas vezes), indisponível (falhou demais), stale (só tem dado
antigo) ou simplesmente não cobrir aquele tipo de dado (NO_COVERAGE —
diferente de falhar).

O registro é em memória, thread-safe e determinístico: os testes injetam o
instante, então o estado nunca depende do relógio real.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum

from .errors import ErrorKind, SourceError
from .times import parse_stamp, utc_stamp

__all__ = ["ProviderStatus", "SourceHealth", "HealthRegistry"]

#: Quanto tempo uma fonte fica em cooldown após cada tipo de falha.
_COOLDOWN_SECONDS = {
    ErrorKind.QUOTA: 3600,
    ErrorKind.RATE_LIMIT: 60,
    ErrorKind.SERVER: 30,
    ErrorKind.AUTH: 3600,
    ErrorKind.FORBIDDEN: 3600,
}


class ProviderStatus(str, Enum):
    """Estado operacional de uma fonte."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    STALE = "STALE"
    NO_COVERAGE = "NO_COVERAGE"
    UNKNOWN = "UNKNOWN"


#: Ordem de preferência na hora de escolher fonte (menor = melhor).
_STATUS_RANK = {
    ProviderStatus.HEALTHY: 0,
    ProviderStatus.UNKNOWN: 1,
    ProviderStatus.STALE: 2,
    ProviderStatus.DEGRADED: 2,
    ProviderStatus.NO_COVERAGE: 4,
    ProviderStatus.UNAVAILABLE: 5,
}


@dataclass
class SourceHealth:
    """Histórico observável de uma fonte."""

    name: str
    status: ProviderStatus = ProviderStatus.UNKNOWN
    consecutive_failures: int = 0
    total_successes: int = 0
    total_failures: int = 0
    last_success_at: str = ""
    last_error_at: str = ""
    last_error: str = ""
    last_kind: ErrorKind | None = None
    status_reason: str = ""
    #: recursos que a fonte NÃO cobre (fato de capacidade, não falha)
    no_coverage: set[str] = field(default_factory=set)
    #: instante (UTC) até o qual a fonte fica de fora, após falha transitória
    cooldown_until: str = ""

    @property
    def rank(self) -> int:
        return _STATUS_RANK.get(self.status, 3)

    @property
    def reliability_pct(self) -> float:
        total = self.total_successes + self.total_failures
        if total == 0:
            return 100.0
        return round(100.0 * self.total_successes / total, 1)

    def record_success(self, at: str | None = None, feature: str | None = None) -> None:
        stamp = at or utc_stamp()
        self.total_successes += 1
        self.consecutive_failures = 0
        self.last_success_at = stamp
        self.last_kind = None
        self.last_error = ""
        self.status = ProviderStatus.HEALTHY
        self.status_reason = ""
        self.cooldown_until = ""
        if feature:
            self.no_coverage.discard(feature)

    def in_cooldown(self, now: str | None = None) -> bool:
        """True enquanto a fonte está pausada por falha transitória recente."""
        if not self.cooldown_until:
            return False
        return self.cooldown_until > (now or utc_stamp())

    def _set_cooldown(self, at: str, seconds: int) -> None:
        moment = parse_stamp(at)
        if moment is None or seconds <= 0:
            self.cooldown_until = ""
            return
        self.cooldown_until = utc_stamp(moment + timedelta(seconds=seconds))

    def record_failure(
        self,
        error: SourceError,
        *,
        at: str | None = None,
        degraded_after: int = 2,
        unavailable_after: int = 5,
    ) -> None:
        stamp = at or utc_stamp()
        if error.kind == ErrorKind.NO_COVERAGE:
            # Não é falha de saúde: a fonte funciona, só não cobre esse dado.
            # A marcação do recurso específico é feita por `mark_no_coverage`.
            self.last_kind = error.kind
            self.status = ProviderStatus.NO_COVERAGE
            self.status_reason = error.message
            return
        self.total_failures += 1
        self.consecutive_failures += 1
        self.last_error_at = stamp
        self.last_error = str(error)
        self.last_kind = error.kind
        self.status_reason = error.message
        if self.consecutive_failures >= unavailable_after:
            self.status = ProviderStatus.UNAVAILABLE
        elif self.consecutive_failures >= degraded_after:
            self.status = ProviderStatus.DEGRADED
        else:
            self.status = ProviderStatus.DEGRADED
        self._set_cooldown(stamp, _COOLDOWN_SECONDS.get(error.kind, 0))

    def mark_no_coverage(self, feature: str) -> None:
        """Declara que a fonte não cobre `feature` (sem contar como falha)."""
        self.no_coverage.add(feature)
        self.status = ProviderStatus.NO_COVERAGE
        self.status_reason = f"sem cobertura para {feature}"

    def mark_stale(self, reason: str) -> None:
        self.status = ProviderStatus.STALE
        self.status_reason = reason

    def mark_unavailable(self, reason: str) -> None:
        self.status = ProviderStatus.UNAVAILABLE
        self.status_reason = reason

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "consecutive_failures": self.consecutive_failures,
            "total_successes": self.total_successes,
            "total_failures": self.total_failures,
            "reliability_pct": self.reliability_pct,
            "last_success_at": self.last_success_at,
            "last_error_at": self.last_error_at,
            "last_error": self.last_error,
            "last_kind": self.last_kind.value if self.last_kind else "",
            "status_reason": self.status_reason,
            "no_coverage": sorted(self.no_coverage),
            "cooldown_until": self.cooldown_until,
        }


class HealthRegistry:
    """Coleção thread-safe de `SourceHealth`, uma por fonte."""

    def __init__(self, *, degraded_after: int = 2, unavailable_after: int = 5) -> None:
        # RLock: os métodos públicos chamam `self.health()` (que também
        # trava) enquanto já seguram o lock — um Lock simples travava.
        self._lock = threading.RLock()
        self._health: dict[str, SourceHealth] = {}
        self.degraded_after = degraded_after
        self.unavailable_after = unavailable_after

    def health(self, name: str) -> SourceHealth:
        with self._lock:
            entry = self._health.get(name)
            if entry is None:
                entry = SourceHealth(name=name)
                self._health[name] = entry
            return entry

    def record_success(self, name: str, at: str | None = None, feature: str | None = None) -> None:
        with self._lock:
            self.health(name).record_success(at, feature)

    def record_failure(self, name: str, error: SourceError, at: str | None = None) -> None:
        with self._lock:
            self.health(name).record_failure(
                error,
                at=at,
                degraded_after=self.degraded_after,
                unavailable_after=self.unavailable_after,
            )

    def mark_no_coverage(self, name: str, feature: str) -> None:
        with self._lock:
            self.health(name).mark_no_coverage(feature)

    def mark_stale(self, name: str, reason: str) -> None:
        with self._lock:
            self.health(name).mark_stale(reason)

    def mark_unavailable(self, name: str, reason: str) -> None:
        with self._lock:
            self.health(name).mark_unavailable(reason)

    def status_of(self, name: str) -> ProviderStatus:
        return self.health(name).status

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {name: h.as_dict() for name, h in sorted(self._health.items())}

    def usable(self, name: str) -> bool:
        """False só quando a fonte está declaradamente fora do ar."""
        return self.health(name).status != ProviderStatus.UNAVAILABLE

    def describe(self, name: str) -> str:
        h = self.health(name)
        if h.status_reason:
            return f"{h.status.value}: {h.status_reason}"
        return h.status.value
