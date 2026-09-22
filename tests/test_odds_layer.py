"""Testes da camada de odds: math, normalizacao, health, fallback, shopping, arb."""
from __future__ import annotations

import json
import urllib.error

import pytest

from betgsn import providers
from betgsn.line_shopping import (
    MIN_CONSENSUS_BOOKS,
    detect_arbitrage,
    line_shop,
    line_shopping_report,
    market_analysis,
)
from betgsn.odds_health import (
    CreditController,
    HealthTracker,
    ProviderState,
    classify_exception,
)
from betgsn.odds_math import (
    devig,
    edge_vs_fair,
    fair_odds_for,
    implied_probabilities,
    market_overround,
    power_fair_probs,
    shin_fair_probs,
)
from betgsn.odds_normalize import (
    NormalizedQuote,
    dedupe_quotes,
    event_key,
    grouped_from_event,
    normalize_event,
    normalize_events,
)
from betgsn.odds_service import OddsService
from betgsn.odds_snapshots import OddsSnapshotStore
from betgsn.providers import (
    FAILURE_AUTH,
    FAILURE_BAD_RESPONSE,
    FAILURE_NO_CREDITS,
    FAILURE_RATE_LIMIT,
    FAILURE_SERVER,
    ProviderError,
    classify_status,
    parse_credit_headers,
    request_json_with_retry,
)

MARKET = "Resultado Final (1X2)"
KICKOFF = "2030-01-01T12:00:00Z"


# ==========================================================================
# 1. Math: implied, overround, de-vig, fair odds
# ==========================================================================


def test_implied_probabilities_and_overround():
    odds = {"1": 1.5, "X": 4.0, "2": 7.0}
    implied = implied_probabilities(odds)
    assert implied["1"] == pytest.approx(1 / 1.5)
    overround = market_overround(odds)
    assert overround == pytest.approx(sum(implied.values()))
    assert overround > 1.0  # mercado tem margem


def test_devig_multiplicative_sums_to_one():
    result = devig({"1": 1.5, "X": 4.0, "2": 7.0}, method="multiplicative")
    assert result.complete is True
    assert sum(result.fair_probabilities.values()) == pytest.approx(1.0)
    assert result.margin == pytest.approx(result.overround - 1.0)


@pytest.mark.parametrize("method", ["multiplicative", "shin", "power"])
def test_devig_methods_produce_valid_distribution(method):
    result = devig({"1": 1.5, "X": 4.0, "2": 7.0}, method=method)
    probs = list(result.fair_probabilities.values())
    assert sum(probs) == pytest.approx(1.0, abs=1e-6)
    assert all(0.0 < p < 1.0 for p in probs)


def test_devig_rejects_unknown_method():
    with pytest.raises(ValueError):
        devig({"1": 2.0, "2": 2.0}, method="nao-existe")


def test_devig_rejects_invalid_odds():
    with pytest.raises(ValueError):
        devig({"1": 1.0, "2": 2.0})


def test_shin_moves_probability_from_longshot_to_favorite():
    """Shin costuma dar mais probabilidade ao favorito que o proporcional."""
    odds = {"1": 1.5, "X": 4.0, "2": 7.0}
    mult = devig(odds, method="multiplicative").fair_probabilities
    shin = devig(odds, method="shin").fair_probabilities
    assert shin["1"] > mult["1"]
    assert shin["2"] < mult["2"]


def test_shin_no_margin_returns_normalized():
    probs = shin_fair_probs([0.5, 0.5])
    assert sum(probs) == pytest.approx(1.0)
    assert probs[0] == pytest.approx(0.5)


def test_power_fair_probs_sums_to_one():
    probs = power_fair_probs([1 / 1.5, 1 / 4.0, 1 / 7.0])
    assert sum(probs) == pytest.approx(1.0, abs=1e-9)


def test_fair_odds_for_returns_decimal_odds():
    fair = fair_odds_for({"1": 1.5, "X": 4.0, "2": 7.0})
    assert sum(1.0 / o for o in fair.values()) == pytest.approx(1.0)
    assert fair["1"] > 1.5  # a odd justa do favorito e maior que a com margem


def test_edge_vs_fair_sign():
    assert edge_vs_fair(2.0, 0.5) == pytest.approx(0.0)
    assert edge_vs_fair(2.2, 0.5) > 0
    assert edge_vs_fair(1.8, 0.5) < 0
    with pytest.raises(ValueError):
        edge_vs_fair(2.0, 0.0)


