"""BETGSN :: api.realtime — rotas do terminal de mercado em tempo real.

Rotas (todas de leitura, exceto start/stop do engine):

  GET  /api/realtime/status     retrato do sistema (vivo? ultimos eventos?)
  GET  /api/realtime/board      signal board: eventos + mercados + sinais
  GET  /api/realtime/match      detalhe de um evento (grid, timeline, fair)
  GET  /api/realtime/signals   sinais ativos com status e motivo
  GET  /api/realtime/providers saude dos providers (loop + store)
  GET  /api/realtime/stream     SSE: ODDS_UPDATE / MOVEMENT / SIGNAL_* /
                                PROVIDER_STATUS / HEALTH_UPDATE
  POST /api/realtime/start      sobe o engine (idempotente)
  POST /api/realtime/stop       para o engine (graceful)

Bootstrap: construir o engine carrega fixtures (I/O). Para nao travar
o startup da API, a construcao roda em thread e as rotas respondem com
estado `building` ate o engine estar pronto — nunca com dado falso.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..config import production_policy_fingerprint
from ..realtime.engine import FairOverride, RealtimeOddsEngine, build_engine_from_env
from ..realtime.events import EventBus


def _policy_fingerprint() -> str:
    return production_policy_fingerprint()

_boot_lock = threading.Lock()
_engine: Optional[RealtimeOddsEngine] = None
_building = False
_boot_error = ""


def _bootstrap() -> None:
    global _engine, _building, _boot_error
    try:
        engine = build_engine_from_env()
        engine.start()
        with _boot_lock:
            _engine = engine
            _building = False
    except Exception as exc:  # noqa: BLE001 - erro de bootstrap e explícito
        with _boot_lock:
            _building = False
            _boot_error = f"{type(exc).__name__}: {exc}"


def ensure_engine_started(force: bool = False) -> None:
    """Soba o engine em background se ainda nao estiver rodando.

    `force=True` (pedido explicito do usuario via POST start) limpa um
    erro de bootstrap anterior e tenta de novo — sem isso, uma falha
    temporaria (fixtures ausentes, env invalido) deixaria o engine
    permanentemente morto ate reiniciar a API.
    """
    global _building, _boot_error
    with _boot_lock:
        if force:
            _boot_error = ""
        if _engine is not None and _engine.running:
            return
        if _engine is not None and not _engine.running:
            if _engine.start():
                return
            #: engine existente nao conseguiu subir (ex.: sem providers):
            #: constroi de novo — o ambiente pode ter mudado.
        if _building:
            return
        if _boot_error and not force:
            return
        _building = True
    threading.Thread(
        target=_bootstrap, name="betgsn-realtime-boot", daemon=True
    ).start()


def engine() -> Optional[RealtimeOddsEngine]:
    return _engine


def engine_or_503() -> RealtimeOddsEngine:
    if _engine is None:
        if _building:
            raise HTTPException(
                status_code=503,
                detail="realtime engine ainda inicializando (fixtures/providers)",
            )
        raise HTTPException(
            status_code=503,
            detail=(
                "realtime engine nao esta rodando: dispare "
                "POST /api/realtime/start"
                + (f" (ultimo erro: {_boot_error})" if _boot_error else "")
            ),
        )
    return _engine


def boot_state() -> dict:
    with _boot_lock:
        return {
            "building": _building,
            "error": _boot_error,
            "engine_ready": _engine is not None,
            "engine_running": bool(_engine and _engine.running),
        }


# --------------------------------------------------------------------------
# helpers de dominio
# --------------------------------------------------------------------------


def _model_comparison(event_key: str) -> dict:
    """MARKET vs MODEL quando existe snapshot do pipeline — separados.

    O snapshot do pipeline e a UNICA fonte de preco de modelo. Sem
    snapshot ou sem jogo correspondente: NO_MODEL — nunca estimativa.
    """
    from ..api.service import service as pipeline_service
    from ..odds_normalize import event_key as make_event_key

    try:
        snapshot = pipeline_service.snapshot(auto=False)
    except Exception:  # noqa: BLE001 - sem snapshot: NO_MODEL explicito
        return {"status": "NO_MODEL", "source": "pipeline", "model": None}

    for analysis in snapshot.result.analyses:
        fixture = analysis.fixture
        if not fixture.kickoff:
            continue
        try:
            if make_event_key(fixture.home, fixture.away, fixture.kickoff) != event_key:
                continue
        except Exception:  # noqa: BLE001 - kickoff ilegivel: pula jogo
            continue
        markets = {
            market: {
                selection: round(probability, 6)
                for selection, probability in probs.items()
            }
            for market, probs in analysis.markets.items()
        }
        return {
            "status": "MODEL_AVAILABLE",
            "source": "pipeline",
            "generated_at": snapshot.generated_at,
            "model_status": (
                "EXPERIMENTAL"
                if snapshot.source != "real" else "EXPERIMENTAL"
            ),
            "model": {
                "lambda_home": analysis.lambdas[0],
                "lambda_away": analysis.lambdas[1],
                "markets": markets,
            },
        }
    return {"status": "NO_MODEL", "source": "pipeline", "model": None}


def _movement_timeline(event_key: str, engine_ref: RealtimeOddsEngine) -> list[dict]:
    """Timeline do store (append-only): toda a historia observada."""
    try:
        observations = engine_ref.store.all_observations(event_key)
    except Exception:  # noqa: BLE001 - store ilegivel: lista vazia explicita
        return []
    timeline: list[dict] = []
    for obs in observations:
        timeline.append(
            {
                "market": obs.market,
                "selection": obs.outcome,
                "bookmaker": obs.bookmaker,
                "price": obs.odd,
                "timestamp": obs.timestamp,
                "provider": obs.provider,
                "minutes_before_kickoff": round(
                    obs.minutes_before_kickoff, 1
                ),
            }
        )
    timeline.sort(key=lambda row: (row["timestamp"], row["bookmaker"]))
    return timeline


def _clv_summary(event_key: str, engine_ref: RealtimeOddsEngine) -> dict:
    """Estado CLV do evento — sem fabricar close."""
    try:
        entries = [
            e for e in engine_ref.store.clv_entries()
            if e.match_key == event_key
        ]
    except Exception:  # noqa: BLE001 - CLV inacessivel: n=0 explicito
        entries = []
    if not entries:
        return {"n": 0, "status": "NO_ENTRIES", "entries": []}
    return {
        "n": len(entries),
        "status": "ENTRIES_PRESENT",
        "entries": [
            {
                "market": e.market,
                "outcome": e.outcome,
                "entry_odd": e.entry_odd,
                "entry_timestamp": e.entry_timestamp,
                "kickoff": e.kickoff,
                "source": e.source,
                "execution_status": e.execution_status,
            }
            for e in entries
        ],
    }


# --------------------------------------------------------------------------
# registro das rotas
# --------------------------------------------------------------------------


def register_realtime_routes(app: FastAPI) -> None:
    from ..realtime.alphas import default_alphas

    @app.get("/api/realtime/status", tags=["realtime"])
    def realtime_status() -> dict:
        if _engine is None:
            return {"engine": None, "boot": boot_state()}
        return {"engine": _engine.status(), "boot": boot_state()}

    @app.post("/api/realtime/start", tags=["realtime"])
    def realtime_start() -> dict:
        ensure_engine_started(force=True)
        return {"started": True, "boot": boot_state()}

    @app.post("/api/realtime/stop", tags=["realtime"])
    def realtime_stop() -> dict:
        if _engine is None:
            return {"stopped": True, "boot": boot_state()}
        stopped = _engine.stop()
        return {
            "stopped": stopped,
            "boot": boot_state(),
            "last_error": _engine.last_error,
        }

    @app.get("/api/realtime/board", tags=["realtime"])
    def realtime_board(
        market: Optional[str] = Query(None),
        signal_type: Optional[str] = Query(None),
        min_books: int = Query(0, ge=0, le=50),
    ) -> dict:
        eng = engine_or_503()
        events = eng.board()
        signals = eng.signals_snapshot()
        if market:
            events = [
                e for e in events
                if any(m["market"] == market for m in e["markets"])
            ]
        if signal_type:
            signals = [s for s in signals if s["signal_type"] == signal_type]
        if min_books:
            events = [
                e for e in events
                if any(m["n_books"] >= min_books for m in e["markets"])
            ]
        return {
            "generated_at": eng.clock().isoformat(),
            "events": events,
            "signals": signals,
            "priced_signals": eng.priced_signals(),
            "policy_fingerprint": _policy_fingerprint(),
            "last_moves": eng.last_moves(),
            "problems": eng.problems(50),
            "boot": boot_state(),
        }

    @app.get("/api/realtime/match", tags=["realtime"])
    def realtime_match(event_key: str = Query(...)) -> dict:
        eng = engine_or_503()
        view = eng.event_view(event_key)
        if view is None:
            raise HTTPException(
                status_code=404,
                detail="evento desconhecido do engine em tempo real",
            )
        return {
            "event": view,
            "signals": eng.signals_snapshot(event_key),
            "priced_signals": eng.priced_signals(event_key),
            "policy_fingerprint": _policy_fingerprint(),
            "execution_diagnostics": eng.execution_diagnostics(event_key),
            "execution_erosion": eng.execution_erosion(event_key),
            "movement_timeline": _movement_timeline(event_key, eng),
            "model_comparison": _model_comparison(event_key),
            "clv": _clv_summary(event_key, eng),
            "problems": [
                p for p in eng.problems(200)
                if p.get("event_id") == event_key
            ],
        }

    @app.get("/api/realtime/priced-signals", tags=["realtime"])
    def realtime_priced_signals(
        event_key: Optional[str] = Query(None),
    ) -> dict:
        eng = engine_or_503()
        return {
            "generated_at": eng.clock().isoformat(),
            "policy_fingerprint": _policy_fingerprint(),
            "priced_signals": eng.priced_signals(event_key),
            "execution_erosion": eng.execution_erosion(event_key),
        }

    @app.post("/api/realtime/executions", tags=["realtime"])
    async def realtime_record_execution(request: Request) -> dict:
        """Registra uma fill medida.

        Sem esta chamada, `execution_status` permanece UNKNOWN e nenhum
        gap é exposto. Requer `executed_at` ISO-8601: fills sem selo
        temporal viram EXPIRED no próximo tick.
        """
        eng = engine_or_503()
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "invalid JSON body")
        required = ("event_key", "market", "selection", "executed_price", "executed_at")
        missing = [k for k in required if k not in body]
        if missing:
            raise HTTPException(400, f"missing: {sorted(missing)}")
        try:
            key = eng.record_execution(
                event_key=str(body["event_key"]),
                market=str(body["market"]),
                selection=str(body["selection"]),
                executed_price=float(body["executed_price"]),
                executed_at=str(body["executed_at"]),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        return {"key": key, "policy_fingerprint": _policy_fingerprint()}

    @app.delete("/api/realtime/executions", tags=["realtime"])
    def realtime_clear_execution(
        event_key: str = Query(...),
        market: str = Query(...),
        selection: str = Query(...),
    ) -> dict:
        """Remove uma fill previamente registrada — usar quando a
        execução expirou ou foi cancelada. Sem execução ativa o próximo
        tick recompõe o gate com EXECUTION PENDING."""
        eng = engine_or_503()
        eng.clear_execution(event_key=event_key, market=market, selection=selection)
        return {"cleared": True}

    @app.post("/api/realtime/fair-override", tags=["realtime"])
    async def realtime_register_fair_override(request: Request) -> dict:
        """Injeta o fair calibrado por janela para uma seleção.

        Requer `calibration_fingerprint`, `window_id`, `method`, `n` e
        `prob`. Sem esse selo o bloco MARKET/PROVENANCE permanece
        PENDING — evidência offline nunca é inferida no live.
        """
        eng = engine_or_503()
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "invalid JSON body")
        required = ("event_key", "market", "selection", "prob",
                    "window_id", "method", "n", "calibration_fingerprint")
        missing = [k for k in required if k not in body]
        if missing:
            raise HTTPException(400, f"missing: {sorted(missing)}")
        try:
            override = FairOverride(
                prob=float(body["prob"]),
                window_id=str(body["window_id"]),
                method=str(body["method"]),
                n=int(body["n"]),
                calibration_fingerprint=str(body["calibration_fingerprint"]),
            )
            key = eng.register_fair_override(
                event_key=str(body["event_key"]),
                market=str(body["market"]),
                selection=str(body["selection"]),
                override=override,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        return {"key": key, "policy_fingerprint": _policy_fingerprint()}

    @app.delete("/api/realtime/fair-override", tags=["realtime"])
    def realtime_clear_fair_override(
        event_key: str = Query(...),
        market: str = Query(...),
        selection: str = Query(...),
    ) -> dict:
        eng = engine_or_503()
        eng.clear_fair_override(event_key=event_key, market=market, selection=selection)
        return {"cleared": True}

    @app.get("/api/realtime/signals", tags=["realtime"])
    def realtime_signals(
        event_key: Optional[str] = Query(None),
        signal_type: Optional[str] = Query(None),
    ) -> dict:
        eng = engine_or_503()
        signals = eng.signals_snapshot(event_key)
        if signal_type:
            signals = [s for s in signals if s["signal_type"] == signal_type]
        return {
            "generated_at": eng.clock().isoformat(),
            "signals": signals,
            "alphas": {
                alpha_id: spec.to_dict()
                for alpha_id, spec in default_alphas().items()
            },
        }

    @app.get("/api/realtime/providers", tags=["realtime"])
    def realtime_providers() -> dict:
        if _engine is None:
            return {"providers": {}, "boot": boot_state()}
        status = _engine.status()
        return {
            "providers": status["providers"],
            "provider_health_store": status["provider_health_store"],
            "state": status["state"],
            "boot": boot_state(),
        }

    @app.get("/api/realtime/stream", tags=["realtime"])
    async def realtime_stream(
        request: Request,
        max_seconds: float = Query(3600.0, ge=1.0, le=86400.0),
    ) -> StreamingResponse:
        """SSE do terminal. `max_seconds` encerra a conexao com encode de
        `event: CLOSE` — o cliente reconecta (EventSource faz sozinho).
        Isso evita conexoes zumbis e mantem o stream testavel.
        """
        import time as _time

        eng = engine_or_503()
        bus: EventBus = eng.bus
        handle, queue = bus.subscribe()
        started = _time.monotonic()

        async def event_source():
            try:
                hello = {
                    "event_type": "HELLO",
                    "event_timestamp": eng.clock().isoformat(),
                    "payload": {"status": "connected"},
                }
                yield f"event: HELLO\ndata: {json.dumps(hello)}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    if _time.monotonic() - started >= max_seconds:
                        yield (
                            "event: CLOSE\n"
                            f'data: {json.dumps({"reason": "max_seconds"})}\n\n'
                        )
                        break
                    events = bus.drain(queue, limit=64)
                    for event in events:
                        payload = json.dumps(event.to_dict(), default=str)
                        yield (
                            f"id: {event.event_id}\n"
                            f"event: {event.type}\n"
                            f"data: {payload}\n\n"
                        )
                    if not events:
                        await asyncio.sleep(0.5)
            finally:
                bus.unsubscribe(handle)

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
