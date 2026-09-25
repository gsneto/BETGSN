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

#: Vocabulario CANONICO de status de evidencia — o mesmo enum do
#: dominio (`betgsn.staking.EVIDENCE_STATUSES`). A decisao carrega o
#: status que a fundamentou; "exploratory" nunca vira "validated"
#: porque existe uma previsao: sao estados de EVIDENCIA, nao de output.
EvidenceStatus = Literal[
    "exploratory", "validated", "timestamped", "real", "synthetic",
]

# --------------------------------------------------------------------------
# Status de provider (observabilidade)
# --------------------------------------------------------------------------
#
# O vocabulario e o do dominio (betgsn.odds_health.ProviderState):
# HEALTHY / DEGRADED / UNAVAILABLE / STALE / NO_COVERAGE. Nao existe
# "CURRENT": saude e estado observado pelo Odds Layer, nao freshness
# inventada na borda. UNKNOWN = provider sem observacao registrada
# (nunca coletado neste processo) — ausencia de informacao continua
# ausencia, nunca vira "ativo".

ProviderAvailability = Literal[
    "HEALTHY", "DEGRADED", "UNAVAILABLE", "STALE", "NO_COVERAGE", "UNKNOWN"
]


class ProviderHealth(BaseModel):
    """Saude detalhada de um provider de dados.

    Todos os campos observaveis sao opcionais e ficam None quando nao ha
    informacao real: latencia nunca medida, quota desconhecida, nenhuma
    coleta bem-sucedida. A API nao inventa nenhum desses valores.
    """
    name: str
    status: ProviderAvailability
    last_update: str | None = None
    last_execution: str | None = None
    latency_ms: float | None = None
    error: str | None = None
    quota_used: int | None = None
    quota_remaining: int | None = None
    coverage: dict[str, bool] = Field(default_factory=dict)
    features: list[str] = Field(default_factory=list)
    message: str | None = None


class ProviderOverview(BaseModel):
    """Visao geral dos providers."""
    providers: list[ProviderHealth]
    generated_at: str
    any_healthy: bool
    any_stale: bool
    any_unavailable: bool


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

