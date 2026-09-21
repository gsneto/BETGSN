"""BETGSN :: datalayer.source — orquestração multi-fonte com fallback.

O coração da camada de dados. Resolve UMA consulta (`fixtures`, `results`,
`stats`, `xg`, `odds`, ...) consultando várias fontes em ordem de
prioridade, com:

  - cache com TTL por tipo de dado (fresco -> stale explícito);
  - quota (`quota.QuotaManager`) e limite de ritmo (`RateLimiter`);
  - retry LIMITADO só para falhas transitórias (429/timeout/rede/5xx);
  - fallback para a próxima fonte quando uma falha ou não cobre o dado;
  - health/cobertura registrados por fonte;
  - proveniência completa no `DataEnvelope` (fonte, carimbo, idade, cache).

Nada aqui inventa dado. Quando tudo falha, o resultado é um envelope
`MISSING`/`NO_COVERAGE` com os motivos — nunca um valor vazio disfarçado
de sucesso.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from ..cache import (
    TTL_FIXTURES,
    TTL_H2H,
    TTL_HISTORICAL,
    TTL_INJURIES,
    TTL_LINEUPS,
    TTL_ODDS,
    TTL_TEAM_STATS,
    DiskCache,
)
from ..data_quality import grade_for_fetch
from ..quota import QuotaManager
from .envelope import (
    DataEnvelope,
    DataStatus,
    Provenance,
    build_provenance,
)
from .errors import ErrorKind, SourceError, classify_exception
from .health import HealthRegistry
from .ratelimit import RateLimiter
from .times import parse_stamp, utc_stamp

__all__ = [
    "Capabilities",
    "RawFetch",
    "RetryPolicy",
    "BaseSource",
    "SourceRegistration",
    "MultiSourceLayer",
    "TTL_BY_KIND",
    "DEFAULT_CACHE_ROOT",
]

#: Tipos de dado que a camada entende (fontes declaram quais suportam).
class Capabilities:
    FIXTURES = "fixtures"
    RESULTS = "results"
    STATS = "stats"
    XG = "xg"
    ODDS = "odds"
    INJURIES = "injuries"
    LINEUPS = "lineups"


#: TTL padrão por tipo de dado (segundos). Histórico nunca expira; odds e
#: escalações envelhecem rápido.
TTL_BY_KIND: dict[str, float] = {
    Capabilities.FIXTURES: TTL_FIXTURES,
    Capabilities.RESULTS: TTL_HISTORICAL,
    Capabilities.STATS: TTL_TEAM_STATS,
    Capabilities.XG: TTL_HISTORICAL,
    Capabilities.ODDS: TTL_ODDS,
    Capabilities.INJURIES: TTL_INJURIES,
    Capabilities.LINEUPS: TTL_LINEUPS,
    "h2h": TTL_H2H,
}

DEFAULT_CACHE_ROOT = Path(__file__).resolve().parent.parent.parent / "output" / "datalayer_cache"

#: Janela padrão em que um cache expirado ainda pode ser servido como STALE.
DEFAULT_MAX_STALE_SECONDS = 6 * 3600.0


@dataclass
class RawFetch:
    """Resposta crua de uma fonte, antes de virar envelope."""

    records: list[Any] = field(default_factory=list)
    source: str = ""
    source_timestamp: str = ""
    coverage: tuple[str, ...] = ()
    complete: bool = True
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_timestamp": self.source_timestamp,
            "coverage": list(self.coverage),
            "complete": self.complete,
            "note": self.note,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "RawFetch":
        return cls(
            source=payload.get("source", ""),
            source_timestamp=payload.get("source_timestamp", ""),
            coverage=tuple(payload.get("coverage", ())),
            complete=bool(payload.get("complete", True)),
            note=payload.get("note", ""),
        )


@dataclass(frozen=True)
class RetryPolicy:
    """Retry limitado com backoff exponencial (nunca infinito)."""

    max_attempts: int = 2
    backoff_seconds: float = 0.5
    backoff_factor: float = 2.0
    max_backoff_seconds: float = 5.0

    def delay_after(self, failure_index: int) -> float:
        """Espera após a falha de índice 0 (a primeira tentativa falhou)."""
        raw = self.backoff_seconds * (self.backoff_factor ** max(0, failure_index))
        return min(raw, self.max_backoff_seconds)


class BaseSource:
    """Contrato de uma fonte de dados plugável na camada.

    Subclasses declaram `name`, `capabilities` e implementam `fetch`. O
    default de encode/decode trata payloads JSON (listas/dicts); fontes que
    devolvem dataclasses podem sobrescrever ou declarar
    `supports_cache=False` no registro.
    """

    name: str = "source"
    capabilities: frozenset[str] = frozenset()

    def available(self) -> bool:
        """False quando falta chave/dado local — não é falha, é indisponibilidade."""
        return True

    def fetch(self, kind: str, **params: Any) -> RawFetch:
        raise NotImplementedError

    def encode(self, records: Iterable[Any]) -> Any:
        return [_jsonable(r) for r in records]

    def decode(self, payload: Any) -> list[Any]:
        return list(payload) if isinstance(payload, list) else []


@dataclass
class SourceRegistration:
    source: Any
    priority: int = 100
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    quota_provider: str = ""
    rate_limiter: RateLimiter | None = None
    ttl: float | None = None
    supports_cache: bool = True

    @property
    def name(self) -> str:
        return getattr(self.source, "name", type(self.source).__name__)

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset(getattr(self.source, "capabilities", frozenset()))


class MultiSourceLayer:
    """Consulta multi-fonte com cache, quota, health e fallback."""

    def __init__(
        self,
        *,
        cache: DiskCache | None = None,
        cache_root: Path | None = None,
        quota: QuotaManager | None = None,
        health: HealthRegistry | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], str] = utc_stamp,
        default_max_stale_seconds: float | None = DEFAULT_MAX_STALE_SECONDS,
        default_retry: RetryPolicy | None = None,
        namespace: str = "datalayer",
    ) -> None:
        self.cache = cache if cache is not None else DiskCache(cache_root or DEFAULT_CACHE_ROOT)
        self.quota = quota if quota is not None else QuotaManager()
        self.health = health if health is not None else HealthRegistry()
        self.sleep = sleep
        self.clock = clock
        self.default_max_stale_seconds = default_max_stale_seconds
        self.default_retry = default_retry or RetryPolicy()
        self.namespace = namespace
        self._registrations: list[SourceRegistration] = []

    # ------------------------------------------------------------ registro

    def register(
        self,
        source: Any,
        *,
        priority: int = 100,
        retry: RetryPolicy | None = None,
        quota_provider: str | None = None,
        rate_limiter: RateLimiter | None = None,
        ttl: float | None = None,
        supports_cache: bool = True,
    ) -> SourceRegistration:
        registration = SourceRegistration(
            source=source,
            priority=priority,
            retry=retry or self.default_retry,
            quota_provider=quota_provider if quota_provider is not None else getattr(source, "name", ""),
            rate_limiter=rate_limiter,
            ttl=ttl,
            supports_cache=supports_cache,
        )
        self._registrations.append(registration)
        return registration

    def registrations(self) -> tuple[SourceRegistration, ...]:
        return tuple(self._registrations)

    def sources_for(self, kind: str) -> list[SourceRegistration]:
        return [r for r in self._registrations if kind in r.capabilities]

    def _effective_ttl(self, kind: str, candidates: list[SourceRegistration]) -> float:
        """TTL do cache: override da fonte mais restritivo, senão o do tipo.

        Um TTL explícito por registro vence o default do tipo de dado, para
        permitir ajuste fino por fonte sem alterar o resto.
        """
        overrides = [r.ttl for r in candidates if r.ttl is not None]
        if overrides:
            return min(overrides)
        return TTL_BY_KIND.get(kind, TTL_FIXTURES)

    # ------------------------------------------------------------ consulta

    def fetch(
        self,
        kind: str,
        *,
        params: dict[str, Any] | None = None,
        ttl: float | None = None,
        allow_stale: bool = True,
        max_stale_seconds: float | None = None,
        require_records: bool = True,
        use_cache: bool = True,
        force: bool = False,
        now: str | None = None,
    ) -> DataEnvelope:
        """Resolve `kind` pela melhor fonte disponível, com fallback.

        Ordem: cache fresco -> fontes (prioridade/health) -> cache stale
        explícito -> MISSING. `force=True` ignora cache e cooldown.
        """
        params = dict(params or {})
        stamp = now or self.clock()
        cache_key = _cache_key(kind, params)
        candidates = self.sources_for(kind)
        ttl_value = (
            ttl if ttl is not None else self._effective_ttl(kind, candidates)
        )
        max_stale = (
            max_stale_seconds
            if max_stale_seconds is not None
            else self.default_max_stale_seconds
        )

        if use_cache and not force:
            fresh = self._read_cache(kind, cache_key, stamp, allow_stale=False)
            if fresh is not None:
                return fresh

        if not candidates:
            return DataEnvelope(
                kind=kind,
                status=DataStatus.NO_COVERAGE,
                note=f"nenhuma fonte registrada cobre {kind!r}",
            )

        ordered = self._order(candidates, kind, stamp, force=force)
        errors: list[str] = []
        alternatives: list[Any] = []
        for registration in ordered:
            name = registration.name
            source_health = self.health.health(name)

            if not force and source_health.in_cooldown(stamp):
                errors.append(f"{name}: em cooldown até {source_health.cooldown_until}")
                continue

            if not _is_available(registration.source):
                errors.append(f"{name}: indisponível (sem chave ou dados locais)")
                continue

            if not force and kind in source_health.no_coverage and len(ordered) > 1:
                errors.append(f"{name}: sem cobertura para {kind}")
                continue

            quota_error = self._consume_quota(registration, name, stamp)
            if quota_error is not None:
                errors.append(quota_error)
                continue

            if registration.rate_limiter is not None:
                registration.rate_limiter.acquire()

            try:
                raw = self._attempt(registration, kind, params)
            except SourceError as err:
                if err.kind == ErrorKind.NO_COVERAGE:
                    self.health.mark_no_coverage(name, kind)
                else:
                    self.health.record_failure(name, err, at=stamp)
                errors.append(str(err))
                alternatives.append(_failure_provenance(name, err, stamp))
                continue

            if raw is None or not raw.complete:
                err = SourceError(
                    ErrorKind.INCOMPLETE, name,
                    (raw.note if raw else "") or "resposta vazia ou incompleta",
                )
                self.health.record_failure(name, err, at=stamp)
                errors.append(str(err))
                alternatives.append(_failure_provenance(name, err, stamp))
                continue

            if require_records and not raw.records:
                self.health.mark_no_coverage(name, kind)
                errors.append(f"{name}: sem registros para {kind}")
                alternatives.append(
                    _failure_provenance(
                        name, SourceError(ErrorKind.NO_COVERAGE, name, f"sem {kind}"), stamp
                    )
                )
                continue

            self.health.record_success(name, at=stamp, feature=kind)
            degraded = bool(errors) or bool(alternatives)
            envelope = self._success_envelope(
                kind, raw, registration, stamp,
                degraded=degraded, alternatives=tuple(alternatives),
                errors=tuple(errors),
            )
            if use_cache and registration.supports_cache:
                self._write_cache(kind, cache_key, raw, registration, ttl_value, stamp)
            return envelope

        if use_cache:
            stale = self._read_cache(
                kind, cache_key, stamp, allow_stale=True, max_stale_seconds=max_stale
            )
            if stale is not None:
                return DataEnvelope(
                    value=stale.value,
                    provenance=stale.provenance,
                    status=DataStatus.STALE,
                    kind=kind,
                    errors=tuple(errors),
                    note=(
                        "todas as fontes falharam; servindo cache antigo "
                        f"({stale.provenance.age_human if stale.provenance else 'idade desconhecida'})"
                    ),
                )

        return DataEnvelope(
            kind=kind,
            status=DataStatus.MISSING,
            errors=tuple(errors),
            note="; ".join(errors) or f"nenhuma fonte devolveu {kind!r}",
        )

    def health_snapshot(self) -> dict[str, dict]:
        return self.health.snapshot()

    def coverage_report(self):
        """Retrato de cobertura das fontes registradas (ver `datalayer.coverage`)."""
        from .coverage import build_coverage

        return build_coverage(self._registrations, self.health)

    # ------------------------------------------------------------ interno

    def _order(
        self,
        candidates: list[SourceRegistration],
        kind: str,
        now: str,
        *,
        force: bool,
    ) -> list[SourceRegistration]:
        def rank(reg: SourceRegistration) -> tuple[int, int]:
            h = self.health.health(reg.name)
            if not force and h.in_cooldown(now):
                return (90, reg.priority)
            if not _is_available(reg.source):
                return (95, reg.priority)
            no_cov = kind in h.no_coverage
            return (h.rank + (3 if no_cov else 0), reg.priority)

        return sorted(candidates, key=rank)

    def _consume_quota(
        self, registration: SourceRegistration, name: str, now: str
    ) -> str | None:
        if not registration.quota_provider:
            return None
        if self.quota.try_consume(registration.quota_provider):
            return None
        err = SourceError(ErrorKind.QUOTA, name, "quota esgotada para hoje")
        self.health.record_failure(name, err, at=now)
        return str(err)

    def _attempt(
        self, registration: SourceRegistration, kind: str, params: dict[str, Any]
    ) -> RawFetch | None:
        policy = registration.retry
        attempt = 0
        while True:
            try:
                return registration.source.fetch(kind, **params)
            except SourceError:
                raise
            except Exception as exc:  # noqa: BLE001 - convertido e classificado
                err = classify_exception(exc, registration.name)
                if err.retryable and attempt + 1 < max(1, policy.max_attempts):
                    delay = policy.delay_after(attempt)
                    if delay > 0:
                        self.sleep(delay)
                    attempt += 1
                    continue
                raise err

    def _success_envelope(
        self,
        kind: str,
        raw: RawFetch,
        registration: SourceRegistration,
        now: str,
        *,
        degraded: bool,
        alternatives: tuple[Any, ...],
        errors: tuple[str, ...] = (),
    ) -> DataEnvelope:
        primary = registration.priority <= min(
            (r.priority for r in self._registrations if kind in r.capabilities),
            default=registration.priority,
        )
        quality = grade_for_fetch(
            primary=primary, from_cache=False, complete=raw.complete, stale=False
        )
        provenance = build_provenance(
            source=raw.source or registration.name,
            source_timestamp=raw.source_timestamp,
            fetched_at=now,
            from_cache=False,
            quality=quality,
            coverage=raw.coverage,
            note=raw.note,
            now=now,
        )
        status = DataStatus.DEGRADED if degraded else DataStatus.OK
        return DataEnvelope(
            value=raw.records,
            provenance=provenance,
            status=status,
            kind=kind,
            errors=errors,
            alternatives=alternatives,
            note=raw.note,
        )

    def _cache_namespace(self, kind: str) -> str:
        return f"{self.namespace}/{kind}"

    def _write_cache(
        self,
        kind: str,
        key: str,
        raw: RawFetch,
        registration: SourceRegistration,
        ttl: float,
        now: str,
    ) -> None:
        payload = raw.to_json()
        payload["records"] = _encode(registration.source, raw.records)
        try:
            self.cache.put(
                self._cache_namespace(kind), key, payload,
                ttl=ttl, source=raw.source, created_at=_stamp_epoch(now),
            )
        except (TypeError, ValueError):
            # Dado não serializável não pode derrubar a consulta: a fonte já
            # respondeu. Simplesmente não cacheia.
            pass

    def _read_cache(
        self,
        kind: str,
        key: str,
        now: str,
        *,
        allow_stale: bool,
        max_stale_seconds: float | None = None,
    ) -> DataEnvelope | None:
        now_epoch = _stamp_epoch(now)
        meta = self.cache.get_with_meta(
            self._cache_namespace(kind),
            key,
            allow_stale=allow_stale,
            max_stale_seconds=max_stale_seconds,
            now=now_epoch,
        )
        if meta is None:
            return None
        data, info = meta
        if not isinstance(data, dict):
            return None
        raw = RawFetch.from_json(data)
        registration = self._registration_for(raw.source)
        if registration is not None:
            records = _decode(registration.source, data.get("records", []))
        else:
            records = list(data.get("records", []) or [])
        created_at = float(info.get("created_at", now_epoch))
        fetched_at = utc_stamp(_epoch_datetime(created_at))
        stale = bool(info.get("stale"))
        quality = grade_for_fetch(
            primary=True, from_cache=True, complete=raw.complete, stale=stale
        )
        provenance = build_provenance(
            source=raw.source,
            source_timestamp=raw.source_timestamp,
            fetched_at=fetched_at,
            from_cache=True,
            quality=quality,
            coverage=raw.coverage,
            note=raw.note,
            now=now,
        )
        return DataEnvelope(
            value=records,
            provenance=provenance,
            status=DataStatus.STALE if stale else DataStatus.OK,
            kind=kind,
            note=raw.note,
        )

    def _registration_for(self, source_name: str) -> SourceRegistration | None:
        for registration in self._registrations:
            if registration.name == source_name:
                return registration
        return None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _failure_provenance(name: str, error: SourceError, now: str) -> Provenance:
    """Proveniência de uma fonte que falhou — fica registrada como alternativa."""
    from ..data_quality import QualityGrade

    return Provenance(
        source=name,
        fetched_at=now,
        quality=QualityGrade.LOW,
        note=str(error),
    )


def _encode(source: Any, records: Any) -> Any:
    encoder = getattr(source, "encode", None)
    if callable(encoder):
        return encoder(records)
    return [_jsonable(r) for r in records]


def _decode(source: Any, payload: Any) -> list[Any]:
    decoder = getattr(source, "decode", None)
    if callable(decoder):
        return list(decoder(payload))
    return list(payload) if isinstance(payload, list) else []


def _is_available(source: Any) -> bool:
    checker = getattr(source, "available", None)
    if checker is None:
        return True
    try:
        return bool(checker())
    except Exception:  # noqa: BLE001 - fonte quebrada = indisponível
        return False


def _cache_key(kind: str, params: dict[str, Any]) -> str:
    try:
        body = json.dumps(params, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        body = repr(sorted((str(k), str(v)) for k, v in params.items()))
    return f"{kind}:{body}"


def _stamp_epoch(stamp: str) -> float:
    moment = parse_stamp(stamp)
    return moment.timestamp() if moment is not None else time.time()


def _epoch_datetime(epoch: float):
    from datetime import datetime

    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _jsonable(value: Any) -> Any:
    """Converte dataclasses/tuplas num formato JSON estável."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
