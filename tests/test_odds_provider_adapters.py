"""FASE B.3 — adapters reais implementando o contrato OddsProvider.

O que esta suíte prova (migracao strangler, zero quebra):

  1. `OddsApiProvider.fetch_odds` devolve quotes canonicas COM paridade
     exata com `normalize_events` (mesmo parser, nenhum paralelo);
  2. divisoes -> sport keys: mapeada vira chamada, sem mapeamento e
     ignorada silenciosamente, escopo vazio e no_coverage explicito;
  3. mercados: rotulo interno -> chave Odds API (reverse de MARKET_MAP),
     rotulo sem mapeamento e ignorado, vazio usa o default do provider;
  4. creditos: headers x-requests-* viram CreditUpdate; sem headers,
     credits None — nunca zero inventado;
  5. `estimated_cost` espelha `estimated_request_cost` (mercados x
     regioes por sport key) e `divisions_for` usa o catalogo existente;
  6. `ParlayApiProvider.fetch_odds` segue a mesma familia de formato,
     com label de snapshot proprio ("parlayapi-live");
  7. os DOIS adapters reais satisfazem a suíte de conformance do
     contrato (Task 1) com quotes nao-vazias;
  8. OddsService: caminho novo (fetch_odds) e caminho legado (duck)
     devolvem OddsFetch identico — paridade estrita;
  9. mercado nao suportado: o adapter tenta de novo sem o mercado
     rejeitado e devolve as quotes do que e suportado; falha dura
     (nao relacionada a mercados) sobe alta.

Os eventos usados os fixtures INVENTADOS no shape da The Odds API
(copiados de tests/test_odds_identity_integration.py): nenhuma rede.
"""
from __future__ import annotations

import pytest

from betgsn.odds_normalize import dedupe_quotes, normalize_events
from betgsn.odds_provider import (
    CreditUpdate,
    OddsFetchRequest,
    divisions_for,
    estimated_cost,
)
from betgsn.odds_service import OddsService
from betgsn.providers import (
    FAILURE_AUTH,
    OddsApiProvider,
    ParlayApiProvider,
    ProviderError,
)
from test_odds_provider_contract import run_contract_conformance

#: Instante INVENTADO, injetado pelo chamador (o contrato nao consulta relogio).
FETCHED_AT = "2029-12-31T12:00:00Z"
#: Kickoff INVENTADO no formato da The Odds API.
KICKOFF = "2030-01-01T12:00:00Z"

MARKET_H2H = "Resultado Final (1X2)"
MARKET_TOTALS = "Total de Gols"


def _odds_api_event() -> dict:
    """Evento cru no shape da The Odds API (fixture de integracao C1/C2)."""
    return {
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "commence_time": KICKOFF,
        "bookmakers": [
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Arsenal", "price": 1.90},
                            {"name": "Draw", "price": 3.40},
                            {"name": "Chelsea", "price": 4.20},
                        ],
                    }
                ],
            }
        ],
    }


class _StubLive:
    """Troca o `live_odds_with_meta` de um provider real por uma resposta fixa.

    Nenhuma rede: eventos e headers sao os INVENTADOS do teste.
    """

    def __init__(self, events, headers=None, calls=None):
        self.events = events
        self.headers = headers or {}
        self.calls = calls

    def __call__(self, sport_key, regions=None, markets=None):
        if self.calls is not None:
            self.calls.append((sport_key, regions, markets))
        return list(self.events), dict(self.headers)


class _DuckProvider:
    """Provider LEGADO: so `live_odds_with_meta`, sem fetch_odds."""

    def __init__(self, events, headers=None):
        self.events = events
        self.headers = headers or {}

    def live_odds_with_meta(self, sport_key, regions=None, markets=None):
        return list(self.events), dict(self.headers)


class _Clock:
    def __init__(self, value: str):
        self.value = value

    def __call__(self) -> str:
        return self.value


