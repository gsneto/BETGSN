"""BETGSN :: api.service — camada de servico entre o pipeline e a API.

Responsabilidade unica: EXECUTAR o pipeline existente e TRADUZIR o
RunResult para os contratos de `schemas.py`. Nenhuma regra estatistica
nova mora aqui: tudo que e numero vem de engine/model/markets/signals.

Mantem um snapshot em memoria (ultimo calculo) com lock, para que varias
requisicoes HTTP leiam o mesmo estado sem recalcular.
"""

from __future__ import annotations

import platform
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean, mean, median

from .. import __version__
from ..backtest import run_backtest
from ..data import BOOKMAKERS, LEAGUE_AVG_GOALS, LEAGUE_HOME_ADVANTAGE, SEED, build_dataset
from ..engine import implied_prob, scan_arbitrage, consensus_fair_probs
from ..model import RHO_DEFAULT, TeamRating
from ..pipeline import FixtureAnalysis, RunResult, run
from ..odds_registry import default_odds_registry
from ..providers import available_providers
from ..signals import EV_FORTE, EV_FRACA, EV_MEDIA, MAX_SPREAD, MIN_BOOKS, Signal as CoreSignal
from ..timeutil import now_utc, utc_key
from ..value_strategy import STRATEGY_NAME as OPERATIONAL_STRATEGY
from . import schemas as S

MODEL_DOC_FALLBACK = "Documentacao do modelo indisponivel."

# --------------------------------------------------------------------------
# Contratos de status: dominio (odds_snapshots) -> API (schemas)
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# O dominio produz estados que a API precisa representar SEM perder
# informacao e SEM aceitar string qualquer. Os mapeamentos abaixo sao
# explicitos e totais: estado desconhecido e erro, nunca silencio.

#: CLV: o dominio (CLVResult) produz OK, NO_CLOSING_ODDS e
#: CLOSING_BEFORE_ENTRY — ver `OddsSnapshotStore.clv` e `clv_prospective`.
#: NO_ENTRY_ODDS e produzido NA BORDA: linha apostavel sem entrada
#: registrada no store (nenhuma observacao PIT valida no instante da
#: decisao). A API expoe os quatro porque nenhum pode ser colapsado sem
#: mentir: "sem fechamento", "entrada pos-fechamento" e "sem odd de
#: entrada observada" sao ausencias diferentes.
_CLV_STATUS_API: dict[str, str] = {
    "OK": "OK",
    "NO_CLOSING_ODDS": "NO_CLOSING_ODDS",
    "CLOSING_BEFORE_ENTRY": "CLOSING_BEFORE_ENTRY",
    "NO_ENTRY_ODDS": "NO_ENTRY_ODDS",
}


def clv_status_to_api(status: str) -> str:
    """Traduz status de CLV do dominio para o enum da API.

    Total e restritivo: mapeia os estados que o dominio e a borda
    produzem e recusa qualquer outro — um status novo precisa de decisao
    explicita de contrato, nao de passagem automatica.
    """
    try:
        return _CLV_STATUS_API[status]
    except KeyError:
        raise ValueError(
            f"status de CLV do dominio sem representacao na API: {status!r}"
        ) from None


def movement_status_to_api(
    n_observations: int | None, price_delta: float | None
) -> str:
    """Traduz o estado de movimento do dominio para o enum da API.

    O dominio (`OddsSnapshotStore.movement`) conhece tres estados:
    NO_DATA (zero observacoes), INSUFFICIENT_DATA (uma unica observacao)
    e OK (duas ou mais). A API preserva os dois primeiros com o mesmo
    nome e subdivide o OK em MOVING/STABLE pelo delta do preco — nunca
    colapsa INSUFFICIENT_DATA em STABLE: uma observacao so nao prova que
    o preco parou.
    """
    if not n_observations:
        return "NO_DATA"
    if n_observations < 2:
        return "INSUFFICIENT_DATA"
    if price_delta is not None and abs(price_delta) > 0.01:
        return "MOVING"
    return "STABLE"


# --------------------------------------------------------------------------
# Borda dominio -> API: saude de provider
# --------------------------------------------------------------------------

#: Mapeamento EXPLICITO do vocabulario canonico do Odds Layer
#: (betgsn.odds_health.ProviderState) para o DTO da API. O dominio manda;
#: a borda so traduz. Sem sinonimos inventados (nao existe "CURRENT").
_DOMAIN_TO_API_STATUS: dict[str, S.ProviderAvailability] = {
    "HEALTHY": "HEALTHY",
    "DEGRADED": "DEGRADED",
    "UNAVAILABLE": "UNAVAILABLE",
    "STALE": "STALE",
    "NO_COVERAGE": "NO_COVERAGE",
}

#: Capacidades de providers FORA do registry de odds (documentadas em
#: providers.py). Nao e health observado: e o que cada fonte oferece
#: quando configurada. Para providers de odds, as features vivem no
#: registry (`ProviderSpec.features`) — este dicionario e o FALLBACK de
#: nomes nao registrados e de specs sem features declaradas, para que o
#: DTO da API nao mude (FASE B, strangler).
_PROVIDER_FEATURES: dict[str, list[str]] = {
    "The Odds API": ["odds"],
    "ParlayAPI": ["odds"],
    "API-Football": ["fixtures", "historical", "statistics"],
    "Football-Data.org": ["fixtures", "historical"],
}


def _provider_features(name: str) -> list[str]:
    """Features de um provider: registry primeiro, dicionario historico depois.

    Providers de odds declaram suas features no registry (FASE B); nomes
    fora dele (API-Football, Football-Data.org) e specs sem features
    declaradas caem no mapeamento historico — o valor servido pela API
    permanece exatamente o de sempre.
    """
    spec = None
    try:
        spec = default_odds_registry().metadata(name)
    except KeyError:
        spec = None
    if spec is not None and spec.features:
        return list(spec.features)
    return list(_PROVIDER_FEATURES.get(name, []))


def api_provider_status(state) -> S.ProviderAvailability:
    """Traduz ProviderState (ou ausencia) para o vocabulario da API.

    Ausencia de observacao (None ou estado fora do vocabulario) e UNKNOWN:
    continua ausencia, nunca vira HEALTHY.
    """
    if state is None:
        return "UNKNOWN"
    key = state.value if hasattr(state, "value") else str(state)
    return _DOMAIN_TO_API_STATUS.get(key, "UNKNOWN")


def _merge_latest(
    primary: dict, secondary: dict, stamp_field: str
) -> dict:
    """Combina registros de duas fontes por provider, mantendo o MAIS recente.

    `primary` e o tracker em memoria deste processo; `secondary`, o registro
    persistido pela captura (processo separado). Para cada provider vale o
    registro com carimbo `stamp_field` mais recente — carimbos sao strings
    ISO canonica UTC (`...Z`), comparaveis lexicograficamente. Provider so
    em uma das fontes entra como esta; registro sem carimbo nunca desloca
    registro com carimbo.
    """
    merged = dict(primary)
    for name, record in secondary.items():
        current = merged.get(name)
        if current is None:
            merged[name] = record
            continue
        if str(record.get(stamp_field) or "") > str(current.get(stamp_field) or ""):
            merged[name] = record
    return merged


