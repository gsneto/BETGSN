"""Correcao do adapter ParlayAPI — endpoint oficial v1 (regressao).

O que esta suite prova (bug real: HTTP 404 em /odds?sport=...):

  1. URL e a rota oficial `GET /v1/sports/{sport_key}/odds` — sport_key
     no PATH (nunca query `sport=`), sem apiKey na URL (auth por header
     X-API-Key, recomendado pela API);
  2. regions serializado corretamente: str "eu" -> eu; tuple ('eu',) ->
     eu; tuple ('eu','br') -> eu,br — nunca o lixo %28%27eu%27%2C%29;
  3. `last_update` REAL de cada bookmaker vira o timestamp das quotes
     daquela casa — nunca substituido por fetched_at;
  4. preco pedido em decimal (oddsFormat=decimal);
  5. base terminando em /v1 nao duplica o prefixo da rota;
  6. eventos seguem fluindo pelo contrato (quotes ParlayAPI canonicas).

Os payloads sao INVENTADOS no shape REAL do OpenAPI v3.2.0 da ParlayAPI
(verificado em 2026-09-23): nenhuma rede.
"""
from __future__ import annotations

import urllib.parse

import pytest

from betgsn.odds_provider import OddsFetchRequest
from betgsn.providers import (
    ParlayApiProvider,
    _parlay_inject_last_update,
    _parlay_regions,
)

FETCHED_AT = "2030-06-01T12:00:00Z"
KICKOFF = "2030-06-05T18:00:00Z"

MARKET_H2H = "Resultado Final (1X2)"


# ==========================================================================
# 1-2. Construcao da URL: rota oficial, sport_key no PATH, regions limpos
# ==========================================================================


def _query(url: str) -> dict[str, str]:
    parsed = urllib.parse.urlsplit(url)
    return dict(urllib.parse.parse_qsl(parsed.query))


def test_url_uses_official_v1_path_with_sport_key_in_path():
    provider = ParlayApiProvider(api_key="k", base_url="https://parlay-api.com")
    url = provider._url("soccer_austria_bundesliga", "eu", "h2h")

    parsed = urllib.parse.urlsplit(url)
    assert parsed.path == "/v1/sports/soccer_austria_bundesliga/odds"
    # a credencial NUNCA circula na URL
    assert "apiKey" not in url
    assert "k" not in _query(url).values()


def test_url_has_no_sport_query_param():
    provider = ParlayApiProvider(api_key="k", base_url="https://parlay-api.com")
    url = provider._url("soccer_spain_la_liga", "eu", "h2h")
    assert "sport" not in _query(url)


def test_url_requests_decimal_odds_format():
    provider = ParlayApiProvider(api_key="k", base_url="https://parlay-api.com")
    url = provider._url("soccer_epl", "eu", "h2h")
    assert _query(url)["oddsFormat"] == "decimal"


@pytest.mark.parametrize(
    "regions,expected",
    [
        ("eu", "eu"),
        (("eu",), "eu"),          # bug real: antes virava %28%27eu%27%2C%29
        (("eu", "br"), "eu,br"),
        (["eu", "br"], "eu,br"),
        ("eu,uk", "eu,uk"),
        (None, None),             # parametro omitido
        ("", None),
        ((), None),
    ],
)
def test_regions_serialization(regions, expected):
    assert _parlay_regions(regions) == (expected or "")


def test_url_regions_parameter_from_tuple():
    provider = ParlayApiProvider(api_key="k", base_url="https://parlay-api.com")

    q = _query(provider._url("soccer_epl", ("eu",), "h2h"))
    assert q["regions"] == "eu"

    q = _query(provider._url("soccer_epl", ("eu", "br"), "h2h"))
    assert q["regions"] == "eu,br"

    # regions ausente: parametro omitido, default da API
    q = _query(provider._url("soccer_epl", None, "h2h"))
    assert "regions" not in q


def test_url_base_trailing_v1_not_duplicated():
    provider = ParlayApiProvider(api_key="k", base_url="https://parlay-api.com/v1")
    url = provider._url("soccer_epl", "eu", "h2h")
    assert urllib.parse.urlsplit(url).path == "/v1/sports/soccer_epl/odds"


def test_url_base_trailing_slash_tolerated():
    provider = ParlayApiProvider(api_key="k", base_url="https://parlay-api.com/")
    url = provider._url("soccer_epl", "eu", "h2h")
    assert urllib.parse.urlsplit(url).path == "/v1/sports/soccer_epl/odds"


def test_url_sport_key_with_special_characters_is_quoted():
    provider = ParlayApiProvider(api_key="k", base_url="https://parlay-api.com")
    url = provider._url("soccer_epl extra", "eu", "h2h")
    assert urllib.parse.urlsplit(url).path == "/v1/sports/soccer_epl%20extra/odds"


# ==========================================================================
# 3. Auth por header X-API-Key
# ==========================================================================


def test_live_odds_sends_api_key_in_header(monkeypatch):
    provider = ParlayApiProvider(api_key="chave-secreta", base_url="https://parlay-api.com")
    captured: dict = {}

    def fake_request(url, headers=None, timeout=20, max_attempts=2, **kwargs):
        captured["url"] = url
        captured["headers"] = headers or {}
        return [{"home_team": "A", "away_team": "B"}], {}

    from betgsn import providers as mod

    monkeypatch.setattr(mod, "request_json_with_retry", fake_request)
    events, _headers = provider.live_odds_with_meta(
        "soccer_austria_bundesliga", regions="eu", markets="h2h"
    )

    assert captured["headers"].get("X-API-Key") == "chave-secreta"
    assert "chave-secreta" not in captured["url"]  # segredo nunca na URL
    assert urllib.parse.urlsplit(captured["url"]).path == (
        "/v1/sports/soccer_austria_bundesliga/odds"
    )