# ==========================================================================
# 1. OddsApiProvider.fetch_odds: quotes canonicas com paridade exata
# ==========================================================================


def test_odds_api_fetch_odds_quotes_parity(monkeypatch):
    """Quotes do adapter sao EXATAMENTE as de normalize_events."""
    provider = OddsApiProvider(api_key="k")
    event = _odds_api_event()
    monkeypatch.setattr(provider, "live_odds_with_meta", _StubLive([event]))

    result = provider.fetch_odds(
        OddsFetchRequest(
            divisions=("E0",),
            markets=(MARKET_H2H,),
            fetched_at=FETCHED_AT,
        )
    )

    assert result.snapshot_provider == "the-odds-api-live"
    assert result.raw_events == (event,)
    assert result.credits is None
    assert result.no_coverage is False
    assert result.errors == ()

    expected = normalize_events(
        [event], "The Odds API", FETCHED_AT, sport_key="soccer_epl"
    )
    assert list(result.quotes) == expected
    assert result.quotes  # nao vacuo
    assert all(q.provider == "The Odds API" for q in result.quotes)
    assert all(isinstance(q.provider, str) for q in result.quotes)
    assert {q.selection for q in result.quotes} == {"1", "X", "2"}


# ==========================================================================
# 2. Divisoes -> sport keys
# ==========================================================================


def test_divisions_translate_to_sport_keys(monkeypatch):
    provider = OddsApiProvider(api_key="k")
    calls: list[tuple[str, str | None, str | None]] = []
    monkeypatch.setattr(provider, "live_odds_with_meta", _StubLive([], calls=calls))

    result = provider.fetch_odds(
        OddsFetchRequest(divisions=("E0", "XX9"), fetched_at=FETCHED_AT)
    )

    # divisao mapeada virou chamada com o sport key certo; XX9 foi
    # ignorada silenciosamente (sem erro, sem chamada inventada)
    assert [c[0] for c in calls] == ["soccer_epl"]
    assert result.quotes == ()
    assert result.raw_events == ()
    assert result.no_coverage is False


def test_empty_or_unmapped_divisions_is_explicit_no_coverage(monkeypatch):
    provider = OddsApiProvider(api_key="k")
    calls: list[tuple[str, str | None, str | None]] = []
    monkeypatch.setattr(provider, "live_odds_with_meta", _StubLive([], calls=calls))

    # divisoes vazias: sem informacao — no_coverage, nenhuma chamada
    empty = provider.fetch_odds(OddsFetchRequest(fetched_at=FETCHED_AT))
    assert empty.no_coverage is True
    assert empty.quotes == ()
    assert empty.raw_events == ()
    assert calls == []

    # so divisoes SEM mapeamento: mesmo tratamento
    unmapped = provider.fetch_odds(
        OddsFetchRequest(divisions=("XX9", "ZZZ"), fetched_at=FETCHED_AT)
    )
    assert unmapped.no_coverage is True
    assert calls == []


# ==========================================================================
# 3. Mercados: rotulo interno -> chave Odds API
# ==========================================================================


def test_market_labels_translate_to_api_keys(monkeypatch):
    provider = OddsApiProvider(api_key="k")
    calls: list[tuple[str, str | None, str | None]] = []
    monkeypatch.setattr(provider, "live_odds_with_meta", _StubLive([], calls=calls))

    provider.fetch_odds(
        OddsFetchRequest(
            divisions=("E0",),
            markets=(MARKET_H2H, MARKET_TOTALS, "Mercado Inexistente"),
            fetched_at=FETCHED_AT,
        )
    )
    # rotulos traduzidos via reverse de MARKET_MAP; sem mapeamento ignorado
    assert calls[0][2] == "h2h,totals"
    # regions ausentes no pedido -> default do provider
    assert calls[0][1] == "eu,uk"

    # mercados vazios -> default do provider (h2h,totals,btts)
    provider.fetch_odds(
        OddsFetchRequest(divisions=("E0",), fetched_at=FETCHED_AT)
    )
    assert calls[1][2] == "h2h,totals,btts"

    # regions do pedido sao respeitadas
    provider.fetch_odds(
        OddsFetchRequest(divisions=("E0",), regions="us", fetched_at=FETCHED_AT)
    )
    assert calls[2][1] == "us"