# ==========================================================================
# 2. Normalizacao
# ==========================================================================


def _book(title, markets):
    return {"key": title.lower(), "title": title, "markets": markets}


def _h2h(home, away, p_home, p_draw, p_away):
    return {"key": "h2h", "outcomes": [
        {"name": home, "price": p_home},
        {"name": away, "price": p_away},
        {"name": "Draw", "price": p_draw},
    ]}


def _event(home="Alfa", away="Bravo", kickoff=KICKOFF, books=None):
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": kickoff,
        "bookmakers": books if books is not None else [
            _book("Pinnacle", [_h2h(home, away, 2.0, 3.4, 3.6)]),
        ],
    }


def _quote(price=2.0, book="Pinnacle", selection="1", market=MARKET,
           timestamp="2029-12-31T12:00:00Z", kickoff=KICKOFF,
           provider="The Odds API", event="ev-1", home="Alfa", away="Bravo"):
    return NormalizedQuote(
        event_id=event, provider=provider, sport_key="soccer_epl", league="EPL",
        home_team=home, away_team=away, kickoff=kickoff, bookmaker=book,
        market=market, selection=selection, price=price, timestamp=timestamp,
    )


def test_normalize_event_maps_1x2():
    quotes = normalize_event(_event(), "The Odds API", "2029-12-31T12:00:00Z")
    selections = {q.selection: q.price for q in quotes}
    assert selections == {"1": 2.0, "2": 3.6, "X": 3.4}
    assert all(q.market == MARKET for q in quotes)
    assert all(q.pre_kickoff for q in quotes)


def test_normalize_event_maps_totals_and_btts():
    event = _event(books=[_book("Bet365", [
        {"key": "totals", "outcomes": [
            {"name": "Over", "point": 2.5, "price": 1.9},
            {"name": "Under", "point": 2.5, "price": 2.0},
        ]},
        {"key": "btts", "outcomes": [
            {"name": "Yes", "price": 1.8},
            {"name": "No", "price": 2.05},
        ]},
    ])])
    quotes = normalize_event(event, "The Odds API", "2029-12-31T12:00:00Z")
    labels = {(q.market, q.selection) for q in quotes}
    assert ("Total de Gols", "Over 2.5") in labels
    assert ("Total de Gols", "Under 2.5") in labels
    assert ("Ambas Marcam", "BTTS Sim") in labels
    assert ("Ambas Marcam", "BTTS Nao") in labels


def test_normalize_event_maps_spreads_to_asian_handicap():
    event = _event(books=[_book("Pinnacle", [
        {"key": "spreads", "outcomes": [
            {"name": "Alfa", "point": -1.5, "price": 2.1},
            {"name": "Bravo", "point": 1.5, "price": 1.75},
        ]},
    ])])
    quotes = normalize_event(event, "The Odds API", "2029-12-31T12:00:00Z")
    labels = {q.selection for q in quotes}
    assert labels == {"AH Casa -1.5", "AH Fora +1.5"}


def test_normalize_event_skips_unknown_market():
    event = _event(books=[_book("Pinnacle", [
        {"key": "player_shots", "outcomes": [{"name": "X", "price": 2.0}]},
    ])])
    assert normalize_event(event, "The Odds API", "2029-12-31T12:00:00Z") == []


def test_normalize_event_requires_kickoff_and_teams():
    assert normalize_event({"home_team": "A", "away_team": "B"}, "p", "t") == []
    assert normalize_event(
        {"home_team": "A", "away_team": "B", "commence_time": "nao-e-data"},
        "p", "t",
    ) == []


def test_normalize_event_requires_timestamp():
    assert normalize_event(_event(), "The Odds API", "") == []


def test_normalize_event_fallback_timestamp_is_the_observation_instant():
    """Evento sem ``timestamp``: o ``fetched_at`` vira o instante da quote.

    O fallback e o INSTANTE DE OBSERVACAO (quando o dado foi visto), nao
    um timestamp da fonte: providers que nao publicam horario por evento
    (endpoint live gratuito) recebem o momento honesto da captura. A
    quote permanece pre-kickoff apenas se a observacao de fato precede o
    kickoff — o filtro temporal continua valendo.
    """
    quotes = normalize_event(_event(), "The Odds API", "2029-12-31T12:00:00Z")
    assert quotes
    assert {q.timestamp for q in quotes} == {"2029-12-31T12:00:00Z"}
    assert all(q.pre_kickoff for q in quotes)

    # Observacao apos o kickoff: a quote nao e utilizavel (o fallback
    # nao "corrige" a observacao para caber no contrato).
    late = normalize_event(_event(), "The Odds API", "2030-01-01T13:00:00Z")
    assert late
    assert all(not q.pre_kickoff for q in late)
    assert all(not q.usable for q in late)


