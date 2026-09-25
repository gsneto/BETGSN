"""PricedSignal engine integrado ao live: sem fabricação, NO_BET quando falta evidência."""
from datetime import datetime, timezone

import pytest

from betgsn.odds_normalize import NormalizedQuote
from betgsn.realtime.state import MarketState
from betgsn.realtime.freshness import FreshnessThresholds
from betgsn.realtime.views import build_event_view
from betgsn.realtime.priced_engine import PricedRealtimeEngine
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
    signals = engine.priced(
        view, decision_ts="2026-01-01T12:00:45Z",
        models={"ev|home|away|Total de Gols|Over 2.5": ("fp", 0.70)},
        fair_probs={"ev|home|away|Total de Gols|Over 2.5": 0.5},
    )
    signal = signals[0].to_dict()
    assert signal["evidence_status"] == "FORTE"
    assert signal["production"] == "REVIEW"
    assert signal["execution_status"] == "UNKNOWN"
    assert signal["model_prob"] == 0.7
    assert signal["edge"] > 0.08
    assert signal["ev"] > 0.08
    assert signal["books_count"] == 3
    assert signal["spread"] <= 0.12


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