# ==========================================================================
# 4. Creditos: headers -> CreditUpdate; sem headers -> None
# ==========================================================================


def test_credits_from_headers_and_none_without(monkeypatch):
    provider = OddsApiProvider(api_key="k")
    event = _odds_api_event()

    monkeypatch.setattr(
        provider,
        "live_odds_with_meta",
        _StubLive(
            [event],
            headers={
                "x-requests-last": "4",
                "x-requests-used": "8",
                "x-requests-remaining": "42",
            },
        ),
    )
    result = provider.fetch_odds(
        OddsFetchRequest(divisions=("E0",), fetched_at=FETCHED_AT)
    )
    assert result.credits == CreditUpdate(last=4, used=8, remaining=42)

    # sem headers: credits None — nunca zero com cara de medido
    monkeypatch.setattr(provider, "live_odds_with_meta", _StubLive([event]))
    result = provider.fetch_odds(
        OddsFetchRequest(divisions=("E0",), fetched_at=FETCHED_AT)
    )
    assert result.credits is None


# ==========================================================================
# 5. estimated_cost / divisions_for
# ==========================================================================


def test_estimated_cost_and_divisions_for():
    provider = OddsApiProvider(api_key="k")

    assert provider.available() is True
    assert provider.divisions_for("soccer_epl") == ("E0",)
    assert provider.divisions_for("sport_desconhecido") == ()

    # 2 sport keys x (2 mercados x 1 regiao) = 4
    request = OddsFetchRequest(
        divisions=("E0", "I1"),
        markets=(MARKET_H2H, MARKET_TOTALS),
        regions="eu",
    )
    assert provider.estimated_cost(request) == 4

    # regions ausentes -> default do provider ("eu,uk" = 2 regioes)
    request = OddsFetchRequest(
        divisions=("E0",), markets=(MARKET_H2H, MARKET_TOTALS)
    )
    assert provider.estimated_cost(request) == 4

    # sem divisoes mapeadas nao ha chamada a estimar
    assert (
        provider.estimated_cost(
            OddsFetchRequest(divisions=("XX9",), markets=(MARKET_H2H,))
        )
        == 0
    )

    # helper do contrato delega ao metodo do adapter
    assert (
        estimated_cost(
            provider,
            OddsFetchRequest(
                divisions=("E0",), markets=(MARKET_H2H,), regions="eu"
            ),
        )
        == 1
    )
    assert divisions_for(provider, "soccer_epl") == ("E0",)


# ==========================================================================
# 6. ParlayApiProvider: mesma familia de formato, label proprio
# ==========================================================================


def test_parlay_fetch_odds_same_format(monkeypatch):
    provider = ParlayApiProvider(api_key="k", base_url="https://parlay.invalid")
    event = _odds_api_event()
    calls: list[tuple[str, str | None, str | None]] = []
    monkeypatch.setattr(provider, "live_odds_with_meta", _StubLive([event], calls=calls))

    result = provider.fetch_odds(
        OddsFetchRequest(
            divisions=("E0",), markets=(MARKET_H2H,), fetched_at=FETCHED_AT
        )
    )

    assert [c[0] for c in calls] == ["soccer_epl"]
    assert result.snapshot_provider == "parlayapi-live"
    assert result.credits is None
    assert all(q.provider == "ParlayAPI" for q in result.quotes)
    expected = normalize_events(
        [event], "ParlayAPI", FETCHED_AT, sport_key="soccer_epl"
    )
    assert list(result.quotes) == expected
    assert result.quotes  # nao vacuo

    assert provider.available() is True
    assert provider.divisions_for("soccer_epl") == ("E0",)
    # ParlayAPI nao tem regions proprios: 1 regiao por clamps do modelo
    assert (
        provider.estimated_cost(
            OddsFetchRequest(
                divisions=("E0",), markets=(MARKET_H2H, MARKET_TOTALS), regions="eu"
            )
        )
        == 2
    )


