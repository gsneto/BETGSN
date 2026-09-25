"""PricedSignal engine integrado ao live: sem fabricação, NO_BET quando falta evidência."""
from datetime import datetime, timezone

import pytest

from betgsn.odds_normalize import NormalizedQuote
from betgsn.realtime.state import MarketState
from betgsn.realtime.freshness import FreshnessThresholds
from betgsn.realtime.views import build_event_view
from betgsn.realtime.priced_engine import FairOverride, PricedRealtimeEngine
from betgsn.production_policy import GateBlock, ProductionGate, REQUIRED_BLOCKS
from betgsn.config import production_policy_fingerprint

MARKET = "Total de Gols"
SELECTION = "Over 2.5"
LINE = 2.5


def _quote(book, price, ts="2026-01-01T12:00:00Z"):
    return NormalizedQuote(
        event_id="ev|home|away",
        provider="provider",
        sport_key="soccer",
        league="Ligue 1",
        home_team="home",
        away_team="away",
        kickoff="2026-01-02T12:00:00Z",
        bookmaker=book,
        market=MARKET,
        selection=SELECTION,
        price=price,
        timestamp=ts,
        line=LINE,
    )


def _view(quotes):
    state = MarketState()
    now = datetime(2026, 1, 1, 12, 0, 30, tzinfo=timezone.utc)
    state.apply(quotes, now=now, matched_keys={"ev|home|away"})
    return build_event_view(state, "ev|home|away", FreshnessThresholds(), now)


def _gate_green():
    return ProductionGate({n: GateBlock("GREEN") for n in REQUIRED_BLOCKS},
                          production_policy_fingerprint())


def _override(prob=0.5):
    return FairOverride(prob=prob, window_id="w23", method="isotonic",
                        n=487, calibration_fingerprint="cal-fp-1")


def test_price_engine_marks_no_model_as_research():
    engine = PricedRealtimeEngine(gate=_gate_green())
    view = _view([_quote("A", 2.10), _quote("B", 2.05), _quote("C", 2.00)])
    signals = engine.priced(view, decision_ts="2026-01-01T12:00:45Z", models={})
    assert len(signals) == 1
    signal = signals[0].to_dict()
    assert signal["production"] == "NO_BET"
    assert signal["evidence_status"] == "RESEARCH"
    assert signal["execution_status"] == "UNKNOWN"
    assert signal["executed_price"] is None
    assert "MODEL_UNAVAILABLE" in signal["research_reasons"]


def test_price_engine_limits_when_only_two_books():
    engine = PricedRealtimeEngine(gate=_gate_green())
    view = _view([_quote("A", 2.10), _quote("B", 2.00)])
    signals = engine.priced(
        view, decision_ts="2026-01-01T12:00:45Z",
        models={"ev|home|away|Total de Gols|Over 2.5": ("fp", 0.62)},
    )
    signal = signals[0].to_dict()
    assert signal["evidence_status"] == "RESEARCH"
    assert "LIMITED_EVIDENCE" in signal["research_reasons"]
    assert signal["production"] == "NO_BET"


def test_price_engine_red_block_never_produces_forte():
    blocks = {n: GateBlock("GREEN") for n in REQUIRED_BLOCKS}
    blocks["MODEL"] = GateBlock("RED", ("no evidence",))
    gate_red = ProductionGate(blocks, production_policy_fingerprint())
    engine = PricedRealtimeEngine(gate=gate_red)
    view = _view([_quote("A", 2.05), _quote("B", 2.03), _quote("C", 2.02)])
    signals = engine.priced(
        view, decision_ts="2026-01-01T12:00:45Z",
        models={"ev|home|away|Total de Gols|Over 2.5": ("fp", 0.62)},
    )
    signal = signals[0].to_dict()
    assert signal["production"] == "NO_BET"
    assert "NO_BET" in signal["research_reasons"]


def test_price_engine_forte_only_with_full_evidence_and_matching_market():
    engine = PricedRealtimeEngine(gate=_gate_green())
    view = _view([_quote("A", 2.30), _quote("B", 2.29), _quote("C", 2.28)])
    key = "ev|home|away|Total de Gols|Over 2.5"
    signals = engine.priced(
        view, decision_ts="2026-01-01T12:00:45Z",
        models={key: ("fp", 0.70)},
        fair_override={key: _override(prob=0.5)},
        executions={key: (2.30, "2026-01-01T12:01:15Z")},
    )
    signal = signals[0].to_dict()
    assert signal["evidence_status"] == "FORTE"
    assert signal["production"] == "REVIEW"
    assert signal["execution_status"] == "EXECUTED"
    assert signal["model_prob"] == 0.7
    assert signal["edge"] > 0.08
    assert signal["ev"] > 0.08
    assert signal["books_count"] == 3
    assert signal["spread"] <= 0.12


def _priced_with_fill(engine, executions):
    view = _view([_quote("A", 2.30), _quote("B", 2.29), _quote("C", 2.28)])
    return engine.priced(
        view, decision_ts="2026-01-01T12:00:45Z",
        models={"ev|home|away|Total de Gols|Over 2.5": ("fp", 0.70)},
        fair_probs={"ev|home|away|Total de Gols|Over 2.5": 0.5},
        executions=executions,
    )[0].to_dict()


