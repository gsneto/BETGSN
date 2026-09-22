"""Testes de betgsn.features.movement — movimento de odds sem leakage."""
from __future__ import annotations

import math

import pytest

from betgsn.features.movement import (
    PricePoint,
    book_count,
    consensus_limited,
    is_stale,
    latest_quote_age_minutes,
    movement_feature_block,
    movement_features,
    odds_before,
    odds_history_before,
)
from betgsn.features.odds import MIN_CONSENSUS_BOOKS as ODDS_MIN_BOOKS
from betgsn.features.odds import OddsQuote, odds_features

MARKET = "Resultado Final (1X2)"
KICKOFF = "2025-05-05T14:00:00Z"

FEATURE_NAMES = (
    "opening_odds",
    "current_odds",
    "price_delta",
    "price_delta_pct",
    "implied_probability_delta",
    "minutes_since_open",
    "minutes_to_kickoff",
    "book_consensus_move",
    "book_dispersion",
    "best_price_move",
    "market_direction",
    "movement_velocity",
    "n_observations",
    "n_books",
)


def point(
    odd: float,
    timestamp: str,
    bookmaker: str = "bet365",
    market: str = MARKET,
    outcome: str = "1",
) -> PricePoint:
    return PricePoint(
        bookmaker=bookmaker,
        market=market,
        outcome=outcome,
        odd=odd,
        timestamp=timestamp,
    )


# --------------------------------------------------------------- PricePoint


@pytest.mark.parametrize("bad_odd", [1.0, 0.5, 0.0, -1.0])
def test_price_point_rejects_odd_not_above_one(bad_odd):
    with pytest.raises(ValueError):
        point(bad_odd, "2025-05-05T12:00:00Z")


def test_price_point_rejects_nan_odd():
    with pytest.raises(ValueError):
        point(float("nan"), "2025-05-05T12:00:00Z")


@pytest.mark.parametrize("bad_odd", [float("inf"), float("-inf")])
def test_price_point_rejects_infinite_odd(bad_odd):
    with pytest.raises(ValueError):
        point(bad_odd, "2025-05-05T12:00:00Z")


def test_price_point_rejects_empty_timestamp():
    with pytest.raises(ValueError):
        point(2.0, "")


def test_price_point_rejects_unparseable_timestamp():
    with pytest.raises(ValueError):
        point(2.0, "ontem a tarde")


def test_price_point_accepts_valid_quote():
    p = point(1.95, "2025-05-05T12:00:00Z")
    assert p.odd == 1.95
    assert p.outcome == "1"


# --------------------------------------------------------------- odds_before


def test_odds_before_is_strict():
    points = [
        point(2.2, "2025-05-05T11:00:00Z"),
        point(2.1, "2025-05-05T12:00:00Z"),
        point(2.0, "2025-05-05T13:00:00Z"),
    ]

    visible = odds_before(points, "2025-05-05T12:00:00Z")

    assert [p.odd for p in visible] == [2.2]
    assert all(p.timestamp < "2025-05-05T12:00:00Z" for p in visible)


def test_odds_before_empty_when_cutoff_precedes_everything():
    points = [point(2.0, "2025-05-05T13:00:00Z")]
    assert odds_before(points, "2025-05-05T09:00:00Z") == []


def test_odds_before_normalizes_timezones():
    # 10:00-03:00 == 13:00Z, portanto anterior ao cutoff de 13:30Z.
    points = [point(2.0, "2025-05-05T10:00:00-03:00")]
    assert len(odds_before(points, "2025-05-05T13:30:00Z")) == 1
    assert odds_before(points, "2025-05-05T12:30:00Z") == []


# --------------------------------------------------------- odds_history_before


def test_odds_history_before_filters_market_and_outcome():
    points = [
        point(2.0, "2025-05-05T12:00:00Z", outcome="1"),
        point(3.4, "2025-05-05T12:00:00Z", outcome="X"),
        point(2.5, "2025-05-05T12:00:00Z", outcome="1", market="Ambas Marcam"),
    ]

    history = odds_history_before(points, "2025-05-05T13:00:00Z", MARKET, "1")

    assert len(history) == 1
    assert history[0].odd == 2.0
    assert history[0].market == MARKET
    assert history[0].outcome == "1"