# ==========================================================================
# 4. last_update real -> timestamp da quote (nunca fetched_at)
# ==========================================================================


def _parlay_event(last_update: str | None = "2030-06-01T10:30:00Z") -> dict:
    """Evento no shape REAL do OpenAPI v3.2.0 (last_update POR BOOKMAKER)."""
    book: dict = {
        "key": "pinnacle",
        "title": "Pinnacle",
        "markets": [
            {
                "key": "h2h",
                "outcomes": [
                    {"name": "Arsenal", "price": 2.10},
                    {"name": "Draw", "price": 3.40},
                ],
            }
        ],
    }
    if last_update:
        book["last_update"] = last_update
    return {
        "id": "evt1",
        "sport_key": "soccer_epl",
        "sport_title": "Premier League",
        "commence_time": KICKOFF,
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "bookmakers": [book],
    }


def test_inject_last_update_propagates_to_outcomes():
    events = _parlay_inject_last_update([_parlay_event()])
    outcomes = events[0]["bookmakers"][0]["markets"][0]["outcomes"]
    assert all(o["timestamp"] == "2030-06-01T10:30:00Z" for o in outcomes)


def test_inject_last_update_does_not_mutate_original():
    original = _parlay_event()
    _parlay_inject_last_update([original])
    assert "timestamp" not in original["bookmakers"][0]["markets"][0]["outcomes"][0]


def test_inject_last_update_absent_leaves_outcomes_untouched():
    events = _parlay_inject_last_update([_parlay_event(last_update=None)])
    outcomes = events[0]["bookmakers"][0]["markets"][0]["outcomes"]
    assert all("timestamp" not in o for o in outcomes)


def test_inject_last_update_preserves_existing_outcome_timestamp():
    event = _parlay_event()
    event["bookmakers"][0]["markets"][0]["outcomes"][0]["timestamp"] = "2030-06-01T09:00:00Z"
    events = _parlay_inject_last_update([event])
    outcomes = events[0]["bookmakers"][0]["markets"][0]["outcomes"]
    assert outcomes[0]["timestamp"] == "2030-06-01T09:00:00Z"  # nao sobrescrito
    assert outcomes[1]["timestamp"] == "2030-06-01T10:30:00Z"  # preenchido


def test_fetch_odds_quotes_carry_real_timestamp(monkeypatch):
    """Quote nasce com last_update REAL da casa — nunca o fetched_at."""
    from betgsn import providers as mod

    provider = ParlayApiProvider(api_key="k", base_url="https://parlay-api.com")
    monkeypatch.setattr(
        mod,
        "request_json_with_retry",
        lambda url, headers=None, timeout=20, max_attempts=2, **kw: (
            [_parlay_event()],
            {},
        ),
    )

    result = provider.fetch_odds(
        OddsFetchRequest(
            divisions=("E0",), markets=(MARKET_H2H,), fetched_at=FETCHED_AT
        )
    )

    assert result.snapshot_provider == "parlayapi-live"
    quotes = list(result.quotes)
    assert quotes
    assert all(q.provider == "ParlayAPI" for q in quotes)
    assert all(q.timestamp == "2030-06-01T10:30:00Z" for q in quotes)
    assert all(q.timestamp != FETCHED_AT for q in quotes)
    assert all(q.pre_kickoff for q in quotes)
    assert all(q.bookmaker == "Pinnacle" for q in quotes)


def test_fetch_odds_without_last_update_falls_back_to_fetched_at(monkeypatch):
    """Casa sem last_update: sem horario proprio, semantica do contrato."""
    from betgsn import providers as mod

    provider = ParlayApiProvider(api_key="k", base_url="https://parlay-api.com")
    monkeypatch.setattr(
        mod,
        "request_json_with_retry",
        lambda url, headers=None, timeout=20, max_attempts=2, **kw: (
            [_parlay_event(last_update=None)],
            {},
        ),
    )

    result = provider.fetch_odds(
        OddsFetchRequest(
            divisions=("E0",), markets=(MARKET_H2H,), fetched_at=FETCHED_AT
        )
    )
    assert result.quotes
    assert all(q.timestamp == FETCHED_AT for q in result.quotes)


# ==========================================================================
# 5. from_env sem base segue inerte (comportamento preservado)
# ==========================================================================


def test_from_env_without_base_returns_none(monkeypatch):
    monkeypatch.setenv("BETGSN_PARLAY_API_KEY", "chave")
    monkeypatch.delenv("BETGSN_PARLAY_API_BASE", raising=False)
    assert ParlayApiProvider.from_env() is None


def test_from_env_with_base_builds_official_route(monkeypatch):
    monkeypatch.setenv("BETGSN_PARLAY_API_KEY", "chave")
    monkeypatch.setenv("BETGSN_PARLAY_API_BASE", "https://parlay-api.com")
    monkeypatch.delenv("BETGSN_PARLAY_ODDS_PATH", raising=False)
    provider = ParlayApiProvider.from_env()
    assert provider is not None
    url = provider._url("soccer_epl", "eu", "h2h")
    assert urllib.parse.urlsplit(url).path == "/v1/sports/soccer_epl/odds"
