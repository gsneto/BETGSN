"""BETGSN :: api.server — aplicacao FastAPI.

Rotas (todas sob /api):

    GET  /api/health                       status do sistema
    GET  /api/config                       configuracao atual do snapshot
    GET  /api/dashboard                    resumo + KPIs
    POST /api/recalculate                  roda o pipeline com nova configuracao
    GET  /api/signals                      relatorio de sinais completo
    GET  /api/games                        analise por jogo
    GET  /api/odds                         visao geral de casas
    GET  /api/odds/comparison              comparacao multi-casa de um mercado
    GET  /api/stats                        estatisticas e breakdowns
    GET  /api/model                        descricao do modelo
    GET  /api/model/performance            backtest (calibracao + aposta)

    GET  /api/backtest/options             periodo, competicoes, mercados, defaults
    POST /api/backtest/run                 dispara o backtest em background
    GET  /api/backtest/status              progresso da execucao atual
    GET  /api/backtest/runs                execucoes salvas
    GET  /api/backtest/runs/{id}           metricas completas de uma execucao
    GET  /api/backtest/runs/{id}/signals   sinais paginados e pesquisaveis
    DEL  /api/backtest/runs/{id}           remove uma execucao
    GET  /api/backtest/compare             compara duas execucoes

    WS   /api/ws                           status/progresso em tempo real

O frontend React consome isso. Nenhum calculo acontece no cliente.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import __version__
from . import backtest_schemas as B
from . import schemas as S
from .backtest_service import backtest_service
from .service import BetgsnService, Snapshot, service

ALLOWED_ORIGINS = [
    # dev do frontend BETGSN (porta dedicada, ver web/vite.config.ts)
    "http://localhost:5180",
    "http://127.0.0.1:5180",
    # preview do build de producao
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    # portas padrao do Vite, caso BETGSN_WEB_PORT seja alterado
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


# --------------------------------------------------------------------------
# eventos em tempo real
# --------------------------------------------------------------------------


class EventHub:
    """Broadcast simples de eventos de calculo para os clientes WebSocket."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def unregister(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, event: str, payload: dict) -> None:
        message = json.dumps({"event": event, "payload": payload}, default=str)
        async with self._lock:
            targets = list(self._clients)
        for ws in targets:
            with suppress(Exception):
                await ws.send_text(message)


