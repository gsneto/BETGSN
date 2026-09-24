"""Estado incremental + market views do terminal em tempo real.

Contratos testados:
- uma quote por (evento, mercado, resultado, casa): timestamp posterior
  substitui em MEMORIA (o store continua append-only);
- quote no futuro (acima do skew) e REJEITADA e vira problema de
  qualidade — nunca entra em sinal;
- evento sem casamento com fixture fica UNMATCHED (flag explicita);
- views: best/second/worst/median/mean/dispersion/n_books, fair via
  devig da mediana, overround, freshness por casa.
"""

from __future__ import annotations

from datetime import datetime, timezone

from betgsn.odds_normalize import NormalizedQuote, event_key
from betgsn.realtime.freshness import FreshnessThresholds
from betgsn.realtime.state import MarketState
from betgsn.realtime.views import (
    build_event_view,
    build_market_view,
    build_selection_view,
    event_views,
    match_status,
)

NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
KICKOFF = "2026-09-28T19:00:00Z"
MARKET = "Resultado Final (1X2)"

THRESHOLDS = FreshnessThresholds(
    fresh_seconds=300, recent_seconds=900, stale_seconds=3600
)


def quote(
    bookmaker: str,
    price: float,
    timestamp: str,
    selection: str = "1",
    market: str = MARKET,
    event_id: str = "",
) -> NormalizedQuote:
    return NormalizedQuote(
        event_id=event_id or event_key("Lens", "Lyon", KICKOFF),
        provider="ParlayAPI",
        sport_key="soccer_france_ligue_one",
        league="Ligue 1",
        home_team="Lens",
        away_team="Lyon",
        kickoff=KICKOFF,
        bookmaker=bookmaker,
        market=market,
        selection=selection,
        price=price,
        timestamp=timestamp,
    )


def test_latest_price_replaces_in_memory_only():
    state = MarketState()
    state.apply(
        [quote("Pinnacle", 2.10, "2026-09-24T11:00:00Z")], now=NOW
    )
    state.apply(
        [quote("Pinnacle", 2.05, "2026-09-24T11:30:00Z")], now=NOW
    )
    key = (
        event_key("Lens", "Lyon", KICKOFF), MARKET, "1", "Pinnacle"
    )
    assert state.latest[key].price == 2.05
    assert len(state.history_for(key)) == 2


def test_duplicate_quote_does_not_duplicate_history():
    state = MarketState()
    q = quote("Pinnacle", 2.10, "2026-09-24T11:00:00Z")
    state.apply([q], now=NOW)
    state.apply([q], now=NOW)
    key = (
        event_key("Lens", "Lyon", KICKOFF), MARKET, "1", "Pinnacle"
    )
    assert len(state.history_for(key)) == 1


def test_future_quote_rejected_as_quality_problem():
    state = MarketState()
    future = quote("Pinnacle", 2.10, "2026-09-24T13:00:00Z")
    events, lines, problems = state.apply([future], now=NOW)
    assert not events and not lines
    assert len(problems) == 1
    assert problems[0].reason == "FUTURE_TIMESTAMP"
    assert state.problems_count == 1


def test_unmatched_event_flagged_not_hidden():
    state = MarketState()
    provider_key = event_key("Weird FC", "Odd United", KICKOFF)
    q = quote(
        "Pinnacle", 2.10, "2026-09-24T11:00:00Z", event_id=provider_key
    )
    state.apply([q], now=NOW, matched_keys=set())
    meta = state.events[provider_key]
    assert meta.matched is False


def test_selection_view_structure():
    quotes = [
        quote("Book A", 2.10, "2026-09-24T11:59:00Z"),
        quote("Book B", 2.08, "2026-09-24T11:58:00Z"),
        quote("Book C", 2.16, "2026-09-24T11:57:00Z"),
    ]
    view = build_selection_view(quotes, "1", THRESHOLDS, NOW)
    assert view is not None
    assert view.best.bookmaker == "Book C"
    assert view.best.price == 2.16
    assert view.second_best is not None and view.second_best.price == 2.10
    assert view.worst.price == 2.08
    assert view.n_books == 3
    assert abs(view.median - 2.10) < 1e-9
    assert view.dispersion is not None and view.dispersion > 0
    assert view.best_vs_median is not None and view.best_vs_median > 0
    assert view.books[0].freshness == "FRESH"


def test_market_view_fair_probabilities_sum_to_one():
    quotes = []
    for book, prices in {
        "Book A": (2.10, 3.40, 3.10),
        "Book B": (2.08, 3.45, 3.12),
        "Book C": (2.12, 3.35, 3.05),
    }.items():
        for selection, price in zip(("1", "X", "2"), prices):
            quotes.append(
                quote(book, price, "2026-09-24T11:59:00Z", selection=selection)
            )
    view = build_market_view(
        quotes,
        event_key("Lens", "Lyon", KICKOFF),
        MARKET,
        THRESHOLDS,
        NOW,
    )
    assert view is not None
    assert view.complete
    assert view.n_books == 3
    assert abs(sum(view.fair_probabilities.values()) - 1.0) < 1e-6
    assert view.overround is not None and view.overround > 1.0
    assert view.fair_probabilities["1"] > 0


def test_event_view_contains_markets_and_status():
    state = MarketState()
    state.apply(
        [
            quote("Book A", 2.10, "2026-09-24T11:59:00Z"),
            quote("Book A", 3.40, "2026-09-24T11:59:00Z", selection="X"),
        ],
        now=NOW,
    )
    view = build_event_view(
        state, event_key("Lens", "Lyon", KICKOFF), THRESHOLDS, NOW
    )
    assert view is not None
    assert view.home == "Lens" and view.away == "Lyon"
    assert view.status == "PRE_MATCH"
    assert any(m.market == MARKET for m in view.markets)
    assert view.matched is True


def test_event_views_excludes_nothing_and_sorts_by_kickoff():
    state = MarketState()
    state.apply(
        [quote("Book A", 2.10, "2026-09-24T11:59:00Z")], now=NOW
    )
    views = event_views(state, THRESHOLDS, NOW)
    assert len(views) == 1


def test_match_status_transitions():
    assert match_status(KICKOFF, NOW) == "PRE_MATCH"
    during = datetime(2026, 9, 28, 19, 30, tzinfo=timezone.utc)
    assert match_status(KICKOFF, during) == "IN_PLAY"
    after = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    assert match_status(KICKOFF, after) == "FINISHED"
    assert match_status("", NOW) == "UNKNOWN"
