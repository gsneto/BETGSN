"""FASE B extensao — adapters OddsPapi, Odds-API.io e OpticOdds.

O que esta suite prova (ADICAO, nao substituicao):

  1. os TRES adapters reais satisfazem a suíte de conformance do
     contrato (`run_contract_conformance`) com quotes nao-vazias;
  2. timestamps REAIS da fonte (changedAt/updatedAt/epoch) viram o
     timestamp da quote — fetched_at NUNCA substitui horario proprio;
  3. divisao sem catalogo e no_coverage EXPLICITO (sem fuzzy, sem erro);
  4. mercado sem mapeamento verificado e descartado, nunca renomeado;
  5. bookmaker e o RETORNADO pela API (nome real do catalogo), nunca
     lista fixa — Betano BR so aparece se a resposta real trazer;
  6. creditos: ausentes sao None (nunca zero); OddsPapi aplica a quota
     REAL de /v4/account quando a conta informa;
  7. falha HTTP sobe alta (ProviderError) com kind/status preservados;
  8. OddsService.fetch_aggregated agrega TODOS os providers (nenhuma
     quote perdida) e registra colisoes multi-provider explicitamente;
  9. sem chave, `from_env` devolve None: provider registrado, porem nao
     configurado — e nada quebra.

Os payloads sao INVENTADOS no shape REAL documentado por cada provider
(consultado em 2026-09-23): nenhuma rede.
"""
from __future__ import annotations

import pytest

from betgsn.odds_provider import (
    CreditUpdate,
    OddsFetchRequest,
)
from betgsn.odds_service import OddsService
from betgsn.providers import (
    FAILURE_AUTH,
    OPTICODDS_DEFAULT_SPORTSBOOKS,
    OddsApiIoProvider,
    OddsPapiProvider,
    OpticOddsProvider,
    ProviderError,
)
from betgsn.odds_normalize import merge_provider_quotes
from test_odds_provider_contract import run_contract_conformance

#: Instante INVENTADO, injetado pelo chamador (o contrato nao consulta relogio).
FETCHED_AT = "2030-06-01T12:00:00Z"
#: Kickoff INVENTADO, posterior a todos os timestamps observados.
KICKOFF = "2030-06-05T18:00:00Z"

MARKET_H2H = "Resultado Final (1X2)"
MARKET_TOTALS = "Total de Gols"
MARKET_BTTS = "Ambas Marcam"


class _Clock:
    def __init__(self, value: str):
        self.value = value

    def __call__(self) -> str:
        return self.value


# ==========================================================================
# Mocks de HTTP: roteador deterministico por path (sem rede)
# ==========================================================================


class _Router:
    """Troca o `_get` do adapter por respostas fixas por path.

    Cada provider novo tem UM ponto de entrada HTTP (`_get(path, params)`)
    — o router devolve (body, headers) do dicionario. Erro por path
    ausente ou entry `ProviderError` levanta igual ao HTTP real.
    """

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, path: str, params: dict):
        self.calls.append((path, dict(params)))
        route = self.routes.get(path)
        if isinstance(route, ProviderError):
            raise route
        if route is None:
            raise ProviderError(
                f"404 simulado em {path}", status=404, kind="BAD_RESPONSE"
            )
        return route, {}


def _patch_http(monkeypatch, provider, routes: dict) -> _Router:
    router = _Router(routes)
    monkeypatch.setattr(provider, "_get", router)
    return router


# ==========================================================================
# Fixtures no shape REAL documentado de cada API
# ==========================================================================


