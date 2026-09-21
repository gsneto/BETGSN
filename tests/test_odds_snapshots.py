"""Testes de betgsn.odds_snapshots — append-only, point-in-time e CLV."""
from __future__ import annotations

import pytest

from betgsn.odds_snapshots import (
    CLVCoverage,
    OddsObservation,
    OddsSnapshotStore,
)

KICKOFF = "2025-05-05T14:00:00Z"
MATCH = "match-001"
MARKET = "Resultado Final (1X2)"


def obs(
    odd: float = 2.0,
    timestamp: str = "2025-05-05T13:00:00Z",
    bookmaker: str = "bet365",
    outcome: str = "1",
    market: str = MARKET,
    match_key: str = MATCH,
    kickoff: str = KICKOFF,
    **kwargs,
) -> OddsObservation:
    return OddsObservation(
        match_key=match_key,
        market=market,
        outcome=outcome,
        bookmaker=bookmaker,
        odd=odd,
        timestamp=timestamp,
        kickoff=kickoff,
        **kwargs,
    )


@pytest.fixture()
def store(tmp_path) -> OddsSnapshotStore:
    return OddsSnapshotStore(tmp_path / "test.db")


# --------------------------------------------------------------- escrita/leitura


def test_add_and_read_back(store):
    written = store.add([obs(), obs(odd=3.1, outcome="X")])
    assert written == 2

    rows = store.all_observations(MATCH)
    assert len(rows) == 2
    assert {r.outcome for r in rows} == {"1", "X"}
    assert all(isinstance(r, OddsObservation) for r in rows)

    first = next(r for r in rows if r.outcome == "1")
    assert first.odd == 2.0
    assert first.bookmaker == "bet365"
    assert first.timestamp == "2025-05-05T13:00:00Z"
    assert first.kickoff == KICKOFF


def test_add_empty_sequence_returns_zero(store):
    assert store.add([]) == 0
    assert store.all_observations(MATCH) == []


def test_add_preserves_optional_flags(store):
    store.add([obs(is_opening=True, provider="theoddsapi")])
    saved = store.all_observations(MATCH)[0]
    assert saved.is_opening is True
    assert saved.is_closing is False
    assert saved.provider == "theoddsapi"


# --------------------------------------------------------------- validacao


@pytest.mark.parametrize("bad_odd", [1.0, 0.5, 0.0, -2.0])
def test_post_init_rejects_odd_not_above_one(bad_odd):
    with pytest.raises(ValueError):
        obs(odd=bad_odd)


def test_post_init_rejects_empty_timestamp():
    with pytest.raises(ValueError):
        obs(timestamp="")


def test_post_init_rejects_empty_kickoff():
    with pytest.raises(ValueError):
        obs(kickoff="")


def test_post_init_rejects_timestamp_after_kickoff():
    with pytest.raises(ValueError):
        obs(timestamp="2025-05-05T15:00:00Z")


def test_post_init_rejects_timestamp_equal_to_kickoff():
    with pytest.raises(ValueError):
        obs(timestamp=KICKOFF)


def test_minutes_before_kickoff():
    assert obs(timestamp="2025-05-05T13:00:00Z").minutes_before_kickoff == 60.0
    assert obs(timestamp="2025-05-05T13:30:00Z").minutes_before_kickoff == 30.0
    assert obs(timestamp="2025-05-04T14:00:00Z").minutes_before_kickoff == 1440.0


def test_minutes_before_kickoff_handles_offset_timezone():
    # 13:00 em -03:00 == 16:00Z; kickoff 17:00Z -> 60 minutos.
    point = obs(timestamp="2025-05-05T13:00:00-03:00", kickoff="2025-05-05T17:00:00Z")
    assert point.minutes_before_kickoff == 60.0


# --------------------------------------------------------------- append-only


def test_duplicate_observation_is_not_stored_twice(store):
    store.add([obs()])
    store.add([obs()])
    assert len(store.all_observations(MATCH)) == 1


def test_different_timestamp_same_line_creates_second_row(store):
    store.add([obs(timestamp="2025-05-05T12:00:00Z", odd=2.0)])
    store.add([obs(timestamp="2025-05-05T13:00:00Z", odd=1.9)])

    rows = store.all_observations(MATCH)
    assert len(rows) == 2
    assert [r.odd for r in rows] == [2.0, 1.9]  # ordenado por timestamp


def test_duplicate_is_ignored_not_overwritten(store):
    store.add([obs(odd=2.0)])
    store.add([obs(odd=9.0)])  # mesma chave UNIQUE, odd diferente

    rows = store.all_observations(MATCH)
    assert len(rows) == 1
    assert rows[0].odd == 2.0  # valor original preservado


def test_different_bookmaker_creates_second_row(store):
    store.add([obs(bookmaker="bet365"), obs(bookmaker="pinnacle", odd=2.05)])
    assert len(store.all_observations(MATCH)) == 2


# --------------------------------------------------------------- point-in-time