def test_odds_history_before_returns_sorted_order():
    points = [
        point(2.0, "2025-05-05T13:00:00Z"),
        point(2.2, "2025-05-05T11:00:00Z"),
        point(2.1, "2025-05-05T12:00:00Z"),
    ]

    history = odds_history_before(points, KICKOFF, MARKET, "1")

    assert [p.odd for p in history] == [2.2, 2.1, 2.0]
    stamps = [p.timestamp for p in history]
    assert stamps == sorted(stamps)


def test_odds_history_before_respects_cutoff():
    points = [
        point(2.2, "2025-05-05T11:00:00Z"),
        point(1.5, "2025-05-05T13:00:00Z"),
    ]
    history = odds_history_before(points, "2025-05-05T13:00:00Z", MARKET, "1")
    assert [p.odd for p in history] == [2.2]


# --------------------------------------------------------------- sem historico


def test_movement_features_all_none_without_history():
    points = [point(2.0, "2025-05-05T13:00:00Z")]

    features = movement_features(
        points, MARKET, "1", "2025-05-05T10:00:00Z", KICKOFF
    )

    assert set(features) == set(FEATURE_NAMES)
    assert all(value is None for value in features.values())


def test_movement_features_all_none_for_unknown_outcome():
    points = [point(2.0, "2025-05-05T11:00:00Z", outcome="1")]
    features = movement_features(
        points, MARKET, "2", "2025-05-05T13:00:00Z", KICKOFF
    )
    assert all(value is None for value in features.values())


def test_movement_features_empty_points():
    features = movement_features([], MARKET, "1", "2025-05-05T13:00:00Z", KICKOFF)
    assert all(value is None for value in features.values())


# --------------------------------------------------------------- LEAKAGE


def test_future_quote_does_not_affect_current_odds():
    """Critico: cotacao posterior a previsao nao pode entrar nas features."""
    points = [
        point(2.10, "2025-05-05T10:00:00Z"),
        point(1.95, "2025-05-05T12:00:00Z"),   # ultima visivel
        point(1.50, "2025-05-05T13:30:00Z"),   # futuro: invisivel
    ]

    features = movement_features(
        points, MARKET, "1", "2025-05-05T12:30:00Z", KICKOFF
    )

    assert features["current_odds"] == 1.95
    assert features["current_odds"] != 1.50
    assert features["opening_odds"] == 2.10
    assert features["n_observations"] == 2.0


def test_future_quote_does_not_leak_into_any_feature():
    visible_only = [
        point(2.10, "2025-05-05T10:00:00Z"),
        point(1.95, "2025-05-05T12:00:00Z"),
    ]
    with_future = visible_only + [point(1.50, "2025-05-05T13:30:00Z")]

    cutoff = "2025-05-05T12:30:00Z"
    a = movement_features(visible_only, MARKET, "1", cutoff, KICKOFF)
    b = movement_features(with_future, MARKET, "1", cutoff, KICKOFF)

    assert a == b  # adicionar o futuro nao muda NADA


def test_cutoff_is_clamped_to_kickoff():
    """Previsao depois do kickoff nao habilita cotacoes pos-kickoff."""
    points = [
        point(2.10, "2025-05-05T10:00:00Z"),
        point(1.95, "2025-05-05T13:00:00Z"),
        point(1.20, "2025-05-05T15:00:00Z"),  # depois do kickoff
    ]

    features = movement_features(
        points, MARKET, "1", "2025-05-05T18:00:00Z", KICKOFF
    )

    assert features["minutes_to_kickoff"] == 0.0
    assert features["current_odds"] == 1.95
    assert features["current_odds"] != 1.20
    assert features["n_observations"] == 2.0