def _oddspapi_routes() -> dict:
    """Rotas no schema oficial v4 (oddspapi.io/us/docs, 2026-09-23)."""
    return {
        "/tournaments": [
            {
                "tournamentId": 17,
                "tournamentSlug": "premier-league",
                "tournamentName": "Premier League",
                "categorySlug": "england",
                "categoryName": "England",
            },
            {
                "tournamentId": 999,
                "tournamentSlug": "serie-a",
                "tournamentName": "Serie A",
                "categorySlug": "brazil",
                "categoryName": "Brazil",
            },
        ],
        "/bookmakers": [
            {"bookmakerName": "Pinnacle", "slug": "pinnacle", "liveOdds": False},
            {"bookmakerName": "Betano", "slug": "betano", "liveOdds": True},
        ],
        "/account": {
            "subscriptions": [
                {
                    "is_active": True,
                    "request_limit": 500,
                    "request_count": 100,
                }
            ]
        },
        "/odds-by-tournaments": [
            {
                "fixtureId": "id100",
                "participant1Id": 35,
                "participant2Id": 34,
                "sportId": 10,
                "tournamentId": 17,
                "statusId": 0,
                "hasOdds": True,
                "startTime": KICKOFF,
                "updatedAt": "2030-06-01T11:00:00Z",
                "bookmakerOdds": {
                    "betano": {
                        "bookmakerIsActive": True,
                        "suspended": False,
                        "markets": {
                            "101": {
                                "marketActive": True,
                                "outcomes": {
                                    "101": {
                                        "players": {
                                            "0": {
                                                "active": True,
                                                "bookmakerOutcomeId": "home",
                                                "changedAt": "2030-06-01T10:59:11Z",
                                                "price": 2.10,
                                                "mainLine": True,
                                            }
                                        }
                                    },
                                    "102": {
                                        "players": {
                                            "0": {
                                                "active": True,
                                                "bookmakerOutcomeId": "draw",
                                                "changedAt": "2030-06-01T10:59:12Z",
                                                "price": 3.40,
                                                "mainLine": True,
                                            }
                                        }
                                    },
                                    "103": {
                                        "players": {
                                            "0": {
                                                "active": True,
                                                "bookmakerOutcomeId": "away",
                                                "changedAt": "2030-06-01T10:59:13Z",
                                                "price": 3.20,
                                                "mainLine": True,
                                            }
                                        }
                                    },
                                },
                            },
                            # 999: mercado SEM mapeamento verificado — descartado
                            "999": {
                                "marketActive": True,
                                "outcomes": {
                                    "9001": {
                                        "players": {
                                            "0": {
                                                "active": True,
                                                "bookmakerOutcomeId": "dupla",
                                                "changedAt": "2030-06-01T10:59:00Z",
                                                "price": 1.50,
                                            }
                                        }
                                    }
                                },
                            },
                        },
                    },
                    "pinnacle": {
                        "bookmakerIsActive": True,
                        "suspended": False,
                        "markets": {
                            # totais: padrao "<linha>/over|under" (doc oficial)
                            "1012": {
                                "marketActive": True,
                                "outcomes": {
                                    "1012": {
                                        "players": {
                                            "0": {
                                                "active": True,
                                                "bookmakerOutcomeId": "3.5/over",
                                                "changedAt": "2030-06-01T10:58:47Z",
                                                "price": 2.71,
                                            }
                                        }
                                    },
                                    "1013": {
                                        "players": {
                                            "0": {
                                                "active": True,
                                                "bookmakerOutcomeId": "3.5/under",
                                                "changedAt": "2030-06-01T10:58:47Z",
                                                "price": 1.497,
                                            }
                                        }
                                    },
                                },
                            },
                        },
                    },
                },
            }
        ],
        # /fixtures responde por tournamentId: o router ignora params,
        # entao devolvemos a lista so quando o tournament bate — usamos
        # um router com funcao no teste quando precisamos disso.
    }


def _oddspapi_fixtures(tournament_id: int) -> list[dict]:
    if tournament_id != 17:
        return []
    return [
        {
            "fixtureId": "id100",
            "participant1Id": 35,
            "participant2Id": 34,
            "sportId": 10,
            "tournamentId": 17,
            "statusId": 0,
            "hasOdds": True,
            "startTime": KICKOFF,
            "updatedAt": "2030-06-01T11:00:00Z",
            "participant1Name": "Arsenal",
            "participant2Name": "Chelsea",
            "sportName": "Soccer",
            "tournamentSlug": "premier-league",
            "categorySlug": "england",
            "categoryName": "England",
            "tournamentName": "Premier League",
        }
    ]


class _OddsPapiRouter(_Router):
    """Router OddsPapi com /fixtures por tournamentId e odds por bookmaker.

    O endpoint real exige EXATAMENTE UM bookmaker por request e devolve
    apenas o bloco daquela casa — o router reproduz o contrato.
    """

    def __init__(self, routes: dict):
        super().__init__(routes)
        self._bookmakers_env: str | None = None

    def __call__(self, path: str, params: dict):
        self.calls.append((path, dict(params)))
        if path == "/fixtures":
            return _oddspapi_fixtures(int(params.get("tournamentId", -1))), {}
        if path == "/odds-by-tournaments":
            slug = str(params.get("bookmaker") or "")
            if "," in slug:
                raise ProviderError(
                    "Invalid number of bookmakers specified.",
                    status=400, kind=FAILURE_BAD_RESPONSE,
                )
            fixtures = [
                {
                    **fx,
                    "bookmakerOdds": {
                        s: block for s, block in fx.get("bookmakerOdds", {}).items()
                        if s == slug
                    },
                }
                for fx in self.routes.get("/odds-by-tournaments", [])
            ]
            return fixtures, {}
        route = self.routes.get(path)
        if isinstance(route, ProviderError):
            raise route
        return route, {}