hub = EventHub()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Liga o broadcaster do backtest ao loop de eventos do servidor.

    O motor de backtest roda em thread separada (processamento pesado fora
    do event loop). `run_coroutine_threadsafe` e o que permite publicar
    progresso no WebSocket a partir dessa thread.
    """
    loop = asyncio.get_running_loop()

    def schedule(event: str, payload: dict) -> None:
        if loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(hub.broadcast(event, payload), loop)

    backtest_service.set_broadcaster(schedule)
    yield


app = FastAPI(
    title="BETGSN API",
    version=__version__,
    lifespan=lifespan,
    description="Camada de apresentacao sobre o motor estatistico do BETGSN. "
                "Todo calculo (Poisson, Dixon-Coles, consenso, Kelly) acontece aqui.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


def _svc() -> BetgsnService:
    return service


def _snapshot() -> Snapshot:
    try:
        return _svc().snapshot()
    except Exception as exc:  # pipeline quebrou
        raise HTTPException(status_code=503, detail=f"pipeline indisponivel: {exc}") from exc


# --------------------------------------------------------------------------
# rotas
# --------------------------------------------------------------------------


@app.get("/api/health", response_model=S.SystemStatus, tags=["system"])
def health() -> S.SystemStatus:
    return _svc().status()


@app.get("/api/config", response_model=S.ModelConfiguration, tags=["system"])
def get_config() -> S.ModelConfiguration:
    return _snapshot().config


@app.get("/api/dashboard", response_model=S.DashboardSummary, tags=["dashboard"])
def dashboard() -> S.DashboardSummary:
    snap = _snapshot()
    return _svc().dashboard(snap)


@app.post("/api/recalculate", response_model=S.DashboardSummary, tags=["dashboard"])
async def recalculate(config: S.ModelConfiguration) -> S.DashboardSummary:
    svc = _svc()
    await hub.broadcast("recalculate:start", {"configuration": config.model_dump()})
    try:
        snap = await asyncio.to_thread(svc.recalculate, config)
    except Exception as exc:
        await hub.broadcast("recalculate:error", {"detail": str(exc)})
        raise HTTPException(status_code=500,
                            detail=f"falha no pipeline: {type(exc).__name__}: {exc}") from exc
    summary = svc.dashboard(snap)
    await hub.broadcast("recalculate:done", summary.model_dump())
    return summary


@app.get("/api/signals", response_model=S.SignalReport, tags=["signals"])
def signals(
    source: str = Query(
        "real",
        pattern="^(real|synthetic)$",
        description="'real' = jogos futuros com odds reais; "
                    "'synthetic' = dataset local gerado em memoria",
    ),
    bankroll: float | None = Query(None, gt=0),
    min_ev: float | None = Query(None, ge=0, le=1),
    use_xg: bool | None = Query(None),
    market_keys: list[str] | None = Query(None),
) -> S.SignalReport:
    """Relatorio de sinais.

    O padrao e `source=real`: jogos futuros reais com odds reais. O modo
    `synthetic` continua disponivel para comparar e para o selftest.
    """
    svc = _svc()
    if source == "synthetic":
        demo = BetgsnService(source="synthetic")
        return demo.signal_report(demo.snapshot())

    config = _snapshot().config
    try:
        return svc.real_signal_report(
            bankroll=bankroll if bankroll is not None else config.bankroll,
            kelly_frac=config.kelly_fraction,
            stake_cap=config.stake_cap,
            min_ev=min_ev if min_ev is not None else config.min_ev,
            max_exposure=config.max_exposure,
            use_xg=use_xg if use_xg is not None else config.use_xg,
            market_keys=market_keys,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"dados reais indisponiveis: {exc}. Rode "
                f"`python betgsn.py --import-fixtures-live` para baixar os "
                f"jogos da rodada, ou use source=synthetic."
            ),
        ) from exc


@app.get("/api/signals/status", tags=["signals"])
def signals_status() -> dict:
    """Disponibilidade das duas fontes de sinal."""
    from ..football_data_uk import FootballDataClient

    try:
        inv = FootballDataClient().fixtures_inventory()
    except Exception as exc:
        inv = {"available": False, "n_fixtures": 0, "error": str(exc)}
    return {
        "real": inv,
        "synthetic": {"available": True},
        "default_source": "real",
    }


@app.get("/api/games", response_model=list[S.GameAnalysis], tags=["games"])
def games() -> list[S.GameAnalysis]:
    snap = _snapshot()
    return _svc().games(snap)


@app.get("/api/odds", response_model=S.OddsOverview, tags=["odds"])
def odds() -> S.OddsOverview:
    snap = _snapshot()
    return _svc().odds_overview(snap)


@app.get("/api/odds/comparison", response_model=S.MarketComparison, tags=["odds"])
def odds_comparison(
    match: str = Query(..., description="Jogo no formato 'Casa vs Fora'"),
    market: str | None = Query(None, description="Mercado; o primeiro disponivel se omitido"),
) -> S.MarketComparison:
    snap = _snapshot()
    try:
        return _svc().market_comparison(snap, match, market)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/stats", response_model=S.StatsOverview, tags=["stats"])
def stats() -> S.StatsOverview:
    snap = _snapshot()
    return _svc().stats(snap)


@app.get("/api/model", response_model=S.ProbabilityModel, tags=["model"])
def model() -> S.ProbabilityModel:
    snap = _snapshot()
    return _svc().model(snap)


@app.get("/api/model/performance", response_model=S.ModelPerformance, tags=["model"])
async def model_performance(
    split: float = Query(0.7, gt=0.1, lt=0.95),
    bankroll: float = Query(1000.0, gt=0),
    min_ev: float = Query(0.03, ge=0.0, le=0.5),
) -> S.ModelPerformance:
    svc = _svc()
    try:
        return await asyncio.to_thread(svc.performance, split, bankroll, min_ev)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# backtest
# --------------------------------------------------------------------------


@app.get("/api/backtest/options", response_model=B.BacktestOptions, tags=["backtest"])
def backtest_options() -> B.BacktestOptions:
    """Periodo, competicoes, mercados e defaults para o painel da UI."""
    return backtest_service.options()


@app.get("/api/backtest/status", response_model=B.BacktestJobStatus, tags=["backtest"])
def backtest_status() -> B.BacktestJobStatus:
    return backtest_service.status()


@app.post("/api/backtest/run", response_model=B.BacktestJobStatus, tags=["backtest"])
async def backtest_run(request: B.BacktestRequest) -> B.BacktestJobStatus:
    """Dispara o backtest em background. Progresso via GET /status ou WebSocket."""
    try:
        status = backtest_service.start(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await hub.broadcast("backtest:start", {"request": request.model_dump()})
    return status


@app.get("/api/backtest/runs", response_model=list[B.BacktestRunSummary], tags=["backtest"])
def backtest_runs(limit: int = Query(50, ge=1, le=200)) -> list[B.BacktestRunSummary]:
    return backtest_service.list_runs(limit)


@app.get("/api/backtest/runs/{run_id}", response_model=B.BacktestRunDetail, tags=["backtest"])
def backtest_run_detail(run_id: str) -> B.BacktestRunDetail:
    detail = backtest_service.run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"backtest nao encontrado: {run_id}")
    return detail


@app.get(
    "/api/backtest/runs/{run_id}/signals",
    response_model=B.SignalPage,
    tags=["backtest"],
)
def backtest_run_signals(
    run_id: str,
    search: str = Query("", max_length=120),
    market: str = Query(""),
    confidence: str = Query(""),
    outcome_result: str = Query("", pattern="^(win|loss|push)?$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> B.SignalPage:
    if backtest_service.run_detail(run_id) is None:
        raise HTTPException(status_code=404, detail=f"backtest nao encontrado: {run_id}")
    return backtest_service.signals(
        run_id,
        search=search,
        market=market,
        confidence=confidence,
        outcome_result=outcome_result,
        offset=offset,
        limit=limit,
    )


@app.delete("/api/backtest/runs/{run_id}", tags=["backtest"])
def backtest_delete_run(run_id: str) -> dict[str, bool]:
    if not backtest_service.delete_run(run_id):
        raise HTTPException(status_code=404, detail=f"backtest nao encontrado: {run_id}")
    return {"deleted": True}


@app.get("/api/backtest/compare", response_model=B.RunComparison, tags=["backtest"])
def backtest_compare(
    a: str = Query(..., description="run_id da execucao A"),
    b: str = Query(..., description="run_id da execucao B"),
) -> B.RunComparison:
    try:
        return backtest_service.compare(a, b)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# portfolio
# --------------------------------------------------------------------------


@app.get("/api/portfolio/best-parlays", tags=["portfolio"])
def portfolio_best_parlays(
    max_legs: int = Query(4, ge=2, le=6),
    min_ev: float = Query(0.0, ge=-1.0),
    max_same_match: int = Query(2, ge=1, le=4),
) -> list[dict]:
    """Melhores múltiplas a partir dos sinais atuais."""
    from ..portfolio.parlay import ParlayLeg, best_parlays
    snap = _snapshot()
    svc = _svc()
    rep = svc.signal_report(snap)
    legs = [
        ParlayLeg(
            match=s.match, market=s.market, outcome=s.outcome,
            model_prob=s.model_prob, odd=s.best_odd, bookmaker=s.best_book,
            ev=s.ev, edge=s.edge,
        )
        for s in rep.signals
    ]
    results = best_parlays(
        legs, max_legs=max_legs, min_ev=min_ev,
        max_same_match=max_same_match, bankroll=snap.config.bankroll,
    )
    return [
        {
            "legs": [
                {"match": l.match, "market": l.market, "outcome": l.outcome,
                 "odd": l.odd, "bookmaker": l.bookmaker, "model_prob": l.model_prob}
                for l in r.legs
            ],
            "n_legs": r.n_legs,
            "combined_odd": r.combined_odd,
            "joint_probability": round(r.joint_probability, 6),
            "ev": round(r.ev, 4),
            "kelly": round(r.kelly, 4),
            "stake": r.stake,
            "payout": r.payout,
            "category": r.category,
            "risk_score": r.risk_score,
        }
        for r in results[:20]
    ]


@app.get("/api/portfolio/exposure", tags=["portfolio"])
def portfolio_exposure() -> dict:
    """Exposição atual do portfólio."""
    from ..portfolio.risk import ExposureLimits, check_exposure
    snap = _snapshot()
    svc = _svc()
    rep = svc.signal_report(snap)
    stakes = [
        {"match": s.match, "league": "", "stake": s.stake, "type": "single"}
        for s in rep.signals
    ]
    limits = ExposureLimits(
        max_total_exposure=snap.config.max_exposure,
        max_single_stake=snap.config.stake_cap,
    )
    report = check_exposure(stakes, snap.config.bankroll, limits)
    return {
        "total_exposure": report.total_exposure,
        "total_exposure_pct": round(report.total_exposure_pct, 4),
        "n_bets": report.n_bets,
        "within_limits": report.within_limits,
        "violations": report.violations,
        "by_match": report.by_match,
    }


# --------------------------------------------------------------------------
# data providers
# --------------------------------------------------------------------------


@app.get("/api/data/providers", tags=["data"])
def data_providers() -> list[dict]:
    """Status dos provedores de dados."""
    from ..providers import available_providers, env_status
    status = env_status()
    providers = available_providers()
    return [
        {
            "name": name,
            "configured": name in providers,
            "status": "ok" if name in providers else "no_key",
            "key_env": env,
        }
        for name, env in [
            ("api_football", "BETGSN_APIFOOTBALL_KEY"),
            ("odds_api", "BETGSN_ODDS_API_KEY"),
            ("football_data_org", "BETGSN_FOOTBALLDATA_KEY"),
            ("football_data_uk", ""),
        ]
    ]


@app.get("/api/data/quality", tags=["data"])
def data_quality_status() -> dict:
    """Resumo da qualidade dos dados."""
    from ..football_data_uk import FootballDataClient
    client = FootballDataClient()
    inv = client.inventory()
    return {
        "football_data_uk": {
            "main_files": inv.get("main_files", 0),
            "extra_files": inv.get("extra_files", 0),
            "total_mb": inv.get("total_mb", 0),
        },
        "cache_inventory": {},
    }


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


@app.get("/api/system/config", tags=["system"])
def system_config() -> dict:
    """Configuração do sistema (sem secrets)."""
    from ..config import get_config
    cfg = get_config()
    return {
        "model": {
            "home_advantage": cfg.model.home_advantage,
            "rho": cfg.model.rho_dixon_coles,
            "max_goals": cfg.model.max_goals_grid,
            "attack_blend": cfg.model.attack_blend,
        },
        "kelly": {
            "fraction": cfg.kelly.fraction,
            "cap": cfg.kelly.cap,
            "max_exposure": cfg.kelly.max_exposure,
        },
        "signals": {
            "ev_forte": cfg.signals.ev_forte,
            "ev_media": cfg.signals.ev_media,
            "ev_fraca": cfg.signals.ev_fraca,
        },
        "portfolio": {
            "max_parlay_legs": cfg.portfolio.max_parlay_legs,
            "parlay_kelly_fraction": cfg.portfolio.parlay_kelly_fraction,
        },
        "seed": cfg.seed,
    }


@app.websocket("/api/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    await hub.register(ws)
    try:
        await ws.send_text(json.dumps(
            {"event": "status", "payload": _svc().status().model_dump()}, default=str))
        await ws.send_text(json.dumps(
            {"event": "backtest:progress",
             "payload": backtest_service.status().model_dump()}, default=str))
        while True:
            raw = await ws.receive_text()
            if raw.strip() in {"ping", '"ping"'}:
                await ws.send_text(json.dumps({"event": "pong", "payload": {}}))
                continue
            if raw.strip() in {"status", '"status"'}:
                await ws.send_text(json.dumps(
                    {"event": "status", "payload": _svc().status().model_dump()}, default=str))
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unregister(ws)


@app.exception_handler(Exception)
async def unhandled(_request, exc: Exception) -> JSONResponse:
    """Nunca devolve traceback cru: mensagem estruturada para a UI."""
    return JSONResponse(
        status_code=500,
        content=S.ApiError(
            error=type(exc).__name__,
            detail=str(exc),
            hint="Consulte os logs do servidor para o traceback completo.",
        ).model_dump(),
    )


def serve(host: str = "127.0.0.1", port: int = 8787, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run("betgsn.api.server:app" if reload else app,
                host=host, port=port, reload=reload)