def test_event_key_is_provider_independent():
    assert event_key("São Paulo", "Flamengo", KICKOFF) == event_key(
        "Sao Paulo FC", "CR Flamengo", KICKOFF
    )


def test_dedupe_keeps_latest_per_book():
    older = _quote(price=2.0, timestamp="2029-12-31T10:00:00Z")
    newer = _quote(price=1.9, timestamp="2029-12-31T11:00:00Z")
    other = _quote(price=2.1, book="Bet365")
    result = dedupe_quotes([older, newer, other])
    assert len(result) == 2
    pinnacle = next(q for q in result if q.bookmaker == "Pinnacle")
    assert pinnacle.price == 1.9


def test_dedupe_drops_post_kickoff_quotes():
    post = _quote(timestamp=KICKOFF, price=2.0)
    assert dedupe_quotes([post]) == []


def test_grouped_from_event_matches_providers_parser():
    event = _event(books=[_book("Pinnacle", [_h2h("Alfa", "Bravo", 2.0, 3.4, 3.6)])])
    assert grouped_from_event(event) == providers.odds_event_to_internal(event)


def test_normalize_events_counts_all_books():
    event = _event(books=[
        _book("Pinnacle", [_h2h("Alfa", "Bravo", 2.0, 3.4, 3.6)]),
        _book("Bet365", [_h2h("Alfa", "Bravo", 1.95, 3.3, 3.5)]),
    ])
    quotes = normalize_events([event], "The Odds API", "2029-12-31T12:00:00Z")
    assert {q.bookmaker for q in quotes} == {"Pinnacle", "Bet365"}
    assert len(quotes) == 6


# ==========================================================================
# 3. Providers: classificacao, retry, creditos, ParlayAPI
# ==========================================================================


@pytest.mark.parametrize(
    ("status", "kind", "retryable"),
    [
        (401, FAILURE_AUTH, False),
        (403, "FORBIDDEN", False),
        (402, FAILURE_NO_CREDITS, False),
        (429, FAILURE_RATE_LIMIT, True),
        (500, FAILURE_SERVER, True),
        (503, FAILURE_SERVER, True),
        (404, FAILURE_BAD_RESPONSE, False),
    ],
)
def test_classify_status(status, kind, retryable):
    assert classify_status(status) == (kind, retryable)


def test_provider_error_carries_classification():
    error = ProviderError("falhou", status=429, kind=FAILURE_RATE_LIMIT, retryable=True)
    assert error.status == 429
    assert error.kind == FAILURE_RATE_LIMIT
    assert error.retryable is True
    assert error.hard is False
    assert ProviderError("x", kind=FAILURE_AUTH).hard is True


def test_parse_credit_headers():
    headers = {
        "x-requests-remaining": "42",
        "x-requests-used": "8",
        "x-requests-last": "4",
        "content-type": "application/json",
    }
    assert parse_credit_headers(headers) == {"remaining": 42, "used": 8, "last": 4}


def test_parse_credit_headers_ignores_garbage():
    assert parse_credit_headers({"x-requests-remaining": "muitos"}) == {}


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(url, code, headers=None):
    return urllib.error.HTTPError(url, code, "erro", headers or {}, None)


def test_request_json_retries_rate_limit_then_succeeds(monkeypatch):
    calls = {"n": 0}
    slept: list[float] = []

    def fake_urlopen(req, timeout=20):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _http_error(req.full_url, 429, {"Retry-After": "0"})
        return _FakeResponse({"ok": True})

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    body, _headers = request_json_with_retry(
        "https://exemplo.invalid/odds", max_attempts=3, sleep=slept.append
    )
    assert body == {"ok": True}
    assert calls["n"] == 3
    assert slept == [0.0, 0.0]  # Retry-After respeitado