def test_observations_before_excludes_cutoff_and_later(store):
    store.add(
        [
            obs(timestamp="2025-05-05T11:00:00Z", odd=2.2),
            obs(timestamp="2025-05-05T12:00:00Z", odd=2.1),
            obs(timestamp="2025-05-05T13:00:00Z", odd=2.0),
        ]
    )

    visible = store.observations_before(MATCH, "2025-05-05T13:00:00Z")
    stamps = [o.timestamp for o in visible]

    assert stamps == ["2025-05-05T11:00:00Z", "2025-05-05T12:00:00Z"]
    assert "2025-05-05T13:00:00Z" not in stamps  # estritamente anterior


def test_observations_before_returns_empty_when_nothing_visible(store):
    store.add([obs(timestamp="2025-05-05T13:00:00Z")])
    assert store.observations_before(MATCH, "2025-05-05T10:00:00Z") == []


def test_observations_before_isolates_match_key(store):
    store.add([obs(), obs(match_key="match-002")])
    visible = store.observations_before(MATCH, KICKOFF)
    assert len(visible) == 1
    assert visible[0].match_key == MATCH


# --------------------------------------------------------------- closing_line


def test_closing_line_none_when_outside_window(store):
    # 300 minutos antes do kickoff, janela de 5 minutos.
    store.add([obs(timestamp="2025-05-05T09:00:00Z", odd=2.0)])
    assert store.closing_line(MATCH, MARKET, "1", window_minutes=5.0) is None


def test_closing_line_none_when_no_observation(store):
    assert store.closing_line(MATCH, MARKET, "1") is None


def test_closing_line_median_across_bookmakers(store):
    store.add(
        [
            obs(bookmaker="bet365", odd=2.0, timestamp="2025-05-05T13:30:00Z"),
            obs(bookmaker="pinnacle", odd=2.1, timestamp="2025-05-05T13:30:00Z"),
            obs(bookmaker="betfair", odd=2.4, timestamp="2025-05-05T13:30:00Z"),
        ]
    )

    result = store.closing_line(MATCH, MARKET, "1", window_minutes=120.0)
    assert result is not None
    median_odd, book, timestamp, minutes, n_books = result

    assert median_odd == 2.1
    assert book == "pinnacle"  # casa mais proxima da mediana
    assert timestamp == "2025-05-05T13:30:00Z"
    assert minutes == 30.0
    assert n_books == 3


def test_closing_line_uses_last_observation_per_bookmaker(store):
    store.add(
        [
            obs(bookmaker="bet365", odd=2.5, timestamp="2025-05-05T10:00:00Z"),
            obs(bookmaker="bet365", odd=1.9, timestamp="2025-05-05T13:50:00Z"),
        ]
    )

    result = store.closing_line(MATCH, MARKET, "1", window_minutes=120.0)
    assert result is not None
    assert result[0] == 1.9  # a mais recente, nao a de abertura
    assert result[4] == 1


def test_closing_line_filters_by_market_and_outcome(store):
    store.add(
        [
            obs(outcome="1", odd=2.0, timestamp="2025-05-05T13:30:00Z"),
            obs(outcome="X", odd=3.4, timestamp="2025-05-05T13:30:00Z"),
        ]
    )

    assert store.closing_line(MATCH, MARKET, "1")[0] == 2.0
    assert store.closing_line(MATCH, MARKET, "X")[0] == 3.4
    assert store.closing_line(MATCH, "Ambas Marcam", "1") is None


# --------------------------------------------------------------- CLV


def test_clv_no_closing_odds_returns_none_fields(store):
    store.add([obs(timestamp="2025-05-05T09:00:00Z", odd=2.0)])

    result = store.clv(MATCH, MARKET, "1", entry_odd=2.0, window_minutes=5.0)

    assert result.status == "NO_CLOSING_ODDS"
    assert result.valid is False
    assert result.closing_odd is None
    assert result.closing_implied is None
    assert result.clv_price is None
    assert result.clv_probability is None
    assert result.clv_percentage is None
    assert result.n_books_closing == 0
    # os campos de entrada continuam preenchidos
    assert result.entry_odd == 2.0
    assert result.entry_implied == pytest.approx(0.5)


def test_clv_rejects_invalid_entry_odd(store):
    with pytest.raises(ValueError):
        store.clv(MATCH, MARKET, "1", entry_odd=1.0)


def test_clv_positive_when_entry_beats_closing(store):
    store.add([obs(odd=2.0, timestamp="2025-05-05T13:30:00Z")])

    result = store.clv(MATCH, MARKET, "1", entry_odd=2.2)

    assert result.status == "OK"
    assert result.valid is True
    assert result.closing_odd == 2.0
    assert result.clv_percentage == pytest.approx(0.1, abs=1e-6)
    assert result.clv_percentage > 0
    assert result.clv_price == pytest.approx(0.2, abs=1e-6)
    assert result.clv_probability > 0  # implied do fechamento e maior
    assert result.closing_bookmaker == "bet365"
    assert result.closing_minutes_before == 30.0


