"""Testes das correções da auditoria (P0/P1).

Cobrem: gate live de 7 blocos, providers operacionais vs legacy, circuit
breaker, provenance de timestamp, future quote, janela de movimento,
persistência de execução, calibração de sinais e MIN_BOOKS.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from betgsn.config import production_policy_fingerprint, production_thresholds
from betgsn.odds_registry import default_odds_registry
from betgsn.production_policy import REQUIRED_BLOCKS


# --------------------------------------------------------------------------
# P1 — gate operacional live
# --------------------------------------------------------------------------


def test_live_gate_tem_os_sete_blocos_e_fingerprint_correto():
    from betgsn.live_gate import build_live_gate

    gate = build_live_gate()
    assert set(gate.blocks) == set(REQUIRED_BLOCKS)
    assert gate.fingerprint == production_policy_fingerprint()


def test_live_gate_nao_e_elegivel_sem_evidencia():
    """Sem OOS/CLV no ambiente de teste, o gate NAO pode ser elegivel."""
    from betgsn.live_gate import build_live_gate

    gate = build_live_gate()
    assert gate.production_eligible is False
    # Blocos que dependem de evidencia que nao existe: nunca GREEN.
    assert gate.blocks["MODEL"].status != "GREEN"
    assert gate.blocks["EXECUTION"].status != "GREEN"
    assert gate.blocks["PROVENANCE"].status != "GREEN"


def test_live_gate_clv_bloco_red_com_n_zero():
    from betgsn.live_gate import build_live_gate

    gate = build_live_gate()
    # CLV sem fechamento: RED (nunca GREEN por ausencia).
    assert gate.blocks["CLV"].status in ("RED", "PENDING")


# --------------------------------------------------------------------------
# P1 — providers operacionais vs legacy
# --------------------------------------------------------------------------


def test_registry_separa_operacionais_de_legacy():
    registry = default_odds_registry()
    legacy = set(registry.legacy_names())
    assert legacy == {"Odds-API.io", "OpticOdds"}
    operational_names = {name for name, _p in registry.operational_providers()}
    assert operational_names == {"The Odds API", "ParlayAPI", "OddsPapi"}
    assert not (operational_names & legacy)


def test_operational_providers_exclui_legacy_mesmo_configurado(monkeypatch):
    """Mesmo com chave legacy presente, o capture operacional nao a inclui."""
    registry = default_odds_registry()
    # Forca todas as factories a "configuradas" (objeto qualquer).
    monkeypatch.setattr(registry, "lookup", lambda name: object())
    names = {name for name, _p in registry.operational_providers()}
    assert names == {"The Odds API", "ParlayAPI", "OddsPapi"}


# --------------------------------------------------------------------------
# P1 — circuit breaker
# --------------------------------------------------------------------------


def test_circuit_breaker_abre_apos_falhas_retentaveis():
    from betgsn.odds_health import CircuitBreaker, CircuitState
    from betgsn.providers import FAILURE_TIMEOUT

    breaker = CircuitBreaker(threshold=3, cooldown_seconds=60.0)
    assert breaker.allow("P")
    for _ in range(3):
        breaker.record_failure("P", FAILURE_TIMEOUT)
    assert breaker.state("P") == CircuitState.OPEN
    assert breaker.allow("P") is False


def test_circuit_breaker_abre_imediatamente_em_falha_dura():
    from betgsn.odds_health import CircuitBreaker, CircuitState
    from betgsn.providers import FAILURE_AUTH

    breaker = CircuitBreaker(threshold=3, hard_cooldown_seconds=3600.0)
    breaker.record_failure("P", FAILURE_AUTH)
    assert breaker.state("P") == CircuitState.OPEN


def test_circuit_breaker_half_open_e_recovery():
    from betgsn.odds_health import CircuitBreaker, CircuitState
    from betgsn.providers import FAILURE_TIMEOUT

    now = {"t": 0.0}
    breaker = CircuitBreaker(threshold=1, cooldown_seconds=30.0,
                             clock=lambda: now["t"])
    breaker.record_failure("P", FAILURE_TIMEOUT)
    assert breaker.state("P") == CircuitState.OPEN
    now["t"] = 31.0
    assert breaker.state("P") == CircuitState.HALF_OPEN
    assert breaker.allow("P") is True
    breaker.record_success("P")
    assert breaker.state("P") == CircuitState.CLOSED


def test_circuit_breaker_isola_providers():
    from betgsn.odds_health import CircuitBreaker, CircuitState
    from betgsn.providers import FAILURE_AUTH

    breaker = CircuitBreaker()
    breaker.record_failure("A", FAILURE_AUTH)
    assert breaker.state("A") == CircuitState.OPEN
    assert breaker.state("B") == CircuitState.CLOSED
    assert breaker.allow("B") is True


# --------------------------------------------------------------------------
# P1 — provenance de timestamp
# --------------------------------------------------------------------------


def _event(price=2.1, outcome_ts=None, event_ts=None):
    outcome = {"name": "Lens", "price": price}
    if outcome_ts:
        outcome["timestamp"] = outcome_ts
    event = {
        "home_team": "Lens", "away_team": "Lyon",
        "commence_time": "2026-09-28T19:00:00Z",
        "bookmakers": [{
            "key": "pinnacle", "title": "Pinnacle",
            "markets": [{"key": "h2h", "outcomes": [outcome]}],
        }],
    }
    if event_ts:
        event["timestamp"] = event_ts
    return event


def test_timestamp_provenance_quote():
    from betgsn.odds_normalize import TS_QUOTE, normalize_event

    quotes = normalize_event(
        _event(outcome_ts="2026-09-24T12:00:00Z"),
        "P", "2026-09-24T12:05:00Z",
    )
    assert quotes[0].timestamp_source == TS_QUOTE


def test_timestamp_provenance_event():
    from betgsn.odds_normalize import TS_EVENT, normalize_event

    quotes = normalize_event(
        _event(event_ts="2026-09-24T11:00:00Z"),
        "P", "2026-09-24T12:05:00Z",
    )
    assert quotes[0].timestamp_source == TS_EVENT


def test_timestamp_provenance_capture_fallback():
    """Sem timestamp de outcome nem de evento, o fallback e CAPTURE."""
    from betgsn.odds_normalize import TS_CAPTURE, normalize_event

    quotes = normalize_event(_event(), "P", "2026-09-24T12:05:00Z")
    assert quotes[0].timestamp_source == TS_CAPTURE
    # O timestamp e o do fetched_at — mas MARCADO como captura.
    assert quotes[0].timestamp == "2026-09-24T12:05:00Z"


# --------------------------------------------------------------------------
# P1 — future quotes
# --------------------------------------------------------------------------

NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
KICKOFF = "2026-09-28T19:00:00Z"


def _quote(book, price, ts):
    from betgsn.odds_normalize import NormalizedQuote

    return NormalizedQuote(
        event_id="Lens|Lyon|2026-09-28T19:00:00Z",
        provider="P", sport_key="soccer", league="Ligue 1",
        home_team="Lens", away_team="Lyon", kickoff=KICKOFF,
        bookmaker=book, market="Resultado Final (1X2)", selection="1",
        price=price, timestamp=ts, line=None,
    )


@pytest.mark.parametrize("stamp", [
    "2026-09-24T12:00:01Z",  # +1s
    "2026-09-24T12:00:30Z",  # +30s
    "2026-09-24T12:02:00Z",  # +120s (antiga tolerancia de skew)
    "2026-09-24T12:02:01Z",  # +121s
    "2026-09-25T12:00:00Z",  # +1 dia
])
def test_future_quote_rejeitada_para_qualquer_offset(stamp):
    from betgsn.realtime.state import MarketState

    state = MarketState()
    events, lines, problems = state.apply([_quote("Pinnacle", 2.10, stamp)],
                                          now=NOW)
    assert not events and not lines
    assert problems and problems[0].reason == "FUTURE_TIMESTAMP"


def test_quote_no_instante_exato_e_aceita():
    from betgsn.realtime.state import MarketState

    state = MarketState()
    events, lines, problems = state.apply(
        [_quote("Pinnacle", 2.10, "2026-09-24T12:00:00Z")], now=NOW)
    assert events and lines and not problems


def test_quote_passada_e_aceita():
    from betgsn.realtime.state import MarketState

    state = MarketState()
    events, lines, problems = state.apply(
        [_quote("Pinnacle", 2.10, "2026-09-24T11:59:00Z")], now=NOW)
    assert events and lines and not problems


# --------------------------------------------------------------------------
# P1 — janela de movimento (lower bound)
# --------------------------------------------------------------------------


def test_moves_in_window_descarta_movimento_futuro():
    from betgsn.realtime.signals import SignalEngine
    from betgsn.realtime.movement import LineMove

    fixed = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
    engine = SignalEngine(now=lambda: fixed)
    past = LineMove(
        event_key="e", market="m", selection="s", bookmaker="A",
        old_price=2.0, new_price=2.1, old_timestamp="2026-09-24T11:50:00Z",
        new_timestamp="2026-09-24T11:55:00Z", provider="P",
    )
    future = LineMove(
        event_key="e", market="m", selection="s", bookmaker="B",
        old_price=2.0, new_price=2.1, old_timestamp="2026-09-24T12:10:00Z",
        new_timestamp="2026-09-24T12:10:00Z", provider="P",
    )
    in_window = engine._moves_in_window([past, future])
    assert past in in_window
    assert future not in in_window


# --------------------------------------------------------------------------
# P1 — persistencia de execucao
# --------------------------------------------------------------------------


def test_execution_persistida_no_store(tmp_path):
    from betgsn.odds_snapshots import OddsSnapshotStore

    store = OddsSnapshotStore(tmp_path / "odds.db")
    store.save_execution(
        match_key="Lens|Lyon|2026-09-28T19:00:00Z",
        market="Resultado Final (1X2)", outcome="1",
        executed_price=2.05, executed_at="2026-09-24T12:01:00Z",
    )
    loaded = store.load_executions()
    key = ("Lens|Lyon|2026-09-28T19:00:00Z", "Resultado Final (1X2)", "1")
    assert key in loaded
    assert loaded[key]["executed_price"] == pytest.approx(2.05)
    store.delete_execution(
        match_key="Lens|Lyon|2026-09-28T19:00:00Z",
        market="Resultado Final (1X2)", outcome="1",
    )
    assert store.load_executions() == {}


def test_execution_invalida_rejeitada(tmp_path):
    from betgsn.odds_snapshots import OddsSnapshotStore

    store = OddsSnapshotStore(tmp_path / "odds.db")
    with pytest.raises(ValueError):
        store.save_execution(
            match_key="e", market="m", outcome="s",
            executed_price=1.0, executed_at="2026-09-24T12:00:00Z",
        )
    with pytest.raises(ValueError):
        store.save_execution(
            match_key="e", market="m", outcome="s",
            executed_price=2.0, executed_at="",
        )


# --------------------------------------------------------------------------
# P1 — calibracao de sinais
# --------------------------------------------------------------------------


def test_calibracao_insufficient_data_sem_amostra():
    from betgsn.realtime.signal_calibration import (
        STATUS_INSUFFICIENT, calibrate_threshold,
    )

    result = calibrate_threshold([0.05, 0.06], metric="outlier", quantile=0.99,
                                 min_sample=200)
    assert result.status == STATUS_INSUFFICIENT
    assert result.threshold is None
    assert result.usable is False


def test_calibracao_quantil_historico():
    from betgsn.realtime.signal_calibration import STATUS_OK, calibrate_threshold

    values = [i / 1000.0 for i in range(1, 1001)]  # 0.001..1.0
    result = calibrate_threshold(values, metric="outlier", quantile=0.99,
                                 min_sample=200)
    assert result.status == STATUS_OK
    assert result.threshold is not None and 0.98 <= result.threshold <= 1.0
    assert result.sample_size == 1000
    assert result.fingerprint


def test_outlier_deviations_metrica():
    from betgsn.realtime.signal_calibration import outlier_deviations

    devs = outlier_deviations([2.00, 2.02, 2.50])
    assert len(devs) == 3
    assert max(devs) > 0.2  # a casa de 2.50 e outlier


# --------------------------------------------------------------------------
# P1 — MIN_BOOKS
# --------------------------------------------------------------------------


def test_classify_exige_min_books():
    from betgsn.signals import Confidence, classify

    # 2 casas: DESCARTE (abaixo do consenso minimo de 3).
    assert classify(0.10, 2, 0.02) == Confidence.DESCARTE
    # 3 casas com EV forte: ao menos MEDIA (nunca DESCARTE).
    assert classify(0.10, 3, 0.02) != Confidence.DESCARTE


# --------------------------------------------------------------------------
# P1 — SSE replay (Last-Event-ID)
# --------------------------------------------------------------------------


def test_event_bus_replay_after():
    from betgsn.realtime.events import EventBus, make_event

    bus = EventBus()
    e1 = make_event("A", {"n": 1})
    e2 = make_event("A", {"n": 2})
    e3 = make_event("A", {"n": 3})
    for e in (e1, e2, e3):
        bus.publish(e)
    replay = bus.replay_after(e1.event_id)
    assert [e.event_id for e in replay] == [e2.event_id, e3.event_id]
    # event_id desconhecido: devolve todo o buffer (best effort), nunca vazio
    assert len(bus.replay_after("desconhecido")) == 3
    # sem id: conexao nova, nada a reenviar
    assert bus.replay_after("") == []


# --------------------------------------------------------------------------
# P1 — engine persiste execucao e hidrata no boot
# --------------------------------------------------------------------------


def test_engine_record_execution_persiste(tmp_path):
    from betgsn.odds_snapshots import OddsSnapshotStore
    from betgsn.realtime.config import RealtimeConfig
    from betgsn.realtime.engine import RealtimeOddsEngine
    from betgsn.realtime.events import EventBus

    store = OddsSnapshotStore(tmp_path / "odds.db")
    engine = RealtimeOddsEngine(
        config=RealtimeConfig(interval_seconds=300.0),
        captures=[], store=store, bus=EventBus(), sport_keys=(),
    )
    engine.record_execution(
        event_key="Lens|Lyon|2026-09-28T19:00:00Z",
        market="Resultado Final (1X2)", selection="1",
        executed_price=2.05, executed_at="2026-09-24T12:01:00Z",
    )
    # persistido no store
    loaded = store.load_executions()
    assert ("Lens|Lyon|2026-09-28T19:00:00Z",
            "Resultado Final (1X2)", "1") in loaded

    # novo engine sobre o MESMO store hidrata a execucao
    engine2 = RealtimeOddsEngine(
        config=RealtimeConfig(interval_seconds=300.0),
        captures=[], store=store, bus=EventBus(), sport_keys=(),
    )
    engine2._load_persisted_executions()
    assert engine2._executions[
        "Lens|Lyon|2026-09-28T19:00:00Z|Resultado Final (1X2)|1"
    ] == (2.05, "2026-09-24T12:01:00Z")


# --------------------------------------------------------------------------
# P1 — best price auditavel (reconstrucao a partir de quotes <= T)
# --------------------------------------------------------------------------


def test_best_price_reconstruivel_ate_o_instante():
    from betgsn.realtime.state import MarketState

    state = MarketState()
    state.apply([
        _quote("A", 2.10, "2026-09-24T11:00:00Z"),
        _quote("B", 2.20, "2026-09-24T11:30:00Z"),
        _quote("C", 2.30, "2026-09-24T12:00:01Z"),  # FUTURO: rejeitada
    ], now=NOW)
    # Estado reconstruido contem apenas quotes <= NOW: best = 2.20 (B).
    quotes = state.event_quotes("Lens|Lyon|2026-09-28T19:00:00Z")
    best = max(q.price for q in quotes)
    assert best == pytest.approx(2.20)
    assert all(q.timestamp <= "2026-09-24T12:00:00Z" for q in quotes)