def test_quote_exactly_at_kickoff_is_excluded():
    points = [
        point(2.10, "2025-05-05T10:00:00Z"),
        point(1.50, KICKOFF),
    ]
    features = movement_features(points, MARKET, "1", KICKOFF, KICKOFF)

    assert features["current_odds"] == 2.10
    assert features["n_observations"] == 1.0


# --------------------------------------------------------------- deltas


def test_price_delta_against_first_visible_quote():
    points = [
        point(2.00, "2025-05-05T10:00:00Z"),  # abertura visivel
        point(2.20, "2025-05-05T11:00:00Z"),
        point(2.50, "2025-05-05T12:00:00Z"),  # atual
    ]

    features = movement_features(
        points, MARKET, "1", "2025-05-05T13:00:00Z", KICKOFF
    )

    assert features["opening_odds"] == 2.00
    assert features["current_odds"] == 2.50
    assert features["price_delta"] == pytest.approx(0.50)
    assert features["price_delta_pct"] == pytest.approx(0.25)


def test_price_delta_uses_first_VISIBLE_quote_not_absolute_first():
    points = [
        point(3.00, "2025-05-05T08:00:00Z"),  # antes do inicio da janela
        point(2.00, "2025-05-05T10:00:00Z"),
        point(2.50, "2025-05-05T12:00:00Z"),
    ]

    # Sem corte: abertura e 3.00.
    full = movement_features(points, MARKET, "1", "2025-05-05T13:00:00Z", KICKOFF)
    assert full["opening_odds"] == 3.00
    assert full["price_delta"] == pytest.approx(-0.50)

    # Com corte anterior: a primeira visivel passa a ser 2.00.
    recent = [p for p in points if p.timestamp >= "2025-05-05T10:00:00Z"]
    cut = movement_features(recent, MARKET, "1", "2025-05-05T13:00:00Z", KICKOFF)
    assert cut["opening_odds"] == 2.00
    assert cut["price_delta"] == pytest.approx(0.50)


def test_implied_probability_delta_sign():
    points = [
        point(2.00, "2025-05-05T10:00:00Z"),
        point(2.50, "2025-05-05T12:00:00Z"),
    ]
    features = movement_features(
        points, MARKET, "1", "2025-05-05T13:00:00Z", KICKOFF
    )
    # Odd subiu -> probabilidade implicita caiu.
    assert features["implied_probability_delta"] == pytest.approx(1 / 2.5 - 1 / 2.0)
    assert features["implied_probability_delta"] < 0


def test_minutes_since_open_and_velocity():
    points = [
        point(2.00, "2025-05-05T10:00:00Z"),
        point(2.60, "2025-05-05T12:00:00Z"),
    ]
    features = movement_features(
        points, MARKET, "1", "2025-05-05T13:00:00Z", KICKOFF
    )

    assert features["minutes_since_open"] == 120.0
    assert features["minutes_to_kickoff"] == 60.0
    assert features["movement_velocity"] == pytest.approx(0.6 / 120.0)


def test_velocity_none_with_single_observation():
    points = [point(2.00, "2025-05-05T10:00:00Z")]
    features = movement_features(
        points, MARKET, "1", "2025-05-05T13:00:00Z", KICKOFF
    )
    assert features["minutes_since_open"] == 0.0
    assert features["movement_velocity"] is None


# --------------------------------------------------------- market_direction


def test_market_direction_up():
    points = [
        point(2.00, "2025-05-05T10:00:00Z"),
        point(2.30, "2025-05-05T12:00:00Z"),
    ]
    features = movement_features(
        points, MARKET, "1", "2025-05-05T13:00:00Z", KICKOFF
    )
    assert features["market_direction"] == 1.0
    assert features["price_delta"] > 0


def test_market_direction_down():
    points = [
        point(2.30, "2025-05-05T10:00:00Z"),
        point(2.00, "2025-05-05T12:00:00Z"),
    ]
    features = movement_features(
        points, MARKET, "1", "2025-05-05T13:00:00Z", KICKOFF
    )
    assert features["market_direction"] == -1.0
    assert features["price_delta"] < 0