def _provider_health_dto(
    name: str,
    configured: bool,
    record: dict | None,
    credit: dict | None,
) -> S.ProviderHealth:
    """Monta o DTO de um provider a partir do registro REAL do Odds Layer.

    `record` e uma entrada de HealthTracker.snapshot(); `credit`, de
    CreditController.snapshot(). Ambos podem nao existir — e nesse caso
    cada campo observavel fica None/UNKNOWN em vez de sintetico.
    """
    status = api_provider_status(record.get("state") if record else None)
    if not configured:
        message = "Chave de API nao configurada"
    elif record is None:
        message = "Configurado; nenhuma coleta registrada pelo Odds Layer"
    else:
        message = (
            f"{record.get('total_successes', 0)} coletas bem-sucedidas, "
            f"{record.get('total_failures', 0)} falhas"
        )
    return S.ProviderHealth(
        name=name,
        status=status,
        last_update=(record.get("last_success_at") or None) if record else None,
        last_execution=(record.get("updated_at") or None) if record else None,
        latency_ms=record.get("latency_ms") if record else None,
        error=(record.get("last_error") or None) if record else None,
        quota_used=credit.get("used") if credit else None,
        quota_remaining=credit.get("known_remaining") if credit else None,
        features=_provider_features(name) if configured else [],
        message=message,
    )


#: Status de evidencia das odds dos jogos futuros do football-data.co.uk.
#: Os CSVs trazem precos REAIS de bookmakers, mas SEM timestamp de
#: publicacao: um preco sem carimbo nao prova que estava disponivel no
#: instante da decisao. Por isso a evidencia e "exploratory" — e o que
#: isso implica (NO_BET) e decidido pelo Quant, nao pela API.
FIXTURES_EVIDENCE_STATUS = "exploratory"


def _model_doc() -> str:
    """Le a documentacao do modelo da GUI legada sem importar Tkinter."""
    try:
        from pathlib import Path

        src = Path(__file__).resolve().parent.parent / "gui.py"
        text = src.read_text(encoding="utf-8")
        start = text.index('MODEL_DOC = """') + len('MODEL_DOC = """')
        end = text.index('"""', start)
        return text[start:end].strip()
    except Exception:
        return MODEL_DOC_FALLBACK


# --------------------------------------------------------------------------
# Snapshot em memoria
# --------------------------------------------------------------------------


@dataclass
class Snapshot:
    result: RunResult
    config: S.ModelConfiguration
    generated_at: str
    computed_in_ms: float
    source: str = "synthetic"


