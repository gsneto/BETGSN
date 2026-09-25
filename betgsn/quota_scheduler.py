"""BETGSN :: quota_scheduler — captura consciente de quota e cooldown.

Por que existe
--------------
Um provider com quota esgotada (401/403/429 externo) não deve ser
martelado a cada tick: cada chamada é desperdício e pode piorar o rate
limit. Este módulo lê o health PERSISTIDO no store (entre processos) e
decide, por provider:

    should_attempt(provider, now) -> bool
    next_attempt_at(provider)     -> str | None

Estados:

    AVAILABLE     saudável
    DEGRADED      falha retentável recente
    RATE_LIMITED  429
    EXHAUSTED     sem créditos / quota
    AUTH_ERROR    chave inválida (não se resolve sozinho)
    DOWN          falha dura não-auth (403 sem quota, 5xx persistente)
    UNKNOWN       sem observação

Quando TODOS os operacionais estão bloqueados, o estado geral é
`WAITING_FOR_PROVIDER_QUOTA`: o sistema não trava e não gasta chamadas.

Nada é fabricado: um provider sem observação fica UNKNOWN (tentável).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence

from .providers import (
    FAILURE_AUTH,
    FAILURE_CONNECTION,
    FAILURE_FORBIDDEN,
    FAILURE_NO_CREDITS,
    FAILURE_TIMEOUT,
    FAILURE_UNKNOWN,
)

STATE_AVAILABLE = "AVAILABLE"
STATE_DEGRADED = "DEGRADED"
STATE_RATE_LIMITED = "RATE_LIMITED"
STATE_EXHAUSTED = "EXHAUSTED"
STATE_AUTH_ERROR = "AUTH_ERROR"
STATE_DOWN = "DOWN"
STATE_UNKNOWN = "UNKNOWN"

OVERALL_READY = "READY"
OVERALL_WAITING = "WAITING_FOR_PROVIDER_QUOTA"

#: Cooldown por estado (segundos). Auth não se resolve sozinho: 24h.
COOLDOWNS: dict[str, float] = {
    STATE_AVAILABLE: 0.0,
    STATE_DEGRADED: 120.0,
    STATE_RATE_LIMITED: 900.0,
    STATE_EXHAUSTED: 6 * 3600.0,
    STATE_AUTH_ERROR: 24 * 3600.0,
    STATE_DOWN: 300.0,
    STATE_UNKNOWN: 0.0,
}

_QUOTA_HINTS = (
    "out_of_usage_credits", "usage quota", "credit limit", "no_credits",
    "quota has been reached", "request limit exceeded", "request_limit_exceeded",
)
_INVALID_KEY_HINTS = (
    "invalid or inactive api key", "valid apikey", "invalid api key",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_stamp(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def classify_provider(record: Mapping) -> str:
    """Estado do provider a partir do health observado (nunca inventado)."""
    state = str(record.get("state") or "").upper()
    kind = str(record.get("last_kind") or "").upper()
    status = record.get("last_status")
    error = str(record.get("last_error") or "").lower()
    credits_exhausted = bool(record.get("exhausted"))

    if credits_exhausted:
        return STATE_EXHAUSTED
    if any(h in error for h in _QUOTA_HINTS):
        return STATE_EXHAUSTED
    if any(h in error for h in _INVALID_KEY_HINTS):
        return STATE_AUTH_ERROR
    if state == "HEALTHY":
        return STATE_AVAILABLE
    if state == "NO_COVERAGE":
        # respondeu: não é falha de quota/auth
        return STATE_AVAILABLE
    if state in ("DEGRADED", "STALE"):
        return STATE_RATE_LIMITED if status == 429 else STATE_DEGRADED
    if state == "UNAVAILABLE":
        if status == 429:
            return STATE_RATE_LIMITED
        if kind in (FAILURE_AUTH,):
            return STATE_AUTH_ERROR
        if kind in (FAILURE_FORBIDDEN, FAILURE_NO_CREDITS):
            return STATE_EXHAUSTED
        if kind in (FAILURE_TIMEOUT, FAILURE_CONNECTION):
            return STATE_DOWN
        return STATE_DOWN
    return STATE_UNKNOWN


@dataclass(frozen=True)
class ProviderQuota:
    provider: str
    state: str
    cooldown_seconds: float
    next_attempt_at: str
    last_failure_at: str
    last_success_at: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "state": self.state,
            "cooldown_seconds": self.cooldown_seconds,
            "next_attempt_at": self.next_attempt_at,
            "last_failure_at": self.last_failure_at,
            "last_success_at": self.last_success_at,
            "reason": self.reason,
        }


class QuotaScheduler:
    """Decide quem pode ser chamado agora, a partir do health persistido."""

    def __init__(
        self,
        health: Mapping[str, Mapping] | None = None,
        credits: Mapping[str, Mapping] | None = None,
        *,
        now: datetime | None = None,
    ) -> None:
        self._health = dict(health or {})
        self._credits = dict(credits or {})
        self._now = now or _utcnow()

    @classmethod
    def from_store(cls, store=None, *, now: datetime | None = None) -> "QuotaScheduler":
        from .odds_snapshots import OddsSnapshotStore

        store = store or OddsSnapshotStore()
        try:
            health = store.load_provider_health()
        except Exception:  # noqa: BLE001
            health = {}
        try:
            credits = store.load_provider_credits()
        except Exception:  # noqa: BLE001
            credits = {}
        return cls(health, credits, now=now)

    def status(self, provider: str) -> ProviderQuota:
        record = dict(self._health.get(provider) or {})
        credit = dict(self._credits.get(provider) or {})
        if credit:
            record = {**record, "exhausted": credit.get("exhausted")}
        state = classify_provider(record)
        cooldown = COOLDOWNS.get(state, 0.0)
        last_failure = str(record.get("last_failure_at") or "")
        last_success = str(record.get("last_success_at") or "")
        next_at = ""
        if cooldown > 0:
            base = _parse_stamp(last_failure) or self._now
            next_at = (base + timedelta(seconds=cooldown)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        return ProviderQuota(
            provider=provider,
            state=state,
            cooldown_seconds=cooldown,
            next_attempt_at=next_at,
            last_failure_at=last_failure,
            last_success_at=last_success,
            reason=str(record.get("last_error") or "")[:200],
        )

    def should_attempt(self, provider: str) -> bool:
        """True se a chamada é permitida agora (AVAILABLE/UNKNOWN/cooldown ok)."""
        quota = self.status(provider)
        if quota.state in (STATE_AVAILABLE, STATE_UNKNOWN):
            return True
        if not quota.next_attempt_at:
            return True
        next_at = _parse_stamp(quota.next_attempt_at)
        return next_at is None or self._now >= next_at

    def filter_attemptable(
        self, providers: Sequence[tuple[str, object]]
    ) -> tuple[list[tuple[str, object]], dict[str, ProviderQuota]]:
        """Separa os providers que podem ser chamados agora dos bloqueados."""
        attemptable: list[tuple[str, object]] = []
        blocked: dict[str, ProviderQuota] = {}
        for name, provider in providers:
            if self.should_attempt(name):
                attemptable.append((name, provider))
            else:
                blocked[name] = self.status(name)
        return attemptable, blocked

    def overall(self, operational_names: Sequence[str]) -> dict:
        """Estado geral: READY se ao menos um operacional é tentável."""
        per = {name: self.status(name) for name in operational_names}
        attemptable = [n for n in operational_names if self.should_attempt(n)]
        waiting = not attemptable and bool(operational_names)
        return {
            "status": OVERALL_WAITING if waiting else OVERALL_READY,
            "attemptable": attemptable,
            "blocked": [n for n in operational_names if n not in attemptable],
            "providers": {n: q.to_dict() for n, q in per.items()},
        }


__all__ = [
    "QuotaScheduler",
    "ProviderQuota",
    "classify_provider",
    "STATE_AVAILABLE",
    "STATE_DEGRADED",
    "STATE_RATE_LIMITED",
    "STATE_EXHAUSTED",
    "STATE_AUTH_ERROR",
    "STATE_DOWN",
    "STATE_UNKNOWN",
    "OVERALL_READY",
    "OVERALL_WAITING",
    "COOLDOWNS",
]
