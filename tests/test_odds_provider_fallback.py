"""FASE B.4 — health/creditos/fallback/scanner/API pela abstracao de providers.

O que esta suite prova:

  1. `CreditController.apply_update`: None e no-op (nunca inventa credito);
     remaining informado e a fonte da verdade (header-first); credito
     desconhecido permanece None;
  2. fallback com providers do CONTRATO (`fetch_odds`): A falha ->
     health de A registra a falha; B responde -> quotes de B com
     `fallback_used=True` e creditos reais (None quando ausentes);
  3. ordem de fallback preservada: registry mantem The Odds API antes de
     ParlayAPI, e o servico prefere o primeiro provider saudavel;
  4. scanner ao vivo consome `fetch_odds` do registry — opportunities
     construidas das quotes normalizadas, sem odds sinteticas quando
     `no_coverage`, sem parse de evento cru, sem leitura de header;
  5. features de /api/providers vem do registry com fallback ao
     dicionario historico para nomes fora dele (DTO/schema identicos);
  6. checagem estrutural: value_strategy.py nao le "x-requests" nem
     constrói OddsApiProvider.from_env (padrao AST da FASE A).

Os providers falsos usam numeros INVENTADOS e declarados como tal:
demonstracao de contrato, sem dados reais externos.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import betgsn.api.service as api_service
import betgsn.value_strategy as value_strategy
from betgsn.api.server import app
from betgsn.api.service import BetgsnService
from betgsn.odds_health import CreditController
from betgsn.odds_normalize import normalize_events
from betgsn.odds_provider import CreditUpdate, OddsFetchRequest, OddsProviderFetch
from betgsn.odds_registry import (
    OddsProviderRegistry,
    ProviderSpec,
    default_odds_registry,
)
from betgsn.odds_service import OddsService
from betgsn.providers import FAILURE_RATE_LIMIT, ProviderError
from betgsn.value_strategy import MARKETS, scan_live

_REPO = Path(__file__).resolve().parents[1]

#: Kickoff INVENTADO no futuro: as quotes nascem pre-kickoff.
KICKOFF = "2030-06-15T18:00:00Z"
#: Instante INVENTADO, injetado pelo chamador (o scanner nao consulta
#: relogio: o fetched_at das requests vem desta funcao falsa).
FETCHED_AT = "2026-09-22T18:00:00Z"


# ------------------------------------------------------------------ fakes


def _h2h(home, away, p_home, p_draw, p_away):
    return {"key": "h2h", "outcomes": [
        {"name": home, "price": p_home},
        {"name": away, "price": p_away},
        {"name": "Draw", "price": p_draw},
    ]}


def _raw_event(home="Alfa", away="Bravo"):
    """Evento cru no shape de The Odds API; numeros INVENTADOS.

    Tres casas com o favorito abaixo de 1,30: o scanner deve achar UMA
    oportunidade (outcome "1", melhor preco na terceira casa).
    """
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": KICKOFF,
        "bookmakers": [
            {"key": "pinnacle", "title": "Pinnacle",
             "markets": [_h2h(home, away, 1.20, 5.5, 8.0)]},
            {"key": "betatest", "title": "BetaTest",
             "markets": [_h2h(home, away, 1.22, 5.4, 8.2)]},
            {"key": "gama", "title": "Gama",
             "markets": [_h2h(home, away, 1.25, 5.3, 8.4)]},
        ],
    }


class Clock:
    def __init__(self, value: str):
        self.value = value

    def __call__(self) -> str:
        return self.value


class FakeContractProvider:
    """Provider de odds FALSO no contrato da FASE B (`fetch_odds`).

    As quotes nascem do parser canonico (`normalize_events`) com o
    `fetched_at` do pedido, exatamente como os adapters reais. Guarda as
    requests recebidas para o teste conferir o pedido canonico.
    """

    def __init__(
        self,
        name,
        *,
        events=(),
        credits=None,
        exc=None,
        no_coverage=False,
        cost=None,
    ):
        self.name = name
        self._events = list(events)
        self._credits = credits
        self._exc = exc
        self._no_coverage = no_coverage
        self._cost = cost
        self.requests: list[OddsFetchRequest] = []

    def available(self) -> bool:
        return True

    def estimated_cost(self, request) -> int:
        return self._cost if self._cost is not None else 1

    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch:
        self.requests.append(request)
        if self._exc is not None:
            raise self._exc
        quotes = tuple(normalize_events(self._events, self.name, request.fetched_at))
        return OddsProviderFetch(
            quotes=quotes,
            raw_events=tuple(self._events),
            credits=self._credits,
            no_coverage=self._no_coverage,
        )


def _registry_with(*pairs):
    """Registry com specs cujas factories devolvem os providers dados."""
    registry = OddsProviderRegistry()
    for priority, (name, provider) in enumerate(pairs, 1):
        registry.register(
            ProviderSpec(name=name, factory=lambda p=provider: p, priority=priority)
        )
    return registry


def _registry_without_anything():
    """Registry cujo provider nao esta configurado (factory -> None)."""
    registry = OddsProviderRegistry()
    registry.register(
        ProviderSpec(name="The Odds API", factory=lambda: None, priority=1)
    )
    return registry


def _patch_scanner(monkeypatch, registry):
    monkeypatch.setattr(value_strategy, "default_odds_registry", lambda: registry)
    monkeypatch.setattr(value_strategy, "_utcnow", lambda: FETCHED_AT)


# ==========================================================================
# (1) CreditController.apply_update — sem credito inventado
# ==========================================================================


def test_apply_update_none_is_noop_and_invents_nothing():
    cc = CreditController()
    assert cc.apply_update("X", None) is None
    assert cc.snapshot() == {}  # nada foi registrado nem estimado


def test_apply_update_remaining_is_source_of_truth():
    cc = CreditController()
    cc.register("X", daily_limit=100)
    cc.apply_update("X", CreditUpdate(remaining=7, used=13))
    state = cc.get("X")
    assert state.remaining == 7
    assert state.used == 13
    # header-first: o saldo informado manda sobre o teto local (7, nao 87)
    assert state.known_remaining == 7
    assert state.exhausted is False


def test_apply_update_used_only_keeps_remaining_none():
    cc = CreditController()
    cc.apply_update("X", CreditUpdate(used=5))
    state = cc.get("X")
    assert state.remaining is None
    assert state.known_remaining is None  # nunca inventado


# ==========================================================================
# (2) fallback com providers do contrato — saude real, creditos reais
# ==========================================================================


def test_contract_fallback_a_fails_b_responds():
    a = FakeContractProvider(
        "A",
        exc=ProviderError("429 limite", status=429,
                          kind=FAILURE_RATE_LIMIT, retryable=True),
    )
    b = FakeContractProvider("B", events=[_raw_event()],
                             credits=CreditUpdate(remaining=42))
    svc = OddsService(providers=[("A", a), ("B", b)],
                      now=Clock("2026-09-22T18:00:00Z"))
    result = svc.fetch("soccer_epl")

    assert result.ok
    assert result.provider == "B"
    assert result.fallback_used is True
    assert result.quotes and all(q.provider == "B" for q in result.quotes)
    assert result.credits_remaining == 42
    assert svc.credits_snapshot()["B"]["remaining"] == 42

    health = svc.health_snapshot()
    assert health["A"]["state"] == "DEGRADED"
    assert health["A"]["total_failures"] == 1
    assert health["B"]["state"] == "HEALTHY"


def test_contract_fallback_credits_none_when_provider_silent():
    a = FakeContractProvider("A", exc=ProviderError("503", status=503))
    b = FakeContractProvider("B", events=[_raw_event()])
    svc = OddsService(providers=[("A", a), ("B", b)],
                      now=Clock("2026-09-22T18:00:00Z"))
    result = svc.fetch("soccer_epl")

    assert result.ok
    assert result.credits_remaining is None  # ausente, nunca zero
    assert svc.credits_snapshot()["B"]["remaining"] is None
    assert svc.credits_snapshot()["B"]["known_remaining"] is None


def test_estimated_cost_gates_can_spend_for_contract_providers():
    """O gate usa odds_provider.estimated_cost, nao o custo default 1.

    Teto local de 2 creditos e custo declarado de 3: com o custo do
    contrato o provider e PULADO (sem saldo); com o custo legacy 1 ele
    passaria — e gastaria o que nao tem.
    """
    p = FakeContractProvider("C", events=[_raw_event()], cost=3)
    credits = CreditController(now=Clock("2026-09-22T18:00:00Z"))
    credits.register("C", daily_limit=2)
    svc = OddsService(providers=[("C", p)], credits=credits,
                      now=Clock("2026-09-22T18:00:00Z"))
    result = svc.fetch("soccer_epl")

    assert result.ok is False
    assert result.attempts[0].status == "SKIPPED"
    assert result.attempts[0].kind == "NO_CREDITS"
    assert p.requests == []  # pulado ANTES da chamada


def test_estimated_cost_drives_local_spend_when_no_credit_update():
    """Sem update de creditos, o spend local usa o custo do contrato."""
    p = FakeContractProvider("C", events=[_raw_event()], cost=3)
    svc = OddsService(providers=[("C", p)], now=Clock("2026-09-22T18:00:00Z"))
    result = svc.fetch("soccer_epl")

    assert result.ok
    assert svc.credits_snapshot()["C"]["used"] == 3


# ==========================================================================
# (3) ordem de fallback preservada
# ==========================================================================


def test_default_registry_order_the_odds_api_before_parlayapi(monkeypatch):
    monkeypatch.setenv("BETGSN_ODDS_API_KEY", "chave-teste")
    monkeypatch.setenv("BETGSN_PARLAY_API_KEY", "chave-teste")
    monkeypatch.setenv("BETGSN_PARLAY_API_BASE", "https://parlay.invalid/api/")

    assert default_odds_registry().names() == ["The Odds API", "ParlayAPI"]
    pairs = default_odds_registry().available_providers()
    assert [name for name, _ in pairs] == ["The Odds API", "ParlayAPI"]


def test_service_prefers_first_healthy_provider():
    a = FakeContractProvider("A", events=[_raw_event()])
    b = FakeContractProvider("B", events=[_raw_event()])
    svc = OddsService(providers=[("A", a), ("B", b)],
                      now=Clock("2026-09-22T18:00:00Z"))
    result = svc.fetch("soccer_epl")

    assert result.ok
    assert result.provider == "A"
    assert result.fallback_used is False
    assert b.requests == []  # o segundo nem e chamado


# ==========================================================================
# (4) scanner ao vivo via registry — quotes, nunca payload cru
# ==========================================================================


def test_scan_live_builds_opportunities_from_quotes(monkeypatch):
    provider = FakeContractProvider(
        "FakeOdds", events=[_raw_event()], credits=CreditUpdate(remaining=7),
    )
    _patch_scanner(monkeypatch, _registry_with(("FakeOdds", provider)))

    found, meta = scan_live(["soccer_epl"])

    assert len(found) == 1
    op = found[0]
    assert op.match == "Alfa vs Bravo"
    assert op.outcome == "1"
    assert op.best_odd == pytest.approx(1.25)
    assert op.best_book == "Gama"
    assert op.n_books == 3
    assert op.is_home is True
    assert op.commence_time == KICKOFF  # kickoff da quote, nao inventado
    assert op.median_odd == pytest.approx(1.22)

    assert meta["sports"]["soccer_epl"] == {"events": 1, "opportunities": 1}
    assert meta["credits_remaining"] == 7  # do contrato, nao de header

    # pedido canonico: mercados internos + fetched_at injetado
    request = provider.requests[0]
    assert request.markets == MARKETS
    assert request.fetched_at == FETCHED_AT


def test_scan_live_falls_back_when_first_provider_errors(monkeypatch):
    a = FakeContractProvider("A", exc=ProviderError("500 do A", status=500))
    b = FakeContractProvider("B", events=[_raw_event()])
    _patch_scanner(monkeypatch, _registry_with(("A", a), ("B", b)))

    found, meta = scan_live(["soccer_epl"])

    assert len(found) == 1
    assert found[0].best_book == "Gama"
    # o erro de A nao sobrevive quando B atende o esporte
    assert "error" not in meta["sports"]["soccer_epl"]
    assert meta["sports"]["soccer_epl"]["opportunities"] == 1


def test_scan_live_no_coverage_yields_no_synthetic_odds(monkeypatch):
    provider = FakeContractProvider("FakeOdds", no_coverage=True)
    _patch_scanner(monkeypatch, _registry_with(("FakeOdds", provider)))

    found, meta = scan_live(["soccer_epl"])

    assert found == []  # ausencia explicita, nunca odd sintetica
    assert meta["sports"]["soccer_epl"] == {"events": 0, "opportunities": 0}
    assert meta["credits_remaining"] is None


def test_scan_live_provider_error_recorded_in_meta(monkeypatch):
    provider = FakeContractProvider("FakeOdds",
                                    exc=ProviderError("sem cobertura de rede"))
    _patch_scanner(monkeypatch, _registry_with(("FakeOdds", provider)))

    found, meta = scan_live(["soccer_epl"])

    assert found == []
    assert meta["sports"]["soccer_epl"] == {"error": "sem cobertura de rede"}


def test_scan_live_without_configured_providers_raises(monkeypatch):
    """Sem provider configurado: o erro explicito de sempre (preservado)."""
    _patch_scanner(monkeypatch, _registry_without_anything())

    with pytest.raises(ProviderError, match="BETGSN_ODDS_API_KEY"):
        scan_live(["soccer_epl"])


# ==========================================================================
# (5) /api/providers — features do registry, fallback historico
# ==========================================================================


def test_api_features_come_from_registry_metadata(monkeypatch):
    monkeypatch.setenv("BETGSN_ODDS_API_KEY", "chave-teste")
    registry = OddsProviderRegistry()
    registry.register(
        ProviderSpec(
            name="The Odds API",
            factory=lambda: None,
            priority=1,
            features=("odds", "live"),
        )
    )
    monkeypatch.setattr(api_service, "default_odds_registry", lambda: registry)

    svc = BetgsnService(source="synthetic")
    by_name = {p.name: p for p in svc.providers().providers}
    dto = by_name["The Odds API"]
    assert dto.features == ["odds", "live"]  # do registry, nao do dicionario
    assert dto.status == "UNKNOWN"  # sem observacao: nunca HEALTHY


def test_api_features_fallback_for_names_outside_registry(monkeypatch):
    """Nomes fora do registry caem no dicionario historico (DTO igual)."""
    monkeypatch.setenv("BETGSN_ODDS_API_KEY", "chave-teste")
    monkeypatch.setenv("BETGSN_PARLAY_API_KEY", "chave-teste")
    monkeypatch.setenv("BETGSN_PARLAY_API_BASE", "https://parlay.invalid/api/")
    monkeypatch.setenv("BETGSN_APIFOOTBALL_KEY", "chave-teste")

    svc = BetgsnService(source="synthetic")
    by_name = {p.name: p for p in svc.providers().providers}

    # specs do registry padrao nao declaram features: fallback historico
    assert by_name["The Odds API"].features == ["odds"]
    assert by_name["ParlayAPI"].features == ["odds"]
    # nao sao providers de odds do registry: sempre o dicionario
    assert by_name["API-Football"].features == [
        "fixtures", "historical", "statistics",
    ]
    # estados continuam no vocabulario canonico da API
    assert {p.status for p in by_name.values()} <= {
        "UNKNOWN", "HEALTHY", "DEGRADED", "UNAVAILABLE", "STALE", "NO_COVERAGE",
    }


def test_api_providers_route_serves_registry_features(monkeypatch):
    monkeypatch.setenv("BETGSN_ODDS_API_KEY", "chave-teste")
    registry = OddsProviderRegistry()
    registry.register(
        ProviderSpec(
            name="The Odds API",
            factory=lambda: None,
            priority=1,
            features=("odds", "live"),
        )
    )
    monkeypatch.setattr(api_service, "default_odds_registry", lambda: registry)

    with TestClient(app) as client:
        body = client.get("/api/providers").json()

    by_name = {p["name"]: p for p in body["providers"]}
    assert by_name["The Odds API"]["features"] == ["odds", "live"]
    assert by_name["The Odds API"]["status"] == "UNKNOWN"


# ==========================================================================
# (6) checagem estrutural — o scanner nao toca mais a camada de transporte
# ==========================================================================


def test_value_strategy_has_no_header_reads_or_direct_provider_construction():
    src = (_REPO / "betgsn" / "value_strategy.py").read_text(encoding="utf-8")
    assert "x-requests" not in src
    assert "OddsApiProvider.from_env" not in src
    assert "live_odds_with_meta" not in src


def test_scan_live_consumes_quotes_not_raw_events():
    """AST: scan_live nao chama scan_events — consome quotes normalizadas."""
    tree = ast.parse(
        (_REPO / "betgsn" / "value_strategy.py").read_text(encoding="utf-8")
    )
    fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "scan_live"
    )
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "scan_events" not in names
    assert "odds_event_to_internal" not in names  # nada de payload cru
    assert "_opportunities_from_quotes" in names  # odds construidas das quotes


# ==========================================================================
# (7) contrato FASE B: quotes SEM raw_events — cobertura e dada pelas
#     quotes, nao pelos eventos (`raw_events` e opcional no contrato)
# ==========================================================================


class _QuotesOnlyFake:
    """Provider FALSO do contrato que devolve o fetch PRONTO.

    Diferente do `FakeContractProvider`, nao deriva quotes de eventos:
    permite simular um provider so-de-quotes (raw_events vazio) sem
    inventar payload.
    """

    def __init__(self, name, fetch: OddsProviderFetch):
        self.name = name
        self._fetch = fetch
        self.requests: list[OddsFetchRequest] = []

    def available(self) -> bool:
        return True

    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch:
        self.requests.append(request)
        return self._fetch


def _contract_quotes(provider_name):
    """Quotes validas (parser canonico + dedupe) para o fetch de um fake.

    O servico aplica `dedupe_quotes` as quotes do contrato; o esperado
    aqui passa pela mesma ordenacao canonica.
    """
    from betgsn.odds_normalize import dedupe_quotes

    return tuple(dedupe_quotes(
        normalize_events([_raw_event()], provider_name, FETCHED_AT)
    ))


def test_contract_quotes_without_raw_events_is_served():
    """Cobertura do contrato: quotes presentes, raw_events vazio.

    `raw_events` e OPCIONAL no contrato (odds_provider.py); um provider
    so-de-quotes nao pode ser classificado como NO_COVERAGE nem ter as
    quotes descartadas.
    """
    quotes = _contract_quotes("QOnly")
    provider = _QuotesOnlyFake("QOnly", OddsProviderFetch(quotes=quotes))
    svc = OddsService(providers=[("QOnly", provider)], now=Clock(FETCHED_AT))

    result = svc.fetch("soccer_epl")

    assert result.ok is True
    assert result.provider == "QOnly"
    assert result.quotes == list(quotes)
    assert result.events == 0  # sem raw_events: nada e inventado
    assert result.attempts[0].status == "OK"
    assert result.attempts[0].quotes == len(quotes)
    assert svc.health_snapshot()["QOnly"]["state"] == "HEALTHY"


def test_contract_no_coverage_without_quotes_or_events():
    """Sem quotes, sem eventos e no_coverage explicito: como hoje."""
    provider = _QuotesOnlyFake(
        "QOnly", OddsProviderFetch(quotes=(), raw_events=(), no_coverage=True)
    )
    svc = OddsService(providers=[("QOnly", provider)], now=Clock(FETCHED_AT))

    result = svc.fetch("soccer_epl")

    assert result.ok is False
    assert result.quotes == []
    assert result.attempts[0].status == "NO_COVERAGE"
    assert svc.health_snapshot()["QOnly"]["state"] == "NO_COVERAGE"


def test_contract_quotes_with_raw_events_parity():
    """Paridade: mesmas quotes COM raw_events — comportamento inalterado."""
    quotes = _contract_quotes("QBoth")
    provider = _QuotesOnlyFake(
        "QBoth",
        OddsProviderFetch(quotes=quotes, raw_events=(_raw_event(),)),
    )
    svc = OddsService(providers=[("QBoth", provider)], now=Clock(FETCHED_AT))

    result = svc.fetch("soccer_epl")

    assert result.ok is True
    assert result.provider == "QBoth"
    assert result.quotes == list(quotes)
    assert result.events == 1
    assert result.attempts[0].status == "OK"


def test_contract_neither_quotes_nor_events_nor_flag_is_no_coverage():
    """Nem quotes, nem eventos, nem flag: sem cobertura (nunca fabrica)."""
    provider = _QuotesOnlyFake("QOnly", OddsProviderFetch())
    svc = OddsService(providers=[("QOnly", provider)], now=Clock(FETCHED_AT))

    result = svc.fetch("soccer_epl")

    assert result.ok is False
    assert result.attempts[0].status == "NO_COVERAGE"
    assert svc.health_snapshot()["QOnly"]["state"] == "NO_COVERAGE"
