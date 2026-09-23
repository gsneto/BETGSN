"""OddsPapi: odds-by-tournaments exige EXATAMENTE UM bookmaker por request.

CAUSA-RAIZ (comprovada em producao e na doc oficial)
----------------------------------------------------
O adapter chamava `/v4/odds-by-tournaments` apenas com `tournamentIds`.
A API real respondeu 400:

    "Invalid number of bookmakers specified."
    "Please provide exactly one bookmaker using the 'bookmaker' query
     parameter."

Doc oficial (oddspapi.io/us/docs, consultada em 2026-09-23) confirma o
formato do exemplo canonico:

    GET /v4/odds-by-tournaments?bookmaker=pinnacle&tournamentIds=17,8

Ou seja: `bookmaker` (singular) com UM slug por request; multiplos
bookmakers exigem chamadas independentes, agregadas no adapter.

Tudo aqui e deterministico (sem rede): o router substitui `_get` e
registra cada chamada, provando o contrato da borda.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from betgsn.odds_provider import OddsFetchRequest, OddsProvider, OddsProviderFetch
from betgsn.providers import (
    FAILURE_AUTH,
    FAILURE_BAD_RESPONSE,
    OddsPapiProvider,
    ProviderError,
    classify_status,
)

FETCHED_AT = "2030-06-01T11:00:00Z"
KICKOFF = "2030-06-01T20:00:00Z"

MARKET_H2H = "Resultado Final (1X2)"
MARKET_TOTALS = "Total de Gols"

BOOKMAKERS_ENV = "BETGSN_ODDSPAPI_BOOKMAKERS"


# ------------------------------------------------------------------ mocks


def _fixture_row() -> dict:
    """Fixture no shape REAL de /v4/fixtures (nomes dos participantes)."""
    return {
        "fixtureId": "id100",
        "participant1Id": 35,
        "participant2Id": 34,
        "sportId": 10,
        "tournamentId": 17,
        "statusId": 0,
        "hasOdds": True,
        "startTime": KICKOFF,
        "updatedAt": "2030-06-01T10:55:00Z",
        "participant1Name": "Arsenal",
        "participant2Name": "Chelsea",
        "tournamentName": "Premier League",
    }


def _odds_fixture(slug: str, *, h2h: bool = True, totals: bool = False) -> dict:
    """Resposta de /v4/odds-by-tournaments?bookmaker=<slug> para UM fixture.

    Cada chamada da API real devolve `bookmakerOdds` SOMENTE do bookmaker
    pedido — e o que este router reproduz.
    """
    markets: dict = {}
    if h2h:
        markets["101"] = {
            "marketActive": True,
            "outcomes": {
                "101": {"players": {"0": {
                    "active": True, "bookmakerOutcomeId": "home",
                    "changedAt": "2030-06-01T10:59:11Z",
                    "price": 2.10, "mainLine": True,
                }}},
                "102": {"players": {"0": {
                    "active": True, "bookmakerOutcomeId": "draw",
                    "changedAt": "2030-06-01T10:59:12Z",
                    "price": 3.40, "mainLine": True,
                }}},
                "103": {"players": {"0": {
                    "active": True, "bookmakerOutcomeId": "away",
                    "changedAt": "2030-06-01T10:59:13Z",
                    "price": 3.20, "mainLine": True,
                }}},
            },
        }
    if totals:
        markets["1012"] = {
            "marketActive": True,
            "outcomes": {
                "1012": {"players": {"0": {
                    "active": True, "bookmakerOutcomeId": "3.5/over",
                    "changedAt": "2030-06-01T10:58:47Z",
                    "price": 2.71,
                }}},
                "1013": {"players": {"0": {
                    "active": True, "bookmakerOutcomeId": "3.5/under",
                    "changedAt": "2030-06-01T10:58:47Z",
                    "price": 1.497,
                }}},
            },
        }
    return {
        "fixtureId": "id100",
        "participant1Id": 35,
        "participant2Id": 34,
        "sportId": 10,
        "tournamentId": 17,
        "statusId": 0,
        "hasOdds": True,
        "startTime": KICKOFF,
        "updatedAt": "2030-06-01T10:55:00Z",
        "bookmakerOdds": {
            slug: {
                "bookmakerIsActive": True,
                "suspended": False,
                "markets": markets,
            },
        },
    }


class _Router:
    """Router deterministico por (path, bookmaker): registra cada chamada.

    `/odds-by-tournaments` responde APENAS o bloco do bookmaker pedido —
    igual a API real. `fail_on` mapeia slug -> ProviderError para simular
    falha de um bookmaker especifico.
    """

    def __init__(self, odds_by_slug: dict[str, list[dict]],
                 fail_on: dict[str, ProviderError] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self._odds_by_slug = odds_by_slug
        self._fail_on = fail_on or {}
        self._catalog = [
            {"bookmakerName": "Pinnacle", "slug": "pinnacle", "liveOdds": False},
            {"bookmakerName": "Betano", "slug": "betano", "liveOdds": True},
        ]

    def __call__(self, path: str, params: dict):
        self.calls.append((path, dict(params)))
        if path == "/tournaments":
            return [{
                "tournamentId": 17,
                "tournamentSlug": "premier-league",
                "tournamentName": "Premier League",
                "categorySlug": "england",
                "categoryName": "England",
            }], {}
        if path == "/bookmakers":
            return list(self._catalog), {}
        if path == "/account":
            return {"subscriptions": []}, {}
        if path == "/fixtures":
            return ([_fixture_row()]
                    if int(params.get("tournamentId", -1)) == 17 else []), {}
        if path == "/odds-by-tournaments":
            slug = str(params.get("bookmaker") or "")
            if slug in self._fail_on:
                raise self._fail_on[slug]
            if "," in slug:
                raise ProviderError(
                    "Invalid number of bookmakers specified.",
                    status=400, kind=FAILURE_BAD_RESPONSE,
                )
            return list(self._odds_by_slug.get(slug, [])), {}
        raise ProviderError(f"404 simulado em {path}", status=404,
                            kind=FAILURE_BAD_RESPONSE)


def _request(divisions=("E0",)):
    return OddsFetchRequest(
        divisions=divisions,
        markets=(MARKET_H2H, MARKET_TOTALS),
        fetched_at=FETCHED_AT,
    )


def _provider(monkeypatch, router: _Router) -> OddsPapiProvider:
    provider = OddsPapiProvider(api_key="k")
    monkeypatch.setattr(provider, "_get", router)
    return provider


def _odds_calls(router: _Router) -> list[dict]:
    return [params for path, params in router.calls
            if path == "/odds-by-tournaments"]


# ==========================================================================
# 1-2. Exatamente UM bookmaker por chamada
# ==========================================================================


def test_every_odds_call_has_exactly_one_bookmaker(monkeypatch):
    """Cada chamada a /odds-by-tournaments leva `bookmaker` com UM slug.

    E o contrato da API real (erro 400 + doc oficial): sem o parametro ou
    com lista, a chamada e invalida.
    """
    monkeypatch.setenv(BOOKMAKERS_ENV, "pinnacle,betano")
    router = _Router({
        "pinnacle": [_odds_fixture("pinnacle")],
        "betano": [_odds_fixture("betano")],
    })
    provider = _provider(monkeypatch, router)

    result = provider.fetch_odds(_request())
    assert result.quotes

    calls = _odds_calls(router)
    assert len(calls) == 2  # um bookmaker por chamada, nenhum a mais
    for params in calls:
        slug = params["bookmaker"]
        assert isinstance(slug, str)
        assert "," not in slug
        assert slug in {"pinnacle", "betano"}
        # tournamentIds continua lista por chamada (multiplos permitidos)
        assert params["tournamentIds"] == "17"


def test_default_bookmaker_is_pinnacle_single_call(monkeypatch):
    """Sem env: default e o slug do exemplo oficial, UMA chamada."""
    monkeypatch.delenv(BOOKMAKERS_ENV, raising=False)
    router = _Router({"pinnacle": [_odds_fixture("pinnacle", totals=True)]})
    provider = _provider(monkeypatch, router)

    result = provider.fetch_odds(_request())
    calls = _odds_calls(router)
    assert len(calls) == 1
    assert calls[0]["bookmaker"] == "pinnacle"
    assert result.quotes


# ==========================================================================
# 3-4. Multiplos bookmakers: chamadas validas + agregacao no adapter
# ==========================================================================


def test_multiple_bookmakers_aggregate_quotes(monkeypatch):
    """betano (h2h) + pinnacle (h2h com preco proprio): quotes das DUAS
    casas chegam ao contrato, sem duplicar a linha de nenhuma."""
    monkeypatch.setenv(BOOKMAKERS_ENV, "pinnacle,betano")
    router = _Router({
        "pinnacle": [_odds_fixture("pinnacle", totals=True)],
        "betano": [_odds_fixture("betano")],
    })
    provider = _provider(monkeypatch, router)

    result = provider.fetch_odds(_request())
    assert result.errors == ()
    quotes = list(result.quotes)

    # bookmaker preservado com o NOME REAL do catalogo (slug -> nome)
    books = {(q.bookmaker, q.market) for q in quotes}
    assert ("Pinnacle", MARKET_TOTALS) in books
    assert ("Betano", MARKET_H2H) in books

    # agregacao sem duplicacao: uma linha por (casa, mercado, resultado)
    lines = [(q.bookmaker, q.market, q.selection) for q in quotes]
    assert len(lines) == len(set(lines))

    h2h_betano = {q.selection: q for q in quotes
                  if q.bookmaker == "Betano" and q.market == MARKET_H2H}
    assert h2h_betano["1"].price == pytest.approx(2.10)
    assert h2h_betano["X"].price == pytest.approx(3.40)
    assert h2h_betano["2"].price == pytest.approx(3.20)


def test_bookmaker_failure_does_not_kill_others(monkeypatch):
    """Um bookmaker falha (erro explicito registrado), os demais seguem —
    falha de casa nao vira falha do provider, nem sucesso falso."""
    monkeypatch.setenv(BOOKMAKERS_ENV, "pinnacle,betano")
    router = _Router(
        {"pinnacle": [_odds_fixture("pinnacle")], "betano": []},
        fail_on={"betano": ProviderError(
            "HTTP 500 simulado", status=500, kind="SERVER", retryable=True)},
    )
    provider = _provider(monkeypatch, router)

    result = provider.fetch_odds(_request())
    # quotes do bookmaker saudavel chegam
    assert any(q.bookmaker == "Pinnacle" for q in result.quotes)
    # e a falha do outro NAO e silenciosa
    assert any("betano" in e for e in result.errors)


def test_all_bookmakers_failing_raises_last_error(monkeypatch):
    """Todos os bookmakers falharam: falha alta (como antes do fix), nao
    um resultado vazio que pareca 'sem cobertura'."""
    from betgsn.providers import FAILURE_SERVER

    monkeypatch.setenv(BOOKMAKERS_ENV, "pinnacle")
    router = _Router(
        {},
        fail_on={"pinnacle": ProviderError(
            "HTTP 500 simulado", status=500, kind=FAILURE_SERVER,
            retryable=True)},
    )
    provider = _provider(monkeypatch, router)

    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_odds(_request())
    assert exc_info.value.kind == FAILURE_SERVER


# ==========================================================================
# 5-6. Bookmaker e timestamp reais preservados no NormalizedQuote
# ==========================================================================


def test_quotes_carry_catalog_bookmaker_and_real_timestamp(monkeypatch):
    monkeypatch.setenv(BOOKMAKERS_ENV, "betano")
    router = _Router({"betano": [_odds_fixture("betano")]})
    provider = _provider(monkeypatch, router)

    result = provider.fetch_odds(_request())
    quotes = list(result.quotes)
    assert quotes

    by_selection = {q.selection: q for q in quotes if q.market == MARKET_H2H}
    # bookmaker: NOME do catalogo /v4/bookmakers (slug -> bookmakerName)
    assert all(q.bookmaker == "Betano" for q in quotes)
    # timestamp: changedAt REAL da fonte — nunca o fetched_at
    assert by_selection["1"].timestamp == "2030-06-01T10:59:11Z"
    assert by_selection["X"].timestamp == "2030-06-01T10:59:12Z"
    assert by_selection["2"].timestamp == "2030-06-01T10:59:13Z"
    assert all(q.timestamp != FETCHED_AT for q in quotes)
    # identidade do contrato comum
    assert all(q.provider == "OddsPapi" for q in quotes)
    assert all(q.home_team == "Arsenal" and q.away_team == "Chelsea"
               for q in quotes)
    assert all(q.kickoff == KICKOFF for q in quotes)
    assert all(q.sport_key == "E0" for q in quotes)


def test_unknown_bookmaker_slug_is_reported_never_invented(monkeypatch):
    """Slug configurado fora do catalogo: erro explicito, nenhuma chamada
    com bookmaker inventado; casa inexistente nao vira quote."""
    monkeypatch.setenv(BOOKMAKERS_ENV, "nao-existe")
    router = _Router({"pinnacle": [_odds_fixture("pinnacle")]})
    provider = _provider(monkeypatch, router)

    result = provider.fetch_odds(_request())
    assert result.quotes == ()
    assert result.no_coverage is True
    assert any("nao-existe" in e for e in result.errors)
    assert _odds_calls(router) == []


# ==========================================================================
# 7-8. Classificacao de erros
# ==========================================================================


def test_http_400_is_bad_response_degraded_not_unavailable():
    """400 por parametro invalido: BAD_RESPONSE -> DEGRADED no tracker."""
    from betgsn.odds_health import HealthTracker, ProviderState

    kind, retryable = classify_status(400)
    assert kind == FAILURE_BAD_RESPONSE
    assert retryable is False

    tracker = HealthTracker(now=lambda: "2030-06-01T11:00:00Z")
    tracker.record_failure("OddsPapi", kind, "Invalid number of bookmakers",
                           status=400)
    assert tracker.state("OddsPapi") == ProviderState.DEGRADED


def test_http_401_remains_unavailable():
    """401: AUTH (falha dura) -> UNAVAILABLE, como sempre foi."""
    from betgsn.odds_health import HealthTracker, ProviderState

    kind, _retryable = classify_status(401)
    assert kind == FAILURE_AUTH

    tracker = HealthTracker(now=lambda: "2030-06-01T11:00:00Z")
    tracker.record_failure("OddsPapi", kind, "chave invalida", status=401)
    assert tracker.state("OddsPapi") == ProviderState.UNAVAILABLE


def test_zero_coverage_is_explicit_not_success(monkeypatch):
    """Tournament sem odds: no_coverage True com quotes vazias — nunca
    'sucesso vazio' nem cobertura fabricada."""
    monkeypatch.setenv(BOOKMAKERS_ENV, "pinnacle")
    router = _Router({"pinnacle": []})  # resposta: lista vazia
    provider = _provider(monkeypatch, router)

    result = provider.fetch_odds(_request())
    assert result.quotes == ()
    assert result.no_coverage is True


# ==========================================================================
# 10. Compatibilidade com o contrato OddsProvider
# ==========================================================================


def test_oddspapi_still_satisfies_odds_provider_protocol(monkeypatch):
    provider = OddsPapiProvider(api_key="k")
    assert isinstance(provider, OddsProvider)
    assert provider.name == "OddsPapi"
    assert provider.available() is True
    # assinatura do contrato preservada
    assert callable(provider.fetch_odds)
    assert callable(provider.divisions_for)
    assert callable(provider.estimated_cost)


def test_estimated_cost_counts_one_call_per_bookmaker(monkeypatch):
    """Custo estimado: chamadas = 2 (catalogos) + tournaments + bookmakers."""
    provider = OddsPapiProvider(api_key="k")

    monkeypatch.delenv(BOOKMAKERS_ENV, raising=False)
    assert provider.estimated_cost(_request()) == 4  # 2 + 1 liga + 1 casa

    monkeypatch.setenv(BOOKMAKERS_ENV, "pinnacle,betano")
    assert provider.estimated_cost(_request()) == 5  # 2 + 1 liga + 2 casas

    assert provider.estimated_cost(_request(divisions=("XX9",))) == 0


# ==========================================================================
# 11-12. Isolamento: nenhum conhecimento OddsPapi vaza para o core
# ==========================================================================


def test_no_oddspapi_logic_leaks_into_generic_layers():
    """O fix e todo da borda: modulos do core do odds layer continuam
    sem qualquer mencao a OddsPapi. (A API cita providers por NOME na
    listagem historica de features `_PROVIDER_FEATURES` — e catalogo de
    capacidades pre-existente, nao logica de adapter.)
    Se o core precisar ser tocado por causa deste provider, o
    isolamento arquitetural foi quebrado."""
    repo = Path(__file__).resolve().parents[1]
    for rel in (
        "betgsn/odds_normalize.py",
        "betgsn/odds_service.py",
        "betgsn/odds_snapshots.py",
        "betgsn/real_signals.py",
        "betgsn/signals.py",
    ):
        source = (repo / rel).read_text(encoding="utf-8")
        assert "oddspapi" not in source.lower(), (
            f"{rel} contem logica especifica de OddsPapi"
        )


def test_the_odds_api_and_parlay_signature_unchanged():
    """Os adapters que ja funcionam nao foram tocados: as assinaturas
    publicas seguem exatamente as originais (regressao estrutural)."""
    from betgsn import providers as mod

    # fetch_odds(self, request) — um unico argumento posicional alem do self
    for cls_name in ("OddsApiProvider", "ParlayApiProvider"):
        cls = getattr(mod, cls_name)
        params = list(inspect.signature(cls.fetch_odds).parameters)
        assert params == ["self", "request"], (
            f"{cls_name}.fetch_odds mudou de assinatura: {params}"
        )