def test_request_json_stops_at_max_attempts(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=20):
        calls["n"] += 1
        raise _http_error(req.full_url, 503)

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ProviderError) as exc:
        request_json_with_retry(
            "https://exemplo.invalid/odds", max_attempts=2, sleep=lambda _s: None
        )
    assert calls["n"] == 2
    assert exc.value.kind == FAILURE_SERVER


def test_request_json_does_not_retry_hard_failure(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=20):
        calls["n"] += 1
        raise _http_error(req.full_url, 401)

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ProviderError) as exc:
        request_json_with_retry(
            "https://exemplo.invalid/odds", max_attempts=5, sleep=lambda _s: None
        )
    assert calls["n"] == 1
    assert exc.value.kind == FAILURE_AUTH
    assert exc.value.retryable is False


def test_parlay_from_env_requires_key_and_base(monkeypatch):
    monkeypatch.delenv("BETGSN_PARLAY_API_KEY", raising=False)
    monkeypatch.delenv("BETGSN_PARLAY_API_BASE", raising=False)
    assert providers.ParlayApiProvider.from_env() is None

    monkeypatch.setenv("BETGSN_PARLAY_API_KEY", "chave-teste")
    assert providers.ParlayApiProvider.from_env() is None  # falta a base

    monkeypatch.setenv("BETGSN_PARLAY_API_BASE", "https://parlay.invalid/api/")
    provider = providers.ParlayApiProvider.from_env()
    assert provider is not None
    assert provider.base_url == "https://parlay.invalid/api"
    assert provider.name == "ParlayAPI"


def test_parlay_events_accepts_known_shapes():
    assert providers._parlay_events([{"a": 1}]) == [{"a": 1}]
    assert providers._parlay_events({"data": [{"a": 1}]}) == [{"a": 1}]
    assert providers._parlay_events({"events": [{"a": 1}]}) == [{"a": 1}]
    with pytest.raises(ProviderError) as exc:
        providers._parlay_events({"formato": "desconhecido"})
    assert exc.value.kind == FAILURE_BAD_RESPONSE


def test_configured_odds_providers_respects_priority(monkeypatch):
    monkeypatch.setenv("BETGSN_ODDS_API_KEY", "k1")
    monkeypatch.setenv("BETGSN_PARLAY_API_KEY", "k2")
    monkeypatch.setenv("BETGSN_PARLAY_API_BASE", "https://parlay.invalid/api")
    names = [name for name, _ in providers.configured_odds_providers()]
    assert names == ["The Odds API", "ParlayAPI"]


def test_env_file_candidates_are_unique_and_include_local(monkeypatch):
    monkeypatch.delenv("BETGSN_ENV_FILE", raising=False)
    candidates = providers.env_file_candidates()
    assert candidates[0] == providers.ENV_FILE
    assert len(candidates) == len(set(candidates))


def test_env_file_candidates_respects_explicit_override(monkeypatch, tmp_path):
    explicit = tmp_path / "custom.env"
    monkeypatch.setenv("BETGSN_ENV_FILE", str(explicit))
    candidates = providers.env_file_candidates()
    assert candidates[0] == explicit


# ==========================================================================
# 4. Health e creditos
# ==========================================================================


def test_health_success_and_transient_failure():
    tracker = HealthTracker(unavailable_after=3)
    tracker.record_success("p", observations=5)
    assert tracker.state("p") == ProviderState.HEALTHY

    tracker.record_failure("p", FAILURE_RATE_LIMIT, "429")
    assert tracker.state("p") == ProviderState.DEGRADED
    tracker.record_failure("p", FAILURE_SERVER, "500")
    assert tracker.state("p") == ProviderState.DEGRADED
    tracker.record_failure("p", FAILURE_SERVER, "500")
    assert tracker.state("p") == ProviderState.UNAVAILABLE
    assert tracker.is_available("p") is False

    tracker.record_success("p")
    assert tracker.state("p") == ProviderState.HEALTHY
    assert tracker.is_available("p") is True


@pytest.mark.parametrize("kind", [FAILURE_AUTH, "FORBIDDEN", FAILURE_NO_CREDITS])
def test_hard_failures_make_provider_unavailable(kind):
    tracker = HealthTracker()
    tracker.record_failure("p", kind, "falha dura")
    assert tracker.state("p") == ProviderState.UNAVAILABLE
    assert tracker.is_available("p") is False