class FixtureItem(BaseModel):
    """Um jogo futuro com sua disponibilidade de odds.

    Contrato temporal:
      - `kickoff` e o INSTANTE do kickoff em UTC canonico
        ("YYYY-MM-DDTHH:MM:SSZ"), igual ao usado em /api/signals e
        /api/games — o mesmo jogo representa o mesmo instante em todos
        os endpoints;
      - `kickoff_local` e `timezone` preservam o horario local da
        competicao e o fuso IANA de origem. Nunca se poe "Z" numa hora
        local: a conversao para UTC usa o fuso da liga (incluindo DST).
    """

    match: str
    home: str
    away: str
    league: str
    round_label: str
    kickoff: str
    kickoff_local: str = ""
    timezone: str = ""
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
    #: Estados do dominio (OddsSnapshotStore): NO_DATA = nenhuma observacao;
    #: INSUFFICIENT_DATA = uma unica observacao (impossivel medir movimento);
    #: MOVING/STABLE = duas ou mais observacoes, subdivididas pelo delta.
    status: Literal["MOVING", "STABLE", "NO_DATA", "INSUFFICIENT_DATA"] = "NO_DATA"


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
    #: Odd de entrada: MEDIANA das casas observadas no instante da decisao
    #: (OddsSnapshotStore.line_at). None = nenhuma observacao PIT valida
    #: naquele instante — nunca odd sintetica, nunca fx.best_odds atual.
    entry_odd: float | None = None
    #: Timestamp REAL da observacao que produziu a entrada — nunca o
    #: prediction_timestamp no lugar do timestamp da odd.
    entry_timestamp: str | None = None
    closing_odd: float | None = None
    closing_bookmaker: str | None = None
    closing_timestamp: str | None = None
    clv_percentage: float | None = None
    clv_probability: float | None = None
    #: Estados do dominio (CLVResult) mais a ausencia de entrada:
    #: OK = fechamento valido apos a entrada;
    #: NO_CLOSING_ODDS = entrada registrada, sem observacao de
    #:   fechamento valida;
    #: CLOSING_BEFORE_ENTRY = fechamento encontrado, mas ANTERIOR a
    #:   entrada (aposta pos-fechamento; CLV nao calculado);
    #: NO_ENTRY_ODDS = sem observacao PIT valida no instante da decisao
    #:   (nada foi registrado — ausencia explicita, nunca odd inventada).
    status: Literal[
        "OK", "NO_CLOSING_ODDS", "CLOSING_BEFORE_ENTRY", "NO_ENTRY_ODDS"
    ] = "NO_CLOSING_ODDS"
    #: ---- Proveniencia da decisao (schema v4 do store) ----
    home: str | None = None
    away: str | None = None
    league: str | None = None
    #: Casas que sustentaram a mediana de entrada.
    entry_n_books: int | None = None
    #: Casa representativa da mediana de entrada.
    entry_bookmaker: str | None = None
    #: EXECUTABILIDADE: o preco de execucao e UNKNOWN enquanto nao houver
    #: execucao real registrada — o preco OBSERVADO na decisao nunca e
    #: presumido igual ao preco EXECUTADO.
    execution_status: Literal["UNKNOWN"] = "UNKNOWN"
    #: Ciclo de vida operacional: PENDING (kickoff no futuro, fechamento
    #: ainda pode chegar), NO_CLOSE (kickoff passou sem fechamento
    #: valido), CLOSED (CLV calculado), INVALID (dado inconsistente),
    #: MISMATCH (entrada sem observacao correspondente no store).
    lifecycle_state: Literal[
        "PENDING", "NO_CLOSE", "CLOSED", "INVALID", "MISMATCH"
    ] | None = None
    lifecycle_detail: str | None = None


class ClvReport(BaseModel):
    """Relatorio agregado de CLV.

    `coverage` e None quando nenhuma entrada foi registrada: sem
    entrada, a cobertura nao foi medida — 0.0 ficaria reservado para
    "medido e zero". As medias/medianas sao medias de verdade sobre os
    CLV validos (status OK); sem CLV valido, ficam None.

    `lifecycle` conta as entradas registradas por estado do ciclo de
    vida (PENDING/NO_CLOSE/CLOSED/INVALID/MISMATCH): "sem fechamento
    AINDA" e diferente de "sem fechamento NUNCA" — ausencia de
    fechamento nunca vira CLV=0. NOTA: esta contagem cobre as entradas
    CASADAS com os fixtures atuais (populacao deste relatorio); a
    visao operacional do store INTEIRO vive em /api/quant/clv/status
    (clv_lifecycle_sweep) — duas populacoes diferentes, ambos
    declaradas.
    """
    generated_at: str
    total_bets: int
    bets_with_clv: int
    coverage: float | None = None
    avg_clv_percentage: float | None = None
    median_clv_percentage: float | None = None
    positive_clv_rate: float | None = None
    avg_clv_probability: float | None = None
    by_market: dict[str, dict] = Field(default_factory=dict)
    entries: list[ClvEntry]
    source: str
    lifecycle: dict[str, int] = Field(default_factory=dict)


