"""BETGSN :: odds_service — orquestracao multi-provider com fallback.

Fluxo:

    provider primario
      -> provider configurado? nao: pula
      -> health check (UNAVAILABLE / sem creditos): pula
      -> chamada com retry LIMITADO (429/5xx/rede)
      -> resposta vazia: NO_COVERAGE, tenta o proximo
      -> resposta com odds: normaliza, deduplica, grava snapshot, devolve

Se TODOS falharem, o servico devolve a ultima coleta conhecida marcada
como STALE (`stale=True`), nunca como odd atual. Sem coleta anterior, a
resposta e vazia com estado explicito.

Nenhum provider e obrigatorio. Se MCP ou um adapter cair, os demais
continuam; se todos caírem, a degradacao e controlada e visivel.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from .odds_health import (
    CreditController,
    HealthTracker,
    ProviderState,
    classify_exception,
)
from .odds_normalize import NormalizedQuote, dedupe_quotes, normalize_events
from .odds_snapshots import OddsSnapshotStore, observations_from_quotes
from .providers import FAILURE_NO_COVERAGE
from .timeutil import KickoffError, parse_kickoff

#: Odds mais velhas que isso nao representam o mercado atual.
DEFAULT_STALE_AFTER_SECONDS = 900.0


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ProviderAttempt:
    provider: str
    status: str          # OK | SKIPPED | FAILED | NO_COVERAGE
    kind: str = ""
    message: str = ""
    events: int = 0
    quotes: int = 0
    credits_remaining: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "status": self.status,
            "kind": self.kind,
            "message": self.message,
            "events": self.events,
            "quotes": self.quotes,
            "credits_remaining": self.credits_remaining,
        }


@dataclass
class OddsFetch:
    """Resultado de uma coleta, com procedencia e estado explicito."""

    provider: str = ""
    state: ProviderState = ProviderState.UNAVAILABLE
    quotes: list[NormalizedQuote] = field(default_factory=list)
    events: int = 0
    fetched_at: str = ""
    stale: bool = False
    fallback_used: bool = False
    attempts: list[ProviderAttempt] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    credits_remaining: Optional[int] = None
    snapshots_saved: int = 0

    @property
    def ok(self) -> bool:
        """True quando a coleta foi SAUDavel e as odds nao sao velhas."""
        return (
            self.state == ProviderState.HEALTHY
            and bool(self.quotes)
            and not self.stale
        )

    @property
    def bookmakers(self) -> list[str]:
        return sorted({q.bookmaker for q in self.quotes})

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "state": self.state.value,
            "events": self.events,
            "quotes": len(self.quotes),
            "bookmakers": self.bookmakers,
            "fetched_at": self.fetched_at,
            "stale": self.stale,
            "fallback_used": self.fallback_used,
            "attempts": [a.to_dict() for a in self.attempts],
            "errors": list(self.errors),
            "credits_remaining": self.credits_remaining,
            "snapshots_saved": self.snapshots_saved,
        }


class OddsService:
    """Coleta odds de varios providers com fallback e degradacao controlada."""

    def __init__(
        self,
        providers: Optional[Sequence[tuple[str, object]]] = None,
        health: Optional[HealthTracker] = None,
        credits: Optional[CreditController] = None,
        store: Optional[OddsSnapshotStore] = None,
        now: Callable[[], str] = _utcnow,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
        max_attempts_per_provider: int = 1,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._providers: list[tuple[str, object]] = list(providers or [])
        self._health = health or HealthTracker(now=now)
        self._credits = credits or CreditController(now=now)
        self._store = store
        self._now = now
        self._stale_after = max(0.0, float(stale_after_seconds))
        self._max_attempts = max(1, int(max_attempts_per_provider))
        self._sleep = sleep
        #: ultima coleta bem-sucedida por esporte (para degradacao controlada)
        self._cache: dict[str, tuple[str, str, list[NormalizedQuote], int]] = {}

    # ---------------------------------------------------------------- setup

    @classmethod
    def from_env(cls, **kwargs) -> "OddsService":
        """Monta o servico com os providers de odds configurados no .env."""
        from .providers import configured_odds_providers

        return cls(providers=configured_odds_providers(), **kwargs)

    @property
    def providers(self) -> list[str]:
        return [name for name, _ in self._providers]

    def health_snapshot(self) -> dict:
        return self._health.snapshot()

    def credits_snapshot(self) -> dict:
        return self._credits.snapshot()

    def status(self) -> dict:
        return {
            "providers": self.providers,
            "health": self.health_snapshot(),
            "credits": self.credits_snapshot(),
        }

    # ---------------------------------------------------------------- coleta

    def fetch(
        self,
        sport_key: str,
        markets: str = "h2h",
        regions: Optional[str] = None,
        cost: int = 1,
        persist: bool = True,
    ) -> OddsFetch:
        fetched_at = self._now()
        result = OddsFetch(fetched_at=fetched_at)
        attempts: list[ProviderAttempt] = []

        for index, (name, provider) in enumerate(self._providers):
            if not self._health.is_available(name):
                attempts.append(
                    ProviderAttempt(name, "SKIPPED", kind="UNAVAILABLE")
                )
                continue
            if not self._credits.can_spend(name, cost):
                attempts.append(
                    ProviderAttempt(name, "SKIPPED", kind="NO_CREDITS")
                )
                continue

            try:
                events, headers = self._call(provider, sport_key, markets, regions)
            except Exception as exc:  # noqa: BLE001 - queremos classificar tudo
                kind, _retryable = classify_exception(exc)
                self._health.record_failure(
                    name, kind, str(exc)[:300], status=getattr(exc, "status", None)
                )
                attempts.append(
                    ProviderAttempt(name, "FAILED", kind=kind, message=str(exc)[:300])
                )
                result.errors.append(f"{name}: {exc}")
                continue

            credits_remaining = self._apply_credits(name, headers, cost)
            if not events:
                self._health.record_no_coverage(name)
                attempts.append(ProviderAttempt(name, "NO_COVERAGE"))
                continue

            quotes = dedupe_quotes(
                normalize_events(events, name, fetched_at, sport_key=sport_key)
            )
            if not quotes:
                self._health.record_no_coverage(name)
                attempts.append(
                    ProviderAttempt(
                        name, "NO_COVERAGE", kind=FAILURE_NO_COVERAGE, events=len(events)
                    )
                )
                continue

            self._health.record_success(
                name, observations=len(quotes), credits_remaining=credits_remaining
            )
            attempts.append(
                ProviderAttempt(
                    name,
                    "OK",
                    events=len(events),
                    quotes=len(quotes),
                    credits_remaining=credits_remaining,
                )
            )
            result.provider = name
            result.state = ProviderState.HEALTHY
            result.quotes = quotes
            result.events = len(events)
            result.fallback_used = index > 0
            result.credits_remaining = credits_remaining
            result.attempts = attempts
            self._cache[sport_key] = (name, fetched_at, quotes, len(events))
            if persist:
                result.snapshots_saved = self._persist(quotes)
            return result

        result.attempts = attempts
        return self._degraded(sport_key, fetched_at, result)

    # ---------------------------------------------------------------- interno

    def _call(
        self,
        provider: object,
        sport_key: str,
        markets: str,
        regions: Optional[str],
    ) -> tuple[list[dict], dict[str, str]]:
        fn = getattr(provider, "live_odds_with_meta")
        try:
            return fn(sport_key, regions=regions, markets=markets)
        except TypeError:
            # adapters mais antigos sem o parametro `regions`
            return fn(sport_key, markets=markets)

    def _apply_credits(
        self, provider: str, headers: Optional[dict], cost: int
    ) -> Optional[int]:
        if headers:
            remaining = self._credits.update_from_headers(provider, headers)
            if remaining is not None:
                return remaining
        self._credits.record_spend(provider, cost)
        return self._credits.get(provider).known_remaining

    def _persist(self, quotes: Sequence[NormalizedQuote]) -> int:
        """Grava as cotacoes no store canonico (SQLite).

        Usa `observations_from_quotes`, o mesmo conversor da captura ao vivo,
        para que exista UMA regra de persistencia de odds no projeto — e para
        que o `match_key` gravado seja o `event_id` canonico que a API le.
        """
        if self._store is None:
            return 0
        observations = observations_from_quotes(quotes)
        if not observations:
            return 0
        try:
            return self._store.add(observations)
        except Exception:  # noqa: BLE001 - persistencia nao derruba a coleta
            return 0

    def _degraded(
        self, sport_key: str, fetched_at: str, result: OddsFetch
    ) -> OddsFetch:
        """Todos falharam: usa a ultima coleta, marcada como STALE se velha."""
        cached = self._cache.get(sport_key)
        if cached is None:
            result.state = (
                ProviderState.NO_COVERAGE
                if any(a.status == "NO_COVERAGE" for a in result.attempts)
                else ProviderState.UNAVAILABLE
            )
            result.provider = ""
            return result

        provider, cached_at, quotes, events = cached
        age = self._age_seconds(cached_at, fetched_at)
        stale = age > self._stale_after
        if stale:
            self._health.mark_stale(provider)
        result.provider = provider
        result.state = ProviderState.STALE if stale else ProviderState.DEGRADED
        result.quotes = list(quotes)
        result.events = events
        result.fetched_at = cached_at
        result.stale = stale
        result.fallback_used = True
        return result

    @staticmethod
    def _age_seconds(earlier: str, later: str) -> float:
        try:
            return (parse_kickoff(later) - parse_kickoff(earlier)).total_seconds()
        except KickoffError:
            return float("inf")
