"""BETGSN :: api.schemas — contratos tipados entre backend e interface.

Modelos Pydantic explicitos. Nada de dict[str, Any] espalhado pela API.
Todos os valores numericos vem prontos do pipeline; o frontend so formata.

Convencoes:
  - probabilidades: fracao em [0, 1] (o frontend multiplica por 100);
  - edge: diferenca de probabilidade (fracao; 0.0776 = +7.76pp);
  - ev / percentuais: fracao (0.1163 = +11.63%);
  - dinheiro: float na moeda da banca.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from .prediction_schemas import Provenance

ConfidenceLevel = Literal["FORTE", "MEDIA", "FRACA", "DESCARTE"]

# --------------------------------------------------------------------------
# Status de provider (observabilidade)
# --------------------------------------------------------------------------

ProviderAvailability = Literal["CURRENT", "STALE", "UNAVAILABLE", "NO_COVERAGE", "DEGRADED"]


class ProviderHealth(BaseModel):
    """Saude detalhada de um provider de dados."""
    name: str
    status: ProviderAvailability
    last_update: str | None = None
    last_execution: str | None = None
    latency_ms: float | None = None
    error: str | None = None
    quota_used: int = 0
    quota_remaining: int = 0
    coverage: dict[str, bool] = Field(default_factory=dict)
    features: list[str] = Field(default_factory=list)
    message: str | None = None


class ProviderOverview(BaseModel):
    """Visao geral dos providers."""
    providers: list[ProviderHealth]
    generated_at: str
    any_current: bool
    any_stale: bool
    any_unavailable: bool


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

class FixtureItem(BaseModel):
    """Um jogo futuro com sua disponibilidade de odds."""
    match: str
    home: str
    away: str
    league: str
    round_label: str
    kickoff: str
    has_odds: bool
    n_bookmakers: int
    bookmakers: list[str]
    markets: list[str]
    best_odds: dict[str, float] | None = None
    status: Literal["UPCOMING", "LIVE", "SETTLED", "NO_ODDS"] = "UPCOMING"


class FixtureOverview(BaseModel):
    """Visao geral dos fixtures."""
    generated_at: str
    n_fixtures: int
    n_with_odds: int
    fixtures: list[FixtureItem]
    source: str
    data_version: str | None = None


# --------------------------------------------------------------------------
# Odds Movement
# --------------------------------------------------------------------------

class PricePoint(BaseModel):
    """Um ponto de preco observado."""
    bookmaker: str
    market: str
    outcome: str
    odd: float
    timestamp: str
    is_opening: bool = False
    is_closing: bool = False


class OddsMovement(BaseModel):
    """Movimento de odds para um mercado/resultado."""
    match: str
    market: str
    outcome: str
    opening_odd: float | None = None
    current_odd: float | None = None
    price_delta: float | None = None
    price_delta_pct: float | None = None
    book_consensus_move: float | None = None
    book_dispersion: float | None = None
    market_direction: float | None = None
    n_observations: int = 0
    n_books: int = 0
    minutes_since_open: float | None = None
    minutes_to_kickoff: float | None = None
    status: Literal["MOVING", "STABLE", "NO_DATA"] = "NO_DATA"


class OddsMovementOverview(BaseModel):
    """Visao geral do movimento de odds."""
    generated_at: str
    movements: list[OddsMovement]
    source: str
    data_version: str | None = None


# --------------------------------------------------------------------------
# CLV e Coverage
# --------------------------------------------------------------------------

class ClvEntry(BaseModel):
    """Uma entrada de CLV (Closing Line Value)."""
    match: str
    market: str
    outcome: str
    entry_odd: float
    closing_odd: float | None = None
    closing_bookmaker: str | None = None
    closing_timestamp: str | None = None
    clv_percentage: float | None = None
    clv_probability: float | None = None
    status: Literal["OK", "NO_CLOSING_ODDS", "BEFORE_OPENING"] = "NO_CLOSING_ODDS"


class ClvReport(BaseModel):
    """Relatorio agregado de CLV."""
    generated_at: str
    total_bets: int
    bets_with_clv: int
    coverage: float
    avg_clv_percentage: float | None = None
    median_clv_percentage: float | None = None
    positive_clv_rate: float | None = None
    avg_clv_probability: float | None = None
    by_market: dict[str, dict] = Field(default_factory=dict)
    entries: list[ClvEntry]
    source: str


class CoverageReport(BaseModel):
    """Relatorio de cobertura de dados."""
    generated_at: str
    providers: list[ProviderHealth]
    clv_coverage: float
    odds_coverage: float
    xg_coverage: float
    fixtures_coverage: float
    n_fixtures_with_odds: int
    n_fixtures_total: int
    n_bookmakers_active: int
    gaps: list[dict]
    source: str


# --------------------------------------------------------------------------
# Configuracao / entrada
# --------------------------------------------------------------------------


class ModelConfiguration(BaseModel):
    """Parametros que a interface controla na barra superior."""

    bankroll: float = Field(default=1000.0, gt=0, description="Banca atual")
    kelly_fraction: float = Field(default=0.25, gt=0, le=1.0,
                                  description="Fracao de Kelly aplicada")
    min_ev: float = Field(default=0.02, ge=0, le=1.0,
                          description="EV minimo (fracao) para entrar na lista")
    stake_cap: float = Field(default=0.01, gt=0, le=1.0,
                             description="Teto de risco por aposta (fracao da banca)")
    max_exposure: float = Field(default=0.25, gt=0, le=1.0,
                                description="Teto de exposicao total (fracao da banca)")
    use_xg: bool = Field(default=True, description="Mistura xG aos gols nos lambdas")
    rounds: int = Field(default=3, ge=1, le=3, description="Rodadas futuras analisadas")


class ModelConstants(BaseModel):
    """Constantes fixas do modelo, expostas para a tela MODELO."""

    rho_dixon_coles: float
    ev_forte: float
    ev_media: float
    ev_fraca: float
    min_books: int
    max_spread: float
    max_goals_grid: int
    league_avg_goals: float
    home_advantage: float
    attack_blend: float
    dataset_seed: int
    bookmakers: list[str]


# --------------------------------------------------------------------------
# Sinais
# --------------------------------------------------------------------------


class Signal(BaseModel):
    id: str
    match: str
    home: str
    away: str
    kickoff: str
    league: str
    round_label: str
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
    expected_profit_pct: float
    gross_profit_if_win: float
    loss_if_lose: float
    confidence: ConfidenceLevel
    rationale: str


class SignalsKpis(BaseModel):
    """KPIs da tela SINAIS. Calculados no backend, nunca no React."""

    total: int
    strong: int
    medium: int
    weak: int
    strong_pct: float
    medium_pct: float
    weak_pct: float
    expected_profit: float
    expected_profit_pct: float
    avg_stake_pct: float
    total_exposure: float
    total_exposure_pct: float
    worst_case_loss: float
    gross_profit_if_all_win: float
    exposure_scaled_by: float
    max_ev: float


class ModelCalibrationInfo(BaseModel):
    """Calibracao MEDIDA do modelo. Existe para a UI poder avisar o usuario."""

    measured_on: str
    ev_predicted: float
    return_realized: float
    gap_pp: float
    simulated_roi: float
    verdict: str


class SignalReport(BaseModel):
    provenance: Provenance = Field(default_factory=Provenance)
    generated_at: str
    bankroll: float
    kpis: SignalsKpis
    signals: list[Signal]
    top_tips: list[str]
    #: "synthetic" = dataset gerado em memoria (datas fixas, odds do proprio
    #: modelo). "real" = jogos futuros e odds reais do football-data.co.uk.
    source: Literal["synthetic", "real"] = "synthetic"
    source_detail: str = ""
    #: jogos futuros descartados por falta de rating do time
    skipped_no_rating: int = 0
    #: jogos futuros com odds mas sem consenso minimo de casas (MIN_BOOKS)
    skipped_insufficient_books: int = 0
    #: presente apenas quando source="real"
    calibration: ModelCalibrationInfo | None = None


# --------------------------------------------------------------------------
# Jogos
# --------------------------------------------------------------------------


class Scoreline(BaseModel):
    home_goals: int
    away_goals: int
    prob: float


class MarketProbabilities(BaseModel):
    """Um mercado e suas probabilidades de modelo por resultado."""

    market: str
    outcomes: dict[str, float]


class TeamSnapshot(BaseModel):
    name: str
    attack: float
    defense: float
    strength: float
    goals_for: float
    goals_against: float
    xg_for: float | None
    xg_against: float | None
    xg_status: Literal["REAL", "ESTIMATED", "UNAVAILABLE"] = "UNAVAILABLE"
    xg_source: str | None = None
    corners_for: float
    corners_against: float
    cards_for: float
    cards_against: float
    shots_for: float
    shots_on_target_for: float
    form_points: float
    matches_played: int


class GameAnalysis(BaseModel):
    provenance: Provenance = Field(default_factory=Provenance)
    id: str
    match: str
    home: str
    away: str
    league: str
    kickoff: str
    round_label: str
    lambda_home: float
    lambda_away: float
    prob_home: float
    prob_draw: float
    prob_away: float
    prob_over_25: float
    prob_btts: float
    prob_home_corners_over_55: float
    prob_cards_over_35: float
    top_scorelines: list[Scoreline]
    markets: list[MarketProbabilities]
    ratings_home: TeamSnapshot
    ratings_away: TeamSnapshot
    n_markets_with_odds: int
    signal_count: int


# --------------------------------------------------------------------------
# Casas / odds
# --------------------------------------------------------------------------


class BookmakerRow(BaseModel):
    book: str
    odds: dict[str, float]
    best_outcomes: list[str]
    margin: float           # overround da casa nesse mercado (fracao)


class ArbLeg(BaseModel):
    outcome: str
    book: str
    odd: float
    stake: float
    payout: float


class ArbitrageCheck(BaseModel):
    arbitrage: bool
    margin: float
    legs: list[ArbLeg]


class MarketComparison(BaseModel):
    """Comparacao multi-casa de um mercado de um jogo."""

    match: str
    market: str
    outcomes: list[str]
    rows: list[BookmakerRow]
    best_odds: dict[str, float]
    best_books: dict[str, str]
    model_probs: dict[str, float]
    market_probs: dict[str, float]
    arbitrage: ArbitrageCheck


class BookmakerSnapshot(BaseModel):
    """Resumo de uma casa em toda a rodada analisada."""

    book: str
    n_markets: int
    n_best_odds: int
    avg_margin: float
    best_odd_share: float
    signals_won: int


class OddsOverview(BaseModel):
    provenance: Provenance = Field(default_factory=Provenance)
    generated_at: str
    matches: list[str]
    markets_by_match: dict[str, list[str]]
    bookmakers: list[BookmakerSnapshot]


# --------------------------------------------------------------------------
# Estatisticas / modelo
# --------------------------------------------------------------------------


class MarketBreakdown(BaseModel):
    market: str
    signals: int
    avg_ev: float
    avg_edge: float
    best_ev: float
    total_stake: float
    expected_profit: float


class ConfidenceBreakdown(BaseModel):
    confidence: ConfidenceLevel
    signals: int
    avg_ev: float
    avg_odd: float
    total_stake: float
    expected_profit: float


class BookBreakdown(BaseModel):
    book: str
    signals: int
    avg_ev: float
    avg_odd: float
    total_stake: float


class StatsOverview(BaseModel):
    provenance: Provenance = Field(default_factory=Provenance)
    generated_at: str
    league_goals: float
    home_advantage: float
    teams: list[TeamSnapshot]
    n_history_matches: int
    n_fixtures: int
    by_market: list[MarketBreakdown]
    by_confidence: list[ConfidenceBreakdown]
    by_book: list[BookBreakdown]
    ev_distribution: list[EvBucket]


class EvBucket(BaseModel):
    label: str
    lower: float
    upper: float
    count: int


class CalibrationBin(BaseModel):
    predicted: float
    empirical: float
    n: int


class ModelPerformance(BaseModel):
    """Saida do backtest existente (betgsn.backtest). Nada recalculado aqui."""

    split: float
    n_train: int
    n_test: int
    logloss: float
    brier: float
    accuracy: float
    calibration_bins: list[CalibrationBin]
    bankroll_start: float
    bankroll_end: float
    n_bets: int
    n_wins: int
    hit_rate: float
    total_staked: float
    profit: float
    roi: float
    ev_mean_pred: float
    return_mean_real: float
    max_drawdown: float
    summary: str
    source: str = "real"
    financial_status: str = "UNAVAILABLE_WITHOUT_TIMESTAMPED_ODDS"


class ProbabilityModel(BaseModel):
    provenance: Provenance = Field(default_factory=Provenance)
    """Descricao viva do modelo para a tela MODELO."""

    version: str
    engine: str
    generated_at: str
    constants: ModelConstants
    configuration: ModelConfiguration
    league_goals: float
    home_advantage: float
    attack_blend: float
    n_teams: int
    n_history_matches: int
    n_fixtures: int
    n_markets: int
    markets: list[str]
    data_source: str
    providers: dict[str, bool]
    documentation: str


# --------------------------------------------------------------------------
# Dashboard / status
# --------------------------------------------------------------------------


class DashboardSummary(BaseModel):
    provenance: Provenance = Field(default_factory=Provenance)
    generated_at: str
    computed_in_ms: float
    configuration: ModelConfiguration
    kpis: SignalsKpis
    n_games: int
    n_teams: int
    n_bookmakers: int
    n_markets: int
    data_source: str


class SystemStatus(BaseModel):
    status: Literal["ok", "computing", "error"]
    version: str
    python_version: str
    generated_at: str | None
    computed_in_ms: float | None
    has_snapshot: bool
    n_signals: int
    n_games: int
    data_source: str
    providers: dict[str, bool]
    message: str | None = None


class ApiError(BaseModel):
    error: str
    detail: str
    hint: str | None = None


# resolve forward ref de EvBucket usado antes da definicao
StatsOverview.model_rebuild()