def _odds_api_io_routes() -> dict:
    """Rotas no schema oficial v3 (docs.odds-api.io, 2026-09-23)."""
    return {
        "/leagues": [
            {
                "name": "England - Premier League",
                "slug": "england-premier-league",
                "eventsCount": 1,
            }
        ],
        "/bookmakers/selected": ["Betano", "Unibet"],
        "/events": [
            {
                "id": 123456,
                "home": "Arsenal",
                "away": "Chelsea",
                "date": KICKOFF,
                "status": "pending",
                "league": {"name": "England - Premier League", "slug": "england-premier-league"},
                "sport": {"name": "Football", "slug": "football"},
            }
        ],
        "/odds/multi": [
            {
                "id": 123456,
                "home": "Arsenal",
                "away": "Chelsea",
                "date": KICKOFF,
                "status": "pending",
                "league": {"name": "England - Premier League", "slug": "england-premier-league"},
                "sport": {"name": "Football", "slug": "football"},
                "bookmakers": {
                    "Betano": [
                        {
                            "name": "ML",
                            "odds": [
                                {"home": "2.10", "draw": "3.40", "away": "3.20"}
                            ],
                            "updatedAt": "2030-06-01T10:30:00Z",
                        },
                        {
                            "name": "Totals",
                            "odds": [
                                {"max": 2.5, "over": "1.90", "under": "1.90"}
                            ],
                            "updatedAt": "2030-06-01T10:31:00Z",
                        },
                        # mercado SEM mapeamento — descartado
                        {
                            "name": "Correct Score",
                            "odds": [{"home": "2-1", "away": "8.50"}],
                            "updatedAt": "2030-06-01T10:32:00Z",
                        },
                    ]
                },
            }
        ],
    }


def _opticodds_routes() -> dict:
    """Rotas no schema oficial v3 (developer.opticodds.com, 2026-09-23)."""
    return {
        "/leagues": {
            "data": [
                {"id": "england_-_premier_league", "name": "England - Premier League"}
            ]
        },
        "/fixtures/active": {
            "data": [
                {
                    "id": "2025020987944BC9",
                    "start_date": KICKOFF,
                    "home_team_display": "Arsenal",
                    "away_team_display": "Chelsea",
                    "status": "unplayed",
                    "is_live": False,
                    "sport": {"id": "soccer", "name": "Soccer"},
                    "league": {"id": "england_-_premier_league", "name": "England - Premier League"},
                    "has_odds": True,
                }
            ],
            "page": 1,
            "has_more": False,
        },
        "/fixtures/odds": {
            "data": [
                {
                    "id": "2025020987944BC9",
                    "start_date": KICKOFF,
                    "home_team_display": "Arsenal",
                    "away_team_display": "Chelsea",
                    "status": "unplayed",
                    "is_live": False,
                    "sport": {"id": "soccer", "name": "Soccer"},
                    "league": {"id": "england_-_premier_league", "name": "England - Premier League"},
                    "odds": [
                        {
                            "id": "fix:Betano:moneyline:arsenal",
                            "sportsbook": "Betano",
                            "market": "Moneyline",
                            "market_id": "moneyline",
                            "name": "Arsenal",
                            "selection": "Arsenal",
                            "is_main": True,
                            "price": 2.10,
                            "timestamp": 1900000000.0,
                            "points": None,
                            "selection_line": None,
                        },
                        {
                            "id": "fix:Betano:moneyline:draw",
                            "sportsbook": "Betano",
                            "market": "Moneyline",
                            "market_id": "moneyline",
                            "name": "Draw",
                            "selection": "Draw",
                            "is_main": True,
                            "price": 3.40,
                            "timestamp": 1900000001.0,
                            "points": None,
                            "selection_line": None,
                        },
                        {
                            "id": "fix:Betano:total_goals:over_2_5",
                            "sportsbook": "Betano",
                            "market": "Total Goals",
                            "market_id": "total_goals",
                            "name": "Over 2.5",
                            "selection": "",
                            "is_main": True,
                            "price": 1.90,
                            "timestamp": 1900000002.0,
                            "points": 2.5,
                            "selection_line": "over",
                        },
                        {
                            "id": "fix:Betano:total_goals:under_2_5",
                            "sportsbook": "Betano",
                            "market": "Total Goals",
                            "market_id": "total_goals",
                            "name": "Under 2.5",
                            "selection": "",
                            "is_main": True,
                            "price": 1.90,
                            "timestamp": 1900000002.0,
                            "points": 2.5,
                            "selection_line": "under",
                        },
                        # market_id SEM mapeamento — descartado
                        {
                            "id": "fix:Betano:corners:over_9_5",
                            "sportsbook": "Betano",
                            "market": "Corners",
                            "market_id": "corners",
                            "name": "Over 9.5",
                            "selection": "",
                            "is_main": True,
                            "price": 1.85,
                            "timestamp": 1900000002.0,
                            "points": 9.5,
                            "selection_line": "over",
                        },
                    ],
                }
            ]
        },
    }


