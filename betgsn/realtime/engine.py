"""BETGSN :: realtime.engine — REAL-TIME ODDS ENGINE.

Loop continuo, um por provider, sobrevivendo a tudo:

  PROVIDER -> SCHEDULER -> CAPTURE -> NORMALIZE -> MATCH -> DEDUP ->
  SNAPSHOT (append-only) -> MOVEMENT -> MARKET VIEW -> SIGNAL ->
  EVENT BROADCAST (SSE)

Propriedades operacionais:
- cada provider tem intervalo proprio (rate limit / custo) e o
  scheduler nunca dispara antes da hora;
- erro de provider, timeout, rate limit ou ausencia de cobertura NAO
  derrubam o loop: viram health/erro contabilizado e o tick seguinte
  segue;
- nenhuma morte silenciosa: o heartbeat HEALTH_UPDATE continua
  publicando enquanto a thread viver; se ela morrer, `status()` mostra
  `running=False` com o ultimo erro;
- a persistencia e a MESMA do pipeline batch (LiveOddsCapture +
  OddsSnapshotStore): append-only, deduplicada por
  (match, market, outcome, book, timestamp).
"""

from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Mapping, Optional, Sequence

from ..backtest_sources import LiveOddsCapture
from ..odds_snapshots import OddsSnapshotStore
from .config import RealtimeConfig
from .events import (
    EVENT_DATA_QUALITY,
    EVENT_HEALTH_UPDATE,
    EVENT_MOVEMENT,
    EVENT_ODDS_UPDATE,
    EVENT_PROVIDER_STATUS,
    EVENT_SIGNAL_CREATED,
    EVENT_SIGNAL_EXPIRED,
    EventBus,
    RealtimeEvent,
    make_event,
)
from .freshness import FreshnessThresholds
from .movement import MovementEngine
from .signals import SignalEngine, SignalRules
from .state import LineKey, MarketState, QuoteProblem
from .views import build_event_view, event_views

#: Heartbeat do sistema: mesmo sem captura, o mundo sabe que estamos vivos.
HEARTBEAT_SECONDS = 30.0

#: Rotacao do log operacional JSONL (uma copia .1 mantida, depois sobrescrita).
LOG_ROTATE_BYTES = 20 * 1024 * 1024


def _default_log_path() -> str:
    from ..config import output_root

    return str(output_root() / "logs" / "realtime.jsonl")


class _JsonlLogger:
    """Log operacional append-only, uma linha por evento relevante.

    Sem biblioteca de logging: o formato e o minimo que a operacao
    precisa (timestamp + tipo + payload pequeno). Rotacao por tamanho
    evita arquivo infinito; falha de escrita NUNCA derruba o engine.
    """

    def __init__(self, path: str | None) -> None:
        self.path = path
        self._failures = 0

    def write(self, kind: str, payload: dict) -> None:
        if not self.path:
            return
        try:
            from pathlib import Path

            target = Path(self.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.stat().st_size > LOG_ROTATE_BYTES:
                target.replace(target.with_suffix(target.suffix + ".1"))
            import json

            with target.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "kind": kind,
                            **payload,
                        },
                        default=str,
                    )
                    + "\n"
                )
            self._failures = 0
        except Exception:  # noqa: BLE001 - log nunca derruba o engine
            self._failures += 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ProviderLoop:
    """Estado do scheduler de um provider."""

    name: str
    capture: LiveOddsCapture
    interval_seconds: float
    next_due: float = 0.0
    ticks: int = 0
    failures: int = 0
    last_tick_at: str = ""
    last_success_at: str = ""
    last_failure_at: str = ""
    last_error: str = ""

    def health_snapshot(self) -> dict:
        return {
            "provider": self.name,
            "interval_seconds": self.interval_seconds,
            "ticks": self.ticks,
            "failures": self.failures,
            "last_tick_at": self.last_tick_at,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_error": self.last_error,
        }


