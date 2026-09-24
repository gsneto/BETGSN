"""Movement engine + signal engine do terminal: cada sinal explica o PORQUE.

Contratos testados (Fase 11/15/20/28):
- movimento so existe quando (timestamp, preco) mudaram de fato;
- cada movimento carrega OLD/NEW/TIMESTAMP/BOOK/DIRECTION/MAGNITUDE;
- sinais tem signal_id deterministico (reavaliar o mesmo estado NAO
  duplica: refreshed, nao created);
- condicao que desaparece expira com motivo (CONDITION_CLEARED);
- evidencia velha expira por TTL;
- evento UNMATCHED nunca gera sinal;
- reconstruction: mesmas entradas no mesmo T => mesmos signal_ids.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from betgsn.odds_normalize import NormalizedQuote, event_key
from betgsn.realtime.freshness import FreshnessThresholds
from betgsn.realtime.movement import MovementEngine
from betgsn.realtime.signals import (
    SIGNAL_ACTIVE,
    SIGNAL_BEST_PRICE_GAP,
    SIGNAL_BOOKMAKER_LAG,
    SIGNAL_BOOKMAKER_LEAD,
    SIGNAL_BOOKMAKER_OUTLIER,
    SIGNAL_CONSENSUS_MOVE,
    SIGNAL_DISPERSION_SPIKE,
    SIGNAL_EXPIRED,
    SIGNAL_PRICE_REVERSAL,
    SIGNAL_RAPID_CONVERGENCE,
    SIGNAL_STALE_PRICE,
    PRODUCTION_NO_BET,
    SignalEngine,
    SignalRules,
    with_status,
)
from betgsn.realtime.state import MarketState
from betgsn.realtime.views import build_event_view

NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
KICKOFF = "2026-09-28T19:00:00Z"
MARKET = "Resultado Final (1X2)"
EVENT = event_key("Lens", "Lyon", KICKOFF)

THRESHOLDS = FreshnessThresholds(
    fresh_seconds=300, recent_seconds=900, stale_seconds=3600
)
RULES = SignalRules(
    move_window_seconds=3600.0,
    ttl_seconds=1800.0,
    stale_after_seconds=900.0,
)


def q(
    bookmaker: str,
    selection: str,
    price: float,
    timestamp: str,
) -> NormalizedQuote:
    return NormalizedQuote(
        event_id=EVENT,
        provider="ParlayAPI",
        sport_key="soccer_france_ligue_one",
        league="Ligue 1",
        home_team="Lens",
        away_team="Lyon",
        kickoff=KICKOFF,
        bookmaker=bookmaker,
        market=MARKET,
        selection=selection,
        price=price,
        timestamp=timestamp,
    )


def _state_with(quotes) -> MarketState:
    state = MarketState()
    state.apply(quotes, now=NOW)
    return state


def _engine(now=NOW):
    return SignalEngine(rules=RULES, thresholds=THRESHOLDS, now=lambda: now)


def _evaluate_state(state, now=NOW, movements=()):
    engine = _engine(now)
    view = build_event_view(state, EVENT, THRESHOLDS, now)
    return engine, engine.evaluate([view], movements)


# ==========================================================================
# movement
# ==========================================================================


def test_no_movement_when_price_and_stamp_unchanged():
    state = _state_with([q("Pinnacle", "1", 2.10, "2026-09-24T11:00:00Z")])
    detector = MovementEngine()
    key = (EVENT, MARKET, "1", "Pinnacle")
    first = detector.process(state, [key])
    assert first == []  # primeira observacao nao e movimento
    second = detector.process(state, [key])
    assert second == []


def test_movement_carries_old_new_book_direction_magnitude():
    state = MarketState()
    state.apply([q("Pinnacle", "1", 2.10, "2026-09-24T11:00:00Z")], now=NOW)
    state.apply([q("Pinnacle", "1", 2.02, "2026-09-24T11:40:00Z")], now=NOW)
    detector = MovementEngine()
    key = (EVENT, MARKET, "1", "Pinnacle")
    summaries = detector.process(state, [key])
    assert len(summaries) == 1
    move = summaries[0].moves[0]
    assert move.direction == "DOWN"
    assert move.old_price == 2.10 and move.new_price == 2.02
    assert move.bookmaker == "Pinnacle"
    assert move.magnitude_pct > 0.03
    assert move.elapsed_seconds == 2400.0


# ==========================================================================
# sinais de microestrutura
# ==========================================================================


def test_stale_price_signal_explains_the_stale_book():
    fresh = "2026-09-24T11:59:00Z"
    stale = "2026-09-24T09:00:00Z"  # 3h de idade: STALE
    state = _state_with(
        [
            q("Pinnacle", "1", 2.10, fresh),
            q("Book B", "1", 2.12, fresh),
            q("Old Book", "1", 2.50, stale),
        ]
    )
    engine, evaluation = _evaluate_state(state)
    stale_signals = [
        s for s in evaluation.created
        if s.signal_type == SIGNAL_STALE_PRICE
    ]
    assert stale_signals, f"esperava STALE_PRICE, criou: {[s.signal_type for s in evaluation.created]}"
    signal = stale_signals[0]
    assert "Old Book" in signal.reason
    assert signal.evidence["stale_age_seconds"] >= 3 * 3600
    assert signal.production == PRODUCTION_NO_BET


def test_outlier_signal_against_median_of_others():
    state = _state_with(
        [
            q("Book A", "1", 2.10, "2026-09-24T11:59:00Z"),
            q("Book B", "1", 2.11, "2026-09-24T11:59:00Z"),
            q("Book C", "1", 2.10, "2026-09-24T11:59:00Z"),
            q("Book D", "1", 2.45, "2026-09-24T11:59:00Z"),
        ]
    )
    engine, evaluation = _evaluate_state(state)
    outliers = [
        s for s in evaluation.created
        if s.signal_type == SIGNAL_BOOKMAKER_OUTLIER
    ]
    assert outliers
    assert outliers[0].evidence["outlier_book"] == "Book D"
    assert outliers[0].evidence["deviation_pct"] > 0.10


def test_dispersion_spike_and_best_price_gap():
    state = _state_with(
        [
            q("Book A", "1", 2.10, "2026-09-24T11:59:00Z"),
            q("Book B", "1", 2.12, "2026-09-24T11:59:00Z"),
            q("Book C", "1", 2.24, "2026-09-24T11:59:00Z"),
        ]
    )
    engine, evaluation = _evaluate_state(state)
    gaps = [
        s for s in evaluation.created
        if s.signal_type == SIGNAL_BEST_PRICE_GAP
    ]
    assert gaps
    assert gaps[0].evidence["best_book"] == "Book C"
    assert gaps[0].evidence["gap_pct"] > 0.04
    assert "nao selecao de aposta" in gaps[0].reason


# ==========================================================================
# sinais de movimento
# ==========================================================================


def _moved_state() -> MarketState:
    state = MarketState()
    for book, old, new in (
        ("Book A", 2.20, 2.10),
        ("Book B", 2.21, 2.08),
        ("Book C", 2.22, 2.12),
    ):
        state.apply([q(book, "1", old, "2026-09-24T11:00:00Z")], now=NOW)
        state.apply(
            [q(book, "1", new, "2026-09-24T11:45:00Z")], now=NOW
        )
    return state


def test_consensus_move_requires_multiple_books_same_direction():
    state = _moved_state()
    detector = MovementEngine()
    keys = [
        (EVENT, MARKET, "1", b)
        for b in ("Book A", "Book B", "Book C")
    ]
    movements = detector.process(state, keys)
    engine, evaluation = _evaluate_state(state, movements=movements)
    consensus = [
        s for s in evaluation.created
        if s.signal_type == SIGNAL_CONSENSUS_MOVE
    ]
    assert consensus
    assert set(consensus[0].evidence["books"]) == {"Book A", "Book B", "Book C"}
    assert consensus[0].evidence["direction"] == "DOWN"
    assert consensus[0].observed_at == "2026-09-24T11:45:00Z"


def test_lead_and_lag_follow_first_mover():
    state = MarketState()
    state.apply([q("Book A", "1", 2.20, "2026-09-24T11:00:00Z")], now=NOW)
    state.apply([q("Book A", "1", 2.10, "2026-09-24T11:10:00Z")], now=NOW)
    state.apply([q("Book B", "1", 2.21, "2026-09-24T11:00:00Z")], now=NOW)
    state.apply([q("Book B", "1", 2.09, "2026-09-24T11:25:00Z")], now=NOW)
    detector = MovementEngine()
    movements = detector.process(
        state,
        [(EVENT, MARKET, "1", "Book A"), (EVENT, MARKET, "1", "Book B")],
    )
    engine, evaluation = _evaluate_state(state, movements=movements)
    leads = [s for s in evaluation.created if s.signal_type == SIGNAL_BOOKMAKER_LEAD]
    lags = [s for s in evaluation.created if s.signal_type == SIGNAL_BOOKMAKER_LAG]
    assert leads and leads[0].evidence["lead_book"] == "Book A"
    assert lags and lags[0].evidence["lag_book"] == "Book B"


def test_reversal_detected_from_book_moves():
    state = MarketState()
    state.apply([q("Book A", "1", 2.20, "2026-09-24T11:00:00Z")], now=NOW)
    state.apply([q("Book A", "1", 2.10, "2026-09-24T11:10:00Z")], now=NOW)
    state.apply([q("Book A", "1", 2.25, "2026-09-24T11:20:00Z")], now=NOW)
    detector = MovementEngine()
    movements = detector.process(state, [(EVENT, MARKET, "1", "Book A")])
    engine, evaluation = _evaluate_state(state, movements=movements)
    reversals = [
        s for s in evaluation.created if s.signal_type == SIGNAL_PRICE_REVERSAL
    ]
    assert reversals
    assert reversals[0].evidence["book"] == "Book A"


def test_convergence_after_moves():
    state = MarketState()
    for book, old, new in (
        ("Book A", 2.40, 2.13),
        ("Book B", 1.95, 2.11),
    ):
        state.apply([q(book, "1", old, "2026-09-24T11:00:00Z")], now=NOW)
        state.apply([q(book, "1", new, "2026-09-24T11:30:00Z")], now=NOW)
    detector = MovementEngine()
    movements = detector.process(
        state,
        [(EVENT, MARKET, "1", "Book A"), (EVENT, MARKET, "1", "Book B")],
    )
    engine, evaluation = _evaluate_state(state, movements=movements)
    convergences = [
        s for s in evaluation.created
        if s.signal_type == SIGNAL_RAPID_CONVERGENCE
    ]
    assert convergences


# ==========================================================================
# ciclo de vida: dedup, expiracao, TTL, unmatched, reconstruction
# ==========================================================================


def test_re_evaluation_same_state_is_refresh_not_created():
    state = _moved_state()
    detector = MovementEngine()
    keys = [(EVENT, MARKET, "1", b) for b in ("Book A", "Book B", "Book C")]
    movements = detector.process(state, keys)
    engine = _engine()
    view = build_event_view(state, EVENT, THRESHOLDS, NOW)
    first = engine.evaluate([view], movements)
    assert first.created
    second = engine.evaluate([view], movements)
    assert not second.created
    assert len(second.refreshed) == len(first.created)


def test_condition_cleared_expires_signal_with_reason():
    state = _state_with(
        [
            q("Book A", "1", 2.10, "2026-09-24T11:59:00Z"),
            q("Book B", "1", 2.11, "2026-09-24T11:59:00Z"),
            q("Book C", "1", 2.40, "2026-09-24T11:59:00Z"),
        ]
    )
    engine = _engine()
    view = build_event_view(state, EVENT, THRESHOLDS, NOW)
    first = engine.evaluate([view])
    assert first.created

    #: book outlier corrige: gap desaparece
    state.apply([q("Book C", "1", 2.12, "2026-09-24T11:59:30Z")], now=NOW)
    view2 = build_event_view(state, EVENT, THRESHOLDS, NOW)
    second = engine.evaluate([view2])
    expired_types = {s.signal_type for s in second.expired}
    assert SIGNAL_BOOKMAKER_OUTLIER in expired_types
    assert all("CONDITION_CLEARED" in s.reason for s in second.expired)


def test_ttl_expiry_by_evidence_age():
    state = _state_with(
        [
            q("Book A", "1", 2.10, "2026-09-24T11:59:00Z"),
            q("Book B", "1", 2.11, "2026-09-24T11:59:00Z"),
            q("Book C", "1", 2.40, "2026-09-24T11:59:00Z"),
        ]
    )
    engine = _engine()
    view = build_event_view(state, EVENT, THRESHOLDS, NOW)
    first = engine.evaluate([view])
    assert first.created

    later = NOW + timedelta(seconds=RULES.ttl_seconds + 60)
    engine._now = lambda: later  # type: ignore[assignment]
    #: reavaliacao no futuro: TTL varre o sinal cujo observed_at envelheceu
    sweep = engine.evaluate([])
    expired_types = {s.signal_type for s in sweep.expired}
    assert SIGNAL_BOOKMAKER_OUTLIER in expired_types
    assert all("EVIDENCE_TTL" in s.reason for s in sweep.expired if s.signal_type == SIGNAL_BOOKMAKER_OUTLIER)


def test_status_degrades_with_age():
    signal = None
    state = _state_with(
        [
            q("Book A", "1", 2.10, "2026-09-24T11:59:00Z"),
            q("Book B", "1", 2.11, "2026-09-24T11:59:00Z"),
            q("Book C", "1", 2.40, "2026-09-24T11:59:00Z"),
        ]
    )
    engine = _engine()
    view = build_event_view(state, EVENT, THRESHOLDS, NOW)
    first = engine.evaluate([view])
    signal = first.created[0]

    fresh = with_status(signal, NOW, RULES)
    assert fresh.status == SIGNAL_ACTIVE
    older = with_status(
        signal, NOW + timedelta(seconds=1000), RULES
    )
    assert older.status in (SIGNAL_ACTIVE, "STALE")
    expired = with_status(
        signal, NOW + timedelta(seconds=RULES.ttl_seconds + 10), RULES
    )
    assert expired.status == SIGNAL_EXPIRED


def test_unmatched_event_never_signals():
    state = MarketState()
    unmatched_event = event_key("Weird FC", "Odd United", KICKOFF)
    state.apply(
        [
            replace(
                q("Book A", "1", 2.10, "2026-09-24T11:59:00Z"),
                event_id=unmatched_event,
            ),
            replace(
                q("Book B", "1", 2.11, "2026-09-24T11:59:00Z"),
                event_id=unmatched_event,
            ),
            replace(
                q("Book C", "1", 2.40, "2026-09-24T11:59:00Z"),
                event_id=unmatched_event,
            ),
        ],
        now=NOW,
        matched_keys=set(),
    )
    engine = _engine()
    view = build_event_view(state, unmatched_event, THRESHOLDS, NOW)
    evaluation = engine.evaluate([view])
    assert not evaluation.created
    assert not engine.active_signals()


def test_signal_ids_reproducible_for_same_inputs():
    """Reconstruction: mesmo estado + mesmo now => mesmos signal_ids."""
    state = _moved_state()
    detector = MovementEngine()
    keys = [(EVENT, MARKET, "1", b) for b in ("Book A", "Book B", "Book C")]
    movements = detector.process(state, keys)

    engine_a = _engine()
    view_a = build_event_view(state, EVENT, THRESHOLDS, NOW)
    ids_a = sorted(
        s.signal_id for s in engine_a.evaluate([view_a], movements).created
    )

    engine_b = _engine()
    view_b = build_event_view(state, EVENT, THRESHOLDS, NOW)
    ids_b = sorted(
        s.signal_id for s in engine_b.evaluate([view_b], movements).created
    )
    assert ids_a == ids_b and ids_a


def test_every_signal_carries_reason_evidence_and_prices():
    state = _moved_state()
    detector = MovementEngine()
    keys = [(EVENT, MARKET, "1", b) for b in ("Book A", "Book B", "Book C")]
    movements = detector.process(state, keys)
    engine = _engine()
    view = build_event_view(state, EVENT, THRESHOLDS, NOW)
    for signal in engine.evaluate([view], movements).created:
        assert signal.reason
        assert signal.evidence
        assert signal.market_price is not None
        assert signal.best_price is not None
        assert signal.production == PRODUCTION_NO_BET
        assert signal.bookmakers