#: Epoch 1900000000/1/2 -> UTC canonico (verificado com datetime, nao adivinhado).
_EPOCH_0 = "2030-03-17T17:46:40Z"
_EPOCH_1 = "2030-03-17T17:46:41Z"
_EPOCH_2 = "2030-03-17T17:46:42Z"


def _request(divisions=("E0",), markets=(MARKET_H2H, MARKET_TOTALS, MARKET_BTTS)):
    return OddsFetchRequest(
        divisions=divisions, markets=markets, fetched_at=FETCHED_AT
    )


# ==========================================================================
# 1. Conformance do contrato contra os TRES adapters reais
# ==========================================================================


def test_oddspapi_satisfies_contract_conformance(monkeypatch):
    provider = OddsPapiProvider(api_key="k")
    router = _OddsPapiRouter(_oddspapi_routes())
    monkeypatch.setattr(provider, "_get", router)
    result = run_contract_conformance(
        provider,
        expect_name="OddsPapi",
        allowed_timestamps=(
            "2030-06-01T10:59:11Z",
            "2030-06-01T10:59:12Z",
            "2030-06-01T10:59:13Z",
            "2030-06-01T10:58:47Z",
        ),
    )
    assert result.quotes, "conformance nao pode ser vacuo"
    assert result.snapshot_provider == "oddspapi-live"


def test_odds_api_io_satisfies_contract_conformance(monkeypatch):
    provider = OddsApiIoProvider(api_key="k")
    _patch_http(monkeypatch, provider, _odds_api_io_routes())
    result = run_contract_conformance(
        provider,
        expect_name="Odds-API.io",
        allowed_timestamps=("2030-06-01T10:30:00Z", "2030-06-01T10:31:00Z"),
    )
    assert result.quotes, "conformance nao pode ser vacuo"
    assert result.snapshot_provider == "odds-api-io-live"


def test_opticodds_satisfies_contract_conformance(monkeypatch):
    provider = OpticOddsProvider(api_key="k")
    _patch_http(monkeypatch, provider, _opticodds_routes())
    result = run_contract_conformance(
        provider,
        expect_name="OpticOdds",
        allowed_timestamps=(_EPOCH_0, _EPOCH_1, _EPOCH_2),
    )
    assert result.quotes, "conformance nao pode ser vacuo"
    assert result.snapshot_provider == "opticodds-live"


# ==========================================================================
# 2. OddsPapi: quotes, timestamp real, bookmaker do catalogo, quota
# ==========================================================================


def test_oddspapi_fetch_odds_quotes_and_real_timestamps(monkeypatch):
    provider = OddsPapiProvider(api_key="k")
    # multi-bookmaker: as duas casas do catalogo, uma chamada por casa
    monkeypatch.setenv("BETGSN_ODDSPAPI_BOOKMAKERS", "betano,pinnacle")
    router = _OddsPapiRouter(_oddspapi_routes())
    monkeypatch.setattr(provider, "_get", router)

    result = provider.fetch_odds(_request())

    assert result.no_coverage is False
    assert result.errors == ()
    quotes = list(result.quotes)
    assert quotes
    assert all(q.provider == "OddsPapi" for q in quotes)
    assert all(q.kickoff == KICKOFF for q in quotes)
    assert all(q.home_team == "Arsenal" and q.away_team == "Chelsea" for q in quotes)
    # bookmaker com o NOME REAL do catalogo /v4/bookmakers (slug -> nome)
    by_market = {}
    for q in quotes:
        by_market.setdefault(q.market, {})[(q.bookmaker, q.selection)] = q
    h2h = by_market[MARKET_H2H]
    assert ("Betano", "1") in h2h and h2h[("Betano", "1")].price == pytest.approx(2.10)
    assert ("Betano", "X") in h2h and h2h[("Betano", "X")].price == pytest.approx(3.40)
    assert ("Betano", "2") in h2h and h2h[("Betano", "2")].price == pytest.approx(3.20)
    totals = by_market[MARKET_TOTALS]
    assert ("Pinnacle", "Over 3.5") in totals
    assert totals[("Pinnacle", "Over 3.5")].line == pytest.approx(3.5)
    assert totals[("Pinnacle", "Over 3.5")].price == pytest.approx(2.71)
    assert ("Pinnacle", "Under 3.5") in totals
    # mercado 999 (sem mapeamento verificado) nao vira quote
    assert {q.market for q in quotes} == {MARKET_H2H, MARKET_TOTALS}

    # TIMESTAMP REAL: changedAt da fonte, NUNCA o fetched_at
    assert h2h[("Betano", "1")].timestamp == "2030-06-01T10:59:11Z"
    assert h2h[("Betano", "X")].timestamp == "2030-06-01T10:59:12Z"
    assert totals[("Pinnacle", "Over 3.5")].timestamp == "2030-06-01T10:58:47Z"
    assert all(q.timestamp != FETCHED_AT for q in quotes)
    assert all(q.pre_kickoff for q in quotes)

    # quota REAL de /v4/account (request_limit - request_count)
    assert result.credits == CreditUpdate(used=100, remaining=400)