class RealtimeOddsEngine:
    """Motor em tempo real: captura -> estado -> movimento -> sinais."""

    def __init__(
        self,
        config: RealtimeConfig,
        captures: Sequence[tuple[str, LiveOddsCapture, float]],
        store: OddsSnapshotStore,
        bus: EventBus | None = None,
        sport_keys: Sequence[str] = (),
        clock: Callable[[], datetime] = _utc_now,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        log_path: str | None = None,
    ) -> None:
        self.config = config
        self.bus = bus or EventBus()
        self.store = store
        self.sport_keys = tuple(sport_keys)
        self.clock = clock
        self.heartbeat_seconds = heartbeat_seconds
        self.logger = _JsonlLogger(log_path if log_path is not None else _default_log_path())
        self.state = MarketState()
        self.movement = MovementEngine()
        self.signals = SignalEngine(
            rules=SignalRules(
                consensus_min_books=max(2, config.consensus_min_books),
                move_window_seconds=max(
                    60.0, config.rapid_move_seconds * 2
                ),
                ttl_seconds=config.signal_ttl_seconds,
                stale_after_seconds=config.recent_seconds,
            ),
            thresholds=FreshnessThresholds(
                fresh_seconds=config.fresh_seconds,
                recent_seconds=config.recent_seconds,
                stale_seconds=config.stale_seconds,
            ),
            now=clock,
        )
        self.loops = [
            ProviderLoop(name=name, capture=capture, interval_seconds=interval)
            for name, capture, interval in captures
        ]
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.started_at = ""
        self.stopped_at = ""
        self.last_error = ""
        self.last_heartbeat = ""
        self._pending_lines: set[LineKey] = set()
        self._pending_events: set[str] = set()
        self._pending_problems: list[QuoteProblem] = []
        self._last_movement_stamp: str = ""
        #: ultimo movimento observado por (evento, mercado) — o board
        #: mostra "o que mudou, quando e quem moveu"
        self._last_moves: dict[tuple[str, str], dict] = {}

    # ------------------------------------------------------------ ciclo

    def start(self) -> bool:
        """Sobe a thread do loop. Devolve False se ja estava rodando."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            if not self.loops:
                self.last_error = "nenhum provider configurado para o engine"
                return False
            self._stop.clear()
            self.started_at = self.clock().isoformat()
            self.logger.write("engine_start", {"providers": [l.name for l in self.loops]})
            self._thread = threading.Thread(
                target=self._run, name="betgsn-realtime", daemon=True
            )
            self._thread.start()
            return True

    def stop(self, timeout: float = 15.0) -> bool:
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        self._stop.set()
        thread.join(timeout=timeout)
        with self._lock:
            self._thread = None
        alive = thread.is_alive()
        if not alive:
            self.stopped_at = self.clock().isoformat()
        return not alive

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------ thread

    def _run(self) -> None:
        """Loop principal: nunca morre silenciosamente."""
        while not self._stop.is_set():
            try:
                self._tick_once()
            except Exception as exc:  # noqa: BLE001 - o loop sobrevive
                self.last_error = f"{exc}\n{traceback.format_exc(limit=4)}"
            sleep_seconds = self._sleep_until_next()
            self._stop.wait(timeout=sleep_seconds)

    def _tick_once(self) -> None:
        now = self.clock()
        now_ts = now.timestamp()
        for loop in self.loops:
            if now_ts >= loop.next_due:
                self._capture_tick(loop)
                loop.next_due = self.clock().timestamp() + loop.interval_seconds
        self._publish_heartbeat()
        self._stop.wait(timeout=0.05)

    def _sleep_until_next(self) -> float:
        now_ts = self.clock().timestamp()
        next_due = min(
            (loop.next_due for loop in self.loops), default=now_ts
        )
        seconds = max(0.25, min(next_due - now_ts, self.heartbeat_seconds))
        return seconds

    def _publish_heartbeat(self) -> None:
        now = self.clock()
        stamp = now.isoformat()
        self.last_heartbeat = stamp
        self.bus.publish(
            make_event(
                EVENT_HEALTH_UPDATE,
                {
                    "running": self.running,
                    "ticks": sum(loop.ticks for loop in self.loops),
                    "last_quote_at": self._last_quote_at(),
                    "last_movement_at": self._last_movement_at(),
                    "last_signal_at": self._last_signal_at(),
                },
                observed_at=stamp,
            )
        )

    # ------------------------------------------------------------ captura

    def _capture_tick(self, loop: ProviderLoop) -> None:
        now = self.clock()
        loop.ticks += 1
        loop.last_tick_at = now.isoformat()
        self._pending_lines = set()
        self._pending_events = set()
        self._pending_problems = []

        try:
            report = loop.capture.capture(list(self.sport_keys), now=now)
        except Exception as exc:  # noqa: BLE001 - provider nao derruba o loop
            loop.failures += 1
            loop.last_failure_at = self.clock().isoformat()
            loop.last_error = str(exc)[:500]
            self.logger.write(
                "provider_error",
                {"provider": loop.name, "error": loop.last_error},
            )
            self.bus.publish(
                make_event(
                    EVENT_PROVIDER_STATUS,
                    {
                        "provider": loop.name,
                        "state": "ERROR",
                        "error": loop.last_error,
                    },
                    observed_at=self.clock().isoformat(),
                )
            )
            return

        for error in report.errors:
            if "observer/" in error:
                loop.last_error = error[:500]

        self._ingest_pending(now)
        errors = [e for e in report.errors if "observer/" not in e]
        if not errors:
            loop.last_success_at = self.clock().isoformat()
        elif loop.last_error == "" or not loop.last_error:
            loop.last_error = "; ".join(errors)[:500]
        self.logger.write(
            "tick",
            {
                "provider": loop.name,
                "captured_at": report.captured_at,
                "observations_saved": report.observations_saved,
                "events_matched": report.events_matched,
                "events_unmatched": report.events_unmatched,
                "events_ambiguous": report.events_ambiguous,
                "errors": len(report.errors),
            },
        )
        self.bus.publish(
            make_event(
                EVENT_PROVIDER_STATUS,
                {
                    "provider": loop.name,
                    "state": "OK" if not errors else "OK_WITH_ERRORS",
                    "captured_at": report.captured_at,
                    "observations_saved": report.observations_saved,
                    "events_matched": report.events_matched,
                    "events_unmatched": report.events_unmatched,
                    "errors": list(report.errors),
                },
                observed_at=report.captured_at,
            )
        )

    def _observer(
        self,
        provider: str,
        quotes: Sequence,
        observations: Sequence,
        match_keys: Mapping[str, str],
    ) -> None:
        """Recebe o lote do LiveOddsCapture e alimenta o estado.

        Roda NA MESMA THREAD do engine (callback sincrono): sem corrida.
        Quotes casadas sao re-chaveadas para a event_key DO FIXTURE — a
        mesma identidade que o store gravou.
        """
        remapped = []
        for quote in quotes:
            if quote.event_id in match_keys:
                remapped.append(
                    replace(
                        quote,
                        event_id=match_keys[quote.event_id],
                    )
                )
            else:
                remapped.append(quote)
        matched_ids = set(match_keys.values())

        changed: set[LineKey] = set()
        for quote in remapped:
            key: LineKey = (
                quote.event_id,
                quote.market,
                quote.selection,
                quote.bookmaker,
            )
            current = self.state.latest.get(key)
            if current is None or quote.timestamp > current.timestamp:
                changed.add(key)

        affected_events, affected_lines, problems = self.state.apply(
            remapped, now=self.clock(), matched_keys=matched_ids
        )
        self._pending_lines.update(changed)
        self._pending_events.update(affected_events)
        self._pending_problems.extend(problems)

    def _ingest_pending(self, now: datetime) -> None:
        """Processa o lote capturado: movimento -> views -> sinais."""
        if self._pending_problems:
            self.bus.publish(
                make_event(
                    EVENT_DATA_QUALITY,
                    {
                        "problems": [
                            p.to_dict() for p in self._pending_problems
                        ]
                    },
                    observed_at=now.isoformat(),
                )
            )

        movements = self.movement.process(self.state, sorted(self._pending_lines))
        for movement in movements:
            if movement.last_move_at:
                self._last_movement_stamp = movement.last_move_at
            self._last_moves[(movement.event_key, movement.market)] = {
                "event_key": movement.event_key,
                "market": movement.market,
                "books_moved": sorted(movement.books_moved),
                "last_move_at": movement.last_move_at,
                "moves": [m.to_dict() for m in movement.moves[-8:]],
            }
            self.bus.publish(
                make_event(
                    EVENT_MOVEMENT,
                    movement.to_dict(),
                    observed_at=movement.last_move_at or now.isoformat(),
                )
            )

        if self._pending_events:
            self.bus.publish(
                make_event(
                    EVENT_ODDS_UPDATE,
                    {
                        "events": sorted(self._pending_events),
                        "quotes": len(self._pending_lines),
                    },
                    observed_at=now.isoformat(),
                )
            )

        thresholds = self.signals.thresholds
        views = []
        for event_key in sorted(self._pending_events):
            view = build_event_view(self.state, event_key, thresholds, now)
            if view is not None:
                views.append(view)
        if views or movements:
            evaluation = self.signals.evaluate(views, movements)
            for signal in evaluation.created:
                self.logger.write(
                    "signal_created",
                    {
                        "signal_id": signal.signal_id,
                        "signal_type": signal.signal_type,
                        "event": signal.event_key,
                        "market": signal.market,
                        "selection": signal.selection,
                        "reason": signal.reason[:300],
                    },
                )
                self.bus.publish(
                    make_event(
                        EVENT_SIGNAL_CREATED,
                        signal.to_dict(),
                        observed_at=signal.observed_at,
                    )
                )
            for signal in evaluation.expired:
                self.bus.publish(
                    make_event(
                        EVENT_SIGNAL_EXPIRED,
                        signal.to_dict(),
                        observed_at=signal.observed_at,
                    )
                )

        self._pending_lines = set()
        self._pending_events = set()
        self._pending_problems = []

    # ------------------------------------------------------------ leitura

    def _last_quote_at(self) -> str:
        stamps = [
            q.timestamp for q in self.state.latest.values()
        ]
        return max(stamps) if stamps else ""

    def _last_movement_at(self) -> str:
        return getattr(self, "_last_movement_stamp", "")

    def _last_signal_at(self) -> str:
        active = self.signals.active_signals()
        stamps = [s.observed_at for s in active]
        return max(stamps) if stamps else ""

    def status(self) -> dict:
        """Retrato do sistema: o usuario precisa saber se esta vivo."""
        with self._lock:
            now = self.clock()
            store_health = {}
            try:
                store_health = self.store.load_provider_health()
            except Exception:  # noqa: BLE001 - health e observabilidade
                store_health = {}
            return {
                "running": self.running,
                "started_at": self.started_at,
                "stopped_at": self.stopped_at,
                "last_error": self.last_error,
                "last_heartbeat": self.last_heartbeat,
                "sport_keys": list(self.sport_keys),
                "interval_seconds": self.config.interval_seconds,
                "providers": {
                    loop.name: loop.health_snapshot()
                    for loop in self.loops
                },
                "provider_health_store": store_health,
                "state": self.state.stats(),
                "signals": self.signals.stats(),
                "bus": {
                    "published": self.bus.published_count,
                    "deduped": self.bus.deduped_count,
                    "dropped": self.bus.dropped_count,
                    "subscribers": self.bus.subscriber_count(),
                    "last_event": (
                        self.bus.last_event.to_dict()
                        if self.bus.last_event
                        else None
                    ),
                },
                "last_quote_at": self._last_quote_at(),
                "last_movement_at": self._last_movement_at(),
                "last_signal_at": self._last_signal_at(),
                "now": now.isoformat(),
            }

    def board(self) -> list[dict]:
        """Views de todos os eventos para o signal board."""
        with self._lock:
            return [
                view.to_dict()
                for view in event_views(
                    self.state, self.signals.thresholds, self.clock()
                )
            ]

    def last_moves(self) -> dict[str, dict]:
        """Ultimo movimento por mercado, indexado por "event|market"."""
        with self._lock:
            return {
                f"{event}|{market}": data
                for (event, market), data in self._last_moves.items()
            }

    def event_view(self, event_key: str) -> dict | None:
        with self._lock:
            view = build_event_view(
                self.state,
                event_key,
                self.signals.thresholds,
                self.clock(),
            )
            return view.to_dict() if view else None

    def signals_snapshot(self, event_key: str | None = None) -> list[dict]:
        with self._lock:
            return [
                signal.to_dict()
                for signal in self.signals.active_signals(event_key)
            ]

    def problems(self, limit: int = 50) -> list[dict]:
        with self._lock:
            recent = list(self.state.problems)[-limit:]
            return [p.to_dict() for p in recent]


def build_engine_from_env(
    config: RealtimeConfig | None = None,
    store: OddsSnapshotStore | None = None,
    bus: EventBus | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> RealtimeOddsEngine:
    """Monta o engine operacional: providers configurados + fixtures reais.

    Fontes e contratos EXATAMENTE como o CLI `--capture-odds`:
    registry por prioridade (filtrado aos providers operacionais),
    fixtures FDUK + aliases versionados, store canonico. Nada e
    inventado aqui — sem chave configurada, o provider simplesmente
    nao entra (e o engine avisa se sobrar vazio).
    """
    from ..football_data_uk import FootballDataClient
    from ..odds_registry import default_odds_registry
    from ..providers import sport_keys_for_divisions
    from ..team_aliases import load_team_aliases

    config = config or RealtimeConfig.from_env()
    registry = default_odds_registry()
    available = registry.available_providers()
    selected = [
        (name, provider)
        for name, provider in available
        if name in config.provider_names
    ]

    fixtures = FootballDataClient().load_fixtures()
    aliases = load_team_aliases()

    if config.sport_keys:
        sport_keys = tuple(config.sport_keys)
    else:
        divisions = sorted(
            {fx.division for fx in fixtures if fx.has_odds and fx.has_kickoff}
        )
        keys, _unmapped = sport_keys_for_divisions(divisions)
        sport_keys = tuple(keys)

    store = store or OddsSnapshotStore()
    captures: list[tuple[str, LiveOddsCapture, float]] = []
    for name, provider in selected:
        capture = LiveOddsCapture(
            providers=[provider],
            regions=config.regions,
            markets=config.markets,
            store=store,
            fixtures=fixtures or None,
            aliases=aliases,
        )
        captures.append((name, capture, config.interval_seconds))

    engine = RealtimeOddsEngine(
        config=config,
        captures=captures,
        store=store,
        bus=bus,
        sport_keys=sport_keys,
        clock=clock,
    )
    #: liga o observer APOS construir o engine (o callback precisa dele)
    for loop in engine.loops:
        loop.capture.observer = engine._observer
    return engine
