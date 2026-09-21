"""BETGSN :: backtest_engine — motor rolling walk-forward.

Como funciona
-------------
Processa as partidas em ordem cronologica. Para cada partida M com kickoff T:

  1. corta o contexto: `prior = corpus.matches_before(T)` (estritamente antes);
  2. refaz o fit dos ratings usando SOMENTE `prior`;
  3. recalcula a media de gols da liga usando SOMENTE `prior`;
  4. chama `pipeline.analyze_fixture` — o MESMO caminho do Scanner;
  5. gera as odds point-in-time (mercado ingenuo por padrao);
  6. chama `signals.generate_signals` — a MESMA funcao do Scanner;
  7. CONGELA os sinais num registro imutavel `FrozenSignal`;
  8. SO ENTAO resolve contra o resultado real, produzindo `SettledSignal`.

A ordem dos passos 7 e 8 e estrutural, nao uma convencao: `FrozenSignal`
nao possui campo de resultado. E impossivel escrever o resultado dentro do
registro congelado, porque o tipo nao permite. O `SettledSignal` envolve o
`FrozenSignal` — nunca o contrario.

Reuso
-----
Nenhuma regra estatistica e reimplementada aqui. O motor usa:
  model.fit_ratings, model.expected_goals, model.build_score_matrix,
  markets.all_markets, engine.evaluate_market, engine.stake_plan,
  signals.generate_signals, signals.scale_stakes_to_cap,
  pipeline.analyze_fixture.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Callable, Sequence

from . import __version__
from .backtest_data import (
    POINT_IN_TIME_SCHEMA,
    HistoricalCorpus,
    MatchResult,
    NaiveSyntheticOddsSource,
    OddsSource,
    Outcome,
    build_context,
    build_fixture,
    league_goals_before,
    profit_for,
    realized_return,
    settle_outcome,
)
from .data import LEAGUE_HOME_ADVANTAGE
from .engine import stake_plan
from .markets import ALL_MARKET_KEYS, validate_market_keys
from .model import HistoricalMatch, fit_ratings
from .pipeline import analyze_fixture
from .providers import ProviderError
from .signals import EV_FRACA, Confidence, generate_signals, scale_stakes_to_cap
from .timeutil import day_of, utc_key

CONF_ORDER: dict[str, int] = {"FORTE": 3, "MEDIA": 2, "FRACA": 1, "DESCARTE": 0}

#: Minimo de partidas no historico anterior para tentar prever algo.
#: Abaixo disso o fit de ratings e ruido puro e o backtest nao teria sentido.
DEFAULT_MIN_HISTORY = 100

ProgressCallback = Callable[[str, int, int, str], None]


# --------------------------------------------------------------------------
# Configuracao
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BacktestConfig:
    """Parametros de uma execucao de backtest. Serializavel e hashavel."""

    # periodo e recorte
    start_date: str | None = None          # "YYYY-MM-DD" inclusive
    end_date: str | None = None            # "YYYY-MM-DD" inclusive
    competitions: tuple[str, ...] = ()

    # mercados
    market_keys: tuple[str, ...] = ALL_MARKET_KEYS

    # parametros do Scanner (mesmos nomes e mesmos defaults)
    bankroll: float = 1000.0
    kelly_fraction: float = 0.25
    stake_cap: float = 0.01
    min_ev: float = EV_FRACA
    max_exposure: float = 0.25
    use_xg: bool = True
    min_confidence: str = "FRACA"

    # controle do backtest
    min_history: int = DEFAULT_MIN_HISTORY
    odds_source: str = "naive_synthetic"
    odds_seed: int = 6767
    #: chave de esporte da The Odds API quando odds_source='real_historical'
    odds_sport_key: str = "soccer_brazil_campeonato"
    #: idade maxima aceita para um snapshot de odds antes do kickoff
    odds_max_age_hours: float = 24.0
    #: diretorio do cache de odds historicas (None = padrao do projeto)
    odds_cache_root: str | None = None
    #: football-data.co.uk: usar a linha de fechamento (True) ou abertura
    odds_closing: bool = True
    #: football-data.co.uk: restringir a estes bookmakers (vazio = todos).
    #: Ex.: ("Pinnacle",) para testar contra a linha mais afiada do mercado.
    odds_books: tuple[str, ...] = ()
    #: Como os ratings sao recalculados ao longo do tempo.
    #:   "day"   -> um fit por dia, usando tudo antes daquele dia (padrao).
    #:              Realista: ninguem refaz o modelo entre dois jogos do
    #:              mesmo dia. E ordens de magnitude mais rapido.
    #:   "match" -> um fit por partida (mais lento; O(n^2) no tamanho do
    #:              historico). Util para auditar um periodo curto.
    refit: str = "day"
    #: Com refit='day', de quantos em quantos dias refazer o fit. 1 = diario.
    #: 7 = semanal (realista para um operador e muito mais rapido em corpus
    #: grande). Valores maiores usam informacao um pouco mais antiga — o que
    #: e conservador, nunca vaza o futuro.
    refit_every_days: int = 1
    apply_exposure_cap: bool = True
    ref_strictness: float = 1.0
    train_window_days: int | None = None
    daily_exposure: float = 0.25
    league_exposure: float = 0.25
    allow_untimestamped_odds: bool = False

    def __post_init__(self) -> None:
        if self.bankroll <= 0:
            raise ValueError("bankroll precisa ser > 0")
        if self.train_window_days is not None and self.train_window_days < 1:
            raise ValueError("train_window_days precisa ser positivo")
        if not 0 < self.daily_exposure <= 1 or not 0 < self.league_exposure <= 1:
            raise ValueError("exposição diária/liga deve estar em (0,1]")
        if not 0 < self.kelly_fraction <= 1:
            raise ValueError("kelly_fraction precisa estar em (0, 1]")
        if not 0 < self.stake_cap <= 1:
            raise ValueError("stake_cap precisa estar em (0, 1]")
        if not 0 <= self.min_ev <= 1:
            raise ValueError("min_ev precisa estar em [0, 1]")
        if not 0 < self.max_exposure <= 1:
            raise ValueError("max_exposure precisa estar em (0, 1]")
        if self.min_confidence not in CONF_ORDER:
            raise ValueError(
                f"min_confidence invalido: {self.min_confidence!r}. "
                f"validos: {list(CONF_ORDER)}"
            )
        if self.min_history < 1:
            raise ValueError("min_history precisa ser >= 1")
        if self.refit not in ("day", "match"):
            raise ValueError(
                f"refit invalido: {self.refit!r}. validos: 'day', 'match'"
            )
        if self.refit_every_days < 1:
            raise ValueError("refit_every_days precisa ser >= 1")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date nao pode ser posterior a end_date")

    @property
    def attack_blend(self) -> float:
        """Peso do xG no ataque. Mesma regra do Scanner (0.5 = meio a meio)."""
        return 0.5 if self.use_xg else 0.0

    def normalized(self) -> "BacktestConfig":
        """Valida e ordena os mercados. Levanta erro em chave desconhecida."""
        return replace(self, market_keys=validate_market_keys(self.market_keys))

    def fingerprint(self) -> str:
        """Hash estavel da configuracao, para auditoria e reprodutibilidade."""
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Registros congelados
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FrozenSignal:
    """Sinal como foi produzido, ANTES de conhecer o resultado.

    Nao possui campo de resultado por construcao — e isso que impede
    vazamento de informacao futura para dentro da previsao.
    """

    signal_id: str
    match_id: str
    competition: str
    season: str
    kickoff: str
    kickoff_utc: str
    home: str
    away: str

    market: str
    outcome: str
    best_odd: float
    best_book: str
    median_odd: float
    fair_odd: float
    n_books: int

    model_prob: float
    market_prob: float
    edge: float
    ev: float
    kelly: float
    stake: float
    stake_pct: float
    expected_profit: float
    confidence: str
    rationale: str

    # features point-in-time efetivamente usadas
    lambda_home: float
    lambda_away: float
    home_attack: float
    home_defense: float
    away_attack: float
    away_defense: float
    home_xg_for: float | None
    away_xg_for: float | None
    n_prior_matches: int
    league_goals: float
    attack_blend: float

    # rastreabilidade das odds
    odds_source: str
    odds_as_of: str | None

    # parametros vigentes na execucao
    model_version: str
    config_hash: str


@dataclass(frozen=True)
class SettledSignal:
    """Sinal congelado + resultado observado. O resultado so entra aqui."""

    signal: FrozenSignal
    result_home_goals: int
    result_away_goals: int
    outcome_result: Outcome | None
    realized_return: float | None
    profit: float | None
    settled: bool

    @property
    def won(self) -> bool:
        return self.outcome_result == "win"

    @property
    def pushed(self) -> bool:
        return self.outcome_result == "push"


@dataclass
class BacktestRun:
    """Resultado completo de uma execucao, pronto para serializar."""

    run_id: str
    config: BacktestConfig
    config_hash: str
    model_version: str
    point_in_time_schema: str
    started_at: str
    finished_at: str
    duration_ms: float
    n_matches_in_period: int
    n_matches_evaluated: int
    n_matches_skipped: int
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    signals: list[SettledSignal] = field(default_factory=list)
    missing_data_notes: tuple[dict[str, str], ...] = ()
    predictions: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------
# Motor
# --------------------------------------------------------------------------


def _signal_id(index: int, match: HistoricalMatch, market: str, outcome: str) -> str:
    raw = f"{index}|{match.kickoff}|{match.home}|{match.away}|{market}|{outcome}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _freeze(
    signal_index: int,
    match: HistoricalMatch,
    analysis,
    sig,
    context,
    odds_source_name: str,
    config: BacktestConfig,
    config_hash: str,
    odds_as_of: str | None,
) -> FrozenSignal:
    """Congela um sinal com tudo que o modelo usou naquele instante."""
    hr, ar = analysis.ratings_home, analysis.ratings_away
    key_utc = utc_key(match.kickoff, match.timezone)
    return FrozenSignal(
        signal_id=_signal_id(signal_index, match, sig.market, sig.outcome),
        match_id=f"{key_utc}|{match.home}|{match.away}",
        competition=match.league,
        season=match.season,
        kickoff=match.kickoff,
        kickoff_utc=key_utc,
        home=match.home,
        away=match.away,
        market=sig.market,
        outcome=sig.outcome,
        best_odd=sig.best_odd,
        best_book=sig.best_book,
        median_odd=sig.median_odd,
        fair_odd=sig.fair_odd,
        n_books=sig.n_books,
        model_prob=sig.model_prob,
        market_prob=sig.market_prob,
        edge=sig.edge,
        ev=sig.ev,
        kelly=sig.kelly,
        stake=sig.stake,
        stake_pct=sig.stake_pct,
        expected_profit=sig.expected_profit,
        confidence=sig.confidence.value,
        rationale=sig.rationale,
        lambda_home=analysis.lambdas[0],
        lambda_away=analysis.lambdas[1],
        home_attack=hr.attack,
        home_defense=hr.defense,
        away_attack=ar.attack,
        away_defense=ar.defense,
        home_xg_for=hr.xg_for,
        away_xg_for=ar.xg_for,
        n_prior_matches=context.n_prior_matches,
        league_goals=context.league_goals,
        attack_blend=config.attack_blend,
        odds_source=odds_source_name,
        odds_as_of=odds_as_of,
        model_version=__version__,
        config_hash=config_hash,
    )


def _settle(frozen: FrozenSignal, result: MatchResult) -> SettledSignal:
    """Resolve um sinal JA CONGELADO contra o resultado real."""
    outcome_result = settle_outcome(frozen.market, frozen.outcome, result)
    if outcome_result is None:
        return SettledSignal(
            signal=frozen,
            result_home_goals=result.home_goals,
            result_away_goals=result.away_goals,
            outcome_result=None,
            realized_return=None,
            profit=None,
            settled=False,
        )
    return SettledSignal(
        signal=frozen,
        result_home_goals=result.home_goals,
        result_away_goals=result.away_goals,
        outcome_result=outcome_result,
        realized_return=realized_return(outcome_result, frozen.best_odd),
        profit=profit_for(outcome_result, frozen.best_odd, frozen.stake),
        settled=True,
    )


def _day_index(day: str) -> int:
    """Indice ordinal de um dia ISO (YYYY-MM-DD), para agrupar em janelas."""
    from datetime import date

    try:
        return date.fromisoformat(day).toordinal()
    except ValueError:
        return 0


def _make_odds_source(config: BacktestConfig) -> OddsSource:
    if config.odds_source == "naive_synthetic":
        return NaiveSyntheticOddsSource(base_seed=config.odds_seed)
    if config.odds_source == "real_historical":
        from pathlib import Path

        from .backtest_data import RealHistoricalOddsSource
        from .backtest_sources import OddsHistoryCache

        cache = (
            OddsHistoryCache(Path(config.odds_cache_root))
            if config.odds_cache_root
            else None
        )
        return RealHistoricalOddsSource(
            cache=cache,
            sport_key=config.odds_sport_key,
            max_age_hours=config.odds_max_age_hours,
        )
    if config.odds_source == "football_data_uk":
        from .football_data_uk import CsvRealOddsSource

        return CsvRealOddsSource(
            csv_odds_store(config.odds_closing),
            book_filter=config.odds_books or None,
        )
    raise ValueError(
        f"odds_source desconhecido: {config.odds_source!r}. "
        "validos: 'naive_synthetic', 'real_historical', 'football_data_uk'"
    )


#: Cache dos stores de odds reais (parsear centenas de CSVs e caro).
_CSV_STORE_CACHE: dict[bool, object] = {}


def csv_odds_store(closing: bool = True):
    """Store de odds reais do football-data.co.uk, construido uma vez.

    Ler ~600 CSVs leva segundos; o backtest roda muitas vezes, entao o
    resultado fica em cache no processo.
    """
    if closing not in _CSV_STORE_CACHE:
        from .football_data_uk import FootballDataClient, RealOddsStore

        client = FootballDataClient()
        store = RealOddsStore(closing=closing)
        store.add(client.load_matches())
        _CSV_STORE_CACHE[closing] = store
    return _CSV_STORE_CACHE[closing]


def invalidate_csv_odds_store() -> None:
    """Descarta o cache — chamar apos baixar CSVs novos."""
    _CSV_STORE_CACHE.clear()


def run_backtest(
    corpus: HistoricalCorpus,
    config: BacktestConfig,
    progress: ProgressCallback | None = None,
    predictor=None,
) -> BacktestRun:
    """Executa o backtest rolling walk-forward sobre o corpus.

    Complexidade: um fit de ratings por partida avaliada. Para 380 partidas
    com historico crescente isso roda em poucos segundos — por isso o
    processamento fica no backend e nao precisa de fila distribuida.
    """
    config = config.normalized()
    config_hash = config.fingerprint()
    odds_source = _make_odds_source(config)
    started = datetime.now()
    t0 = time.perf_counter()

    def report(phase: str, done: int, total: int, message: str) -> None:
        if progress is not None:
            progress(phase, done, total, message)

    period = corpus.filter(config.start_date, config.end_date, config.competitions)

    report("preparing", 0, len(period), "Preparando dados historicos")

    signals: list[SettledSignal] = []
    skipped: dict[str, int] = defaultdict(int)
    evaluated = 0
    signal_index = 0
    total = len(period)
    predictions = []

    # Cache por janela de refit. Com `refit='day'`, todos os jogos de um
    # mesmo dia compartilham o contexto calculado com o historico anterior
    # ao primeiro kickoff daquele dia. Isso e o que um operador real faria
    # (ninguem refaz o modelo entre dois jogos da mesma tarde).
    #
    # Guardar tambem o `prior` e essencial: `matches_before` devolve uma
    # COPIA do historico (ate centenas de milhares de itens). Chama-la uma
    # vez por partida custava mais que toda a analise junta — num corpus de
    # 250 mil partidas isso levava o backtest de minutos para horas.
    cached_bucket: int | None = None
    cached: tuple[dict, float, object, list] | None = None

    for i, match in enumerate(period, 1):
        # ---------------------------------------------------- corte no tempo
        day = day_of(match.kickoff, match.timezone)
        bucket = _day_index(day) // config.refit_every_days

        if (
            config.refit == "day"
            and cached_bucket == bucket
            and cached is not None
        ):
            ratings, league_goals, context, n_prior = cached
        else:
            prior = corpus.available_before(match.kickoff, match.timezone)
            if config.train_window_days is not None:
                from datetime import timedelta
                from .timeutil import parse_kickoff
                lower = parse_kickoff(match.kickoff, match.timezone) - timedelta(days=config.train_window_days)
                prior = [m for m in prior if parse_kickoff(m.kickoff, m.timezone) >= lower]
            n_prior = len(prior)
            if n_prior < config.min_history:
                # sem historico suficiente nao ha o que ajustar. Guarda a
                # contagem para nao refazer o corte a cada partida da janela.
                cached_bucket, cached = bucket, ({}, 0.0, None, n_prior)
                skipped["historico_insuficiente"] += 1
                if i % 250 == 0 or i == total:
                    report("analyzing", i, total, f"Analisando partidas ({i}/{total})")
                continue
            teams = sorted({m.home for m in prior} | {m.away for m in prior})
            ratings = fit_ratings(prior, teams, home_advantage=LEAGUE_HOME_ADVANTAGE, use_xg=config.use_xg)
            league_goals = league_goals_before(prior)
            context = build_context(
                prior,
                cutoff=utc_key(match.kickoff, match.timezone),
                market_keys=config.market_keys,
                home_advantage=LEAGUE_HOME_ADVANTAGE,
                attack_blend=config.attack_blend,
                # fonte de odds reais nao consome o mercado de referencia
                with_reference_probs=getattr(
                    odds_source, "needs_reference_probs", True
                ),
            )
            cached_bucket, cached = bucket, (ratings, league_goals, context, n_prior)

        if n_prior < config.min_history:
            skipped["historico_insuficiente"] += 1
            if i % 250 == 0 or i == total:
                report("analyzing", i, total, f"Analisando partidas ({i}/{total})")
            continue

        home_rating = ratings.get(match.home)
        away_rating = ratings.get(match.away)
        if home_rating is None or away_rating is None:
            skipped["time_sem_rating"] += 1
            continue

        # MESMO caminho de analise do Scanner
        fixture = build_fixture(match)
        analysis = analyze_fixture(
            fixture,
            home_rating,
            away_rating,
            league_goals,
            LEAGUE_HOME_ADVANTAGE,
            attack_blend=config.attack_blend,
            market_keys=config.market_keys,
            ref_strictness=config.ref_strictness,
        )
        if predictor is not None:
            analysis.markets = predictor(fixture, analysis)
        # Congela probabilidades de TODOS os jogos, antes de observar o resultado.
        frozen_prediction = {
            "kickoff": utc_key(match.kickoff, match.timezone),
            "home": match.home, "away": match.away, "league": match.league,
            "markets": {k: dict(v) for k, v in analysis.markets.items()},
            "feature_cutoff": context.cutoff, "n_prior": n_prior,
        }
        predictions.append({"prediction": frozen_prediction,
                            "home_goals": match.home_goals, "away_goals": match.away_goals})

        # odds point-in-time (nao conhecem o resultado nem a forca dos times)
        try:
            odds = odds_source.odds_for(match, context.reference_probs)
        except (KeyError, ProviderError) as exc:
            # odds reais ausentes para esta partida: pula em vez de derrubar
            # a execucao inteira. O motivo fica contabilizado.
            skipped["sem_odds"] += 1
            if len(skipped) <= 3:
                report("analyzing", i, total, f"sem odds: {exc}"[:120])
            continue
        # instante em que o mercado tinha esse estado. Para o mercado
        # sintetico e o proprio kickoff; para odds reais e o timestamp do
        # snapshot, sempre anterior ao kickoff.
        odds_as_of = getattr(odds_source, "last_as_of", None)
        if odds_source.name != "naive_synthetic":
            if odds_as_of is None and not config.allow_untimestamped_odds:
                skipped["sem_timestamp_odds"] += 1
                continue
            if odds_as_of is not None and utc_key(odds_as_of) >= utc_key(match.kickoff, match.timezone):
                skipped["odds_futuras"] += 1
                continue

        # MESMA geracao de sinais do Scanner
        raw_signals = generate_signals(
            fixture,
            analysis.markets,
            odds,
            bankroll=config.bankroll,
            kelly_frac=config.kelly_fraction,
            stake_cap=config.stake_cap,
            min_ev=config.min_ev,
        )

        min_conf = CONF_ORDER[config.min_confidence]
        for sig in raw_signals:
            if CONF_ORDER.get(sig.confidence.value, 0) < min_conf:
                continue
            frozen = _freeze(
                signal_index, match, analysis, sig, context,
                odds_source.name, config, config_hash, odds_as_of,
            )
            signal_index += 1
            # o resultado entra DEPOIS do congelamento
            signals.append(_settle(frozen, MatchResult.from_match(match)))

        evaluated += 1
        if i % 25 == 0 or i == total:
            report("analyzing", i, total, f"Analisando partidas ({i}/{total})")

    signals.sort(key=lambda s: (s.signal.kickoff_utc, -s.signal.ev))
    report("metrics", total, total, "Calculando metricas")

    # Se a fonte de odds nao serviu NENHUMA partida, o backtest nao rodou de
    # verdade — isso e erro de configuracao, nao "zero oportunidades". Falhar
    # alto evita a pior leitura possivel: um resultado vazio parecendo valido.
    if evaluated == 0 and skipped.get("sem_odds", 0) > 0:
        raise ValueError(
            f"nenhuma partida tinha odds na fonte '{config.odds_source}': "
            f"{skipped['sem_odds']} partidas sem odds. Verifique se o cache "
            f"cobre o periodo testado (ex.: rode `python betgsn.py --sources`)."
        )

    finished = datetime.now()
    run_id = (
        f"bt_{finished.strftime('%Y%m%d_%H%M%S')}_{config_hash[:8]}"
    )

    from .backtest_data import MISSING_DATA_NOTES

    return BacktestRun(
        run_id=run_id,
        config=config,
        config_hash=config_hash,
        model_version=__version__,
        point_in_time_schema=POINT_IN_TIME_SCHEMA,
        started_at=started.strftime("%Y-%m-%d %H:%M:%S"),
        finished_at=finished.strftime("%Y-%m-%d %H:%M:%S"),
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        n_matches_in_period=len(period),
        n_matches_evaluated=evaluated,
        n_matches_skipped=sum(skipped.values()),
        skipped_reasons=dict(skipped),
        signals=signals,
        missing_data_notes=MISSING_DATA_NOTES,
        predictions=predictions,
    )


# --------------------------------------------------------------------------
# Simulacao de banca virtual
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EquityPoint:
    """Um ponto da curva de capital, ao fim de um dia de sinais."""

    day: str
    bankroll: float
    peak: float
    drawdown: float
    staked: float
    profit: float
    n_signals: int


@dataclass(frozen=True)
class SimulationResult:
    initial_bankroll: float
    final_bankroll: float
    profit: float
    return_pct: float
    total_staked: float
    roi: float
    max_drawdown: float
    max_drawdown_day: str
    longest_win_streak: int
    longest_loss_streak: int
    n_bets: int
    n_wins: int
    n_losses: int
    n_pushes: int
    return_std: float
    equity: list[EquityPoint]
    exposure_scaled_days: int


def simulate_bankroll(
    signals: Sequence[SettledSignal],
    config: BacktestConfig,
) -> SimulationResult:
    """SIMULACAO historica de banca usando as MESMAS regras do Scanner.

    Regras aplicadas:
      - stake por `engine.stake_plan` (Kelly fracionado + teto por aposta),
        recalculado sobre a banca vigente;
      - teto de exposicao aplicado POR DIA (`signals.scale_stakes_to_cap`),
        porque os sinais de um mesmo dia sao simultaneos: a banca nao muda
        entre eles. E o analogo historico do snapshot do Scanner.

    Isto e uma simulacao de resultado passado, nao previsao de lucro.
    """
    bankroll = config.bankroll
    peak = bankroll
    max_dd = 0.0
    max_dd_day = ""
    total_staked = 0.0
    wins = losses = pushes = 0
    cur_win = cur_loss = 0
    best_win = best_loss = 0
    returns: list[float] = []
    equity: list[EquityPoint] = []
    scaled_days = 0

    settled = [s for s in signals if s.settled]
    by_day: dict[str, list[SettledSignal]] = defaultdict(list)
    for s in settled:
        by_day[s.signal.kickoff_utc[:10]].append(s)

    for day in sorted(by_day):
        day_signals = by_day[day]
        # stakes calculadas sobre a banca no inicio do dia
        plans = [
            stake_plan(
                bankroll, s.signal.model_prob, s.signal.best_odd,
                fraction=config.kelly_fraction, cap=config.stake_cap,
            )
            for s in day_signals
        ]
        stakes = [p.stake for p in plans]
        if config.apply_exposure_cap:
            stakes, scale = scale_stakes_to_cap(stakes, bankroll * min(config.max_exposure, config.daily_exposure))
            for league in {s.signal.competition for s in day_signals}:
                indices = [i for i, s in enumerate(day_signals) if s.signal.competition == league]
                limited, _ = scale_stakes_to_cap([stakes[i] for i in indices], bankroll * config.league_exposure)
                for idx, amount in zip(indices, limited):
                    stakes[idx] = amount
            if scale < 1.0:
                scaled_days += 1

        day_profit = 0.0
        day_staked = 0.0
        for s, stake in zip(day_signals, stakes):
            if stake <= 0:
                continue
            day_staked += stake
            total_staked += stake
            profit = profit_for(s.outcome_result or "loss", s.signal.best_odd, stake)
            day_profit += profit
            if s.won:
                wins += 1
                cur_win += 1
                cur_loss = 0
                best_win = max(best_win, cur_win)
            elif s.pushed:
                pushes += 1
            else:
                losses += 1
                cur_loss += 1
                cur_win = 0
                best_loss = max(best_loss, cur_loss)
            if s.realized_return is not None:
                returns.append(s.realized_return)

        bankroll += day_profit
        if bankroll > peak:
            peak = bankroll
        dd = (peak - bankroll) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            max_dd_day = day
        equity.append(EquityPoint(
            day=day,
            bankroll=round(bankroll, 2),
            peak=round(peak, 2),
            drawdown=dd,
            staked=round(day_staked, 2),
            profit=round(day_profit, 2),
            n_signals=len(day_signals),
        ))

    mean_ret = sum(returns) / len(returns) if returns else 0.0
    var = (
        sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        if len(returns) > 1
        else 0.0
    )
    profit = bankroll - config.bankroll
    return SimulationResult(
        initial_bankroll=config.bankroll,
        final_bankroll=round(bankroll, 2),
        profit=round(profit, 2),
        return_pct=profit / config.bankroll if config.bankroll else 0.0,
        total_staked=round(total_staked, 2),
        roi=(profit / total_staked) if total_staked > 0 else 0.0,
        max_drawdown=max_dd,
        max_drawdown_day=max_dd_day,
        longest_win_streak=best_win,
        longest_loss_streak=best_loss,
        n_bets=wins + losses + pushes,
        n_wins=wins,
        n_losses=losses,
        n_pushes=pushes,
        return_std=var ** 0.5,
        equity=equity,
        exposure_scaled_days=scaled_days,
    )