def test_oddspapi_unmapped_division_is_no_coverage(monkeypatch):
    provider = OddsPapiProvider(api_key="k")
    router = _Router({})
    monkeypatch.setattr(provider, "_get", router)

    result = provider.fetch_odds(_request(divisions=("XX9",)))
    assert result.no_coverage is True
    assert result.quotes == ()
    assert router.calls == []  # sem divisao mapeada, nenhuma chamada


def test_oddspapi_division_not_in_catalog_is_no_coverage_with_error(monkeypatch):
    provider = OddsPapiProvider(api_key="k")
    routes = _oddspapi_routes()
    # catalogo sem a liga pedida: E1 (championship) nao existe
    router = _OddsPapiRouter(routes)
    monkeypatch.setattr(provider, "_get", router)

    result = provider.fetch_odds(_request(divisions=("E1",)))
    assert result.no_coverage is True
    assert result.quotes == ()
    assert result.errors and "E1" in result.errors[0]


def test_oddspapi_http_error_propagates(monkeypatch):
    provider = OddsPapiProvider(api_key="k")
    routes = _oddspapi_routes()
    routes["/tournaments"] = ProviderError(
        "HTTP 401 simulado", status=401, kind=FAILURE_AUTH
    )
    monkeypatch.setattr(provider, "_get", _Router(routes))

    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_odds(_request())
    assert exc_info.value.kind == FAILURE_AUTH


def test_oddspapi_estimated_cost_and_divisions_for():
    provider = OddsPapiProvider(api_key="k")
    assert provider.available() is True
    assert provider.divisions_for("soccer_epl") == ("E0",)
    assert provider.divisions_for("sport_desconhecido") == ()
    assert provider.estimated_cost(_request(divisions=("E0",))) == 4
    assert provider.estimated_cost(_request(divisions=("XX9",))) == 0


# ==========================================================================
# 3. Odds-API.io: quotes, updatedAt real, casas da conta
# ==========================================================================


def test_odds_api_io_fetch_odds_quotes_and_real_timestamps(monkeypatch):
    provider = OddsApiIoProvider(api_key="k")
    router = _patch_http(monkeypatch, provider, _odds_api_io_routes())

    result = provider.fetch_odds(_request())

    assert result.no_coverage is False
    quotes = list(result.quotes)
    assert quotes
    assert all(q.provider == "Odds-API.io" for q in quotes)
    assert all(q.kickoff == KICKOFF for q in quotes)
    by_market = {}
    for q in quotes:
        by_market.setdefault(q.market, {})[(q.bookmaker, q.selection)] = q
    h2h = by_market[MARKET_H2H]
    assert ("Betano", "1") in h2h and h2h[("Betano", "1")].price == pytest.approx(2.10)
    assert ("Betano", "X") in h2h
    assert ("Betano", "2") in h2h
    totals = by_market[MARKET_TOTALS]
    assert totals[("Betano", "Over 2.5")].line == pytest.approx(2.5)
    assert totals[("Betano", "Over 2.5")].price == pytest.approx(1.90)
    assert ("Betano", "Under 2.5") in totals
    # Correct Score (sem mapeamento) nao vira quote
    assert {q.market for q in quotes} == {MARKET_H2H, MARKET_TOTALS}

    # TIMESTAMP REAL por bloco de mercado: updatedAt da fonte
    assert h2h[("Betano", "1")].timestamp == "2030-06-01T10:30:00Z"
    assert totals[("Betano", "Over 2.5")].timestamp == "2030-06-01T10:31:00Z"
    assert all(q.timestamp != FETCHED_AT for q in quotes)
    assert all(q.pre_kickoff for q in quotes)

    # quota NAO vem na resposta: None, nunca zero
    assert result.credits is None

    # casas da CONTA: bookmakers=selected nao veio do override
    odds_call = [c for c in router.calls if c[0] == "/odds/multi"][0]
    assert odds_call[1]["bookmakers"] == "Betano,Unibet"


def test_odds_api_io_unmapped_division_is_no_coverage(monkeypatch):
    provider = OddsApiIoProvider(api_key="k")
    router = _Router(_odds_api_io_routes())
    monkeypatch.setattr(provider, "_get", router)

    result = provider.fetch_odds(_request(divisions=("XX9",)))
    assert result.no_coverage is True
    assert router.calls == []


