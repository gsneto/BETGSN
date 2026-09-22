"""FASE B.1 — contrato de provider de odds: aditivo, sem tocar o core.

O que esta suíte prova (conformance do contrato `OddsProvider`):

  1. provider valido devolve `NormalizedQuote`s canonicas, identificadas
     pelo nome do provider, com mercado, preco, timestamp e kickoff;
  2. provider indisponivel falha ALTO (`ProviderError`), nunca vazio;
  3. ausencia de cobertura e explicita (`no_coverage`), nunca sintetica;
  4. erro de provider preserva status/kind para o fallback decidir;
  5. creditos desconhecidos sao None — nunca zero com cara de medido;
  6. `fetched_at` injetado pelo chamador vira timestamp da quote sem
     horario proprio (comportamento de `normalize_event`);
  7. evento sem kickoff nao gera quote — o adapter nunca fabrica jogo;
  8. `estimated_cost` nunca propaga falha e nunca inventa credito;
  9. `divisions_for` devolve () quando o adapter nao sabe;
  10. `OddsProvider` e `runtime_checkable` (estrutural).

Mais duas verificacoes estruturais (estilo FASE A): o contrato NAO
carrega staking/strategy/promotion/CLV/portfolio/quant e NAO redefine
`ProviderError` (importa de `.providers`).

Os providers falsos usam eventos INVENTADOS e declarados como tal: sao
demonstracao de contrato, sem dados reais externos.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from betgsn.odds_normalize import (
    CANONICAL_MARKETS,
    NormalizedQuote,
    normalize_events,
)
from betgsn.odds_provider import (
    CreditUpdate,
    OddsFetchRequest,
    OddsProvider,
    OddsProviderFetch,
    divisions_for,
    estimated_cost,
)
from betgsn.providers import FAILURE_AUTH, FAILURE_RATE_LIMIT, ProviderError

_REPO = Path(__file__).resolve().parents[1]

#: Instante INVENTADO, injetado pelo chamador (o contrato nao consulta relogio).
FETCHED_AT = "2026-09-22T18:00:00+00:00"
#: Kickoff INVENTADO no formato ISO 8601 com offset (parseavel em 3.10+).
KICKOFF = "2026-09-25T16:00:00+00:00"
#: Timestamp proprio do provider, INVENTADO, anterior ao fetched_at.
EVENT_TIMESTAMP = "2026-09-22T17:59:00+00:00"


def _raw_event(
    home: str = "Casa Teste",
    away: str = "Visitante Teste",
    kickoff: str = KICKOFF,
    timestamp: str | None = None,
) -> dict:
    """Evento cru no shape de The Odds API; numeros INVENTADOS p/ contrato."""
    event = {
        "home_team": home,
        "away_team": away,
        "commence_time": kickoff,
        "bookmakers": [
            {
                "key": "betatest",
                "title": "BetaTest",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": home, "price": 2.10},
                            {"name": away, "price": 3.40},
                            {"name": "Draw", "price": 3.20},
                        ],
                    }
                ],
            }
        ],
    }
    if timestamp is not None:
        event["timestamp"] = timestamp
    return event


# ==========================================================================
# Fakes locais — espelham as regras que um adapter real deve seguir
# ==========================================================================


class FakeOddsProvider:
    """Provider de odds FALSO — usa o parser canonico como um adapter real.

    Nao inventa normalizacao paralela: as quotes nascem de
    `normalize_events` com o `fetched_at` do pedido, exatamente como o
    contrato manda.
    """

    name = "fake-odds-test"

    def __init__(
        self,
        events,
        *,
        snapshot_provider: str = "fake-odds-test",
        credits: CreditUpdate | None = None,
    ) -> None:
        self._events = list(events)
        self._snapshot_provider = snapshot_provider
        self._credits = credits

    def available(self) -> bool:
        return True

    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch:
        quotes = tuple(normalize_events(self._events, self.name, request.fetched_at))
        return OddsProviderFetch(
            quotes=quotes,
            raw_events=tuple(self._events),
            snapshot_provider=self._snapshot_provider,
            credits=self._credits,
        )


class FakeUnavailableProvider:
    """`available()` False — e fetch falha ALTO se chamado mesmo assim."""

    name = "fake-unavailable"

    def available(self) -> bool:
        return False

    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch:
        raise ProviderError(
            f"{self.name} indisponivel: chave ausente", kind=FAILURE_AUTH
        )


class FakeNoCoverageProvider:
    """Provider disponivel que respondeu SEM cobertura para o escopo."""

    name = "fake-no-coverage"

    def available(self) -> bool:
        return True

    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch:
        return OddsProviderFetch(quotes=(), no_coverage=True)


class FakeErrorProvider:
    """Provider disponivel cujo fetch falha com status/kind classificados."""

    name = "fake-error"

    def available(self) -> bool:
        return True

    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch:
        raise ProviderError(
            "limite de requisicoes excedido",
            status=429,
            kind=FAILURE_RATE_LIMIT,
            retryable=True,
        )


class FakeCostedProvider:
    """Provider com custo proprio declarado (5 creditos por fetch)."""

    name = "fake-costed"

    def available(self) -> bool:
        return True

    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch:
        return OddsProviderFetch()

    def estimated_cost(self, request: OddsFetchRequest) -> int:
        return 5


class FakeZeroCostProvider:
    """Custo proprio ZERO: o helper nunca deixa cair abaixo de 1."""

    name = "fake-zero-cost"

    def available(self) -> bool:
        return True

    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch:
        return OddsProviderFetch()

    def estimated_cost(self, request: OddsFetchRequest) -> int:
        return 0


class FakeExplodingCostProvider:
    """Custo proprio que explode: o helper engole e devolve o default."""

    name = "fake-exploding-cost"

    def available(self) -> bool:
        return True

    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch:
        return OddsProviderFetch()

    def estimated_cost(self, request: OddsFetchRequest) -> int:
        raise RuntimeError("boom")


class FakeScopedProvider:
    """Provider que sabe traduzir escopo (sport key) em divisoes."""

    name = "fake-scoped"

    def available(self) -> bool:
        return True

    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch:
        return OddsProviderFetch()

    def divisions_for(self, scope: str):
        return ("E0", "E1")


class MissingFetchProvider:
    """Tem name e available, mas NAO tem fetch_odds: falha no isinstance."""

    name = "sem-fetch"

    def available(self) -> bool:
        return True


# ==========================================================================
# Suite de conformance reutilizavel (casos 1, 6 e 10 validos)
# ==========================================================================


def run_contract_conformance(
    provider,
    *,
    expect_name: str,
    request: OddsFetchRequest | None = None,
    allowed_timestamps: tuple[str, ...] = (),
) -> OddsProviderFetch:
    """Suite de conformance REUTILIZAVEL para qualquer OddsProvider.

    As tasks seguintes (adapters reais) chamam esta MESMA funcao: o
    contrato e um so. Verifica o caso valido por completo — protocolo
    estrutural, identidade, mercados canonicos, preco, presenca de
    timestamp/kickoff e timestamp restrito a {timestamp do provider,
    fetched_at}.

    `allowed_timestamps` carrega os timestamps proprios que os eventos
    do provider declararam; tudo fora desse conjunto e fabricacao.
    """
    assert isinstance(provider, OddsProvider), (
        "provider precisa satisfazer o Protocol OddsProvider"
    )
    assert provider.name == expect_name
    assert provider.available() is True

    req = request or OddsFetchRequest(
        divisions=("E0",),
        markets=("Resultado Final (1X2)",),
        fetched_at=FETCHED_AT,
    )
    result = provider.fetch_odds(req)
    assert isinstance(result, OddsProviderFetch)

    allowed = {req.fetched_at, *allowed_timestamps}
    for quote in result.quotes:
        assert isinstance(quote, NormalizedQuote)
        assert quote.provider == provider.name
        assert quote.market in CANONICAL_MARKETS
        assert quote.price > 1.0
        assert quote.timestamp, "cotacao exige timestamp"
        assert quote.kickoff, "cotacao exige kickoff"
        assert quote.timestamp in allowed, (
            f"timestamp {quote.timestamp!r} fora de {{provider, fetched_at}}"
        )
    return result


# ==========================================================================
# Casos obrigatorios 1-10
# ==========================================================================


def test_valid_provider_conformance():
    """Caso 1: provider valido — quotes canonicas, completas, identificadas."""
    provider = FakeOddsProvider([_raw_event()])
    result = run_contract_conformance(provider, expect_name="fake-odds-test")
    assert len(result.quotes) == 3
    assert result.snapshot_provider == "fake-odds-test"
    assert result.no_coverage is False
    assert result.raw_events == (_raw_event(),)


def test_unavailable_provider_fails_loud():
    """Caso 2: indisponivel — fetch levanta ProviderError, nunca vazio."""
    provider = FakeUnavailableProvider()
    assert provider.available() is False
    with pytest.raises(ProviderError, match="indisponivel"):
        provider.fetch_odds(OddsFetchRequest(fetched_at=FETCHED_AT))


def test_no_coverage_is_explicit():
    """Caso 3: sem cobertura — quotes vazias E no_coverage True, explicito."""
    result = FakeNoCoverageProvider().fetch_odds(
        OddsFetchRequest(fetched_at=FETCHED_AT)
    )
    assert result.quotes == ()
    assert result.no_coverage is True
    assert result.errors == ()


def test_provider_error_preserves_status_kind():
    """Caso 4: ProviderError preserva status/kind para o fallback decidir."""
    provider = FakeErrorProvider()
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_odds(OddsFetchRequest(fetched_at=FETCHED_AT))
    assert exc_info.value.status == 429
    assert exc_info.value.kind == FAILURE_RATE_LIMIT
    assert exc_info.value.retryable is True


def test_unknown_credits_are_none_never_zero():
    """Caso 5: creditos nao informados = None; zero seria dado inventado."""
    provider = FakeOddsProvider([_raw_event()])
    result = run_contract_conformance(provider, expect_name="fake-odds-test")
    assert result.credits is None
    assert result.credits != CreditUpdate(0, 0, 0)


def test_fetched_at_injected_as_timestamp():
    """Caso 6: fetched_at do chamador e o timestamp da quote sem horario proprio.

    Quote de evento COM timestamp do provider mantem o dele; nenhuma
    quote carrega timestamp fora de {provider, fetched_at}.
    """
    provider = FakeOddsProvider(
        [
            _raw_event(timestamp=None),
            _raw_event(
                home="Outro Mandante",
                away="Outro Visitante",
                timestamp=EVENT_TIMESTAMP,
            ),
        ]
    )
    result = run_contract_conformance(
        provider,
        expect_name="fake-odds-test",
        allowed_timestamps=(EVENT_TIMESTAMP,),
    )
    assert {q.timestamp for q in result.quotes} == {FETCHED_AT, EVENT_TIMESTAMP}


def test_event_without_kickoff_yields_no_quotes():
    """Caso 7: evento sem kickoff gera ZERO quotes — kickoff nunca e fabricado."""
    provider = FakeOddsProvider([_raw_event(kickoff="")])
    result = provider.fetch_odds(OddsFetchRequest(fetched_at=FETCHED_AT))
    assert result.quotes == ()


def test_estimated_cost_default_is_one():
    """Caso 8a: sem metodo proprio, o custo estimado e o default (1)."""
    provider = FakeOddsProvider([])
    assert estimated_cost(provider, OddsFetchRequest(fetched_at=FETCHED_AT)) == 1


def test_estimated_cost_uses_provider_method():
    """Caso 8b: metodo proprio do provider e respeitado."""
    provider = FakeCostedProvider()
    assert estimated_cost(provider, OddsFetchRequest()) == 5


def test_estimated_cost_never_below_one():
    """Caso 8c: custo proprio 0 nao desce abaixo de 1."""
    provider = FakeZeroCostProvider()
    assert estimated_cost(provider, OddsFetchRequest()) == 1


def test_estimated_cost_swallows_failure():
    """Caso 8d: metodo que explode nao propaga — default 1."""
    provider = FakeExplodingCostProvider()
    assert estimated_cost(provider, OddsFetchRequest()) == 1


def test_divisions_for_default_is_empty():
    """Caso 9a: adapter sem divisions_for devolve () — nao sabe."""
    assert divisions_for(FakeOddsProvider([]), "soccer_epl") == ()


def test_divisions_for_uses_provider_method():
    """Caso 9b: metodo proprio devolve as divisoes cobertas pelo escopo."""
    assert divisions_for(FakeScopedProvider(), "soccer_epl") == ("E0", "E1")


def test_runtime_checkable_accepts_full_fake():
    """Caso 10a: fake com name/available/fetch_odds passa no isinstance."""
    assert isinstance(FakeOddsProvider([]), OddsProvider)


def test_runtime_checkable_rejects_missing_fetch():
    """Caso 10b: objeto sem fetch_odds NAO satisfaz o Protocol."""
    assert not isinstance(MissingFetchProvider(), OddsProvider)


# ==========================================================================
# Verificacoes estruturais (estilo FASE A)
# ==========================================================================


def test_structural_contract_carries_no_betting_or_quant_logic():
    """O contrato NAO carrega staking/strategy/promotion/CLV/portfolio/quant."""
    path = _REPO / "betgsn" / "odds_provider.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    identifiers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
    forbidden = {"staking", "strategy", "promotion", "clv", "portfolio", "quant"}
    assert not identifiers & forbidden, identifiers & forbidden


def test_structural_provider_error_imported_not_redefined():
    """`ProviderError` e importado de .providers, nunca redefinido aqui."""
    src = (_REPO / "betgsn" / "odds_provider.py").read_text(encoding="utf-8")
    assert "class ProviderError" not in src
    assert "from .providers import ProviderError" in src