def test_market_direction_flat():
    points = [
        point(2.00, "2025-05-05T10:00:00Z"),
        point(2.00, "2025-05-05T12:00:00Z"),
    ]
    features = movement_features(
        points, MARKET, "1", "2025-05-05T13:00:00Z", KICKOFF
    )
    assert features["market_direction"] == 0.0
    assert features["price_delta"] == pytest.approx(0.0)


# --------------------------------------------------------------- dispersao


def test_book_dispersion_none_with_single_bookmaker():
    points = [
        point(2.00, "2025-05-05T10:00:00Z", bookmaker="bet365"),
        point(2.10, "2025-05-05T12:00:00Z", bookmaker="bet365"),
    ]
    features = movement_features(
        points, MARKET, "1", "2025-05-05T13:00:00Z", KICKOFF
    )

    assert features["book_dispersion"] is None
    assert features["n_books"] == 1.0


def test_book_dispersion_positive_with_several_bookmakers():
    points = [
        point(2.00, "2025-05-05T10:00:00Z", bookmaker="bet365"),
        point(2.20, "2025-05-05T10:00:00Z", bookmaker="pinnacle"),
        point(2.50, "2025-05-05T10:00:00Z", bookmaker="betfair"),
    ]
    features = movement_features(
        points, MARKET, "1", "2025-05-05T13:00:00Z", KICKOFF
    )

    dispersion = features["book_dispersion"]
    assert isinstance(dispersion, float)
    assert dispersion > 0.0
    assert features["n_books"] == 3.0


def test_book_dispersion_zero_when_books_agree():
    points = [
        point(2.00, "2025-05-05T10:00:00Z", bookmaker="bet365"),
        point(2.00, "2025-05-05T10:00:00Z", bookmaker="pinnacle"),
    ]
    features = movement_features(
        points, MARKET, "1", "2025-05-05T13:00:00Z", KICKOFF
    )
    assert features["book_dispersion"] == pytest.approx(0.0)


def test_book_consensus_and_best_price_move():
    points = [
        point(2.00, "2025-05-05T10:00:00Z", bookmaker="bet365"),
        point(2.20, "2025-05-05T10:00:00Z", bookmaker="pinnacle"),
        point(2.10, "2025-05-05T12:00:00Z", bookmaker="bet365"),
        point(2.40, "2025-05-05T12:00:00Z", bookmaker="pinnacle"),
    ]
    features = movement_features(
        points, MARKET, "1", "2025-05-05T13:00:00Z", KICKOFF
    )

    # media final (2.25) - media inicial (2.10) = 0.15
    assert features["book_consensus_move"] == pytest.approx(0.15)
    # melhor final (2.40) - melhor inicial (2.20) = 0.20
    assert features["best_price_move"] == pytest.approx(0.20)
    assert features["n_observations"] == 4.0
    assert features["n_books"] == 2.0


# --------------------------------------------------------- feature block


def test_movement_feature_block_has_42_keys():
    points = [
        point(2.00, "2025-05-05T10:00:00Z", outcome="1"),
        point(3.40, "2025-05-05T10:00:00Z", outcome="X"),
        point(3.80, "2025-05-05T10:00:00Z", outcome="2"),
    ]

    block = movement_feature_block(points, "2025-05-05T13:00:00Z", KICKOFF)

    assert len(block) == 42  # 14 features x 3 resultados


def test_movement_feature_block_prefixes():
    points = [point(2.00, "2025-05-05T10:00:00Z", outcome="1")]
    block = movement_feature_block(points, "2025-05-05T13:00:00Z", KICKOFF)

    for tag in ("home", "draw", "away"):
        keys = [k for k in block if k.startswith(f"movement_{tag}_")]
        assert len(keys) == 14
        for name in FEATURE_NAMES:
            assert f"movement_{tag}_{name}" in block