def test_odds_api_io_league_missing_from_catalog_is_no_coverage(monkeypatch):
    provider = OddsApiIoProvider(api_key="k")
    monkeypatch.setattr(provider, "_get", _Router(_odds_api_io_routes()))
    # E1 (Championship) nao esta no catalogo simulado
    result = provider.fetch_odds(_request(divisions=("E1",)))
    assert result.no_coverage is True
    assert result.errors and "E1" in result.errors[0]


def test_odds_api_io_no_selected_bookmakers_is_no_coverage(monkeypatch):
    provider = OddsApiIoProvider(api_key="k")
    routes = _odds_api_io_routes()
    routes["/bookmakers/selected"] = []
    monkeypatch.setattr(provider, "_get", _Router(routes))
    result = provider.fetch_odds(_request())
    assert result.no_coverage is True
    assert result.errors


def test_odds_api_io_http_error_propagates(monkeypatch):
    provider = OddsApiIoProvider(api_key="k")
    routes = _odds_api_io_routes()
    routes["/leagues"] = ProviderError("HTTP 401", status=401, kind=FAILURE_AUTH)
    monkeypatch.setattr(provider, "_get", _Router(routes))
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_odds(_request())
    assert exc_info.value.kind == FAILURE_AUTH


def test_odds_api_io_estimated_cost_and_divisions_for():
    provider = OddsApiIoProvider(api_key="k")
    assert provider.available() is True
    assert provider.divisions_for("soccer_epl") == ("E0",)
    assert provider.estimated_cost(_request(divisions=("E0",))) == 4
    assert provider.estimated_cost(_request(divisions=("XX9",))) == 0


# ==========================================================================
# 4. OpticOdds: quotes, epoch -> UTC real, DECIMAL, cesta de request
# ==========================================================================


def test_opticodds_fetch_odds_quotes_and_real_timestamps(monkeypatch):
    provider = OpticOddsProvider(api_key="k")
    router = _patch_http(monkeypatch, provider, _opticodds_routes())

    result = provider.fetch_odds(_request())

    assert result.no_coverage is False
    quotes = list(result.quotes)
    assert quotes
    assert all(q.provider == "OpticOdds" for q in quotes)
    assert all(q.kickoff == KICKOFF for q in quotes)
    by_market = {}
    for q in quotes:
        by_market.setdefault(q.market, {})[(q.bookmaker, q.selection)] = q
    h2h = by_market[MARKET_H2H]
    assert h2h[("Betano", "1")].price == pytest.approx(2.10)
    assert h2h[("Betano", "X")].price == pytest.approx(3.40)
    totals = by_market[MARKET_TOTALS]
    assert totals[("Betano", "Over 2.5")].line == pytest.approx(2.5)
    assert totals[("Betano", "Under 2.5")].price == pytest.approx(1.90)
    # market_id "corners" (sem mapeamento) nao vira quote
    assert {q.market for q in quotes} == {MARKET_H2H, MARKET_TOTALS}

    # TIMESTAMP REAL: epoch da fonte convertido para UTC canonico
    assert h2h[("Betano", "1")].timestamp == _EPOCH_0
    assert h2h[("Betano", "X")].timestamp == _EPOCH_1
    assert totals[("Betano", "Over 2.5")].timestamp == _EPOCH_2
    assert all(q.timestamp != FETCHED_AT for q in quotes)
    assert all(q.pre_kickoff for q in quotes)

    # quota NAO vem na resposta: None, nunca zero
    assert result.credits is None

    # chamada com odds_format=DECIMAL, is_main=true e cesta de sportsbook
    odds_call = [c for c in router.calls if c[0] == "/fixtures/odds"][0]
    assert odds_call[1]["odds_format"] == "DECIMAL"
    assert odds_call[1]["is_main"] == "true"
    assert odds_call[1]["sportsbook"] == list(OPTICODDS_DEFAULT_SPORTSBOOKS)


def test_opticodds_unmapped_division_is_no_coverage(monkeypatch):
    provider = OpticOddsProvider(api_key="k")
    router = _Router({})
    monkeypatch.setattr(provider, "_get", router)
    result = provider.fetch_odds(_request(divisions=("XX9",)))
    assert result.no_coverage is True
    assert router.calls == []


def test_opticodds_league_missing_from_catalog_is_no_coverage(monkeypatch):
    provider = OpticOddsProvider(api_key="k")
    monkeypatch.setattr(provider, "_get", _Router(_opticodds_routes()))
    # E1 nao esta no catalogo simulado
    result = provider.fetch_odds(_request(divisions=("E1",)))
    assert result.no_coverage is True
    assert result.errors and "E1" in result.errors[0]