def test_parlay_empty_divisions_is_no_coverage(monkeypatch):
    provider = ParlayApiProvider(api_key="k", base_url="https://parlay.invalid")
    calls: list[tuple[str, str | None, str | None]] = []
    monkeypatch.setattr(provider, "live_odds_with_meta", _StubLive([], calls=calls))
    result = provider.fetch_odds(OddsFetchRequest(fetched_at=FETCHED_AT))
    assert result.no_coverage is True
    assert calls == []


# ==========================================================================
# 7. Conformance do contrato (Task 1) contra os DOIS adapters reais
# ==========================================================================


def test_real_adapters_satisfy_contract_conformance(monkeypatch):
    odds_api = OddsApiProvider(api_key="k")
    monkeypatch.setattr(
        odds_api, "live_odds_with_meta", _StubLive([_odds_api_event()])
    )
    result = run_contract_conformance(odds_api, expect_name="The Odds API")
    assert result.quotes, "conformance nao pode ser vacuo"
    assert result.snapshot_provider == "the-odds-api-live"

    parlay = ParlayApiProvider(api_key="k", base_url="https://parlay.invalid")
    monkeypatch.setattr(
        parlay, "live_odds_with_meta", _StubLive([_odds_api_event()])
    )
    result = run_contract_conformance(parlay, expect_name="ParlayAPI")
    assert result.quotes, "conformance nao pode ser vacuo"
    assert result.snapshot_provider == "parlayapi-live"


# ==========================================================================
# 8. OddsService: caminho novo (fetch_odds) com paridade com o legado
# ==========================================================================


def test_service_new_path_returns_ok_with_quotes(monkeypatch):
    provider = OddsApiProvider(api_key="k")
    event = _odds_api_event()
    headers = {"x-requests-remaining": "17", "x-requests-used": "8"}
    monkeypatch.setattr(provider, "live_odds_with_meta", _StubLive([event], headers))

    service = OddsService(
        providers=[("The Odds API", provider)],
        now=_Clock(FETCHED_AT),
    )
    result = service.fetch("soccer_epl", markets="h2h")

    assert result.ok is True
    assert result.provider == "The Odds API"
    assert result.state.value == "HEALTHY"
    assert result.events == 1
    assert result.credits_remaining == 17
    assert service.credits_snapshot()["The Odds API"]["remaining"] == 17
    expected = dedupe_quotes(
        normalize_events([event], "The Odds API", FETCHED_AT, sport_key="soccer_epl")
    )
    assert result.quotes == expected


def test_service_dual_path_parity(monkeypatch):
    """Quotes e shape identicos entre o caminho novo e o legado."""
    event = _odds_api_event()
    headers = {"x-requests-remaining": "17", "x-requests-used": "8"}

    new_provider = OddsApiProvider(api_key="k")
    monkeypatch.setattr(
        new_provider, "live_odds_with_meta", _StubLive([event], headers)
    )
    legacy_provider = _DuckProvider([event], headers)

    new_service = OddsService(
        providers=[("The Odds API", new_provider)], now=_Clock(FETCHED_AT)
    )
    legacy_service = OddsService(
        providers=[("The Odds API", legacy_provider)], now=_Clock(FETCHED_AT)
    )

    new_result = new_service.fetch("soccer_epl", markets="h2h")
    legacy_result = legacy_service.fetch("soccer_epl", markets="h2h")

    assert new_result.quotes == legacy_result.quotes
    assert new_result.credits_remaining == legacy_result.credits_remaining == 17
    assert new_result.events == legacy_result.events
    assert new_result.to_dict() == legacy_result.to_dict()


