"""BETGSN :: api.backtest_service — orquestracao do backtest na API.

Responsabilidades:
  - montar o corpus historico (point-in-time) uma vez e cachea-lo;
  - executar o backtest em thread separada, com progresso observavel;
  - persistir a execucao no SQLite e servir os resultados ja calculados.

Nada aqui recalcula metrica: o motor e as metricas vivem em
`backtest_engine`/`backtest_metrics` e sao compartilhados com o CLI.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Callable

from .. import __version__
from ..backtest_data import (
    MISSING_DATA_NOTES,
    HistoricalCorpus,
    RealHistoricalOddsSource,
)
from ..backtest_engine import BacktestConfig, run_backtest, simulate_bankroll
from ..backtest_metrics import MIN_SAMPLE, compute_metrics, SCANNER_EV_THRESHOLDS
from ..backtest_sources import (
    ODDS_SPORT_KEYS,
    OddsHistoryCache,
    corpus_fingerprint,
    load_imported_matches,
)
from ..backtest_store import BacktestStore, compare_runs, simulation_to_json
from ..data import build_dataset
from ..markets import MARKET_GROUPS
from ..providers import ProviderError, available_providers
from . import backtest_schemas as S

Broadcaster = Callable[[str, dict], None]

logger = logging.getLogger("betgsn.backtest")


def default_corpus() -> HistoricalCorpus:
    """Corpus historico do dataset local (sintetico, seed fixo)."""
    return HistoricalCorpus(build_dataset().history)


def imported_corpus() -> HistoricalCorpus:
    """Corpus de temporadas reais importadas (API-Football).

    Le do cache local em `output/backtest_cache/fixtures`. Levanta erro
    explicativo se nada foi importado — nunca roda um backtest vazio.
    """
    matches = load_imported_matches()
    if not matches:
        raise ValueError(
            "Nenhuma temporada real importada. Rode "
            "`python betgsn.py --import-fixtures --league=71 --season=2024` "
            "(requer BETGSN_APIFOOTBALL_KEY) ou use corpus_source='local'."
        )
    return HistoricalCorpus(matches)


def football_data_corpus() -> HistoricalCorpus:
    """Corpus dos CSVs do football-data.co.uk (ligas europeias + extras).

    Este corpus e o unico que vem COM odds reais — e o que permite testar
    o modelo contra a Pinnacle de fechamento em vez de um baseline.
    """
    from ..football_data_uk import FootballDataClient

    matches = FootballDataClient().load_matches()
    if not matches:
        raise ValueError(
            "Nenhum CSV do football-data.co.uk em cache. Rode "
            "`python betgsn.py --import-fduk --seasons=2012-2025` "
            "(sem chave de API; fonte publica)."
        )
    return HistoricalCorpus([m.to_historical() for m in matches])


def corpus_for(source: str) -> HistoricalCorpus:
    if source == "imported":
        return imported_corpus()
    if source == "football_data_uk":
        return football_data_corpus()
    return default_corpus()


class BacktestService:
    """Estado e execucao dos backtests."""

    def __init__(self, store: BacktestStore | None = None) -> None:
        self._store = store or BacktestStore()
        self._lock = threading.RLock()
        self._corpora: dict[str, HistoricalCorpus] = {}
        self._fduk_option: S.CorpusOption | None = None
        self._job_id: str | None = None
        self._phase = "idle"
        self._done = 0
        self._total = 0
        self._message = "Pronto."
        self._error: str | None = None
        self._run_id: str | None = None
        self._broadcast: Broadcaster | None = None

    # ------------------------------------------------------------- infra

    def set_broadcaster(self, fn: Broadcaster) -> None:
        self._broadcast = fn

    def corpus(self, source: str = "local") -> HistoricalCorpus:
        """Corpus cacheado por origem (montar o corpus e caro)."""
        with self._lock:
            if source not in self._corpora:
                self._corpora[source] = corpus_for(source)
            return self._corpora[source]

    def invalidate_corpus(self, source: str | None = None) -> None:
        """Descarta o cache — chamar apos importar dados novos."""
        with self._lock:
            if source is None:
                self._corpora.clear()
                self._fduk_option = None
            else:
                self._corpora.pop(source, None)
                if source == "football_data_uk":
                    self._fduk_option = None

    @property
    def store(self) -> BacktestStore:
        return self._store

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._broadcast is not None:
            try:
                self._broadcast(event, payload)
            except Exception:
                # progresso nunca pode derrubar a execucao
                pass

    def _publish_status(self) -> None:
        self._emit("backtest:progress", self.status().model_dump())

    # ----------------------------------------------------------- opcoes

    def options(self) -> S.BacktestOptions:
        corpus = self.corpus("local")
        stats = corpus.stats()
        providers = available_providers()
        odds_cache = OddsHistoryCache()
        cache_stats = odds_cache.stats()

        fduk_books: list[str] = []
        fduk_available = False
        try:
            from ..football_data_uk import FootballDataClient

            client = FootballDataClient()
            inv = client.inventory()
            fduk_available = inv["main_files"] + inv["extra_files"] > 0
            if fduk_available:
                # amostra em vez do store inteiro: carregar ~600 CSVs leva
                # dezenas de segundos e este endpoint precisa responder rapido
                fduk_books = client.sample_books()
        except Exception:
            fduk_available = False

        return S.BacktestOptions(
            min_date=stats.first_kickoff[:10],
            max_date=stats.last_kickoff[:10],
            competitions=list(stats.competitions),
            markets=[S.MarketOption(key=k, label=v) for k, v in MARKET_GROUPS],
            default_config=S.BacktestRequest(),
            min_history_default=BacktestConfig().min_history,
            odds_sources=[
                S.OddsSourceOption(
                    key="naive_synthetic",
                    label="Mercado ingênuo sintético (offline)",
                    available=True,
                    note="Mercado que só conhece as médias da liga anteriores ao "
                         "kickoff e ignora a força dos times. Não é um bookmaker "
                         "real: mede se o modelo agrega informação sobre esse baseline.",
                ),
                S.OddsSourceOption(
                    key="football_data_uk",
                    label="Odds reais — football-data.co.uk",
                    available=fduk_available,
                    note="Odds reais de Pinnacle, Betfair Exchange, Bet365 e mais, "
                         "com linha de abertura e de fechamento. É o teste de verdade: "
                         "bater a linha de fechamento da Pinnacle é o padrão profissional.",
                ),
                S.OddsSourceOption(
                    key="real_historical",
                    label="Odds históricas reais (cache local)",
                    available=providers.get("The Odds API", False)
                    or bool(cache_stats),
                    note="Usa capturas reais da The Odds API, importadas com "
                         "timestamp por snapshot. Só entram snapshots anteriores ao "
                         "kickoff e dentro da janela de validade.",
                ),
            ],
            scanner_ev_thresholds=dict(SCANNER_EV_THRESHOLDS),
            min_sample=MIN_SAMPLE,
            point_in_time_schema="pit-1",
            model_version=__version__,
            missing_data_notes=[S.MissingDataNote(**n) for n in MISSING_DATA_NOTES],
            corpora=self._corpus_options(),
            odds_cache=[
                S.OddsCacheStatus(sport_key=key, snapshots=int(value["snapshots"]),
                                  first=value.get("first"), last=value.get("last"))
                for key, value in sorted(cache_stats.items())
            ],
            books=fduk_books,
        )

    def _corpus_options(self) -> list[S.CorpusOption]:
        """Corpora disponiveis: local (sempre) + temporadas importadas."""
        out: list[S.CorpusOption] = []
        try:
            local = self.corpus("local")
            stats = local.stats()
            out.append(S.CorpusOption(
                key="local",
                label="Dataset local (sintético, seed fixo)",
                available=True,
                n_matches=stats.n_matches,
                first_kickoff=stats.first_kickoff[:10],
                last_kickoff=stats.last_kickoff[:10],
                competitions=list(stats.competitions),
                fingerprint=corpus_fingerprint(local.matches),
                note="Serve para validar o pipeline. Não é mercado real.",
            ))
        except Exception as exc:  # corpus local indisponivel
            out.append(S.CorpusOption(
                key="local", label="Dataset local (indisponível)",
                available=False, n_matches=0, note=str(exc),
            ))

        try:
            matches = load_imported_matches()
            if matches:
                imported = HistoricalCorpus(matches)
                st = imported.stats()
                out.append(S.CorpusOption(
                    key="imported",
                    label="Temporadas reais importadas",
                    available=True,
                    n_matches=st.n_matches,
                    first_kickoff=st.first_kickoff[:10],
                    last_kickoff=st.last_kickoff[:10],
                    competitions=list(st.competitions),
                    fingerprint=corpus_fingerprint(imported.matches),
                    note=f"{len(st.seasons)} temporada(s) via API-Football.",
                ))
            else:
                out.append(S.CorpusOption(
                    key="imported",
                    label="Temporadas reais importadas",
                    available=False,
                    n_matches=0,
                    note="Nada importado. Rode `python betgsn.py --import-fixtures` "
                         "(requer BETGSN_APIFOOTBALL_KEY).",
                ))
        except Exception as exc:
            out.append(S.CorpusOption(
                key="imported", label="Temporadas reais importadas",
                available=False, n_matches=0, note=str(exc),
            ))

        # football-data.co.uk: o unico corpus com ODDS REAIS
        out.append(self._fduk_corpus_option())
        return out

    def _fduk_corpus_option(self) -> S.CorpusOption:
        """Resumo do corpus europeu, cacheado.

        Parsear ~600 CSVs leva dezenas de segundos; o resultado nao muda
        entre requests (so quando o usuario importa de novo), entao fica
        em cache no processo.
        """
        with self._lock:
            if self._fduk_option is not None:
                return self._fduk_option
        try:
            from ..football_data_uk import FootballDataClient

            client = FootballDataClient()
            inv = client.inventory()
            if inv["main_files"] + inv["extra_files"] == 0:
                option = S.CorpusOption(
                    key="football_data_uk",
                    label="Ligas europeias (football-data.co.uk)",
                    available=False,
                    n_matches=0,
                    note="Nada baixado. Rode `python betgsn.py --import-fduk` "
                         "(fonte pública, sem chave de API).",
                )
            else:
                # resumo vem do indice em disco quando possivel: parsear
                # ~600 CSVs leva ~20s e nao pode travar a primeira carga da UI
                summary = client.corpus_summary()
                option = S.CorpusOption(
                    key="football_data_uk",
                    label="Ligas europeias (football-data.co.uk)",
                    available=True,
                    n_matches=int(summary["n_matches"]),
                    first_kickoff=summary.get("first_kickoff"),
                    last_kickoff=summary.get("last_kickoff"),
                    competitions=list(summary.get("competitions") or []),
                    fingerprint=corpus_fingerprint_key(summary),
                    note=f"{len(summary.get('competitions') or [])} competições · "
                         f"{summary.get('n_with_closing_odds', 0)} partidas com odds "
                         f"de fechamento.",
                )
        except Exception as exc:
            option = S.CorpusOption(
                key="football_data_uk",
                label="Ligas europeias (football-data.co.uk)",
                available=False,
                n_matches=0,
                note=str(exc),
            )
        with self._lock:
            self._fduk_option = option
        return option

    def invalidate_corpora(self) -> None:
        """Descarta os caches de corpus — chamar apos importar dados novos."""
        with self._lock:
            self._corpora.clear()
            self._fduk_option = None

    # ------------------------------------------------------------ status

    def status(self) -> S.BacktestJobStatus:
        total = max(0, self._total)
        progress = (self._done / total) if total else 0.0
        if self._phase in ("done",):
            progress = 1.0
        return S.BacktestJobStatus(
            job_id=self._job_id,
            phase=self._phase,  # type: ignore[arg-type]
            done=self._done,
            total=self._total,
            progress=round(progress, 4),
            message=self._message,
            run_id=self._run_id,
            error=self._error,
        )

    # ---------------------------------------------------------- execucao

    def start(self, request: S.BacktestRequest) -> S.BacktestJobStatus:
        """Dispara o backtest em background. Devolve o status inicial."""
        with self._lock:
            if self._phase in ("preparing", "analyzing", "metrics", "saving"):
                raise RuntimeError(
                    "ja existe um backtest em execucao; aguarde ou cancele"
                )
            self._job_id = datetime.now().strftime("btjob_%Y%m%d_%H%M%S")
            self._phase = "preparing"
            self._done = 0
            self._total = 0
            self._message = "Preparando dados históricos…"
            self._error = None
            self._run_id = None
        self._publish_status()
        threading.Thread(
            target=self._execute, args=(request,), daemon=True, name="betgsn-backtest"
        ).start()
        return self.status()

    def _execute(self, request: S.BacktestRequest) -> None:
        try:
            config = BacktestConfig(
                start_date=request.start_date,
                end_date=request.end_date,
                competitions=tuple(request.competitions),
                market_keys=tuple(request.market_keys),
                bankroll=request.bankroll,
                kelly_fraction=request.kelly_fraction,
                stake_cap=request.stake_cap,
                min_ev=request.min_ev,
                max_exposure=request.max_exposure,
                use_xg=request.use_xg,
                min_confidence=request.min_confidence,
                min_history=request.min_history,
                odds_source=request.odds_source,
                odds_sport_key=request.odds_sport_key,
                odds_closing=request.odds_closing,
                odds_books=tuple(request.odds_books),
                apply_exposure_cap=request.apply_exposure_cap,
                allow_untimestamped_odds=request.allow_untimestamped_odds,
                refit_every_days=request.refit_every_days,
                train_window_days=request.train_window_days,
                daily_exposure=request.daily_exposure,
                league_exposure=request.league_exposure,
            ).normalized()

            def on_progress(phase: str, done: int, total: int, message: str) -> None:
                with self._lock:
                    self._phase = phase
                    self._done = done
                    self._total = total
                    self._message = message
                self._publish_status()

            run = run_backtest(
                self.corpus(request.corpus_source), config, progress=on_progress
            )

            with self._lock:
                self._phase = "metrics"
                self._message = "Calculando métricas…"
            self._publish_status()

            simulation = simulate_bankroll(run.signals, config)
            metrics = compute_metrics(run, simulation)

            with self._lock:
                self._phase = "saving"
                self._message = "Finalizando e salvando…"
            self._publish_status()

            self._store.save_run(run, metrics, simulation_to_json(simulation))

            with self._lock:
                self._phase = "done"
                self._message = (
                    f"Concluído: {metrics.aggregate.n_signals} sinais em "
                    f"{run.n_matches_evaluated} partidas ({run.duration_ms:.0f} ms)."
                )
                self._run_id = run.run_id
                self._done = 1
                self._total = 1
            self._publish_status()
        except (ValueError, ProviderError) as exc:
            # configuracao invalida ou dado externo ausente: mensagem clara,
            # sem traceback — sao condicoes esperadas, nao bugs
            logger.warning("backtest rejeitado: %s", exc)
            with self._lock:
                self._phase = "error"
                self._message = (
                    "Dado histórico necessário indisponível."
                    if isinstance(exc, ProviderError)
                    else "Configuração inválida."
                )
                self._error = str(exc)
            self._publish_status()
        except Exception as exc:  # nunca esconder o erro real
            logger.exception("falha inesperada no backtest")
            with self._lock:
                self._phase = "error"
                self._message = "Falha na execução do backtest."
                self._error = f"{type(exc).__name__}: {exc}"
            self._publish_status()

    # ----------------------------------------------------------- consulta

    def list_runs(self, limit: int = 50) -> list[S.BacktestRunSummary]:
        out: list[S.BacktestRunSummary] = []
        for row in self._store.list_runs(limit):
            metrics = self._store.get_metrics(row["run_id"])
            agg = metrics.get("aggregate", {})
            sim = row.get("simulation", {})
            out.append(S.BacktestRunSummary(
                run_id=row["run_id"],
                created_at=row["created_at"],
                config_hash=row["config_hash"],
                model_version=row["model_version"],
                n_signals=row["n_signals"],
                n_matches_evaluated=row["n_matches_evaluated"],
                n_matches_skipped=row["n_matches_skipped"],
                duration_ms=row["duration_ms"],
                hit_rate=_opt_float(agg.get("hit_rate")),
                brier=_opt_float(agg.get("brier")),
                return_pct=_opt_float(sim.get("return_pct")),
                max_drawdown=_opt_float(sim.get("max_drawdown")),
            ))
        return out

    def run_detail(self, run_id: str) -> S.BacktestRunDetail | None:
        row = self._store.get_run(run_id)
        if row is None:
            return None
        metrics = self._store.get_metrics(run_id)
        agg = metrics.get("aggregate", {})
        sim = row.get("simulation_full") or row.get("simulation") or {}

        calibration = sorted(
            metrics.get("calibration", {}).values(),
            key=lambda b: b.get("lower", 0.0),
        )
        ev_buckets = sorted(
            metrics.get("ev_bucket", {}).values(),
            key=lambda b: b.get("lower", 0.0),
        )
        segments = [
            S.SegmentRowOut(**payload)
            for group, items in metrics.items()
            if group.startswith("segment_")
            for payload in items.values()
        ]
        temporal = {
            gran: [
                S.TemporalBucketOut(**payload)
                for payload in sorted(
                    metrics.get(f"temporal_{gran}", {}).values(),
                    key=lambda p: p.get("label", ""),
                )
            ]
            for gran in ("day", "week", "month")
        }

        meta = row.get("meta", {})
        notes = meta.get("missing_data_notes") or list(MISSING_DATA_NOTES)

        return S.BacktestRunDetail(
            run_id=row["run_id"],
            created_at=row["created_at"],
            config_hash=row["config_hash"],
            model_version=row["model_version"],
            point_in_time_schema=row.get("point_in_time_schema", "pit-1"),
            config=S.BacktestRequest(**row["config"]),
            aggregate=S.AggregateMetricsOut(**agg),
            calibration=[S.CalibrationBinOut(**b) for b in calibration],
            ev_buckets=[S.EvBucketOut(**b) for b in ev_buckets],
            temporal=temporal,
            segments=segments,
            simulation=S.SimulationOut(**sim),
            meta=meta,
            missing_data_notes=[S.MissingDataNote(**n) for n in notes],
        )

    def signals(
        self,
        run_id: str,
        *,
        search: str = "",
        market: str = "",
        confidence: str = "",
        outcome_result: str = "",
        offset: int = 0,
        limit: int = 100,
    ) -> S.SignalPage:
        page = self._store.get_signals(
            run_id,
            search=search,
            market=market,
            confidence=confidence,
            outcome_result=outcome_result,
            offset=offset,
            limit=limit,
        )
        return S.SignalPage(
            total=page["total"],
            offset=page["offset"],
            limit=page["limit"],
            items=[S.BacktestSignalOut(**item) for item in page["items"]],
        )

    def delete_run(self, run_id: str) -> bool:
        return self._store.delete_run(run_id)

    def compare(self, run_id_a: str, run_id_b: str) -> S.RunComparison:
        data = compare_runs(self._store, run_id_a, run_id_b)
        return S.RunComparison(
            run_a=data["run_a"],
            run_b=data["run_b"],
            same_config=data["same_config"],
            metrics={k: S.MetricDelta(**v) for k, v in data["metrics"].items()},
            temporal_month=[S.TemporalComparison(**t) for t in data["temporal_month"]],
        )


def _opt_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def corpus_fingerprint_key(summary: dict) -> str:
    """Fingerprint do corpus a partir do resumo em cache.

    Identifica o conjunto de dados sem reparsear os CSVs: dois backtests
    com o mesmo fingerprint sao comparaveis; com fingerprints diferentes,
    a comparacao mistura efeito de codigo com efeito de dados.
    """
    import hashlib

    payload = (
        f"{summary.get('n_matches')}|{summary.get('first_kickoff')}|"
        f"{summary.get('last_kickoff')}|"
        f"{','.join(summary.get('competitions') or [])}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


backtest_service = BacktestService()