def test_opticodds_http_error_propagates(monkeypatch):
    provider = OpticOddsProvider(api_key="k")
    routes = _opticodds_routes()
    routes["/leagues"] = ProviderError("HTTP 401", status=401, kind=FAILURE_AUTH)
    monkeypatch.setattr(provider, "_get", _Router(routes))
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_odds(_request())
    assert exc_info.value.kind == FAILURE_AUTH


def test_opticodds_estimated_cost_and_divisions_for():
    provider = OpticOddsProvider(api_key="k")
    assert provider.available() is True
    assert provider.divisions_for("soccer_epl") == ("E0",)
    assert provider.estimated_cost(_request(divisions=("E0",))) == 3
    assert provider.estimated_cost(_request(divisions=("XX9",))) == 0


def test_opticodds_sportsbooks_env_override(monkeypatch):
    provider = OpticOddsProvider(api_key="k", sportsbooks_env="Betano, EstrelaBet")
    router = _patch_http(monkeypatch, provider, _opticodds_routes())
    provider.fetch_odds(_request())
    odds_call = [c for c in router.calls if c[0] == "/fixtures/odds"][0]
    assert odds_call[1]["sportsbook"] == ["Betano", "EstrelaBet"]


# ==========================================================================
# 5. from_env: sem chave -> None (registrado, nao configurado)
# ==========================================================================


@pytest.mark.parametrize(
    "cls,env_name",
    [
        (OddsPapiProvider, "BETGSN_ODDSPAPI_API_KEY"),
        (OddsApiIoProvider, "BETGSN_ODDS_API_IO_KEY"),
        (OpticOddsProvider, "BETGSN_OPTICODDS_API_KEY"),
    ],
)
def test_from_env_without_key_returns_none(monkeypatch, cls, env_name):
    for name in (
        env_name,
        env_name.replace("BETGSN_", "", 1),
    ):
        monkeypatch.delenv(name, raising=False)
    assert cls.from_env() is None


@pytest.mark.parametrize(
    "cls,env_name",
    [
        (OddsPapiProvider, "BETGSN_ODDSPAPI_API_KEY"),
        (OddsApiIoProvider, "BETGSN_ODDS_API_IO_KEY"),
        (OpticOddsProvider, "BETGSN_OPTICODDS_API_KEY"),
    ],
)
def test_from_env_with_key_returns_provider(monkeypatch, cls, env_name):
    monkeypatch.delenv(env_name.replace("BETGSN_", "", 1), raising=False)
    monkeypatch.setenv(env_name, "chave-teste")
    provider = cls.from_env()
    assert provider is not None
    assert provider.available() is True


@pytest.mark.parametrize(
    "cls,plain_name",
    [
        (OddsPapiProvider, "ODDSPAPI_API_KEY"),
        (OddsApiIoProvider, "ODDS_API_IO_KEY"),
        (OpticOddsProvider, "OPTICODDS_API_KEY"),
    ],
)
def test_from_env_accepts_unprefixed_name(monkeypatch, cls, plain_name):
    monkeypatch.delenv(f"BETGSN_{plain_name}", raising=False)
    monkeypatch.setenv(plain_name, "chave-teste")
    assert cls.from_env() is not None


# ==========================================================================
# 6. merge_provider_quotes + OddsService.fetch_aggregated
# ==========================================================================


def test_merge_provider_quotes_keeps_most_recent_and_reports_collision():
    from betgsn.odds_normalize import NormalizedQuote

    def _quote(provider: str, price: float, timestamp: str) -> NormalizedQuote:
        return NormalizedQuote(
            event_id="arsenal|chelsea|2030-06-05T18:00:00Z",
            provider=provider,
            sport_key="E0",
            league="Premier League",
            home_team="Arsenal",
            away_team="Chelsea",
            kickoff=KICKOFF,
            bookmaker="Betano",
            market=MARKET_H2H,
            selection="1",
            price=price,
            timestamp=timestamp,
        )

    older = _quote("The Odds API", 2.05, "2030-06-01T10:00:00Z")
    newer = _quote("OddsPapi", 2.10, "2030-06-01T11:00:00Z")
    same = _quote("OpticOdds", 1.98, "2030-06-01T10:00:00Z")

    merged, collisions = merge_provider_quotes([older, newer, same])

    # um so dono por linha fisica: o mais recente
    assert len(merged) == 1
    assert merged[0].provider == "OddsPapi"
    assert merged[0].price == pytest.approx(2.10)
    # colisoes EXPLICITAS: 2 (older<->newer e same<->newer); em AMBAS a
    # linha fica com o mais recente (OddsPapi) e o descartado e registrado
    assert len(collisions) == 2
    assert {c[4] for c in collisions} == {"OddsPapi"}
    assert {c[5] for c in collisions} == {"The Odds API", "OpticOdds"}