def test_no_coverage_and_stale_states():
    tracker = HealthTracker()
    tracker.record_no_coverage("p")
    assert tracker.state("p") == ProviderState.NO_COVERAGE
    tracker.mark_stale("p")
    assert tracker.state("p") == ProviderState.STALE
    assert tracker.is_available("p") is True


def test_health_snapshot_is_serializable():
    tracker = HealthTracker()
    tracker.record_success("p", observations=2, credits_remaining=10)
    payload = tracker.snapshot()["p"]
    assert payload["state"] == "HEALTHY"
    assert payload["credits_remaining"] == 10
    json.dumps(payload)  # nao pode conter objeto nao serializavel


def test_classify_exception():
    assert classify_exception(TimeoutError("x"))[0] == "TIMEOUT"
    assert classify_exception(OSError("x"))[0] == "CONNECTION"
    assert classify_exception(ValueError("x"))[0] == "UNKNOWN"
    assert classify_exception(ProviderError("x", kind="AUTH"))[0] == "AUTH"


def test_credit_controller_daily_limit():
    credits = CreditController()
    credits.register("p", daily_limit=2)
    assert credits.can_spend("p") is True
    credits.record_spend("p")
    assert credits.get("p").known_remaining == 1
    assert credits.can_spend("p") is True
    credits.record_spend("p")
    assert credits.get("p").known_remaining == 0
    assert credits.can_spend("p") is False


def test_credit_controller_headers_override_local_budget():
    credits = CreditController()
    credits.register("p", daily_limit=1000)
    remaining = credits.update_from_headers("p", {"x-requests-remaining": "0"})
    assert remaining == 0
    assert credits.can_spend("p") is False
    credits.update_from_headers("p", {"x-requests-remaining": "7"})
    assert credits.can_spend("p") is True


# ==========================================================================
# 5. Line shopping
# ==========================================================================


def test_line_shop_best_price_and_book_count():
    quotes = [
        _quote(price=2.0, book="Pinnacle"),
        _quote(price=2.1, book="Bet365"),
        _quote(price=2.05, book="Betano"),
    ]
    result = line_shop(quotes, MARKET, "1")
    assert result.best.price == 2.1
    assert result.best.bookmaker == "Bet365"
    assert result.worst.bookmaker == "Pinnacle"
    assert result.n_books == 3
    assert result.book_count == 3
    assert result.consensus_limited is False
    assert result.comparable is True


def test_line_shop_two_books_is_limited():
    quotes = [
        _quote(price=2.0, book="Pinnacle"),
        _quote(price=2.1, book="Bet365"),
    ]
    result = line_shop(quotes, MARKET, "1")
    assert result.n_books == 2
    assert result.book_count == 2
    assert result.consensus_limited is True
    assert "CONSENSUS_LIMITED" in result.warnings
    assert MIN_CONSENSUS_BOOKS == 3


def test_line_shop_single_book_is_limited():
    result = line_shop([_quote(price=2.0)], MARKET, "1")
    assert result.n_books == 1
    assert result.consensus_limited is True
    assert result.best.price == result.worst.price


def test_line_shop_flags_incomparable_timestamps():
    quotes = [
        _quote(price=2.0, book="Pinnacle", timestamp="2029-12-31T10:00:00Z"),
        _quote(price=2.2, book="Bet365", timestamp="2029-12-31T11:00:00Z"),
    ]
    result = line_shop(quotes, MARKET, "1")
    assert result.comparable is False
    assert "TIMESTAMPS_INCOMPARABLE" in result.warnings
    assert result.timestamp_span_seconds == pytest.approx(3600.0)


def test_line_shop_fair_odd_from_consensus():
    quotes = []
    for book, (home, draw, away) in {
        "Pinnacle": (2.0, 3.4, 3.6),
        "Bet365": (2.1, 3.5, 3.3),
        "Betano": (2.2, 3.6, 3.2),
    }.items():
        quotes.extend([
            _quote(price=home, book=book, selection="1"),
            _quote(price=draw, book=book, selection="X"),
            _quote(price=away, book=book, selection="2"),
        ])
    result = line_shop(quotes, MARKET, "1")
    assert result.fair_probability is not None
    assert 0.0 < result.fair_probability < 1.0
    assert result.edge == pytest.approx(result.best.price * result.fair_probability - 1)
    assert result.overround is not None and result.overround > 1.0