def test_service_dual_path_parity_without_headers(monkeypatch):
    event = _odds_api_event()

    new_provider = OddsApiProvider(api_key="k")
    monkeypatch.setattr(new_provider, "live_odds_with_meta", _StubLive([event]))
    legacy_provider = _DuckProvider([event])

    new_service = OddsService(
        providers=[("The Odds API", new_provider)], now=_Clock(FETCHED_AT)
    )
    legacy_service = OddsService(
        providers=[("The Odds API", legacy_provider)], now=_Clock(FETCHED_AT)
    )

    new_result = new_service.fetch("soccer_epl", markets="h2h")
    legacy_result = legacy_service.fetch("soccer_epl", markets="h2h")

    assert new_result.ok is True
    assert new_result.quotes == legacy_result.quotes
    # sem headers: credito desconhecido e None nos dois caminhos
    assert new_result.credits_remaining is None
    assert new_result.credits_remaining == legacy_result.credits_remaining


def test_service_new_path_no_coverage_for_unknown_sport_key(monkeypatch):
    provider = OddsApiProvider(api_key="k")
    calls: list[tuple[str, str | None, str | None]] = []
    monkeypatch.setattr(
        provider, "live_odds_with_meta", _StubLive([_odds_api_event()], calls=calls)
    )
    service = OddsService(
        providers=[("The Odds API", provider)], now=_Clock(FETCHED_AT)
    )
    result = service.fetch("soccer_sem_mapeamento")
    assert result.ok is False
    assert result.quotes == []
    assert result.attempts[0].status == "NO_COVERAGE"
    assert calls == []  # sem divisoes mapeadas, nenhuma chamada aconteceu


# ==========================================================================
# 9. Retry de mercado nao suportado dentro do adapter
# ==========================================================================


def test_fetch_odds_retries_dropping_unsupported_markets(monkeypatch):
    provider = OddsApiProvider(api_key="k")
    calls: list[str | None] = []

    def fake_live(sport_key, regions=None, markets=None):
        calls.append(markets)
        if markets and "totals" in markets.split(","):
            raise ProviderError(
                "Markets not supported by this endpoint: totals"
            )
        return [_odds_api_event()], {}

    monkeypatch.setattr(provider, "live_odds_with_meta", fake_live)

    result = provider.fetch_odds(
        OddsFetchRequest(
            divisions=("E0",),
            markets=(MARKET_H2H, MARKET_TOTALS),
            fetched_at=FETCHED_AT,
        )
    )

    # tentou com os dois, recebeu a rejeicao, tentou de novo sem totals
    assert calls == ["h2h,totals", "h2h"]
    assert result.quotes  # quotes do mercado suportado
    assert all(q.market == MARKET_H2H for q in result.quotes)
    assert result.errors  # a queda do mercado e registrada, nao escondida


def test_fetch_odds_raises_when_no_market_remains(monkeypatch):
    provider = OddsApiProvider(api_key="k")

    def fake_live(sport_key, regions=None, markets=None):
        raise ProviderError("Markets not supported by this endpoint: totals")

    monkeypatch.setattr(provider, "live_odds_with_meta", fake_live)

    with pytest.raises(ProviderError):
        provider.fetch_odds(
            OddsFetchRequest(
                divisions=("E0",), markets=(MARKET_TOTALS,), fetched_at=FETCHED_AT
            )
        )


def test_fetch_odds_propagates_hard_provider_error(monkeypatch):
    provider = OddsApiProvider(api_key="k")

    def fake_live(sport_key, regions=None, markets=None):
        raise ProviderError("HTTP 401", status=401, kind=FAILURE_AUTH)

    monkeypatch.setattr(provider, "live_odds_with_meta", fake_live)

    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_odds(
            OddsFetchRequest(divisions=("E0",), fetched_at=FETCHED_AT)
        )
    assert exc_info.value.kind == FAILURE_AUTH
    assert exc_info.value.status == 401