def test_clv_negative_when_closing_beats_entry(store):
    store.add([obs(odd=2.0, timestamp="2025-05-05T13:30:00Z")])

    result = store.clv(MATCH, MARKET, "1", entry_odd=1.8)

    assert result.status == "OK"
    assert result.clv_percentage == pytest.approx(-0.1, abs=1e-6)
    assert result.clv_percentage < 0
    assert result.clv_price == pytest.approx(-0.2, abs=1e-6)
    assert result.clv_probability < 0


def test_clv_zero_when_entry_equals_closing(store):
    store.add([obs(odd=2.0, timestamp="2025-05-05T13:30:00Z")])

    result = store.clv(MATCH, MARKET, "1", entry_odd=2.0)

    assert result.clv_percentage == pytest.approx(0.0, abs=1e-9)
    assert result.clv_price == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------- coverage


def test_coverage_full(store):
    store.add(
        [
            obs(outcome="1", odd=2.0, timestamp="2025-05-05T13:30:00Z"),
            obs(outcome="X", odd=3.0, timestamp="2025-05-05T13:30:00Z"),
        ]
    )
    bets = [
        {"match_key": MATCH, "market": MARKET, "outcome": "1", "odd": 2.2},
        {"match_key": MATCH, "market": MARKET, "outcome": "X", "odd": 2.7},
    ]

    report = store.coverage(bets)

    assert report.total_bets == 2
    assert report.bets_with_clv == 2
    assert report.coverage == 1.0
    assert report.positive_clv_rate == 0.5  # uma positiva, uma negativa
    assert report.avg_clv_percentage is not None
    assert report.median_clv_percentage is not None
    assert report.by_market[MARKET]["n"] == 2


def test_coverage_partial(store):
    # "1" tem fechamento dentro da janela; "X" so tem odd antiga demais.
    store.add(
        [
            obs(outcome="1", odd=2.0, timestamp="2025-05-05T13:30:00Z"),
            obs(outcome="X", odd=3.0, timestamp="2025-05-05T09:00:00Z"),
        ]
    )
    bets = [
        {"match_key": MATCH, "market": MARKET, "outcome": "1", "odd": 2.2},
        {"match_key": MATCH, "market": MARKET, "outcome": "X", "odd": 3.3},
    ]

    report = store.coverage(bets, window_minutes=60.0)

    assert report.total_bets == 2
    assert report.bets_with_clv == 1
    assert report.coverage == pytest.approx(0.5)
    assert report.avg_clv_percentage == pytest.approx(0.1, abs=1e-6)


def test_coverage_zero_when_no_closing(store):
    store.add([obs(timestamp="2025-05-05T09:00:00Z", odd=2.0)])
    bets = [{"match_key": MATCH, "market": MARKET, "outcome": "1", "odd": 2.0}]

    report = store.coverage(bets, window_minutes=5.0)

    assert report.total_bets == 1
    assert report.bets_with_clv == 0
    assert report.coverage == 0.0
    assert report.avg_clv_percentage is None
    assert report.median_clv_percentage is None
    assert report.positive_clv_rate is None
    assert report.by_market == {}


def test_coverage_empty_bets_list(store):
    report = store.coverage([])
    assert report.total_bets == 0
    assert report.bets_with_clv == 0
    assert report.coverage == 0.0  # sem divisao por zero


def test_coverage_to_dict_keys(store):
    report = store.coverage([])
    payload = report.to_dict()
    assert set(payload) == {
        "total_bets",
        "bets_with_clv",
        "coverage",
        "avg_clv_percentage",
        "median_clv_percentage",
        "avg_clv_probability",
        "positive_clv_rate",
        "by_market",
    }


def test_clv_coverage_ratio_is_derived_not_stored():
    report = CLVCoverage(total_bets=4, bets_with_clv=3)
    assert report.coverage == pytest.approx(0.75)


# --------------------------------------------------------------- stats


def test_stats_empty_store(store):
    summary = store.stats()
    assert summary["observations"] == 0
    assert summary["matches"] == 0
    assert summary["markets"] == 0
    assert summary["bookmakers"] == 0
    assert summary["first_timestamp"] == ""
    assert summary["last_timestamp"] == ""


def test_stats_counts(store):
    store.add(
        [
            obs(bookmaker="bet365", timestamp="2025-05-05T11:00:00Z"),
            obs(bookmaker="pinnacle", timestamp="2025-05-05T12:00:00Z", odd=2.1),
            obs(
                match_key="match-002",
                market="Ambas Marcam",
                bookmaker="bet365",
                timestamp="2025-05-05T13:00:00Z",
                odd=1.8,
            ),
        ]
    )

    summary = store.stats()

    assert summary["observations"] == 3
    assert summary["matches"] == 2
    assert summary["markets"] == 2
    assert summary["bookmakers"] == 2
    assert summary["first_timestamp"] == "2025-05-05T11:00:00Z"
    assert summary["last_timestamp"] == "2025-05-05T13:00:00Z"


def test_stats_does_not_count_ignored_duplicates(store):
    store.add([obs()])
    store.add([obs()])
    assert store.stats()["observations"] == 1