def test_market_analysis_complete_and_incomplete():
    complete = [
        _quote(price=2.0, selection="1"),
        _quote(price=3.4, selection="X"),
        _quote(price=3.6, selection="2"),
    ]
    analysis = market_analysis(complete, MARKET)
    assert analysis is not None
    assert analysis.complete is True
    assert sum(analysis.fair_probabilities.values()) == pytest.approx(1.0)

    incomplete = [_quote(price=2.0, selection="1")]
    partial = market_analysis(incomplete, MARKET)
    assert partial is not None
    assert partial.complete is False


def test_line_shopping_report_sorted_by_edge():
    quotes = []
    for book, (home, draw, away) in {
        "Pinnacle": (1.8, 3.6, 4.2),
        "Bet365": (2.0, 3.4, 4.0),
        "Betano": (2.2, 3.2, 3.8),
    }.items():
        quotes.extend([
            _quote(price=home, book=book, selection="1"),
            _quote(price=draw, book=book, selection="X"),
            _quote(price=away, book=book, selection="2"),
        ])
    report = line_shopping_report(quotes)
    assert report  # mercado completo, ha linhas
    edges = [r.edge for r in report if r.edge is not None]
    assert edges == sorted(edges, reverse=True)


# ==========================================================================
# 6. Arbitragem
# ==========================================================================


def test_detect_arbitrage_finds_positive_margin():
    quotes = [
        _quote(price=3.0, book="Pinnacle", selection="1"),
        _quote(price=4.0, book="Bet365", selection="X"),
        _quote(price=8.0, book="Betano", selection="2"),
    ]
    opportunities = detect_arbitrage(quotes)
    assert len(opportunities) == 1
    arb = opportunities[0]
    assert arb.margin > 0
    assert arb.total_implied < 1.0
    assert len(arb.legs) == 3
    assert {leg.selection for leg in arb.legs} == {"1", "X", "2"}
    assert sum(leg.stake for leg in arb.legs) == pytest.approx(1000.0, abs=0.05)


def test_detect_arbitrage_none_when_market_has_margin():
    quotes = [
        _quote(price=2.0, book="Pinnacle", selection="1"),
        _quote(price=3.0, book="Bet365", selection="X"),
        _quote(price=3.5, book="Betano", selection="2"),
    ]
    assert detect_arbitrage(quotes) == []


def test_detect_arbitrage_respects_min_books():
    quotes = [
        _quote(price=3.0, book="Pinnacle", selection="1"),
        _quote(price=4.0, book="Pinnacle", selection="X"),
        _quote(price=8.0, book="Pinnacle", selection="2"),
    ]
    assert detect_arbitrage(quotes, min_books=2) == []
    assert len(detect_arbitrage(quotes, min_books=1)) == 1


def test_detect_arbitrage_flags_incomparable_timestamps():
    quotes = [
        _quote(price=3.0, book="Pinnacle", selection="1",
               timestamp="2029-12-31T08:00:00Z"),
        _quote(price=4.0, book="Bet365", selection="X",
               timestamp="2029-12-31T08:00:00Z"),
        _quote(price=8.0, book="Betano", selection="2",
               timestamp="2029-12-31T11:00:00Z"),
    ]
    arb = detect_arbitrage(quotes)[0]
    assert arb.comparable is False
    assert "TIMESTAMPS_INCOMPARABLE" in arb.warnings


# ==========================================================================
# 7. Servico multi-provider: fallback, health, stale, creditos
# ==========================================================================


class FakeProvider:
    def __init__(self, events, headers=None, exc=None):
        self.events = events
        self.headers = headers or {}
        self.exc = exc
        self.calls = 0

    def live_odds_with_meta(self, sport_key, regions=None, markets=None):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.events, self.headers


class Clock:
    def __init__(self, value: str):
        self.value = value

    def __call__(self) -> str:
        return self.value


def _events(home="Alfa", away="Bravo"):
    return [_event(home, away)]


def test_service_uses_primary_provider():
    primary = FakeProvider(_events())
    secondary = FakeProvider([])
    service = OddsService(
        providers=[("The Odds API", primary), ("ParlayAPI", secondary)],
        now=Clock("2029-12-31T12:00:00Z"),
    )
    result = service.fetch("soccer_epl")
    assert result.ok is True
    assert result.provider == "The Odds API"
    assert result.fallback_used is False
    assert result.state == ProviderState.HEALTHY
    assert secondary.calls == 0
    assert result.bookmakers == ["Pinnacle"]