def test_executed_within_window_marks_executed_and_gap():
    engine = PricedRealtimeEngine(gate=_gate_green())
    signal = _priced_with_fill(engine, {
        "ev|home|away|Total de Gols|Over 2.5": (2.27, "2026-01-01T12:01:15Z"),
    })
    assert signal["execution_status"] == "EXECUTED"
    assert signal["executed_price"] == 2.27
    assert signal["executed_at"] == "2026-01-01T12:01:15Z"
    # gap = 2.27 - 2.30 = -0.03 (fill pior que o preço selecionado)
    assert signal["execution_absolute_gap"] == pytest.approx(-0.03)
    assert signal["execution_relative_gap"] == pytest.approx(-0.03 / 2.30)


def test_executed_without_timestamp_marks_expired():
    engine = PricedRealtimeEngine(gate=_gate_green())
    signal = _priced_with_fill(engine, {
        "ev|home|away|Total de Gols|Over 2.5": (2.27, ""),
    })
    assert signal["execution_status"] == "EXPIRED"
    assert signal["executed_price"] is None
    assert signal["execution_absolute_gap"] is None
    assert signal["execution_relative_gap"] is None


def test_executed_outside_window_marks_expired():
    engine = PricedRealtimeEngine(gate=_gate_green())
    signal = _priced_with_fill(engine, {
        # decision = 12:00:45, fill @ 12:05:00 = 255s > 60s (janela)
        "ev|home|away|Total de Gols|Over 2.5": (2.27, "2026-01-01T12:05:00Z"),
    })
    assert signal["execution_status"] == "EXPIRED"
    assert signal["executed_price"] is None


def test_execution_erosion_flags_over_fifty_percent():
    from betgsn.priced_signals import execution_diagnostics, execution_erosion

    # observed=2.20, executed=2.15, closing=2.00
    # clv_before = 2.20/2.00 - 1 = +10.0%
    # clv_after  = 2.15/2.00 - 1 =  +7.5%
    # ratio = (10 - 7.5) / 10 = 25% → abaixo do teto de 50%
    below = execution_diagnostics(2.20, 2.15, 2.00)
    assert execution_erosion([below])["status"] == "MEASURED"
    assert execution_erosion([below])["ratio"] == pytest.approx(0.25)

    # observed=2.20, executed=2.05, closing=2.00
    # clv_before = +10%; clv_after = +2.5%; ratio = 75% → EROSION
    eroded = execution_diagnostics(2.20, 2.05, 2.00)
    result = execution_erosion([eroded])
    assert result["status"] == "EXECUTION_EROSION"
    assert result["ratio"] > 0.5


def test_fair_override_alone_leaves_execution_pending():
    # Base gate PENDING nos blocos live (o default do live). Override MARKET
    # sozinho não pode virar FORTE: EXECUTION continua PENDING.
    from betgsn.realtime.engine import _default_priced_gate
    engine = PricedRealtimeEngine(gate=_default_priced_gate())
    view = _view([_quote("A", 2.30), _quote("B", 2.29), _quote("C", 2.28)])
    key = "ev|home|away|Total de Gols|Over 2.5"
    signal = engine.priced(
        view, decision_ts="2026-01-01T12:00:45Z",
        models={key: ("fp", 0.70)},
        fair_override={key: _override(prob=0.5)},
    )[0].to_dict()
    assert signal["evidence_status"] == "RESEARCH"
    assert signal["production"] == "NO_BET"
    assert "NO_BET" in signal["research_reasons"]


def test_invalid_fair_override_is_ignored():
    engine = PricedRealtimeEngine(gate=_gate_green())
    view = _view([_quote("A", 2.30), _quote("B", 2.29), _quote("C", 2.28)])
    key = "ev|home|away|Total de Gols|Over 2.5"
    bad = FairOverride(prob=0.5, window_id="w0", method="", n=0,
                       calibration_fingerprint="")
    signal = engine.priced(
        view, decision_ts="2026-01-01T12:00:45Z",
        models={key: ("fp", 0.70)},
        fair_override={key: bad},
        executions={key: (2.30, "2026-01-01T12:01:15Z")},
    )[0].to_dict()
    # Sem fair_override válido, MARKET/PROVENANCE ficam PENDING → NO_BET.
    assert signal["production"] == "NO_BET"


def test_execution_expired_does_not_promote_execution_block():
    engine = PricedRealtimeEngine(gate=_gate_green())
    view = _view([_quote("A", 2.30), _quote("B", 2.29), _quote("C", 2.28)])
    key = "ev|home|away|Total de Gols|Over 2.5"
    # Fill fora da janela (255s > 60s): EXPIRED → EXECUTION PENDING.
    signal = engine.priced(
        view, decision_ts="2026-01-01T12:00:45Z",
        models={key: ("fp", 0.70)},
        fair_override={key: _override(prob=0.5)},
        executions={key: (2.27, "2026-01-01T12:05:00Z")},
    )[0].to_dict()
    assert signal["execution_status"] == "EXPIRED"
    assert signal["production"] == "NO_BET"


def test_stale_quote_drops_by_freshness():
    engine = PricedRealtimeEngine(gate=_gate_green())
    view = _view([_quote("A", 2.30, "2026-01-01T11:00:00Z"),
                  _quote("B", 2.05), _quote("C", 2.02)])
    signals = engine.priced(
        view, decision_ts="2026-01-01T12:00:45Z",
        models={"ev|home|away|Total de Gols|Over 2.5": ("fp", 0.62)},
    )
    signal = signals[0].to_dict()
    assert signal["books_count"] == 2
    assert "LIMITED_EVIDENCE" in signal["research_reasons"]
