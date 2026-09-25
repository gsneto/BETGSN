"""BETGSN :: api.server — aplicacao FastAPI.

Rotas (todas sob /api):

    GET  /api/health                       status do sistema
    GET  /api/config                       configuracao atual do snapshot
    GET  /api/dashboard                    resumo + KPIs
    POST /api/recalculate                  dispara o pipeline em background
    GET  /api/recalculate/status           progresso do recalculo corrente
    POST /api/recalculate/cancel           cancelamento cooperativo
    GET  /api/signals                      relatorio de sinais completo
    GET  /api/games                        analise por jogo
    GET  /api/odds                         visao geral de casas
    GET  /api/odds/comparison              comparacao multi-casa de um mercado
    GET  /api/stats                        estatisticas e breakdowns
    GET  /api/model                        descricao do modelo
    GET  /api/model/performance            backtest (calibracao + aposta)

    GET  /api/quant/benchmarks             manifest da referencia (Etapa 19)
    GET  /api/quant/model-vs-market        modelo vs mercado (cache validado)
    GET  /api/quant/line-shopping          auditoria do line-shopping
    GET  /api/quant/ml                     modelos experimentais (24 janelas)
    GET  /api/quant/clv/status             ciclo de vida do CLV prospectivo

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
import threading
import time as _time_mod
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import __version__
from ..config import production_policy_fingerprint
from . import backtest_schemas as B
from . import schemas as S
from .backtest_service import get_backtest_service
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
    """Liga os broadcasters (backtest + recalculo) ao loop do servidor.

    O motor de backtest e o job de recalculo rodam em threads separadas
    (processamento pesado fora do event loop). `run_coroutine_threadsafe`
    e o que permite publicar progresso no WebSocket a partir dessas
    threads.

    O REALTIME ENGINE tambem sobe aqui (a menos que BETGSN_REALTIME=0):
    a construcao (fixtures/providers) acontece em thread propria para
    nao travar o startup.
    """
    loop = asyncio.get_running_loop()

    def schedule(event: str, payload: dict) -> None:
        if loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(hub.broadcast(event, payload), loop)

    get_backtest_service().set_broadcaster(schedule)
    _svc().set_broadcaster(schedule)

    from .realtime import ensure_engine_started
    from ..realtime.config import RealtimeConfig

    try:
        if RealtimeConfig.from_env().enabled:
            ensure_engine_started()
    except Exception as exc:  # noqa: BLE001 - config invalida nao derruba a API
        print(f"realtime engine nao iniciado: {exc}")
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

#: terminal de mercado em tempo real (SSE + board + sinais)
from .realtime import register_realtime_routes  # noqa: E402

register_realtime_routes(app)


def _svc() -> BetgsnService:
    return service


# ---------------------------------------------------------------------------
# Cache do /api/signals (real). Justificativa: cada request roda o pipeline
# de valor sobre ~600 fixtures reais e leva ~60s. Sem cache, chamadas
# repetidas (recarregar UI, dois usuarios) serializam pelo GIL e pioram.
#
# Chave: (source, market_keys, min_ev, use_xg, bankroll, snapshot_gen_at,
# policy_fingerprint). Invalidacao natural: recalculate muda o snapshot,
# muda o generated_at, muda a chave. Nunca fabrica dado — cache hit
# devolve o mesmo objeto que a computacao original devolveu, com
# `cache.status='STALE'` e `age_seconds` real. `LIVE fresco` NAO existe
# como valor de status: computo fresco NAO expoe o bloco `cache`.
# ---------------------------------------------------------------------------

SIGNALS_CACHE_TTL_SECONDS = 60.0

_signals_cache: dict[tuple, tuple[S.SignalReport, float, float]] = {}
_signals_cache_locks: dict[tuple, threading.Lock] = {}
_signals_cache_registry_lock = threading.Lock()


def _signals_cache_key(*, source: str, market_keys: tuple[str, ...] | None,
                       min_ev: float | None, use_xg: bool | None,
                       bankroll: float | None, snapshot_generated_at: str,
                       ) -> tuple:
    return (
        source,
        tuple(sorted(market_keys)) if market_keys else (),
        min_ev,
        use_xg,
        bankroll,
        snapshot_generated_at,
        production_policy_fingerprint(),
    )


def _signals_cache_lock_for(key: tuple) -> threading.Lock:
    with _signals_cache_registry_lock:
        lock = _signals_cache_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _signals_cache_locks[key] = lock
        return lock


def _signals_cache_get_fresh(key: tuple, now: float,
                              ) -> tuple[S.SignalReport, float, float] | None:
    entry = _signals_cache.get(key)
    if entry is None:
        return None
    _report, computed_at, computed_in_ms = entry
    if now - computed_at > SIGNALS_CACHE_TTL_SECONDS:
        return None
    return entry


def _signals_cache_reset_for_tests() -> None:
    """Usado APENAS pelos testes. Nao expor via HTTP."""
    with _signals_cache_registry_lock:
        _signals_cache.clear()
        _signals_cache_locks.clear()


def _fingerprint_key(key: tuple) -> str:
    import hashlib

    return hashlib.sha256(repr(key).encode()).hexdigest()[:16]


def _snapshot() -> Snapshot:
    try:
        return _svc().snapshot(auto=False)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "nenhum snapshot disponivel: dispare o recalculo via "
                "POST /api/recalculate (job assincrono) e acompanhe o "
                "progresso em GET /api/recalculate/status ou no WebSocket."
            ),
        ) from exc
    except Exception as exc:  # pipeline quebrou
        raise HTTPException(status_code=503, detail=f"pipeline indisponivel: {exc}") from exc


# --------------------------------------------------------------------------
# rotas
# --------------------------------------------------------------------------


@app.get("/api/health", response_model=S.SystemStatus, tags=["system"])
def health() -> S.SystemStatus:
    return _svc().status()


@app.get("/api/providers", response_model=S.ProviderOverview, tags=["system"])
def providers() -> S.ProviderOverview:
    return _svc().providers()


@app.get("/api/fixtures", response_model=S.FixtureOverview, tags=["fixtures"])
def fixtures() -> S.FixtureOverview:
    return _svc().fixtures()


@app.get("/api/movement", response_model=S.OddsMovementOverview, tags=["odds"])
def movement() -> S.OddsMovementOverview:
    return _svc().movement()


@app.get("/api/coverage", response_model=S.CoverageReport, tags=["coverage"])
def coverage() -> S.CoverageReport:
    return _svc().coverage()


@app.get("/api/clv", response_model=S.ClvReport, tags=["clv"])
def clv() -> S.ClvReport:
    return _svc().clv()


# --------------------------------------------------------------------------
# observabilidade quantitativa (leitura; nada recalcula aqui)
# --------------------------------------------------------------------------


@app.get("/api/quant/benchmarks", tags=["quant"])
def quant_benchmarks() -> dict:
    """Manifest da referência oficial de benchmark (Etapa 19).

    Corpus, protocolo (janelas/embargo/seed/bandas), fingerprints,
    versões e estado de reprodutibilidade de cada cache. Cache stale é
    DECLARADO, nunca servido como atual."""
    from .quant_service import benchmarks

    return benchmarks()


@app.get("/api/quant/model-vs-market", tags=["quant"])
def quant_model_vs_market() -> dict:
    """Modelo (BASELINE_V1) vs mercado nas 24 janelas OOS.

    Fontes separadas (market_raw/market_fair/model_raw/model_calibrated)
    + comparação pareada. Sem cache válido: status NO_VALID_CACHE."""
    from .quant_service import model_vs_market

    return model_vs_market()


@app.get("/api/quant/line-shopping", tags=["quant"])
def quant_line_shopping() -> dict:
    """Auditoria do efeito do line-shopping (+6,4pp decomposto).

    População constante (preço variando), semântica da ablação,
    segmentações e limitações temporais declaradas."""
    from .quant_service import line_shopping

    return line_shopping()


@app.get("/api/quant/ml", tags=["quant"])
def quant_ml() -> dict:
    """Modelos experimentais (Elo/XGBoost/LightGBM) nas 24 janelas.

    Evidência comparativa sem ranking, sem vencedor, sem promoção."""
    from .quant_service import ml_models

    return ml_models()


@app.get("/api/quant/clv/status", tags=["quant"])
def quant_clv_status() -> dict:
    """Ciclo de vida do CLV prospectivo (PENDING/NO_CLOSE/CLOSED/
    INVALID/MISMATCH) + evidência prospectiva do gate."""
    from .quant_service import clv_status

    return clv_status()


@app.get("/api/config", response_model=S.ModelConfiguration, tags=["system"])
def get_config() -> S.ModelConfiguration:
    return _snapshot().config


@app.get("/api/dashboard", response_model=S.DashboardSummary, tags=["dashboard"])
def dashboard() -> S.DashboardSummary:
    snap = _snapshot()
    return _svc().dashboard(snap)


@app.post("/api/recalculate", response_model=S.RecalculateJobStatus, tags=["dashboard"])
async def recalculate(config: S.ModelConfiguration) -> S.RecalculateJobStatus:
    """Dispara o pipeline em background e devolve o job imediatamente.

    O pipeline real leva dezenas de segundos; a UX nao pode depender de
    uma requisicao HTTP longa. O progresso chega via WebSocket
    (`recalculate:progress`) ou em GET /api/recalculate/status; ao
    concluir, o evento `recalculate:done` carrega o DashboardSummary
    atualizado. Uma falha NUNCA remove o snapshot anterior.
    """
    svc = _svc()
    try:
        status = svc.start_recalculate(config)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await hub.broadcast("recalculate:start", {"job_id": status.job_id})
    return status


@app.get("/api/recalculate/status", response_model=S.RecalculateJobStatus,
         tags=["dashboard"])
def recalculate_status() -> S.RecalculateJobStatus:
    """Estado do job de recalculo corrente (ou do ultimo concluido)."""
    return _svc().recalculate_status()


@app.post("/api/recalculate/cancel", response_model=S.RecalculateJobStatus,
          tags=["dashboard"])
def recalculate_cancel() -> S.RecalculateJobStatus:
    """Cancelamento cooperativo: aplicado entre fases, nunca no meio.

    Um job cancelado preserva o snapshot anterior — nada e trocado pela
    metade.
    """
    return _svc().request_cancel()


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
        # Synthetic e barato (~ms): NAO cacheia — evita mascarar bugs de
        # reprodutibilidade do modo demo.
        demo = BetgsnService(source="synthetic")
        return demo.signal_report(demo.snapshot())

    snap = _snapshot()
    config = snap.config
    resolved_min_ev = min_ev if min_ev is not None else config.min_ev
    resolved_use_xg = use_xg if use_xg is not None else config.use_xg
    resolved_bankroll = bankroll if bankroll is not None else config.bankroll
    key = _signals_cache_key(
        source=source,
        market_keys=tuple(market_keys) if market_keys else None,
        min_ev=resolved_min_ev,
        use_xg=resolved_use_xg,
        bankroll=resolved_bankroll,
        snapshot_generated_at=snap.generated_at,
    )

    now = _time_mod.monotonic()
    entry = _signals_cache_get_fresh(key, now)
    if entry is not None:
        report, computed_at, computed_in_ms = entry
        # Devolve o MESMO relatorio, com o bloco `cache` recalculado a
        # cada leitura (age muda). Copia via model_copy para nao mutar o
        # objeto cacheado (ausencia = cache miss em requests futuros).
        return report.model_copy(update={"cache": S.CacheMeta(
            status="STALE",
            age_seconds=max(0.0, now - computed_at),
            ttl_seconds=SIGNALS_CACHE_TTL_SECONDS,
            key_fingerprint=_fingerprint_key(key),
            computed_at=report.generated_at,
            computed_in_ms=computed_in_ms,
        )})

    # Cache miss: trava por chave para nao computar em paralelo. Segunda
    # request espera a primeira e sai com STALE (a primeira acabou de
    # gravar o cache — computou fresco, mas as demais leem cache).
    lock = _signals_cache_lock_for(key)
    with lock:
        # Double-check: outra thread pode ter populado enquanto esperava.
        now = _time_mod.monotonic()
        entry = _signals_cache_get_fresh(key, now)
        if entry is not None:
            report, computed_at, computed_in_ms = entry
            return report.model_copy(update={"cache": S.CacheMeta(
                status="STALE",
                age_seconds=max(0.0, now - computed_at),
                ttl_seconds=SIGNALS_CACHE_TTL_SECONDS,
                key_fingerprint=_fingerprint_key(key),
                computed_at=report.generated_at,
                computed_in_ms=computed_in_ms,
            )})
        started = _time_mod.monotonic()
        try:
            report = svc.real_signal_report(
                bankroll=resolved_bankroll,
                kelly_frac=config.kelly_fraction,
                stake_cap=config.stake_cap,
                min_ev=resolved_min_ev,
                max_exposure=config.max_exposure,
                use_xg=resolved_use_xg,
                market_keys=market_keys,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"dados reais indisponiveis: {exc}. Rode "
                    f"`python betgsn.py --import-fixtures-live` para baixar "
                    f"os jogos da rodada, ou use source=synthetic."
                ),
            ) from exc
        computed_in_ms = (_time_mod.monotonic() - started) * 1000.0
        # Grava snapshot e o resultado fresco. Ausencia do bloco `cache`
        # no retorno = computo fresco (contrato).
        _signals_cache[key] = (report, _time_mod.monotonic(), computed_in_ms)
        return report


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
    return get_backtest_service().options()


@app.get("/api/backtest/status", response_model=B.BacktestJobStatus, tags=["backtest"])
def backtest_status() -> B.BacktestJobStatus:
    return get_backtest_service().status()


@app.post("/api/backtest/run", response_model=B.BacktestJobStatus, tags=["backtest"])
async def backtest_run(request: B.BacktestRequest) -> B.BacktestJobStatus:
    """Dispara o backtest em background. Progresso via GET /status ou WebSocket."""
    try:
        status = get_backtest_service().start(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await hub.broadcast("backtest:start", {"request": request.model_dump()})
    return status


@app.get("/api/backtest/runs", response_model=list[B.BacktestRunSummary], tags=["backtest"])
def backtest_runs(limit: int = Query(50, ge=1, le=200)) -> list[B.BacktestRunSummary]:
    return get_backtest_service().list_runs(limit)


@app.get("/api/backtest/runs/{run_id}", response_model=B.BacktestRunDetail, tags=["backtest"])
def backtest_run_detail(run_id: str) -> B.BacktestRunDetail:
    detail = get_backtest_service().run_detail(run_id)
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
    if get_backtest_service().run_detail(run_id) is None:
        raise HTTPException(status_code=404, detail=f"backtest nao encontrado: {run_id}")
    return get_backtest_service().signals(
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
    if not get_backtest_service().delete_run(run_id):
        raise HTTPException(status_code=404, detail=f"backtest nao encontrado: {run_id}")
    return {"deleted": True}


@app.get("/api/backtest/compare", response_model=B.RunComparison, tags=["backtest"])
def backtest_compare(
    a: str = Query(..., description="run_id da execucao A"),
    b: str = Query(..., description="run_id da execucao B"),
) -> B.RunComparison:
    try:
        return get_backtest_service().compare(a, b)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# portfolio
# --------------------------------------------------------------------------

#: Teto computacional do endpoint de multiplas: so os N melhores
#: candidatos por EV entram nas combinacoes. `best_parlays` e
#: combinatorio exaustivo (C(n, k)); com ~180 sinais seriam ~47 milhoes
#: de combinacoes. Os sinais ja vem ordenados por EV (desc), entao o
#: corte remove apenas a cauda — multiplas continuam construidas sobre
#: os melhores candidatos, e a decisao de apostar continua upstream.
PARLAY_CANDIDATE_CAP = 20


@app.get("/api/portfolio/best-parlays", tags=["portfolio"])
def portfolio_best_parlays(
    max_legs: int = Query(4, ge=2, le=6),
    min_ev: float = Query(0.0, ge=-1.0),
    max_same_match: int = Query(2, ge=1, le=4),
) -> list[dict]:
    """Melhores múltiplas a partir dos sinais atuais.

    I-13: uma múltipla é uma aposta. Se o Quant decidiu NO_BET, nenhuma
    é construída — a lista vem vazia em vez de trazer stakes que a
    decisão já rejeitou.
    """
    from ..portfolio.parlay import ParlayLeg, best_parlays
    snap = _snapshot()
    svc = _svc()
    rep = svc.signal_report(snap)
    if rep.decision is not None and rep.decision.action == "NO_BET":
        return []
    legs = [
        ParlayLeg(
            match=s.match, market=s.market, outcome=s.outcome,
            model_prob=s.model_prob, odd=s.best_odd, bookmaker=s.best_book,
            ev=s.ev, edge=s.edge,
        )
        for s in rep.signals[:PARLAY_CANDIDATE_CAP]
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
    """Exposição atual do portfólio.

    I-13: a exposição é derivada da DECISÃO, não apenas das stakes dos
    sinais. Com NO_BET do Quant, toda stake vira zero — nenhuma
    exposição é criada — e a resposta carrega a ação da decisão para a
    UI explicar o motivo.
    """
    from ..portfolio.policy import enforce_decision
    from ..portfolio.risk import ExposureLimits, check_exposure
    from ..portfolio.simulation import PortfolioBet
    snap = _snapshot()
    svc = _svc()
    rep = svc.signal_report(snap)
    bets = [
        PortfolioBet(
            label=s.id, probability=s.model_prob, odd=s.best_odd,
            stake=s.stake, match=s.match, league=s.league, market=s.market,
        )
        for s in rep.signals
    ]
    effective = enforce_decision(bets, rep.decision)
    stakes = [
        {"match": b.match or b.label, "league": b.league or "-",
         "stake": b.stake, "type": "single"}
        for b in effective
    ]
    limits = ExposureLimits(
        max_total_exposure=snap.config.max_exposure,
        max_single_stake=snap.config.stake_cap,
    )
    report = check_exposure(stakes, snap.config.bankroll, limits)
    return {
        "decision_action": rep.decision.action if rep.decision else None,
        "decision_reason": rep.decision.reason if rep.decision else None,
        "decision_evidence_status": (
            rep.decision.evidence_status if rep.decision else None),
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
    """Status dos provedores de dados.

    Bug historico corrigido: o `configured` comparava labels LOCAIS
    ("odds_api") com as chaves CANONICAS de `available_providers()`
    ("The Odds API") e devolvia sempre False. Agora o label e traduzido
    para o nome canonico antes da consulta — o status reflete a
    configuracao REAL (chave presente).
    """
    from ..providers import available_providers, env_status
    status = env_status()
    providers = available_providers()
    #: label da resposta -> nome canonico em available_providers()
    canonical = {
        "api_football": "API-Football",
        "odds_api": "The Odds API",
        "football_data_org": "Football-Data.org",
        # football-data.co.uk e corpus CSV local: sem chave, sem API.
        "football_data_uk": "",
    }
    return [
        {
            "name": name,
            "configured": bool(canonical[name] and providers.get(canonical[name])),
            "status": (
                "ok"
                if canonical[name] and providers.get(canonical[name])
                else "no_key"
            ),
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
             "payload": get_backtest_service().status().model_dump()}))
        await ws.send_text(json.dumps(
            {"event": "recalculate:progress",
             "payload": _svc().recalculate_status().model_dump()}, default=str))
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
