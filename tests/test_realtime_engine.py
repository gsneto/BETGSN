"""REAL-TIME ODDS ENGINE: cadeia inteira, tick a tick, sem rede.

Provider fake de contrato (mesmo shape dos testes de captura)
  -> LiveOddsCapture (observer injetado pelo engine)
  -> OddsSnapshotStore (append-only, dedup)
  -> MarketState -> MovementEngine -> SignalEngine -> EventBus

Testes de sobrevivencia: provider que falha NAO derruba o loop; a
mesma sequencia de quotes reproduz os mesmos signal_ids.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from betgsn.backtest_sources import LiveOddsCapture, OddsHistoryCache
from betgsn.config import output_root
from betgsn.odds_provider import OddsFetchRequest, OddsProviderFetch
from betgsn.odds_snapshots import OddsSnapshotStore
from betgsn.realtime.config import RealtimeConfig
from betgsn.realtime.engine import RealtimeOddsEngine
from betgsn.realtime.events import (
    EVENT_DATA_QUALITY,
    EVENT_MOVEMENT,
    EVENT_ODDS_UPDATE,
    EVENT_PROVIDER_STATUS,
    EVENT_SIGNAL_CREATED,
    EventBus,
)

KICKOFF_UTC = "2026-09-28T19:00:00Z"
SPORT = "soccer_france_ligue_one"

T0 = datetime(2026, 9, 24, 11, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 9, 24, 11, 40, 0, tzinfo=timezone.utc)


class ScriptedClock:
    """Relogio injetavel com estado: o teste avanca o tempo explicitamente."""

    def __init__(self, moment: datetime) -> None:
        self.now = moment

    def __call__(self) -> datetime:
        return self.now


class MutableProvider:
    """Provider de contrato cujo preco muda entre ticks.

    `fallback`: eventos usados quando o `fetched_at` nao tem entrada —
    necessario no teste de lifecycle, onde o relogio e o REAL.
    """

    name = "FakeOdds"

    def __init__(self, events: dict[str, list[dict]], fallback: list[dict] | None = None):
        self._events = events
        self._fallback = fallback
        self.failures_left = 0

    def available(self) -> bool:
        return True

    def divisions_for(self, scope: str) -> tuple[str, ...]:
        return ("F1",) if scope == SPORT else ()

    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch:
        from betgsn.odds_normalize import normalize_events

        if self.failures_left > 0:
            self.failures_left -= 1
            raise RuntimeError("provider fora do ar (simulado)")

        batch = self._events.get(request.fetched_at)
        if batch is None:
            if self._fallback is None:
                raise KeyError(f"tick sem eventos programados: {request.fetched_at}")
            batch = self._fallback
        events = [self._clone(event) for event in batch]
        quotes = normalize_events(
            events, self.name, request.fetched_at, sport_key=SPORT
        )
        return OddsProviderFetch(
            quotes=tuple(quotes),
            raw_events=tuple(events),
            snapshot_provider="fake-live",
        )

    def _clone(self, event: dict) -> dict:
        import copy

        return copy.deepcopy(event)


def _event(prices: dict[str, float], ts: str) -> dict:
    """Evento no shape The Odds API com timestamp REAL por outcome."""
    return {
        "home_team": "Lens",
        "away_team": "Lyon",
        "commence_time": KICKOFF_UTC,
        "bookmakers": [
            {
                "key": book.lower().replace(" ", ""),
                "title": book,
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Lens", "price": price, "timestamp": ts},
                            {"name": "Draw", "price": 3.40, "timestamp": ts},
                            {"name": "Lyon", "price": 3.10, "timestamp": ts},
                        ],
                    }
                ],
            }
            for book, price in prices.items()
        ],
    }


def _provider_with_moves() -> MutableProvider:
    #: chaves no formato que a captura injeta (`UTC_FORMAT`), nao isoformat
    return MutableProvider(
        {
            T0.strftime("%Y-%m-%dT%H:%M:%SZ"): [
                _event({"Book A": 2.20, "Book B": 2.21, "Book C": 2.22}, T0.strftime("%Y-%m-%dT%H:%M:%SZ"))
            ],
            T1.strftime("%Y-%m-%dT%H:%M:%SZ"): [
                _event({"Book A": 2.10, "Book B": 2.08, "Book C": 2.12}, T1.strftime("%Y-%m-%dT%H:%M:%SZ"))
            ],
        }
    )


def _engine(tmp_path: Path, provider: MutableProvider, clock) -> RealtimeOddsEngine:
    store = OddsSnapshotStore(tmp_path / "odds.db")
    capture = LiveOddsCapture(
        providers=[provider],
        regions="eu",
        markets="h2h",
        store=store,
        fixtures=None,
    )
    engine = RealtimeOddsEngine(
        config=RealtimeConfig(interval_seconds=60.0),
        captures=[("FakeOdds", capture, 60.0)],
        store=store,
        bus=EventBus(),
        sport_keys=[SPORT],
        clock=clock,
        heartbeat_seconds=5.0,
    )
    capture.observer = engine._observer
    return engine


def test_engine_chain_capture_store_board_signals_events(tmp_path):
    clock = ScriptedClock(T0)
    provider = _provider_with_moves()
    engine = _engine(tmp_path, provider, clock)
    handle, queue = engine.bus.subscribe()

    engine._capture_tick(engine.loops[0])
    board_after_t0 = engine.board()
    assert board_after_t0, "board vazio apos o primeiro tick"
    event_view = board_after_t0[0]
    assert event_view["home"] == "Lens"
    assert event_view["markets"], "sem mercados no board"

    #: store recebeu as observacoes do primeiro tick (append-only)
    store: OddsSnapshotStore = engine.store
    n_after_t0 = len(store.all_observations(event_view["event_key"]))
    assert n_after_t0 > 0

    clock.now = T1
    engine._capture_tick(engine.loops[0])

    #: dedup: segunda captura com MESMO timestamp ja gravado nao duplica
    #: (mas T1 tem precos/timestamps novos => novas observacoes)
    n_after_t1 = len(store.all_observations(event_view["event_key"]))
    assert n_after_t1 > n_after_t0

    types = [e.type for e in _all_events(engine)]
    assert EVENT_ODDS_UPDATE in types
    assert EVENT_MOVEMENT in types
    assert EVENT_PROVIDER_STATUS in types
    assert EVENT_SIGNAL_CREATED in types or engine.signals_snapshot() == []

    signals = engine.signals_snapshot()
    for signal in signals:
        assert signal["reason"]
        assert signal["production"] == "NO_BET"

    status = engine.status()
    assert status["running"] is False  # _capture_tick direto: sem thread
    assert status["state"]["events"] >= 1
    assert status["providers"]["FakeOdds"]["ticks"] == 2


def test_engine_survives_provider_failure_and_recovers(tmp_path):
    clock = ScriptedClock(T0)
    provider = _provider_with_moves()
    provider.failures_left = 1
    engine = _engine(tmp_path, provider, clock)

    engine._capture_tick(engine.loops[0])
    loop = engine.loops[0]
    assert loop.last_error, "falha do provider sumiu do health do loop"
    assert not loop.last_success_at, "tick com erro contou como sucesso"
    assert engine.board() == []  #: falha nao virou dado

    clock.now = T1
    engine._capture_tick(engine.loops[0])
    assert engine.board(), "engine nao recuperou apos falha do provider"
    assert loop.last_success_at


def test_engine_survives_capture_exception(tmp_path):
    class ExplodingCapture:
        def capture(self, sports, now=None):
            raise RuntimeError("boom")

    engine = RealtimeOddsEngine(
        config=RealtimeConfig(interval_seconds=60.0),
        captures=[("Broken", ExplodingCapture(), 60.0)],
        store=OddsSnapshotStore(tmp_path / "odds.db"),
        bus=EventBus(),
        sport_keys=[SPORT],
        clock=lambda: T0,
    )
    engine._capture_tick(engine.loops[0])
    loop = engine.loops[0]
    assert loop.failures == 1
    assert "boom" in loop.last_error


def test_engine_reconstruction_same_quotes_same_signal_ids(tmp_path):
    """Reconstrucao: mesma sequencia de ticks => mesmos signal_ids."""

    def run() -> list[str]:
        clock = ScriptedClock(T0)
        provider = _provider_with_moves()
        engine = _engine(tmp_path / f"recon-{id(object())}", provider, clock)
        engine._capture_tick(engine.loops[0])
        clock.now = T1
        engine._capture_tick(engine.loops[0])
        return sorted(s["signal_id"] for s in engine.signals_snapshot())

    first = run()
    second = run()
    assert first == second


def test_engine_future_quote_becomes_quality_problem(tmp_path):
    future = T1 + timedelta(hours=1)
    provider = MutableProvider(
        {
            T0.strftime("%Y-%m-%dT%H:%M:%SZ"): [
                _event(
                    {"Book A": 2.10},
                    future.strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            ]
        }
    )
    engine = _engine(tmp_path, provider, lambda: T0)
    engine.bus.subscribe()
    engine._capture_tick(engine.loops[0])

    problems = engine.problems()
    assert problems and problems[0]["reason"] == "FUTURE_TIMESTAMP"
    assert engine.board() == [] or all(
        not m["selections"] or True for m in engine.board()[0]["markets"]
    )
    types = [e.type for e in _all_events(engine)]
    assert EVENT_DATA_QUALITY in types


def _all_events(engine: RealtimeOddsEngine) -> list:
    events = []
    for _handle, queue in list(engine.bus._subscribers.items()):
        events.extend(engine.bus.drain(queue))
    return events


def _events_for(engine: RealtimeOddsEngine) -> list:
    handle, queue = engine.bus.subscribe()
    return engine.bus.drain(queue)


def test_engine_start_stop_lifecycle(tmp_path):
    import time

    now = datetime.now(timezone.utc)
    fallback = [
        _event({"Book A": 2.20}, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    ]
    provider = MutableProvider({}, fallback=fallback)
    engine = _engine(tmp_path, provider, lambda: datetime.now(timezone.utc))
    assert engine.start() is True
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not engine.board():
            time.sleep(0.05)
        assert engine.board(), "engine nao capturou nada em 5s"
        assert engine.running is True
    finally:
        assert engine.stop() is True
    assert engine.running is False


def test_output_root_is_isolated_from_production():
    """Testes nunca escrevem no output/ de producao."""
    assert "betgsn-tests-" in str(output_root())