def test_service_falls_back_on_rate_limit():
    primary = FakeProvider([], exc=ProviderError("429", status=429,
                                                  kind=FAILURE_RATE_LIMIT, retryable=True))
    secondary = FakeProvider(_events())
    service = OddsService(
        providers=[("The Odds API", primary), ("ParlayAPI", secondary)],
        now=Clock("2029-12-31T12:00:00Z"),
    )
    result = service.fetch("soccer_epl")
    assert result.ok is True
    assert result.provider == "ParlayAPI"
    assert result.fallback_used is True
    assert result.attempts[0].status == "FAILED"
    assert result.attempts[0].kind == FAILURE_RATE_LIMIT
    assert service.health_snapshot()["The Odds API"]["state"] == "DEGRADED"


def test_service_skips_provider_after_hard_auth_failure():
    primary = FakeProvider([], exc=ProviderError("401", status=401, kind=FAILURE_AUTH))
    secondary = FakeProvider(_events())
    service = OddsService(
        providers=[("The Odds API", primary), ("ParlayAPI", secondary)],
        now=Clock("2029-12-31T12:00:00Z"),
    )
    first = service.fetch("soccer_epl")
    assert first.provider == "ParlayAPI"
    assert service.health_snapshot()["The Odds API"]["state"] == "UNAVAILABLE"

    calls_before = primary.calls
    second = service.fetch("soccer_epl")
    assert second.provider == "ParlayAPI"
    assert primary.calls == calls_before  # nao tentou de novo
    assert second.attempts[0].status == "SKIPPED"
    assert second.attempts[0].kind == "UNAVAILABLE"


def test_service_handles_timeout_with_fallback():
    primary = FakeProvider([], exc=TimeoutError("sem resposta"))
    secondary = FakeProvider(_events())
    service = OddsService(
        providers=[("The Odds API", primary), ("ParlayAPI", secondary)],
        now=Clock("2029-12-31T12:00:00Z"),
    )
    result = service.fetch("soccer_epl")
    assert result.provider == "ParlayAPI"
    assert result.attempts[0].kind == "TIMEOUT"
    assert service.health_snapshot()["The Odds API"]["state"] == "DEGRADED"


def test_service_no_coverage_tries_next_provider():
    primary = FakeProvider([])   # respondeu, mas sem eventos
    secondary = FakeProvider(_events())
    service = OddsService(
        providers=[("The Odds API", primary), ("ParlayAPI", secondary)],
        now=Clock("2029-12-31T12:00:00Z"),
    )
    result = service.fetch("soccer_epl")
    assert result.provider == "ParlayAPI"
    assert result.attempts[0].status == "NO_COVERAGE"
    assert service.health_snapshot()["The Odds API"]["state"] == "NO_COVERAGE"


def test_service_all_no_coverage_returns_no_coverage_state():
    service = OddsService(
        providers=[("The Odds API", FakeProvider([])),
                   ("ParlayAPI", FakeProvider([]))],
        now=Clock("2029-12-31T12:00:00Z"),
    )
    result = service.fetch("soccer_epl")
    assert result.quotes == []
    assert result.state == ProviderState.NO_COVERAGE
    assert result.ok is False


def test_service_all_fail_without_cache_is_unavailable():
    service = OddsService(
        providers=[("The Odds API", FakeProvider([], exc=ProviderError("boom")))],
        now=Clock("2029-12-31T12:00:00Z"),
    )
    result = service.fetch("soccer_epl")
    assert result.quotes == []
    assert result.state == ProviderState.UNAVAILABLE
    assert result.ok is False


def test_service_returns_stale_cache_never_as_current():
    clock = Clock("2029-12-31T12:00:00Z")
    primary = FakeProvider(_events())
    service = OddsService(
        providers=[("The Odds API", primary)],
        now=clock,
        stale_after_seconds=900.0,
    )
    fresh = service.fetch("soccer_epl")
    assert fresh.ok is True

    # 20 minutos depois o provider cai: o dado ja e velho
    primary.exc = ProviderError("503", status=503, kind=FAILURE_SERVER, retryable=True)
    clock.value = "2029-12-31T12:20:00Z"
    stale = service.fetch("soccer_epl")
    assert stale.stale is True
    assert stale.ok is False
    assert stale.state == ProviderState.STALE
    assert stale.fetched_at == "2029-12-31T12:00:00Z"  # instante real do dado
    assert stale.quotes  # ainda entrega, mas marcado