class _FakeProvider:
    """Provider de contrato com resposta fixa (sem rede)."""

    def __init__(self, name: str, events: list[dict], fetched_at: str):
        self.name = name
        self._events = events
        self._fetched_at = fetched_at

    def available(self) -> bool:
        return True

    def divisions_for(self, scope: str) -> tuple[str, ...]:
        return ("E0",)

    def fetch_odds(self, request):
        from betgsn.odds_normalize import normalize_events
        from betgsn.odds_provider import OddsProviderFetch

        quotes = normalize_events(
            self._events, self.name, request.fetched_at, sport_key="E0"
        )
        return OddsProviderFetch(
            quotes=tuple(quotes),
            raw_events=tuple(self._events),
            snapshot_provider=f"{self.name.lower()}-live",
        )


def _aggregation_event(timestamp: str | None = None) -> dict:
    event = {
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "commence_time": KICKOFF,
        "bookmakers": [
            {
                "key": "betano",
                "title": "Betano",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [{"name": "Arsenal", "price": 2.10}],
                    }
                ],
            }
        ],
    }
    if timestamp:
        event["timestamp"] = timestamp
    return event


def test_fetch_aggregated_merges_all_providers(monkeypatch):
    a = _FakeProvider("A", [_aggregation_event("2030-06-01T10:00:00Z")], FETCHED_AT)
    b = _FakeProvider("B", [_aggregation_event("2030-06-01T11:00:00Z")], FETCHED_AT)

    svc = OddsService(providers=[("A", a), ("B", b)], now=_Clock(FETCHED_AT))
    result = svc.fetch_aggregated("soccer_epl")

    assert result.ok is True
    assert result.provider == "A+B"  # agregacao, nao fallback
    assert result.fallback_used is False
    # UMA linha por chave fisica: a observacao mais recente (B) vence
    assert len(result.quotes) == 1
    assert result.quotes[0].timestamp == "2030-06-01T11:00:00Z"
    # colisao EXPLICITAMENTE registrada, nunca mascarada
    assert any("colisao multi-provider" in e for e in result.errors)
    # health SEPARADO por provider: ambos HEALTHY
    health = svc.health_snapshot()
    assert health["A"]["state"] == "HEALTHY"
    assert health["B"]["state"] == "HEALTHY"
    assert [a.status for a in result.attempts] == ["OK", "OK"]


def test_fetch_aggregated_failure_of_one_does_not_contaminate(monkeypatch):
    class _Broken:
        name = "Broken"

        def available(self):
            return True

        def divisions_for(self, scope):
            return ("E0",)

        def fetch_odds(self, request):
            raise ProviderError("500", status=500, kind="SERVER", retryable=True)

    ok = _FakeProvider("OK", [_aggregation_event("2030-06-01T11:00:00Z")], FETCHED_AT)
    svc = OddsService(
        providers=[("Broken", _Broken()), ("OK", ok)], now=_Clock(FETCHED_AT)
    )
    result = svc.fetch_aggregated("soccer_epl")

    assert result.ok is True
    assert result.provider == "OK"
    assert result.quotes and result.quotes[0].provider == "OK"
    statuses = {a.provider: a.status for a in result.attempts}
    assert statuses == {"Broken": "FAILED", "OK": "OK"}
    assert svc.health_snapshot()["Broken"]["state"] == "DEGRADED"
    assert svc.health_snapshot()["OK"]["state"] == "HEALTHY"


def test_fetch_aggregated_no_quotes_degrades(monkeypatch):
    empty = _FakeProvider("Empty", [], FETCHED_AT)
    svc = OddsService(providers=[("Empty", empty)], now=_Clock(FETCHED_AT))
    result = svc.fetch_aggregated("soccer_epl")
    assert result.ok is False
    assert result.quotes == []
    assert result.attempts[0].status == "NO_COVERAGE"


# ==========================================================================
# 7. fetch (fallback) preservado: agregacao e um MODO novo, nao troca
# ==========================================================================


def test_fetch_fallback_semantics_unchanged(monkeypatch):
    """`fetch` continua atendendo com o PRIMEIRO provider (fallback)."""
    a = _FakeProvider("A", [_aggregation_event("2030-06-01T10:00:00Z")], FETCHED_AT)
    b = _FakeProvider("B", [_aggregation_event("2030-06-01T11:00:00Z")], FETCHED_AT)
    svc = OddsService(providers=[("A", a), ("B", b)], now=_Clock(FETCHED_AT))

    result = svc.fetch("soccer_epl")
    assert result.provider == "A"
    assert all(q.provider == "A" for q in result.quotes)
