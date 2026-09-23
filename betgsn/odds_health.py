"""BETGSN :: odds_health — saude de provider, fallback e controle de creditos.

Estados
-------
  HEALTHY      ultima coleta funcionou e os dados sao recentes;
  DEGRADED     falhou agora, mas pode voltar (429/5xx/timeout/rede);
  UNAVAILABLE  falha dura (401/403/sem creditos) ou falhas seguidas demais;
  STALE        ha dado, mas e velho demais para representar o mercado atual;
  NO_COVERAGE  provider respondeu, mas nao cobre aquele esporte/evento.

Regra central: um provider quebrado NUNCA derruba a coleta inteira. A
camada tenta o proximo e devolve o resultado com o estado explicito. E
odds antigas jamais sao apresentadas como atuais: quando o dado e velho,
o estado vira STALE e `stale=True` acompanha a resposta.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Mapping, Optional

from .providers import (
    FAILURE_AUTH,
    FAILURE_CONNECTION,
    FAILURE_FORBIDDEN,
    FAILURE_NO_CREDITS,
    FAILURE_TIMEOUT,
    FAILURE_UNKNOWN,
    ProviderError,
    parse_credit_headers,
)

#: Falhas que tornam o provider indisponivel ate intervencao/renovacao.
_HARD_KINDS = frozenset({FAILURE_AUTH, FAILURE_FORBIDDEN, FAILURE_NO_CREDITS})


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProviderState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    STALE = "STALE"
    NO_COVERAGE = "NO_COVERAGE"


@dataclass
class ProviderHealth:
    """Historico observavel de um provider. Sem segredos, sem dados de mercado."""

    provider: str
    state: ProviderState = ProviderState.HEALTHY
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_success_at: str = ""
    last_failure_at: str = ""
    last_error: str = ""
    last_status: Optional[int] = None
    last_kind: str = ""
    credits_remaining: Optional[int] = None
    observations: int = 0
    updated_at: str = ""
    #: latencia da ultima chamada bem-sucedida, em ms. None = nunca medida;
    #: nunca e estimada nem preenchida com valor padrao.
    latency_ms: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_error": self.last_error,
            "last_status": self.last_status,
            "last_kind": self.last_kind,
            "credits_remaining": self.credits_remaining,
            "observations": self.observations,
            "updated_at": self.updated_at,
            "latency_ms": self.latency_ms,
        }


class HealthTracker:
    """Acumula sucesso/falha por provider e deriva o estado."""

    def __init__(
        self,
        now: Callable[[], str] = _utcnow,
        unavailable_after: int = 3,
    ) -> None:
        self._now = now
        self._unavailable_after = max(1, int(unavailable_after))
        self._health: dict[str, ProviderHealth] = {}

    def get(self, provider: str) -> ProviderHealth:
        record = self._health.get(provider)
        if record is None:
            record = ProviderHealth(provider=provider, updated_at=self._now())
            self._health[provider] = record
        return record

    def record_success(
        self,
        provider: str,
        observations: int = 0,
        credits_remaining: Optional[int] = None,
        latency_ms: Optional[float] = None,
    ) -> ProviderHealth:
        record = self.get(provider)
        record.state = ProviderState.HEALTHY
        record.consecutive_failures = 0
        record.total_successes += 1
        record.last_success_at = self._now()
        record.observations += int(observations)
        record.last_error = ""
        record.last_status = None
        record.last_kind = ""
        if credits_remaining is not None:
            record.credits_remaining = credits_remaining
        if latency_ms is not None:
            record.latency_ms = float(latency_ms)
        record.updated_at = record.last_success_at
        return record

    def record_failure(
        self,
        provider: str,
        kind: str,
        message: str = "",
        status: Optional[int] = None,
    ) -> ProviderHealth:
        record = self.get(provider)
        record.total_failures += 1
        record.consecutive_failures += 1
        record.last_failure_at = self._now()
        record.last_error = message
        record.last_status = status
        record.last_kind = kind
        if kind in _HARD_KINDS:
            record.state = ProviderState.UNAVAILABLE
        elif record.consecutive_failures >= self._unavailable_after:
            record.state = ProviderState.UNAVAILABLE
        else:
            record.state = ProviderState.DEGRADED
        record.updated_at = record.last_failure_at
        return record

    def record_no_coverage(self, provider: str) -> ProviderHealth:
        """Provider respondeu bem, mas nao cobre o esporte pedido.

        Nao conta como falha: e ausencia de cobertura, nao doenca.
        """
        record = self.get(provider)
        record.state = ProviderState.NO_COVERAGE
        record.consecutive_failures = 0
        record.last_error = ""
        record.last_status = None
        record.last_kind = ""
        record.updated_at = self._now()
        return record

    def mark_stale(self, provider: str) -> ProviderHealth:
        """Ha dado, mas velho demais para ser tratado como mercado atual."""
        record = self.get(provider)
        record.state = ProviderState.STALE
        record.updated_at = self._now()
        return record

    def reset(self, provider: str) -> None:
        self._health.pop(provider, None)

    def state(self, provider: str) -> ProviderState:
        return self.get(provider).state

    def is_available(self, provider: str) -> bool:
        """False apenas quando nao adianta tentar (falha dura)."""
        return self.get(provider).state != ProviderState.UNAVAILABLE

    def snapshot(self) -> dict[str, dict]:
        return {name: record.to_dict() for name, record in sorted(self._health.items())}


def classify_exception(exc: BaseException) -> tuple[str, bool]:
    """(kind, retryable) para uma excecao qualquer."""
    if isinstance(exc, ProviderError):
        return exc.kind or FAILURE_UNKNOWN, bool(exc.retryable)
    if isinstance(exc, TimeoutError):
        return FAILURE_TIMEOUT, True
    if isinstance(exc, OSError):
        return FAILURE_CONNECTION, True
    return FAILURE_UNKNOWN, False


@dataclass
class CreditState:
    provider: str
    daily_limit: int = 0            # 0 = sem teto local
    used: int = 0
    remaining: Optional[int] = None  # do header do provider, quando informado
    exhausted: bool = False
    last_updated: str = ""

    @property
    def known_remaining(self) -> Optional[int]:
        """Saldo conhecido: o do header manda; senao, teto local menos uso."""
        if self.remaining is not None:
            return self.remaining
        if self.daily_limit > 0:
            return max(0, self.daily_limit - self.used)
        return None

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "daily_limit": self.daily_limit,
            "used": self.used,
            "remaining": self.remaining,
            "known_remaining": self.known_remaining,
            "exhausted": self.exhausted,
            "last_updated": self.last_updated,
        }


class CreditController:
    """Controle de creditos por provider.

    Quando o provider informa o saldo (The Odds API: x-requests-remaining),
    ele e a fonte da verdade. Sem header, vale o teto local configurado.
    Nunca estima credito que nao conhece: devolve None.
    """

    def __init__(self, now: Callable[[], str] = _utcnow) -> None:
        self._now = now
        self._credits: dict[str, CreditState] = {}

    def register(self, provider: str, daily_limit: int = 0) -> CreditState:
        state = CreditState(
            provider=provider,
            daily_limit=max(0, int(daily_limit)),
            last_updated=self._now(),
        )
        self._credits[provider] = state
        return state

    def get(self, provider: str) -> CreditState:
        state = self._credits.get(provider)
        if state is None:
            state = self.register(provider)
        return state

    def peek(self, provider: str) -> Optional[CreditState]:
        """Estado de credito SEM registrar o provider.

        `get` cria (e publica no snapshot) um estado default `used=0` para
        quem nunca informou creditos — persistir isso seria fabricar quota
        zero onde o correto e desconhecido (None). `peek` consulta sem
        efeito colateral: provider sem observacao de credito devolve None.
        """
        return self._credits.get(provider)

    def update_from_headers(
        self, provider: str, headers: Mapping[str, str]
    ) -> Optional[int]:
        """Atualiza o saldo a partir dos headers. Devolve o saldo ou None."""
        parsed = parse_credit_headers(headers)
        state = self.get(provider)
        if "used" in parsed:
            state.used = parsed["used"]
        if "remaining" in parsed:
            state.remaining = parsed["remaining"]
            state.exhausted = state.remaining <= 0
        state.last_updated = self._now()
        return state.remaining

    def apply_update(self, provider: str, update) -> Optional[int]:
        """Aplica um `CreditUpdate` do contrato de providers (FASE B).

        `update` None e no-op: creditos desconhecidos NAO sao inventados.
        A transferencia espelha `update_from_headers` — o saldo informado
        pelo provider e a fonte da verdade. Devolve o remaining informado
        ou None quando o update nao o carrega.
        """
        if update is None:
            return None
        state = self.get(provider)
        if update.used is not None:
            state.used = update.used
        if update.remaining is not None:
            state.remaining = update.remaining
            state.exhausted = state.remaining <= 0
        state.last_updated = self._now()
        return state.remaining

    def record_spend(self, provider: str, amount: int = 1) -> CreditState:
        state = self.get(provider)
        state.used += max(0, int(amount))
        if state.remaining is not None:
            state.remaining = max(0, state.remaining - max(0, int(amount)))
            state.exhausted = state.remaining <= 0
        elif state.daily_limit > 0:
            state.exhausted = state.used >= state.daily_limit
        state.last_updated = self._now()
        return state

    def can_spend(self, provider: str, amount: int = 1) -> bool:
        state = self.get(provider)
        if state.exhausted:
            return False
        remaining = state.known_remaining
        if remaining is None:
            return True
        return remaining >= max(0, int(amount))

    def mark_exhausted(self, provider: str) -> CreditState:
        state = self.get(provider)
        state.exhausted = True
        state.remaining = 0
        state.last_updated = self._now()
        return state

    def snapshot(self) -> dict[str, dict]:
        return {name: state.to_dict() for name, state in sorted(self._credits.items())}


def estimated_request_cost(n_markets: int, n_regions: int) -> int:
    """Custo estimado de uma chamada na The Odds API: mercados x regioes.

    Serve para orcamento local ANTES de gastar. O custo real vem no header
    x-requests-last e substitui esta estimativa.
    """
    return max(1, int(n_markets)) * max(1, int(n_regions))