class CoverageReport(BaseModel):
    """Relatorio de cobertura de dados.

    Cada metrica so existe quando ha evidencia OBSERVADA. `None` quer
    dizer "nao medido"; 0.0 fica reservado para "medido e zero" (ex.:
    existem linhas apostaveis, nenhuma com fechamento valido). Fabricar
    0.0 no lugar de None confundiria ausencia de medicao com cobertura
    nula.

    - `odds_coverage`: fracao dos fixtures OBSERVADOS com odds de ao
      menos uma casa. None quando nenhum fixture foi observado.
    - `clv_coverage`: fracao das linhas apostaveis (mercado/resultado
      dos fixtures com odds) com fechamento valido no store canonico de
      odds — a mesma fonte operacional do /api/clv. None quando nao ha
      linha apostavel a medir.
    - `xg_coverage`: fracao de fixtures com xG REAL observado. None
      quando nao ha fonte de xG real; xG estimado/sintetico nao conta.
    - `n_bookmakers_active`/`bookmakers_observed`: casas efetivamente
      OBSERVADAS nas odds dos fixtures. Chave de provider configurada
      nao e bookmaker — e potencialidade, nao evidencia.
    """
    generated_at: str
    providers: list[ProviderHealth]
    clv_coverage: float | None
    odds_coverage: float | None
    xg_coverage: float | None
    n_fixtures_with_odds: int
    n_fixtures_total: int
    n_bookmakers_active: int
    bookmakers_observed: list[str] = Field(default_factory=list)
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


class DecisionCheck(BaseModel):
    """Uma verificacao individual da decisao de apostar (auditavel)."""

    name: str
    passed: bool
    detail: str


class BetDecision(BaseModel):
    """Decisao do Quant: apostar (BET) ou nao apostar (NO_BET).

    A decisao vem do Quant (`staking.decide_bet`); a API so traduz.
    NO_BET e resultado de primeira classe: nao vira aposta, nao ganha
    stake inventado — `fraction` e 0.0 e o `reason` preserva o motivo.

    `evidence_status` e o vocabulario canonico do dominio
    (`staking.EVIDENCE_STATUSES`): o status da evidencia que fundamentou
    a decisao atravessa explicito, em vez de virar texto de detail de
    um check. Nao existe promocao implicita de "exploratory" para
    "validated": o valor chega intacto ou a roda falha.
    """

    action: Literal["BET", "NO_BET"]
    reason: str
    fraction: float = 0.0
    conservative_roi: float | None = None
    kelly_full: float | None = None
    evidence_status: EvidenceStatus = "exploratory"
    checks: list[DecisionCheck] = Field(default_factory=list)

    @property
    def should_bet(self) -> bool:
        return self.action == "BET"


class CacheMeta(BaseModel):
    """Metadados de cache do relatorio.

    `status` NUNCA e 'LIVE': computo fresco (cache miss) NAO expoe
    `cache` no payload. Presenca deste bloco = leitura de cache com
    idade explicita.
      - STALE: dentro do TTL, servido do cache com `age_seconds` real
      - EXPIRED: retornado apenas quando a computacao pos-TTL ainda
        esta em andamento e o cliente pediu snapshot antigo (o Servico
        prefere recomputar; usado em cenarios de contencao)
    """
    status: Literal["STALE", "EXPIRED"]
    age_seconds: float
    ttl_seconds: float
    key_fingerprint: str
    computed_at: str
    computed_in_ms: float


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
    #: decisao do Quant sobre a evidencia atual (BET | NO_BET), com motivo
    decision: BetDecision | None = None
    #: presente apenas em cache hit; ausente = computo fresco.
    cache: CacheMeta | None = None


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


RecalcPhase = Literal[
    "idle", "starting", "fixtures", "history", "ratings",
    "analyzing", "signals", "done", "error", "cancelled",
]


class RecalculateJobStatus(BaseModel):
    """Estado do job assincrono de recalculo (POST /api/recalculate).

    Fases correspondem a etapas REAIS do pipeline (ver
    RealSignalsService.snapshot/report): nenhuma fase e inventada. O
    progresso e exato dentro da fase `analyzing` (i/n fixtures) e
    conservador nas demais (a fase ainda nao terminou).
    """

    job_id: str | None
    phase: RecalcPhase
    progress: float = Field(ge=0.0, le=1.0)
    message: str
    error: str | None = None
    #: timestamp do snapshot EM MEMORIA (preservado quando o job falha)
    snapshot_generated_at: str | None = None
    #: snapshot em memoria? a UI usa para saber se ha dados validos
    has_snapshot: bool = False


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