class BetgsnService:
    """Executa o pipeline e serve o ultimo snapshot calculado."""

    def __init__(self, source: str = "synthetic", odds_service=None) -> None:
        self._lock = threading.RLock()
        self._snapshot: Snapshot | None = None
        self._computing = False
        self._last_error: str | None = None
        self._doc = _model_doc()
        self._backtest_cache: dict[tuple, S.ModelPerformance] = {}
        self.source = source
        #: Odds Layer injetavel (testes) ou criado uma vez por processo.
        #: E dele que vem o health REAL: HealthTracker + CreditController
        #: alimentados pelas coletas. A API nunca inventa estado.
        self._odds_service = odds_service

    def odds_service(self):
        """O Odds Layer deste processo (um so, para acumular health real)."""
        if self._odds_service is None:
            from ..odds_service import OddsService
            self._odds_service = OddsService.from_env()
        return self._odds_service

    # ------------------------------------------------------------- calculo

    @property
    def computing(self) -> bool:
        return self._computing

    def recalculate(self, config: S.ModelConfiguration) -> Snapshot:
        """Roda o pipeline com os parametros informados e guarda o snapshot."""
        with self._lock:
            self._computing = True
            self._last_error = None
        started = time.perf_counter()
        try:
            result = self._run_real(config) if self.source == "real" else run(
                bankroll=config.bankroll,
                kelly_frac=config.kelly_fraction,
                stake_cap=config.stake_cap,
                min_ev=config.min_ev,
                use_xg=config.use_xg,
                rounds=config.rounds,
                max_exposure_frac=config.max_exposure,
            )
        except Exception as exc:
            with self._lock:
                self._computing = False
                self._last_error = f"{type(exc).__name__}: {exc}"
            raise
        elapsed = (time.perf_counter() - started) * 1000.0
        snap = Snapshot(
            result=result,
            config=config,
            generated_at=result.report.generated_at if result.report
            else now_utc(),
            computed_in_ms=round(elapsed, 2),
            source=self.source,
        )
        with self._lock:
            self._snapshot = snap
            self._computing = False
        return snap

    def _run_real(self, config):
        from ..real_signals import real_signals_service
        from ..data import LeagueDataset
        from ..signals import top_tips
        report, snap = real_signals_service.report(
            bankroll=config.bankroll, kelly_frac=config.kelly_fraction,
            stake_cap=config.stake_cap, min_ev=config.min_ev,
            max_exposure=config.max_exposure, use_xg=config.use_xg)
        ds = LeagueDataset(snap.teams, snap.history, [a.fixture for a in snap.analyses],
                           snap.league_goals, LEAGUE_HOME_ADVANTAGE)
        return RunResult(ds, snap.ratings, list(snap.analyses), report, top_tips(report), snap.league_goals)

    def snapshot(self, *, auto: bool = True) -> Snapshot:
        """Devolve o snapshot atual; calcula com o default se ainda nao existir."""
        with self._lock:
            snap = self._snapshot
        if snap is None and auto:
            snap = self.recalculate(S.ModelConfiguration())
        if snap is None:
            raise RuntimeError("nenhum snapshot disponivel")
        return snap

    # -------------------------------------------------------------- status

    def status(self) -> S.SystemStatus:
        with self._lock:
            snap = self._snapshot
            computing = self._computing
            err = self._last_error
        n_sig = len(snap.result.report.signals) if snap and snap.result.report else 0
        return S.SystemStatus(
            status="computing" if computing else ("error" if err else "ok"),
            version=__version__,
            python_version=platform.python_version(),
            generated_at=snap.generated_at if snap else None,
            computed_in_ms=snap.computed_in_ms if snap else None,
            has_snapshot=snap is not None,
            n_signals=n_sig,
            n_games=len(snap.result.analyses) if snap else 0,
            data_source=self.data_source(),
            providers=available_providers(),
            message=err,
        )

    def data_source(self) -> str:
        return "football-data.co.uk — dados reais em cache; timestamp das odds indisponível" if self.source == "real" else "Demonstração: dataset sintético"

    def provenance(self, snap):
        from .prediction_schemas import Provenance
        from ..football_data_uk import FootballDataClient
        return Provenance(source=snap.source, prediction_timestamp=snap.generated_at,
                          data_version=FootballDataClient().corpus_signature() if snap.source == "real" else f"demo-{SEED}",
                          xg_status="ESTIMATED" if snap.source == "synthetic" else "UNAVAILABLE")

    # ------------------------------------------------------------- sinais

    def _quant_decision(self, evidence_status: str) -> S.BetDecision:
        """Decisao de apostar ou nao, produzida pelo Quant.

        A decisao vem de `staking.decide_bet` (o UNICO produtor de
        BetDecision) sobre a evidencia da estrategia OPERACIONAL,
        encadeada pelo runner (`strategy_runner.run_strategy_decision`:
        registry -> evidencia validada -> decide_bet). A vantagem vem do
        cache de validacao QUANDO o fingerprint bate com a regra atual
        sobre o corpus atual (I-14); sem cache valido, as constantes
        validadas da estrategia — nunca numeros de outra medicao.

        A API NUNCA fabrica NO_BET nem inventa stake: so traduz o que o
        Quant decidiu, preservando o motivo, as verificacoes e o status
        de evidencia que fundamentou a decisao.
        """
        from ..strategy_runner import run_strategy_decision

        core = run_strategy_decision(
            OPERATIONAL_STRATEGY, evidence_status=evidence_status)
        return S.BetDecision(
            action=core.action,
            reason=core.reason,
            fraction=core.fraction,
            conservative_roi=core.conservative_roi,
            kelly_full=core.kelly_full,
            evidence_status=evidence_status,
            checks=[S.DecisionCheck(name=name, passed=passed, detail=detail)
                    for name, passed, detail in core.checks],
        )

    def signal_report(self, snap: Snapshot) -> S.SignalReport:
        rep = snap.result.report
        signals = [self._signal(s, i, snap) for i, s in enumerate(rep.signals)] if rep else []
        # A decisao de apostar e do Quant: evidencia "synthetic" para o
        # dataset de demonstracao; para dados reais, o status das odds
        # dos fixtures (sem timestamp de publicacao).
        evidence = "synthetic" if snap.source == "synthetic" else FIXTURES_EVIDENCE_STATUS
        return S.SignalReport(
            provenance=self.provenance(snap),
            generated_at=snap.generated_at,
            bankroll=snap.config.bankroll,
            kpis=self.kpis(snap),
            signals=signals,
            top_tips=list(snap.result.tips),
            source=snap.source,
            decision=self._quant_decision(evidence),
            source_detail=(
                "Dataset local gerado em memoria: datas fixas no codigo e "
                "odds sintetizadas a partir do proprio modelo. Serve para "
                "testar o pipeline — NAO para decidir aposta."
            ),
        )

    def real_signal_report(
        self,
        bankroll: float = 1000.0,
        kelly_frac: float = 0.25,
        stake_cap: float = 0.01,
        min_ev: float = 0.02,
        max_exposure: float = 0.25,
        use_xg: bool = True,
        market_keys: list[str] | None = None,
    ) -> S.SignalReport:
        """Sinais a partir de jogos FUTUROS REAIS com odds reais.

        Reusa `_signal` e `kpis` (o mesmo tradutor dos sinais sinteticos),
        montando um `RunResult` a partir do relatorio real. Assim existe
        UMA traducao de sinal no projeto, nao duas que divergem.
        """
        from ..real_signals import MODEL_CALIBRATION, real_signals_service
        from ..pipeline import RunResult
        from ..data import LeagueDataset

        report, snap = real_signals_service.report(
            bankroll=bankroll,
            kelly_frac=kelly_frac,
            stake_cap=stake_cap,
            min_ev=min_ev,
            max_exposure=max_exposure,
            use_xg=use_xg,
            market_keys=market_keys,
        )

        # I-02: registra as entradas de CLV a partir de observacao REAL do
        # OddsSnapshotStore, no instante da decisao. Sem observacao PIT
        # valida, a linha nao ganha entrada — a ausencia aparece como
        # NO_ENTRY_ODDS no /api/clv, nunca como odd sintetica.
        self._register_clv_entries(snap)

        # adapter minimo: o tradutor precisa de um RunResult. As ANALISES
        # reais do snapshot sao preservadas — `_signal` busca nelas a liga
        # e a rodada de cada sinal. Sem elas, todo sinal chegaria com
        # league=""/round_label="" mesmo quando o snapshot sabe a resposta.
        # O dataset segue explicito e vazio: `_signal`/`kpis` nao o usam.
        empty_dataset = LeagueDataset(
            teams=snap.teams, history=[], fixtures=[],
            league_goals=snap.league_goals, home_advantage=0.0,
        )
        proxy = Snapshot(
            result=RunResult(
                dataset=empty_dataset,
                ratings=snap.ratings,
                analyses=list(snap.analyses),
                report=report,
                tips=[],
                league_goals=snap.league_goals,
            ),
            config=S.ModelConfiguration(
                bankroll=bankroll, kelly_fraction=kelly_frac,
                min_ev=min_ev, stake_cap=stake_cap,
                max_exposure=max_exposure, use_xg=use_xg,
            ),
            generated_at=snap.generated_at,
            computed_in_ms=snap.computed_in_ms,
            source="real",
        )

        base = self.signal_report(proxy)
        return base.model_copy(update={
            "source": "real",
            "source_detail": (
                f"Jogos futuros reais com odds reais ({', '.join(snap.sources)}). "
                f"Ratings ajustados em {snap.n_history} partidas "
                f"({snap.history_window[0]} a {snap.history_window[1]})."
            ),
            "skipped_no_rating": snap.skipped_no_rating,
            "skipped_insufficient_books": snap.skipped_insufficient_books,
            "calibration": S.ModelCalibrationInfo(**MODEL_CALIBRATION),
        })

    def _register_clv_entries(self, snap) -> int:
        """Registra entradas de CLV para as linhas apostaveis do snapshot.

        Entrada = observacao REAL do OddsSnapshotStore no instante da
        decisao: `line_at(event_key, market, outcome, prediction_timestamp)`
        devolve a MEDIANA das ultimas observacoes por casa com timestamp
        <= decisao. Nada e fabricado:

        - sem observacao PIT valida, a linha NAO ganha entrada;
        - entry_timestamp e timestamp real da observacao, nunca o
          prediction_timestamp;
        - entry_odd e a mediana das casas, nunca fx.best_odds atual;
        - o store congela a primeira entrada (FIRST-WINS).

        Devolve quantas entradas foram inseridas agora (chamadas
        repetidas devolvem 0 para as linhas ja congeladas).
        """
        from ..odds_snapshots import CLV_ENTRY_SOURCE, OddsSnapshotStore

        store = OddsSnapshotStore()
        prediction_ts = snap.generated_at
        registered = 0
        for fx in snap.fixtures:
            if not fx.has_odds:
                continue
            if not fx.has_kickoff:
                # Sem horario publicado nao existe instante mensuravel.
                continue
            for market, outcomes in fx.best_odds.items():
                for oc in outcomes:
                    line = store.line_at(
                        fx.event_key, market, oc, prediction_ts)
                    if line is None:
                        # Sem observacao valida no instante da decisao:
                        # nao registrar nada — ausencia nao e odd.
                        continue
                    if store.register_entry(
                        match_key=fx.event_key,
                        market=market,
                        outcome=oc,
                        entry_odd=line.odd,
                        entry_timestamp=line.timestamp,
                        entry_n_books=line.n_books,
                        kickoff=utc_key(fx.kickoff, fx.timezone),
                        prediction_timestamp=prediction_ts,
                        source=CLV_ENTRY_SOURCE,
                    ):
                        registered += 1
        return registered

    def _fixture_of(self, snap: Snapshot, match: str) -> FixtureAnalysis | None:
        for a in snap.result.analyses:
            if f"{a.fixture.home} vs {a.fixture.away}" == match:
                return a
        return None

    def _signal(self, s: CoreSignal, index: int, snap: Snapshot) -> S.Signal:
        a = self._fixture_of(snap, s.match)
        home, away = (a.fixture.home, a.fixture.away) if a else _split_match(s.match)
        return S.Signal(
            id=f"{index}|{s.match}|{s.market}|{s.outcome}",
            match=s.match,
            home=home,
            away=away,
            kickoff=utc_key(s.kickoff),
            league=a.fixture.league if a else "",
            round_label=a.fixture.round_label if a else "",
            market=s.market,
            outcome=s.outcome,
            best_odd=s.best_odd,
            best_book=s.best_book,
            median_odd=s.median_odd,
            fair_odd=s.fair_odd,
            n_books=s.n_books,
            model_prob=s.model_prob,
            market_prob=s.market_prob,
            edge=s.edge,
            ev=s.ev,
            kelly=s.kelly,
            stake=s.stake,
            stake_pct=s.stake_pct,
            expected_profit=s.expected_profit,
            expected_profit_pct=s.expected_profit_pct,
            gross_profit_if_win=s.gross_profit_if_win,
            loss_if_lose=s.loss_if_lose,
            confidence=s.confidence.value,  # type: ignore[arg-type]
            rationale=s.rationale,
        )

    def kpis(self, snap: Snapshot) -> S.SignalsKpis:
        rep = snap.result.report
        rows = rep.signals if rep else []
        total = len(rows)
        denom = total or 1
        strong = sum(1 for s in rows if s.confidence.value == "FORTE")
        medium = sum(1 for s in rows if s.confidence.value == "MEDIA")
        weak = sum(1 for s in rows if s.confidence.value == "FRACA")
        bankroll = rep.bankroll if rep else snap.config.bankroll
        profit = rep.expected_profit if rep else 0.0
        return S.SignalsKpis(
            total=total,
            strong=strong,
            medium=medium,
            weak=weak,
            strong_pct=strong / denom,
            medium_pct=medium / denom,
            weak_pct=weak / denom,
            expected_profit=round(profit, 2),
            expected_profit_pct=rep.expected_profit_pct if rep else 0.0,
            avg_stake_pct=mean([s.stake_pct for s in rows]) if rows else 0.0,
            total_exposure=round(rep.total_exposure(), 2) if rep else 0.0,
            total_exposure_pct=rep.total_exposure_pct if rep else 0.0,
            worst_case_loss=round(rep.worst_case_loss, 2) if rep else 0.0,
            gross_profit_if_all_win=round(rep.gross_profit_if_all_win, 2) if rep else 0.0,
            exposure_scaled_by=rep.exposure_scaled_by if rep else 1.0,
            max_ev=max((s.ev for s in rows), default=0.0),
        )

    # -------------------------------------------------------------- jogos

    def games(self, snap: Snapshot) -> list[S.GameAnalysis]:
        rep = snap.result.report
        by_match: dict[str, int] = {}
        if rep:
            for s in rep.signals:
                by_match[s.match] = by_match.get(s.match, 0) + 1
        return [self._game(a, by_match) for a in snap.result.analyses]

    def _game(self, a: FixtureAnalysis, sig_count: dict[str, int]) -> S.GameAnalysis:
        key = f"{a.fixture.home} vs {a.fixture.away}"
        m1 = a.markets.get("Resultado Final (1X2)", {})
        return S.GameAnalysis(
            id=key,
            match=key,
            home=a.fixture.home,
            away=a.fixture.away,
            league=a.fixture.league,
            kickoff=utc_key(a.fixture.kickoff),
            round_label=a.fixture.round_label,
            lambda_home=a.lambdas[0],
            lambda_away=a.lambdas[1],
            prob_home=m1.get("1", 0.0),
            prob_draw=m1.get("X", 0.0),
            prob_away=m1.get("2", 0.0),
            prob_over_25=a.markets.get("Total de Gols", {}).get("Over 2.5", 0.0),
            prob_btts=a.markets.get("Ambas Marcam", {}).get("BTTS Sim", 0.0),
            prob_home_corners_over_55=a.markets.get("Escanteios", {}).get(
                "Casa Cantos Over 5.5", 0.0),
            prob_cards_over_35=a.markets.get("Cartoes", {}).get("Cartoes Over 3.5", 0.0),
            top_scorelines=[S.Scoreline(home_goals=h, away_goals=aw, prob=p)
                            for h, aw, p in a.top_scorelines],
            markets=[S.MarketProbabilities(market=name, outcomes=dict(out))
                     for name, out in a.markets.items()],
            ratings_home=_team(a.ratings_home),
            ratings_away=_team(a.ratings_away),
            n_markets_with_odds=len(a.odds),
            signal_count=sig_count.get(key, 0),
        )

    # -------------------------------------------------------- casas / odds

    def odds_overview(self, snap: Snapshot) -> S.OddsOverview:
        matches = [f"{a.fixture.home} vs {a.fixture.away}" for a in snap.result.analyses]
        markets_by_match = {
            f"{a.fixture.home} vs {a.fixture.away}": list(a.odds.keys())
            for a in snap.result.analyses
        }
        return S.OddsOverview(
            provenance=self.provenance(snap),
            generated_at=snap.generated_at,
            matches=matches,
            markets_by_match=markets_by_match,
            bookmakers=self._bookmaker_snapshots(snap),
        )

    def _bookmaker_snapshots(self, snap: Snapshot) -> list[S.BookmakerSnapshot]:
        n_markets: dict[str, int] = {b: 0 for b in BOOKMAKERS}
        n_best: dict[str, int] = {b: 0 for b in BOOKMAKERS}
        margins: dict[str, list[float]] = {b: [] for b in BOOKMAKERS}
        total_outcomes = 0
        for a in snap.result.analyses:
            for market, books in a.odds.items():
                groups = _n_groups(a.markets.get(market, {}))
                best_by_outcome: dict[str, tuple[float, str]] = {}
                for book, outcomes in books.items():
                    n_markets[book] = n_markets.get(book, 0) + 1
                    if outcomes:
                        margins.setdefault(book, []).append(
                            _overround(outcomes.values(), groups))
                    for oc, odd in outcomes.items():
                        if odd and (oc not in best_by_outcome or odd > best_by_outcome[oc][0]):
                            best_by_outcome[oc] = (odd, book)
                total_outcomes += len(best_by_outcome)
                for _oc, (_odd, book) in best_by_outcome.items():
                    n_best[book] = n_best.get(book, 0) + 1

        signals_won: dict[str, int] = {b: 0 for b in BOOKMAKERS}
        if snap.result.report:
            for s in snap.result.report.signals:
                signals_won[s.best_book] = signals_won.get(s.best_book, 0) + 1

        out: list[S.BookmakerSnapshot] = []
        for book in sorted(n_markets):
            ms = margins.get(book) or [0.0]
            out.append(S.BookmakerSnapshot(
                book=book,
                n_markets=n_markets.get(book, 0),
                n_best_odds=n_best.get(book, 0),
                avg_margin=sum(ms) / len(ms),
                best_odd_share=(n_best.get(book, 0) / total_outcomes) if total_outcomes else 0.0,
                signals_won=signals_won.get(book, 0),
            ))
        out.sort(key=lambda b: b.n_best_odds, reverse=True)
        return out

    def market_comparison(self, snap: Snapshot, match: str,
                          market: str | None) -> S.MarketComparison:
        a = self._fixture_of(snap, match)
        if a is None:
            raise KeyError(f"jogo desconhecido: {match}")
        available = list(a.odds.keys())
        if not available:
            raise KeyError(f"sem odds para {match}")
        chosen = market if market in available else available[0]
        books = a.odds[chosen]
        model_probs = a.markets.get(chosen, {})

        outcomes: list[str] = []
        for book_map in books.values():
            for oc in book_map:
                if oc not in outcomes:
                    outcomes.append(oc)
        # ordem do modelo (1/X/2, Over antes de Under...) em vez de alfabetica
        model_order = {oc: i for i, oc in enumerate(model_probs)}
        outcomes.sort(key=lambda oc: (model_order.get(oc, 10_000), oc))

        best_odds: dict[str, float] = {}
        best_books: dict[str, str] = {}
        for book, book_map in books.items():
            for oc, odd in book_map.items():
                if odd and (oc not in best_odds or odd > best_odds[oc]):
                    best_odds[oc] = odd
                    best_books[oc] = book

        groups = _n_groups(model_probs)
        rows: list[S.BookmakerRow] = []
        for book, book_map in books.items():
            rows.append(S.BookmakerRow(
                book=book,
                odds={oc: book_map.get(oc, 0.0) for oc in outcomes},
                best_outcomes=[oc for oc in outcomes if best_books.get(oc) == book],
                margin=_overround(book_map.values(), groups),
            ))
        rows.sort(key=lambda r: r.margin)

        # probabilidade justa de mercado pela mediana das casas, igual ao engine
        market_probs = consensus_fair_probs(books)

        arb = scan_arbitrage(books, total_stake=snap.config.bankroll)
        return S.MarketComparison(
            match=match,
            market=chosen,
            outcomes=outcomes,
            rows=rows,
            best_odds=best_odds,
            best_books=best_books,
            model_probs={oc: model_probs.get(oc, 0.0) for oc in outcomes},
            market_probs=market_probs,
            arbitrage=S.ArbitrageCheck(
                arbitrage=arb.arbitrage,
                margin=arb.margin,
                legs=[S.ArbLeg(outcome=l.outcome, book=l.book, odd=l.odd,
                               stake=l.stake, payout=l.payout) for l in arb.legs],
            ),
        )

    # ------------------------------------------------------- estatisticas

    def stats(self, snap: Snapshot) -> S.StatsOverview:
        res = snap.result
        rep = res.report
        rows = rep.signals if rep else []

        by_market: dict[str, list[CoreSignal]] = {}
        by_conf: dict[str, list[CoreSignal]] = {}
        by_book: dict[str, list[CoreSignal]] = {}
        for s in rows:
            by_market.setdefault(s.market, []).append(s)
            by_conf.setdefault(s.confidence.value, []).append(s)
            by_book.setdefault(s.best_book, []).append(s)

        markets = [
            S.MarketBreakdown(
                market=name,
                signals=len(items),
                avg_ev=mean([i.ev for i in items]),
                avg_edge=mean([i.edge for i in items]),
                best_ev=max(i.ev for i in items),
                total_stake=round(sum(i.stake for i in items), 2),
                expected_profit=round(sum(i.expected_profit for i in items), 2),
            )
            for name, items in by_market.items()
        ]
        markets.sort(key=lambda m: m.signals, reverse=True)

        confs = [
            S.ConfidenceBreakdown(
                confidence=name,  # type: ignore[arg-type]
                signals=len(items),
                avg_ev=mean([i.ev for i in items]),
                avg_odd=mean([i.best_odd for i in items]),
                total_stake=round(sum(i.stake for i in items), 2),
                expected_profit=round(sum(i.expected_profit for i in items), 2),
            )
            for name, items in by_conf.items()
        ]
        order = {"FORTE": 0, "MEDIA": 1, "FRACA": 2, "DESCARTE": 3}
        confs.sort(key=lambda c: order.get(c.confidence, 9))

        books = [
            S.BookBreakdown(
                book=name,
                signals=len(items),
                avg_ev=mean([i.ev for i in items]),
                avg_odd=mean([i.best_odd for i in items]),
                total_stake=round(sum(i.stake for i in items), 2),
            )
            for name, items in by_book.items()
        ]
        books.sort(key=lambda b: b.signals, reverse=True)

        return S.StatsOverview(
            provenance=self.provenance(snap),
            generated_at=snap.generated_at,
            league_goals=res.league_goals,
            home_advantage=res.dataset.home_advantage,
            teams=sorted((_team(r) for r in res.ratings.values()),
                         key=lambda t: t.strength, reverse=True),
            n_history_matches=len(res.dataset.history),
            n_fixtures=len(res.dataset.fixtures),
            by_market=markets,
            by_confidence=confs,
            by_book=books,
            ev_distribution=_ev_buckets(rows),
        )

    # -------------------------------------------------------------- modelo

    def model(self, snap: Snapshot) -> S.ProbabilityModel:
        res = snap.result
        markets = list(res.analyses[0].markets.keys()) if res.analyses else []
        return S.ProbabilityModel(
            provenance=self.provenance(snap),
            version=__version__,
            engine="Poisson bivariado + Dixon-Coles + consenso multi-casa",
            generated_at=snap.generated_at,
            constants=self.constants(),
            configuration=snap.config,
            league_goals=res.league_goals,
            home_advantage=res.dataset.home_advantage,
            attack_blend=0.5 if snap.config.use_xg and any(r.xg_status == "REAL" for r in res.ratings.values()) else 0.0,
            n_teams=len(res.ratings),
            n_history_matches=len(res.dataset.history),
            n_fixtures=len(res.dataset.fixtures),
            n_markets=len(markets),
            markets=markets,
            data_source=self.data_source(),
            providers=available_providers(),
            documentation=self._doc,
        )

    @staticmethod
    def constants() -> S.ModelConstants:
        return S.ModelConstants(
            rho_dixon_coles=RHO_DEFAULT,
            ev_forte=EV_FORTE,
            ev_media=EV_MEDIA,
            ev_fraca=EV_FRACA,
            min_books=MIN_BOOKS,
            max_spread=MAX_SPREAD,
            max_goals_grid=8,
            league_avg_goals=LEAGUE_AVG_GOALS,
            home_advantage=LEAGUE_HOME_ADVANTAGE,
            attack_blend=0.5,
            dataset_seed=SEED,
            bookmakers=list(BOOKMAKERS),
        )

    def performance(self, split: float = 0.7, bankroll: float = 1000.0,
                    min_ev: float = 0.03) -> S.ModelPerformance:
        key = (round(split, 3), round(bankroll, 2), round(min_ev, 4))
        cached = self._backtest_cache.get(key)
        if cached is not None:
            return cached
        if self.source == "real":
            from ..football_data_uk import FootballDataClient
            from ..data import LeagueDataset
            history = [m.to_historical() for m in FootballDataClient().load_matches(["E0"])]
            history = sorted(history, key=lambda m: m.kickoff)[-1140:]
            if len(history) < 100:
                raise ValueError("histórico real insuficiente para performance")
            ds = LeagueDataset(sorted({m.home for m in history}|{m.away for m in history}), history, [], 0, LEAGUE_HOME_ADVANTAGE)
        else:
            ds = build_dataset()
        res = run_backtest(ds, split=split, bankroll=bankroll, min_ev=min_ev,
                           odds_source="football_data_uk" if self.source == "real" else "naive_synthetic")
        perf = S.ModelPerformance(
            source=self.source,
            split=res.split,
            n_train=res.n_train,
            n_test=res.n_test,
            logloss=res.calibration.logloss,
            brier=res.calibration.brier,
            accuracy=res.calibration.accuracy,
            calibration_bins=[S.CalibrationBin(predicted=p, empirical=e, n=n)
                              for p, e, n in res.calibration.bins],
            bankroll_start=res.betting.bankroll_start,
            bankroll_end=res.betting.bankroll_end,
            n_bets=res.betting.n_bets,
            n_wins=res.betting.n_wins,
            hit_rate=res.betting.hit_rate,
            total_staked=res.betting.total_staked,
            profit=res.betting.profit,
            roi=res.betting.roi,
            ev_mean_pred=res.betting.ev_mean_pred,
            return_mean_real=res.betting.return_mean_real,
            max_drawdown=res.betting.max_drawdown,
            summary=res.summary,
        )
        self._backtest_cache[key] = perf
        return perf

    # ----------------------------------------------------------- dashboard

    def dashboard(self, snap: Snapshot) -> S.DashboardSummary:
        res = snap.result
        n_markets = len(res.analyses[0].markets) if res.analyses else 0
        return S.DashboardSummary(
            provenance=self.provenance(snap),
            generated_at=snap.generated_at,
            computed_in_ms=snap.computed_in_ms,
            configuration=snap.config,
            kpis=self.kpis(snap),
            n_games=len(res.analyses),
            n_teams=len(res.ratings),
            n_bookmakers=len(BOOKMAKERS),
            n_markets=n_markets,
            data_source=self.data_source(),
        )

    # ---------------------------------------------------------- providers

    def providers(self) -> S.ProviderOverview:
        """Traduz o health REAL do Odds Layer para o DTO da API.

        Duas fontes de observacao, MESMO contrato:

        1. o `OddsService` DESTE processo (HealthTracker/CreditController
           em memoria — alimentado por coletas que acontecem aqui);
        2. o health PERSISTIDO no store operacional pela captura de odds
           (processo separado, CLI `--capture-odds`) — veja
           `OddsSnapshotStore.save_provider_health`.

        Para cada provider vale o registro MAIS RECENTE (updated_at). Sem
        observacao em nenhuma fonte: UNKNOWN — nunca "HEALTHY por chave
        configurada", nunca latency fixa, nunca quota ficticia.
        """
        from ..odds_snapshots import OddsSnapshotStore

        odds = self.odds_service()
        health = odds.health_snapshot()      # real: HealthTracker
        credits = odds.credits_snapshot()    # real: CreditController
        try:
            store = OddsSnapshotStore()
            persisted_health = store.load_provider_health()
            persisted_credits = store.load_provider_credits()
        except Exception:  # noqa: BLE001 - store ilegivel nao derruba a rota
            persisted_health, persisted_credits = {}, {}
        health = _merge_latest(health, persisted_health, "updated_at")
        credits = _merge_latest(credits, persisted_credits, "last_updated")

        configured = available_providers()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        provider_healths: list[S.ProviderHealth] = []
        for name in configured:
            provider_healths.append(
                _provider_health_dto(name, configured[name], health.get(name), credits.get(name))
            )

        return S.ProviderOverview(
            providers=provider_healths,
            generated_at=now,
            any_healthy=any(p.status == "HEALTHY" for p in provider_healths),
            any_stale=any(p.status == "STALE" for p in provider_healths),
            any_unavailable=any(p.status == "UNAVAILABLE" for p in provider_healths),
        )

    # ---------------------------------------------------------- fixtures

    def fixtures(self) -> S.FixtureOverview:
        from ..football_data_uk import FootballDataClient
        from ..real_signals import real_signals_service
        from ..signals import Signal as CoreSignal
        from ..pipeline import analyze_fixture

        client = FootballDataClient()
        inv = client.fixtures_inventory()
        fixtures_raw = client.load_fixtures()
        now = now_utc()

        fixture_items: list[S.FixtureItem] = []
        for fx in fixtures_raw:
            has_odds = bool(fx.odds)
            # `fx.odds` e {mercado: {casa: {resultado: odd}}}. A lista de
            # casas precisa ser achatada entre mercados; usar as chaves de
            # `fx.odds` aqui devolveria NOMES DE MERCADO como se fossem casas.
            markets = list(fx.odds.keys())
            bookmakers = sorted({b for books in fx.odds.values() for b in books})
            # Melhor odd por resultado, achatando os mercados. O schema
            # FixtureItem.best_odds e dict[str, float]: o valor e a odd real,
            # nunca um dict de resultados.
            best_odds: dict[str, float] | None = None
            if has_odds:
                best_odds = {}
                for books in fx.odds.values():
                    for outcomes in books.values():
                        for oc, odd in outcomes.items():
                            if odd and (oc not in best_odds or odd > best_odds[oc]):
                                best_odds[oc] = odd
            fixture_items.append(S.FixtureItem(
                match=fx.match,
                home=fx.home,
                away=fx.away,
                league=fx.league,
                round_label=fx.division,
                kickoff=utc_key(fx.kickoff, fx.timezone) if fx.has_kickoff else "",
                kickoff_local=fx.kickoff,
                timezone=fx.timezone,
                has_odds=has_odds,
                n_bookmakers=len(bookmakers),
                bookmakers=bookmakers,
                markets=markets,
                best_odds=best_odds,
                status="UPCOMING" if has_odds else "NO_ODDS",
            ))

        return S.FixtureOverview(
            generated_at=now,
            n_fixtures=len(fixture_items),
            n_with_odds=sum(1 for f in fixture_items if f.has_odds),
            fixtures=fixture_items,
            source="football-data.co.uk",
            data_version=client.corpus_signature() if inv.get("available") else None,
        )

    # ---------------------------------------------------------- movement

    def movement(self) -> S.OddsMovementOverview:
        from ..football_data_uk import FootballDataClient
        from ..features.movement import movement_features, PricePoint
        from ..odds_snapshots import OddsSnapshotStore

        client = FootballDataClient()
        fixtures = client.load_fixtures()
        store = OddsSnapshotStore()
        # O instante da previsao e AGORA — carimbo canonico UTC. Uma hora
        # local sem offset, lida como UTC pelo corte temporal, deslocaria
        # o cutoff (e o point-in-time) pelo fuso da maquina.
        now = now_utc()

        movements: list[S.OddsMovement] = []

        for fx in fixtures[:20]:
            if not fx.has_odds:
                continue
            if not fx.has_kickoff:
                # Sem horario publicado nao existe corte temporal possivel.
                continue
            # Cotacoes persistidas para esta partida, se houver. Sem historico
            # o movimento e NO_DATA — nunca zero, que se confundiria com
            # "o preco nao se moveu".
            points = [
                PricePoint(
                    bookmaker=o.bookmaker,
                    market=o.market,
                    outcome=o.outcome,
                    odd=o.odd,
                    timestamp=o.timestamp,
                )
                for o in store.all_observations(fx.event_key)
            ]
            # `fx.odds` e {mercado: {casa: {resultado: odd}}}; iterar as
            # chaves internas daria NOMES DE CASA como outcome. As linhas
            # reais sao mercado + resultado.
            # O kickoff entra convertido para UTC com o fuso de publicacao
            # do site (FIXTURES_TZ): tratar a hora publicada com outro fuso
            # deslocaria o cutoff temporal.
            kickoff_utc = utc_key(fx.kickoff, fx.timezone)
            for market, best in fx.best_odds.items():
                for oc in list(best.keys())[:3]:
                    # Assinatura: (points, market, outcome,
                    # prediction_timestamp, kickoff). Ambos os instantes
                    # em UTC canonico; o kickoff e o da partida.
                    feat = movement_features(points, market, oc, now, kickoff_utc)
                    delta = feat.get("price_delta")
                    observed = feat.get("n_observations")
                    # `current_odd` sem historico observado: o preco ATUAL
                    # do fixture (dado real de hoje, sem timestamp), com o
                    # status declarando NO_DATA — a ausencia de historico
                    # e EXPLICITA, nunca mascarada como movimento. Os
                    # campos de MOVIMENTO (opening/delta/consenso) seguem
                    # None: nada e calculado sobre o que nao foi observado.
                    current = feat.get("current_odds") or best.get(oc)
                    movements.append(S.OddsMovement(
                        match=fx.match,
                        market=market,
                        outcome=oc,
                        opening_odd=feat.get("opening_odds"),
                        current_odd=current,
                        price_delta=delta,
                        price_delta_pct=feat.get("price_delta_pct"),
                        book_consensus_move=feat.get("book_consensus_move"),
                        book_dispersion=feat.get("book_dispersion"),
                        market_direction=feat.get("market_direction"),
                        n_observations=int(observed or 0),
                        n_books=int(feat.get("n_books") or 0),
                        minutes_since_open=feat.get("minutes_since_open"),
                        minutes_to_kickoff=feat.get("minutes_to_kickoff"),
                        status=movement_status_to_api(
                            int(observed) if observed else None, delta),
                    ))

        return S.OddsMovementOverview(
            generated_at=now,
            movements=movements,
            source="football-data.co.uk",
            data_version=client.corpus_signature(),
        )

    # ---------------------------------------------------------- coverage

    def coverage(self) -> S.CoverageReport:
        from ..providers import env_status
        from ..odds_snapshots import OddsSnapshotStore
        from ..football_data_uk import FootballDataClient
        env = env_status()
        store = OddsSnapshotStore()
        client = FootballDataClient()
        fixtures_raw = client.load_fixtures()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        providers_health = self.providers().providers

        n_fixtures_total = len(fixtures_raw)
        n_fixtures_with_odds = sum(1 for f in fixtures_raw if f.has_odds)

        # Bookmaker ativo = bookmaker EFETIVAMENTE OBSERVADO nas odds dos
        # fixtures. Chave de provider no ambiente nao e bookmaker: e
        # potencialidade de coleta, nao evidencia de cotacao (H2).
        books_observed: set[str] = set()
        for fx in fixtures_raw:
            for books in fx.odds.values():
                books_observed.update(books)
        bookmakers_observed = sorted(books_observed)

        # Sem fixture observado nao existe medicao de cobertura de odds —
        # None, nunca 0.0 (0.0 diria "medimos e nenhuma tem odds").
        odds_cov = (
            n_fixtures_with_odds / n_fixtures_total if n_fixtures_total else None
        )

        # CLV pela fonte operacional canonica (C2): o MESMO store onde as
        # capturas sao gravadas sob `event_key`. A populacao medida e a
        # das linhas apostaveis — cada mercado/resultado com melhor odd
        # nos fixtures com odds. Sem linhas, a medicao nao existe.
        bets = [
            {"match_key": fx.event_key, "market": market, "outcome": oc, "odd": odd}
            for fx in fixtures_raw
            if fx.has_odds and fx.has_kickoff
            for market, best in fx.best_odds.items()
            for oc, odd in best.items()
            if odd
        ]
        clv_cov = store.coverage(bets).coverage if bets else None

        # xG: o football-data.co.uk nao publica xG e nao existe store de
        # observacoes de xG neste pipeline. Sem evidencia real, a medicao
        # e ausente (None) — 0.0 significaria "xG real observado em 0%
        # dos jogos", que e falso.
        xg_cov = None

        gaps: list[dict] = [
            {"provider": name, "gap": "no_key", "detail": "Chave de API não configurada"}
            for name, configured in env.items() if not configured
        ]
        if xg_cov is None:
            gaps.append({
                "provider": "xg",
                "gap": "no_real_xg_source",
                "detail": "Nenhuma fonte de xG real observada; cobertura de xG não medida",
            })
        if not bets:
            gaps.append({
                "provider": "clv",
                "gap": "no_bettable_lines",
                "detail": "Sem linhas apostáveis observadas; cobertura de CLV não medida",
            })

        return S.CoverageReport(
            generated_at=now,
            providers=providers_health,
            clv_coverage=clv_cov,
            odds_coverage=odds_cov,
            xg_coverage=xg_cov,
            n_fixtures_with_odds=n_fixtures_with_odds,
            n_fixtures_total=n_fixtures_total,
            n_bookmakers_active=len(bookmakers_observed),
            bookmakers_observed=bookmakers_observed,
            gaps=gaps,
            source="football-data.co.uk",
        )

    # ---------------------------------------------------------- clv

    def clv(self) -> S.ClvReport:
        from ..odds_snapshots import ClvEntryRecord, OddsSnapshotStore
        from ..football_data_uk import FootballDataClient

        client = FootballDataClient()
        store = OddsSnapshotStore()
        fixtures_raw = client.load_fixtures()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Entradas registradas pelo fluxo real (real_signal_report ->
        # register_entry, FIRST-WINS). Em caso de fontes concorrentes,
        # vale a primeira registrada para cada linha.
        registered: dict[tuple[str, str, str], ClvEntryRecord] = {}
        for rec in store.clv_entries():
            registered.setdefault(
                (rec.match_key, rec.market, rec.outcome), rec)

        entries: list[S.ClvEntry] = []
        for fx in fixtures_raw:
            if not fx.has_odds:
                continue
            if not fx.has_kickoff:
                # Sem horario publicado nao existe fechamento mensuravel.
                continue
            for market, best in fx.best_odds.items():
                for oc in best:
                    rec = registered.get((fx.event_key, market, oc))
                    if rec is None:
                        # Nao houve observacao PIT valida no instante da
                        # decisao: nada foi registrado. Ausencia explicita
                        # — nunca odd sintetica, nunca now() como timestamp
                        # da odd.
                        entries.append(S.ClvEntry(
                            match=fx.match,
                            market=market,
                            outcome=oc,
                            entry_odd=None,
                            entry_timestamp=None,
                            status="NO_ENTRY_ODDS",
                        ))
                        continue
                    # I-07: mediana (entrada congelada) vs mediana
                    # (fechamento), mesma fonte (store), mesma populacao
                    # metodologica. clv_prospective so calcula CLV quando
                    # entry_timestamp < closing_timestamp.
                    result = store.clv_prospective(
                        rec.match_key, market, oc,
                        entry_odd=rec.entry_odd,
                        entry_timestamp=rec.entry_timestamp,
                    )
                    entries.append(S.ClvEntry(
                        match=fx.match,
                        market=market,
                        outcome=oc,
                        entry_odd=rec.entry_odd,
                        entry_timestamp=rec.entry_timestamp,
                        closing_odd=result.closing_odd,
                        closing_bookmaker=result.closing_bookmaker or None,
                        closing_timestamp=result.closing_timestamp or None,
                        clv_percentage=result.clv_percentage,
                        clv_probability=result.clv_probability,
                        status=clv_status_to_api(result.status),
                    ))

        total = len(entries)
        n_registered = sum(1 for e in entries if e.entry_odd is not None)
        with_clv = sum(1 for e in entries if e.status == "OK")
        pcts = [e.clv_percentage for e in entries
                if e.clv_percentage is not None]
        probs = [e.clv_probability for e in entries
                 if e.clv_probability is not None]
        # Medias de verdade (I-03): fmean sobre os CLV validos, nunca soma.
        avg_clv = round(fmean(pcts), 6) if pcts else None
        median_clv = round(median(pcts), 6) if pcts else None
        avg_prob = round(fmean(probs), 6) if probs else None
        pos_rate = (
            round(sum(1 for p in pcts if p > 0) / len(pcts), 6)
            if pcts else None
        )
        # Sem entrada registrada a cobertura nao foi medida: None, nunca
        # 0.0 (que diria "medimos e nenhuma linha fechou").
        coverage = with_clv / total if (total and n_registered) else None

        # by_market com a MESMA semantica do store (CLVCoverage.by_market):
        # {n, avg_clv_percentage} — media por mercado, nao soma.
        by_market: dict[str, dict] = {}
        for e in entries:
            bucket = by_market.setdefault(
                e.market, {"n": 0, "with_clv": 0, "clvs": []})
            bucket["n"] += 1
            if e.status == "OK":
                bucket["with_clv"] += 1
                bucket["clvs"].append(e.clv_percentage)
        by_market = {
            market: {
                "n": bucket["n"],
                "with_clv": bucket["with_clv"],
                "avg_clv_percentage": (
                    round(fmean(bucket["clvs"]), 6)
                    if bucket["clvs"] else None
                ),
            }
            for market, bucket in sorted(by_market.items())
        }

        return S.ClvReport(
            generated_at=now,
            total_bets=total,
            bets_with_clv=with_clv,
            coverage=coverage,
            avg_clv_percentage=avg_clv,
            median_clv_percentage=median_clv,
            positive_clv_rate=pos_rate,
            avg_clv_probability=avg_prob,
            by_market=by_market,
            entries=entries,
            source="football-data.co.uk",
        )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _n_groups(model_probs: dict[str, float]) -> int:
    """Quantos grupos complementares existem num mercado.

    Cada grupo de resultados mutuamente exclusivos soma 1.0 nas
    probabilidades do modelo. 'Resultado Final (1X2)' soma 1 (1 grupo);
    'Total de Gols' com 3 linhas soma 3 (3 grupos over/under). Isso
    permite normalizar o overround por grupo, em vez de somar as
    implicitas de linhas independentes e reportar margem inflada.
    """
    total = sum(model_probs.values())
    return max(1, round(total))


def _overround(odds, groups: int = 1) -> float:
    """Margem media por grupo: soma das implicitas / n_grupos - 1."""
    valid = [o for o in odds if o and o > 1.0]
    if not valid:
        return 0.0
    return sum(implied_prob(o) for o in valid) / max(1, groups) - 1.0


def _team(r: TeamRating) -> S.TeamSnapshot:
    return S.TeamSnapshot(
        name=r.name,
        attack=r.attack,
        defense=r.defense,
        strength=r.strength,
        goals_for=r.goals_for,
        goals_against=r.goals_against,
        xg_for=r.xg_for,
        xg_against=r.xg_against,
        xg_status=r.xg_status,
        xg_source=r.xg_source,
        corners_for=r.corners_for,
        corners_against=r.corners_against,
        cards_for=r.cards_for,
        cards_against=r.cards_against,
        shots_for=r.shots_for,
        shots_on_target_for=r.shots_on_target_for,
        form_points=r.form_points,
        matches_played=r.matches_played,
    )


def _split_match(match: str) -> tuple[str, str]:
    parts = match.split(" vs ")
    return (parts[0], parts[1]) if len(parts) == 2 else (match, "")


_EV_EDGES: list[tuple[str, float, float]] = [
    ("2-3%", 0.02, 0.03),
    ("3-4.5%", 0.03, 0.045),
    ("4.5-6%", 0.045, 0.06),
    ("6-8%", 0.06, 0.08),
    ("8-12%", 0.08, 0.12),
    ("12%+", 0.12, float("inf")),
]


def _ev_buckets(rows: list[CoreSignal]) -> list[S.EvBucket]:
    out: list[S.EvBucket] = []
    for label, lo, hi in _EV_EDGES:
        n = sum(1 for s in rows if lo <= s.ev < hi)
        out.append(S.EvBucket(label=label, lower=lo,
                              upper=(hi if hi != float("inf") else 1.0), count=n))
    return out


service = BetgsnService(source="real")