def test_movement_feature_block_maps_outcomes_correctly():
    points = [
        point(2.00, "2025-05-05T10:00:00Z", outcome="1"),
        point(3.40, "2025-05-05T10:00:00Z", outcome="X"),
        point(3.80, "2025-05-05T10:00:00Z", outcome="2"),
    ]

    block = movement_feature_block(points, "2025-05-05T13:00:00Z", KICKOFF)

    assert block["movement_home_current_odds"] == 2.00
    assert block["movement_draw_current_odds"] == 3.40
    assert block["movement_away_current_odds"] == 3.80


def test_movement_feature_block_none_for_missing_outcome():
    points = [point(2.00, "2025-05-05T10:00:00Z", outcome="1")]
    block = movement_feature_block(points, "2025-05-05T13:00:00Z", KICKOFF)

    assert block["movement_home_current_odds"] == 2.00
    assert block["movement_draw_current_odds"] is None
    assert block["movement_away_current_odds"] is None


def test_movement_feature_block_does_not_leak_future():
    visible = [point(1.95, "2025-05-05T12:00:00Z", outcome="1")]
    with_future = visible + [point(1.50, "2025-05-05T13:30:00Z", outcome="1")]

    cutoff = "2025-05-05T12:30:00Z"
    a = movement_feature_block(visible, cutoff, KICKOFF)
    b = movement_feature_block(with_future, cutoff, KICKOFF)

    assert a == b
    assert b["movement_home_current_odds"] == 1.95


def test_movement_feature_block_values_are_float_or_none():
    points = [
        point(2.00, "2025-05-05T10:00:00Z", outcome="1"),
        point(2.10, "2025-05-05T12:00:00Z", outcome="1", bookmaker="pinnacle"),
    ]
    block = movement_feature_block(points, "2025-05-05T13:00:00Z", KICKOFF)

    for key, value in block.items():
        assert value is None or isinstance(value, float), key
        if isinstance(value, float):
            assert math.isfinite(value), key


# --------------------------------------------------------- book count / stale


def test_book_count_counts_distinct_bookmakers():
    points = [
        point(2.00, "2025-05-05T10:00:00Z", bookmaker="bet365"),
        point(2.05, "2025-05-05T11:00:00Z", bookmaker="bet365"),
        point(2.10, "2025-05-05T10:00:00Z", bookmaker="pinnacle"),
    ]
    assert book_count(points, MARKET, "1", KICKOFF) == 2
    assert book_count(points, MARKET, "X", KICKOFF) == 0


def test_book_count_respects_cutoff():
    points = [
        point(2.00, "2025-05-05T10:00:00Z", bookmaker="bet365"),
        point(2.10, "2025-05-05T13:00:00Z", bookmaker="pinnacle"),
    ]
    assert book_count(points, MARKET, "1", "2025-05-05T12:00:00Z") == 1


def test_consensus_limited_threshold():
    assert consensus_limited(1) is True
    assert consensus_limited(2) is True
    assert consensus_limited(3) is False
    assert ODDS_MIN_BOOKS == 3


def test_latest_quote_age_minutes():
    points = [point(2.00, "2025-05-05T12:00:00Z")]
    assert latest_quote_age_minutes(points, "2025-05-05T12:30:00Z") == 30.0
    assert latest_quote_age_minutes([], "2025-05-05T12:30:00Z") is None


def test_is_stale_true_without_data_or_when_old():
    points = [point(2.00, "2025-05-05T12:00:00Z")]
    assert is_stale(points, "2025-05-05T12:10:00Z", max_age_minutes=15) is False
    assert is_stale(points, "2025-05-05T12:30:00Z", max_age_minutes=15) is True
    assert is_stale([], "2025-05-05T12:30:00Z") is True


# --------------------------------------------------------- features de odds