def test_service_fresh_cache_is_degraded_not_healthy():
    clock = Clock("2029-12-31T12:00:00Z")
    primary = FakeProvider(_events())
    service = OddsService(providers=[("The Odds API", primary)], now=clock)
    service.fetch("soccer_epl")

    primary.exc = ProviderError("boom")
    clock.value = "2029-12-31T12:05:00Z"  # 5 min < 15 min
    result = service.fetch("soccer_epl")
    assert result.stale is False
    assert result.state == ProviderState.DEGRADED
    assert result.ok is False


def test_service_respects_credit_exhaustion():
    service = OddsService(
        providers=[("The Odds API", FakeProvider(_events()))],
        now=Clock("2029-12-31T12:00:00Z"),
    )
    service._credits.register("The Odds API", daily_limit=1)
    first = service.fetch("soccer_epl")
    assert first.provider == "The Odds API"

    second = service.fetch("soccer_epl")
    assert second.attempts[0].status == "SKIPPED"
    assert second.attempts[0].kind == "NO_CREDITS"


def test_service_updates_credits_from_headers():
    primary = FakeProvider(_events(), headers={"x-requests-remaining": "17"})
    service = OddsService(
        providers=[("The Odds API", primary)],
        now=Clock("2029-12-31T12:00:00Z"),
    )
    result = service.fetch("soccer_epl")
    assert result.credits_remaining == 17
    assert service.credits_snapshot()["The Odds API"]["remaining"] == 17


def test_odds_service_has_no_persistence_path():
    """I-04: o OddsService NAO persiste odds — writer unico e a captura.

    A persistencia operacional do store SQLite pertence exclusivamente ao
    `LiveOddsCapture` (CLI `--capture-odds`). O contrato deste teste impede
    o retorno de um caminho de escrita paralelo no OddsService.
    """
    import inspect

    from betgsn.odds_service import OddsService

    init_params = inspect.signature(OddsService.__init__).parameters
    assert "store" not in init_params, "OddsService nao deve aceitar store"

    fetch_params = inspect.signature(OddsService.fetch).parameters
    assert "persist" not in fetch_params, "fetch nao deve ter flag de persistencia"
    assert not hasattr(OddsService, "_persist"), "sem metodo de persistencia"


def test_service_status_has_no_secret():
    service = OddsService(
        providers=[("The Odds API", FakeProvider(_events()))],
        now=Clock("2029-12-31T12:00:00Z"),
    )
    service.fetch("soccer_epl")
    payload = json.dumps(service.status())
    assert "apiKey" not in payload


# ==========================================================================
# 8. value_strategy: book count e consenso limitado
# ==========================================================================


def _value_event(n_books: int) -> dict:
    prices = [(1.20, 12.0, 6.0), (1.25, 13.0, 6.5), (1.22, 14.0, 7.0)]
    books = [
        {"key": f"b{i}", "title": f"b{i}", "markets": [{
            "key": "h2h", "outcomes": [
                {"name": "A", "price": prices[i][0]},
                {"name": "B", "price": prices[i][1]},
                {"name": "Draw", "price": prices[i][2]},
            ]}]}
        for i in range(n_books)
    ]
    return {
        "home_team": "A", "away_team": "B",
        "commence_time": "2030-01-01T12:00:00Z", "bookmakers": books,
    }


def test_value_strategy_three_books_is_not_limited():
    from betgsn.value_strategy import scan_events

    opportunities = scan_events([_value_event(3)])
    assert opportunities
    assert all(o.book_count == 3 for o in opportunities)
    assert all(o.consensus_limited is False for o in opportunities)


def test_value_strategy_two_books_is_limited():
    from betgsn.value_strategy import scan_events

    # min_books=2 permite avaliar, mas o consenso continua marcado.
    opportunities = scan_events([_value_event(2)], min_books=2)
    assert opportunities
    assert all(o.book_count == 2 for o in opportunities)
    assert all(o.consensus_limited is True for o in opportunities)


def test_value_strategy_one_book_needs_explicit_min_books():
    from betgsn.value_strategy import scan_events

    assert scan_events([_value_event(1)]) == []  # default exige 3 casas
    opportunities = scan_events([_value_event(1)], min_books=1)
    assert opportunities
    assert all(o.book_count == 1 for o in opportunities)
    assert all(o.consensus_limited is True for o in opportunities)
