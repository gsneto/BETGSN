"""BETGSN :: api.backtest_schemas — contratos do modulo de backtest.

Espelham os dataclasses de `backtest_engine`/`backtest_metrics`. A regra de
apresentacao e a mesma do resto da API: fracoes em [0,1] (o frontend
multiplica por 100), dinheiro na moeda da banca.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..markets import ALL_MARKET_KEYS, MARKET_GROUPS

OutcomeResult = Literal["win", "loss", "push"]
JobPhase = Literal["idle", "preparing", "analyzing", "metrics", "saving", "done", "error"]


# --------------------------------------------------------------------------
# Configuracao
# --------------------------------------------------------------------------


class BacktestRequest(BaseModel):
    """Corpo de POST /api/backtest/run. Defaults identicos ao Scanner."""

    start_date: str | None = Field(None, description="YYYY-MM-DD, inclusive")
    end_date: str | None = Field(None, description="YYYY-MM-DD, inclusive")
    competitions: list[str] = Field(default_factory=list)

    market_keys: list[str] = Field(default_factory=lambda: list(ALL_MARKET_KEYS))

    bankroll: float = Field(1000.0, gt=0)
    kelly_fraction: float = Field(0.25, gt=0, le=1)
    stake_cap: float = Field(0.01, gt=0, le=1)
    min_ev: float = Field(0.02, ge=0, le=1)
    max_exposure: float = Field(0.25, gt=0, le=1)
    use_xg: bool = True
    min_confidence: Literal["FORTE", "MEDIA", "FRACA", "DESCARTE"] = "FRACA"

    min_history: int = Field(100, ge=1)
    odds_source: Literal[
        "naive_synthetic", "real_historical", "football_data_uk"
    ] = "naive_synthetic"
    apply_exposure_cap: bool = True
    #: corpus historico a usar
    corpus_source: Literal["local", "imported", "football_data_uk"] = "local"
    #: chave de esporte da The Odds API quando odds_source='real_historical'
    odds_sport_key: str = "soccer_brazil_campeonato"
    #: football-data.co.uk: linha de fechamento (True) ou abertura (False)
    odds_closing: bool = True
    #: football-data.co.uk: restringir a estes bookmakers (vazio = todos)
    odds_books: list[str] = Field(default_factory=list)
    allow_untimestamped_odds: bool = False
    refit_every_days: int = Field(1, ge=1)
    train_window_days: int | None = Field(None, ge=1)
    daily_exposure: float = Field(.25, gt=0, le=1)
    league_exposure: float = Field(.25, gt=0, le=1)


class BacktestOptions(BaseModel):
    """Tudo que a UI precisa para montar o painel de configuracao."""

    min_date: str
    max_date: str
    competitions: list[str]
    markets: list[MarketOption]
    default_config: BacktestRequest
    min_history_default: int
    odds_sources: list[OddsSourceOption]
    scanner_ev_thresholds: dict[str, float]
    min_sample: int
    point_in_time_schema: str
    model_version: str
    missing_data_notes: list[MissingDataNote]
    #: corpora disponiveis (local sintetico + temporadas importadas + fduk)
    corpora: list[CorpusOption]
    #: estado do cache de odds historicas reais
    odds_cache: list[OddsCacheStatus]
    #: bookmakers disponiveis na fonte football-data.co.uk
    books: list[str] = Field(default_factory=list)


class CorpusOption(BaseModel):
    key: str
    label: str
    available: bool
    n_matches: int
    first_kickoff: str | None = None
    last_kickoff: str | None = None
    competitions: list[str] = Field(default_factory=list)
    fingerprint: str | None = None
    note: str = ""


class OddsCacheStatus(BaseModel):
    sport_key: str
    snapshots: int
    first: str | None = None
    last: str | None = None


class MarketOption(BaseModel):
    key: str
    label: str


class OddsSourceOption(BaseModel):
    key: str
    label: str
    available: bool
    note: str


class MissingDataNote(BaseModel):
    dado: str
    por_que: str
    quem_fornece: str
    como_plugar: str


# --------------------------------------------------------------------------
# Progresso
# --------------------------------------------------------------------------


class BacktestJobStatus(BaseModel):
    job_id: str | None
    phase: JobPhase
    done: int
    total: int
    progress: float
    message: str
    run_id: str | None = None
    error: str | None = None


# --------------------------------------------------------------------------
# Resultados
# --------------------------------------------------------------------------


class AggregateMetricsOut(BaseModel):
    n_signals: int
    n_settled: int
    n_unsettled: int
    n_wins: int
    n_losses: int
    n_pushes: int
    hit_rate: float
    hit_rate_ci: list[float]
    avg_odd: float
    avg_model_prob: float
    avg_market_prob: float
    avg_edge: float
    avg_ev: float
    avg_realized_return: float
    realized_return_ci: list[float]
    brier: float
    brier_ci: list[float]
    logloss: float
    ev_gap: float
    sample_sufficient: bool


class CalibrationBinOut(BaseModel):
    label: str
    lower: float
    upper: float
    n: int
    avg_predicted: float
    observed_rate: float
    ci_low: float
    ci_high: float
    sufficient: bool


class EvBucketOut(BaseModel):
    label: str
    lower: float
    upper: float
    n: int
    avg_ev: float
    observed_rate: float
    avg_realized_return: float
    ci_low: float
    ci_high: float
    sufficient: bool


class TemporalBucketOut(BaseModel):
    label: str
    n: int
    hit_rate: float
    avg_ev: float
    realized_return: float
    profit: float
    bankroll_end: float | None = None


class SegmentRowOut(BaseModel):
    dimension: str
    segment: str
    n: int
    observed_rate: float
    avg_predicted: float
    avg_ev: float
    brier: float
    ci_low: float
    ci_high: float
    sufficient: bool


class EquityPointOut(BaseModel):
    day: str
    bankroll: float
    peak: float
    drawdown: float
    staked: float
    profit: float
    n_signals: int


class SimulationOut(BaseModel):
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
    equity: list[EquityPointOut]
    exposure_scaled_days: int


class BacktestSignalOut(BaseModel):
    """Registro completo do sinal, com o contexto point-in-time usado."""

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
    odds_source: str
    odds_as_of: str | None
    model_version: str
    config_hash: str
    result_home_goals: int
    result_away_goals: int
    outcome_result: OutcomeResult | None
    settled: bool
    realized_return: float | None
    profit: float | None


class SignalPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[BacktestSignalOut]


class BacktestRunDetail(BaseModel):
    run_id: str
    created_at: str
    config_hash: str
    model_version: str
    point_in_time_schema: str
    config: BacktestRequest
    aggregate: AggregateMetricsOut
    calibration: list[CalibrationBinOut]
    ev_buckets: list[EvBucketOut]
    temporal: dict[str, list[TemporalBucketOut]]
    segments: list[SegmentRowOut]
    simulation: SimulationOut
    meta: dict
    missing_data_notes: list[MissingDataNote]


class BacktestRunSummary(BaseModel):
    run_id: str
    created_at: str
    config_hash: str
    model_version: str
    n_signals: int
    n_matches_evaluated: int
    n_matches_skipped: int
    duration_ms: float
    hit_rate: float | None = None
    brier: float | None = None
    return_pct: float | None = None
    max_drawdown: float | None = None


class MetricDelta(BaseModel):
    a: float
    b: float
    delta: float


class TemporalComparison(BaseModel):
    label: str
    hit_rate_a: float
    hit_rate_b: float
    n_a: float
    n_b: float


class RunComparison(BaseModel):
    run_a: dict
    run_b: dict
    same_config: bool
    metrics: dict[str, MetricDelta]
    temporal_month: list[TemporalComparison]


BacktestOptions.model_rebuild()