def test_odds_features_marks_limited_consensus_with_two_books():
    quotes = [
        OddsQuote("b1", "1", 2.0, "2025-05-05T10:00:00Z"),
        OddsQuote("b2", "1", 2.1, "2025-05-05T10:00:00Z"),
    ]
    features = odds_features(quotes, "2025-05-05T11:00:00Z", KICKOFF)
    entry = features["1x2_1"]
    assert entry["book_count"] == 2
    assert entry["consensus_limited"] is True
    assert entry["best_book"] == "b2"
    assert entry["best"] == 2.1


def test_odds_features_full_market_has_fair_odd():
    quotes = []
    for book, (home, draw, away) in {
        "b1": (1.8, 3.6, 4.2),
        "b2": (2.0, 3.4, 4.0),
        "b3": (2.2, 3.2, 3.8),
    }.items():
        quotes.extend([
            OddsQuote(book, "1", home, "2025-05-05T10:00:00Z"),
            OddsQuote(book, "X", draw, "2025-05-05T10:00:00Z"),
            OddsQuote(book, "2", away, "2025-05-05T10:00:00Z"),
        ])
    features = odds_features(quotes, "2025-05-05T11:00:00Z", KICKOFF)
    entry = features["1x2_1"]
    assert entry["book_count"] == 3
    assert entry["consensus_limited"] is False
    assert entry["fair_probability"] is not None
    assert entry["fair_odd"] == pytest.approx(1.0 / entry["fair_probability"])
    assert entry["edge_vs_fair"] == pytest.approx(
        entry["best"] * entry["fair_probability"] - 1.0
    )
    assert entry["overround"] is not None and entry["overround"] > 1.0


def test_odds_features_never_sees_future_quotes():
    quotes = [
        OddsQuote("b1", "1", 2.0, "2025-05-05T10:00:00Z"),
        OddsQuote("b1", "1", 9.0, "2025-05-05T13:30:00Z"),  # futuro
    ]
    features = odds_features(quotes, "2025-05-05T11:00:00Z", KICKOFF)
    assert features["1x2_1"]["best"] == 2.0


# --------------------------------------------------- contrato de suficiencia


def test_single_observation_yields_no_movement_not_zero():
    """I-11: uma observacao so nao mede movimento.

    price_delta 0.0 leria-se como "preco estavel" — uma afirmacao forte
    sem evidencia. Os campos de MOVIMENTO ficam None; o dado observado
    (abertura/atual, janelas, contagens) continua presente.
    """
    points = [point(2.00, "2025-05-05T10:00:00Z")]
    features = movement_features(
        points, MARKET, "1", "2025-05-05T13:00:00Z", KICKOFF
    )
    assert features["n_observations"] == 1.0
    assert features["opening_odds"] == 2.00
    assert features["current_odds"] == 2.00
    for name in ("price_delta", "price_delta_pct", "implied_probability_delta",
                 "book_consensus_move", "best_price_move", "market_direction",
                 "movement_velocity"):
        assert features[name] is None, name


def test_line_is_median_across_books_like_the_store():
    """I-11: a linha canônica e a MEDIANA entre casas.

    A mesma definicao de `OddsSnapshotStore.movement` (primeira/ultima
    observacao de cada casa, mediana entre elas) — nunca a primeira e a
    ultima cotacao crua, que deixar uma casa atrasada ditar o preco.
    """
    points = [
        # casa A: 2.00 -> 2.10 (atrasada)
        point(2.00, "2025-05-05T10:00:00Z", bookmaker="bet365"),
        point(2.10, "2025-05-05T12:00:00Z", bookmaker="bet365"),
        # casa B: 2.20 -> 2.40
        point(2.20, "2025-05-05T10:00:00Z", bookmaker="pinnacle"),
        point(2.40, "2025-05-05T12:00:00Z", bookmaker="pinnacle"),
    ]
    features = movement_features(
        points, MARKET, "1", "2025-05-05T13:00:00Z", KICKOFF
    )
    # mediana das aberturas: median(2.00, 2.20) = 2.10
    assert features["opening_odds"] == 2.10
    # mediana das atuais: median(2.10, 2.40) = 2.25
    assert features["current_odds"] == 2.25
    assert features["price_delta"] == pytest.approx(0.15)
