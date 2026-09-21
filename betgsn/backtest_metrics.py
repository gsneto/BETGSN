"""BETGSN :: backtest_metrics — metricas estatisticas do backtest.

O objetivo NAO e taxa de acerto. E responder se as probabilidades do
modelo sao confiaveis (calibracao) e se o EV previsto tem poder preditivo.

Metricas implementadas
----------------------
- agregados: sinais, acertos, erros, push, taxa observada, odd media,
  EV medio previsto, retorno medio realizado;
- calibracao por faixa de probabilidade (previsto vs observado) com
  intervalo de confianca de Wilson;
- Brier score e Log Loss (probabilisticos, penalizam confianca errada);
- analise por faixa de EV;
- performance temporal (dia/semana/mes);
- curva de capital e drawdown (via simulacao);
- segmentacao (competicao, mercado, odd, probabilidade, EV, confianca,
  mes, temporada);
- intervalos de confianca e marcacao de AMOSTRA INSUFICIENTE.

Metodo estatistico
------------------
Proporcoes usam o intervalo de Wilson (apropriado para binomial com N
pequeno, ao contrario do intervalo normal). Metricas continuas (Brier,
retorno medio) usam bootstrap com seed fixa, para ser reproduzivel.

Nada aqui afirma causalidade. As faixas de EV medem associacao historica
entre o EV previsto e o resultado observado, dentro deste dataset.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date
from statistics import fmean, mean, pstdev
from typing import Callable, Sequence

from .backtest_engine import BacktestRun, SettledSignal, SimulationResult
from .signals import EV_FORTE, EV_FRACA, EV_MEDIA

#: Abaixo disso o resultado e reportado como AMOSTRA INSUFICIENTE.
MIN_SAMPLE = 30

#: Nivel de confianca dos intervalos (95%).
Z_95 = 1.959963984540054

BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 424242


# --------------------------------------------------------------------------
# Estatistica basica
# --------------------------------------------------------------------------


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Intervalo de confianca de Wilson para uma proporcao binomial.

    Preferido ao intervalo normal porque se comporta bem com N pequeno e
    com proporcoes proximas de 0 ou 1 — exatamente o caso de faixas de
    probabilidade alta como 80%+.
    """
    if n <= 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, round(center - half, 15)), min(1.0, round(center + half, 15)))


def bootstrap_ci(
    values: Sequence[float],
    statistic: Callable[[Sequence[float]], float] = fmean,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """IC 95% por bootstrap percentil. Determinismo via seed fixa.

    Usa `random.choices` (C) e `statistics.fmean` (C) em vez de lacos
    Python: com milhares de sinais a diferenca e de segundos para
    milissegundos. O resultado nao muda — `fmean` e a media aritmetica
    em ponto flutuante.
    """
    if len(values) < 2:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    stats: list[float] = []
    for _ in range(resamples):
        stats.append(statistic(rng.choices(values, k=n)))
    stats.sort()
    lo = stats[int(0.025 * resamples)]
    hi = stats[min(resamples - 1, int(0.975 * resamples))]
    return (lo, hi)


def brier_score(pairs: Sequence[tuple[float, int]]) -> float:
    """Media de (p - y)^2. y em {0,1}. 0 = perfeito; 0.25 = sempre 50%."""
    if not pairs:
        return 0.0
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def log_loss(pairs: Sequence[tuple[float, int]], eps: float = 1e-12) -> float:
    """-media(y*ln p + (1-y)*ln(1-p)). Menor = melhor."""
    if not pairs:
        return 0.0
    total = 0.0
    for p, y in pairs:
        pc = min(1.0 - eps, max(eps, p))
        total += -(y * math.log(pc) + (1 - y) * math.log(1 - pc))
    return total / len(pairs)


# --------------------------------------------------------------------------
# Estruturas de resultado
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AggregateMetrics:
    n_signals: int
    n_settled: int
    n_unsettled: int
    n_wins: int
    n_losses: int
    n_pushes: int
    hit_rate: float
    hit_rate_ci: tuple[float, float]
    avg_odd: float
    avg_model_prob: float
    avg_market_prob: float
    avg_edge: float
    avg_ev: float
    avg_realized_return: float
    realized_return_ci: tuple[float, float]
    brier: float
    brier_ci: tuple[float, float]
    logloss: float
    ev_gap: float
    sample_sufficient: bool


@dataclass(frozen=True)
class CalibrationBin:
    label: str
    lower: float
    upper: float
    n: int
    avg_predicted: float
    observed_rate: float
    ci_low: float
    ci_high: float
    sufficient: bool


@dataclass(frozen=True)
class EvBucket:
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


@dataclass(frozen=True)
class TemporalBucket:
    label: str
    n: int
    hit_rate: float
    avg_ev: float
    realized_return: float
    profit: float
    bankroll_end: float | None = None


@dataclass(frozen=True)
class SegmentRow:
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


@dataclass(frozen=True)
class SignalRow:
    """Linha da tabela de detalhes — o registro congelado + resultado."""

    signal_id: str
    kickoff: str
    kickoff_utc: str
    home: str
    away: str
    competition: str
    season: str
    market: str
    outcome: str
    best_odd: float
    best_book: str
    model_prob: float
    market_prob: float
    edge: float
    ev: float
    confidence: str
    stake: float
    result: str
    outcome_result: str
    realized_return: float | None
    profit: float | None
    rationale: str
    n_prior_matches: int
    lambda_home: float
    lambda_away: float
    home_attack: float
    home_defense: float
    away_attack: float
    away_defense: float
    league_goals: float
    odds_source: str
    odds_as_of: str | None
    model_version: str
    config_hash: str


@dataclass
class BacktestMetrics:
    aggregate: AggregateMetrics
    calibration: list[CalibrationBin] = field(default_factory=list)
    ev_buckets: list[EvBucket] = field(default_factory=list)
    temporal: dict[str, list[TemporalBucket]] = field(default_factory=dict)
    segments: list[SegmentRow] = field(default_factory=list)
    signals: list[SignalRow] = field(default_factory=list)
    prediction_metrics: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# Helpers de recorte
# --------------------------------------------------------------------------


def _resolvable(signals: Sequence[SettledSignal]) -> list[SettledSignal]:
    """Somente sinais liquidados como win/loss (push nao entra em proporcao)."""
    return [s for s in signals if s.settled and s.outcome_result in ("win", "loss")]


def _pairs(signals: Sequence[SettledSignal]) -> list[tuple[float, int]]:
    return [(s.signal.model_prob, 1 if s.won else 0) for s in _resolvable(signals)]


def _outcome_label(s: SettledSignal) -> str:
    if not s.settled:
        return "nao liquidado"
    return {"win": "acerto", "loss": "erro", "push": "push"}[s.outcome_result or "loss"]


def _bucketize(
    value: float,
    edges: Sequence[tuple[str, float, float]],
) -> tuple[str, float, float]:
    for label, lo, hi in edges:
        if lo <= value < hi:
            return label, lo, hi
    label, lo, hi = edges[-1]
    return label, lo, hi


CALIBRATION_EDGES: tuple[tuple[str, float, float], ...] = tuple(
    (f"{i}–{i + 5}%", i / 100.0, (i + 5) / 100.0) for i in range(0, 100, 5)
)

EV_EDGES: tuple[tuple[str, float, float], ...] = (
    ("0–2%", 0.0, 0.02),
    ("2–5%", 0.02, 0.05),
    ("5–10%", 0.05, 0.10),
    ("10–15%", 0.10, 0.15),
    ("15%+", 0.15, float("inf")),
)

ODD_EDGES: tuple[tuple[str, float, float], ...] = (
    ("< 1.50", 0.0, 1.50),
    ("1.50–2.00", 1.50, 2.00),
    ("2.00–3.00", 2.00, 3.00),
    ("3.00–5.00", 3.00, 5.00),
    ("5.00+", 5.00, float("inf")),
)

PROB_EDGES: tuple[tuple[str, float, float], ...] = tuple(
    (f"{i}–{i + 10}%", i / 100.0, (i + 10) / 100.0) for i in range(0, 100, 10)
)


# --------------------------------------------------------------------------
# Calculo
# --------------------------------------------------------------------------


def aggregate_metrics(signals: Sequence[SettledSignal]) -> AggregateMetrics:
    resolvable = _resolvable(signals)
    n = len(resolvable)
    wins = sum(1 for s in resolvable if s.won)
    pushes = sum(1 for s in signals if s.pushed)
    unsettled = sum(1 for s in signals if not s.settled)
    pairs = _pairs(signals)
    rets = [s.realized_return for s in resolvable if s.realized_return is not None]
    hit = wins / n if n else 0.0
    avg_ev = mean([s.signal.ev for s in resolvable]) if resolvable else 0.0
    avg_ret = mean(rets) if rets else 0.0
    return AggregateMetrics(
        n_signals=len(signals),
        n_settled=n,
        n_unsettled=unsettled,
        n_wins=wins,
        n_losses=n - wins,
        n_pushes=pushes,
        hit_rate=hit,
        hit_rate_ci=wilson_interval(wins, n),
        avg_odd=mean([s.signal.best_odd for s in resolvable]) if resolvable else 0.0,
        avg_model_prob=mean([s.signal.model_prob for s in resolvable]) if resolvable else 0.0,
        avg_market_prob=mean([s.signal.market_prob for s in resolvable]) if resolvable else 0.0,
        avg_edge=mean([s.signal.edge for s in resolvable]) if resolvable else 0.0,
        avg_ev=avg_ev,
        avg_realized_return=avg_ret,
        realized_return_ci=bootstrap_ci(rets) if len(rets) > 1 else (0.0, 0.0),
        brier=brier_score(pairs),
        brier_ci=bootstrap_ci(
            [(p - y) ** 2 for p, y in pairs]
        ) if len(pairs) > 1 else (0.0, 0.0),
        logloss=log_loss(pairs),
        # gap entre o EV que o modelo promete e o retorno que ele entrega
        ev_gap=avg_ev - avg_ret,
        sample_sufficient=n >= MIN_SAMPLE,
    )


def calibration_bins(signals: Sequence[SettledSignal]) -> list[CalibrationBin]:
    """Faixas de probabilidade: previsto medio vs frequencia observada.

    Retorna apenas faixas com sinal — nao inventa linha vazia.
    """
    resolvable = _resolvable(signals)
    buckets: dict[str, list[SettledSignal]] = {}
    for s in resolvable:
        label, _, _ = _bucketize(s.signal.model_prob, CALIBRATION_EDGES)
        buckets.setdefault(label, []).append(s)

    out: list[CalibrationBin] = []
    for label, lo, hi in CALIBRATION_EDGES:
        items = buckets.get(label)
        if not items:
            continue
        wins = sum(1 for s in items if s.won)
        ci = wilson_interval(wins, len(items))
        out.append(CalibrationBin(
            label=label,
            lower=lo,
            upper=hi,
            n=len(items),
            avg_predicted=mean([s.signal.model_prob for s in items]),
            observed_rate=wins / len(items),
            ci_low=ci[0],
            ci_high=ci[1],
            sufficient=len(items) >= MIN_SAMPLE,
        ))
    return out


def ev_buckets(signals: Sequence[SettledSignal]) -> list[EvBucket]:
    """Faixas de EV previsto vs comportamento observado."""
    resolvable = _resolvable(signals)
    buckets: dict[str, list[SettledSignal]] = {}
    for s in resolvable:
        label, _, _ = _bucketize(s.signal.ev, EV_EDGES)
        buckets.setdefault(label, []).append(s)

    out: list[EvBucket] = []
    for label, lo, hi in EV_EDGES:
        items = buckets.get(label)
        if not items:
            continue
        wins = sum(1 for s in items if s.won)
        rets = [s.realized_return for s in items if s.realized_return is not None]
        ci = wilson_interval(wins, len(items))
        out.append(EvBucket(
            label=label,
            lower=lo,
            upper=(hi if hi != float("inf") else 1.0),
            n=len(items),
            avg_ev=mean([s.signal.ev for s in items]),
            observed_rate=wins / len(items),
            avg_realized_return=mean(rets) if rets else 0.0,
            ci_low=ci[0],
            ci_high=ci[1],
            sufficient=len(items) >= MIN_SAMPLE,
        ))
    return out


def _period_key(kickoff_utc: str, granularity: str) -> str:
    """Chave de janela temporal a partir de um instante UTC canonico."""
    d = date.fromisoformat(kickoff_utc[:10])
    if granularity == "day":
        return d.isoformat()
    if granularity == "week":
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    return f"{d.year}-{d.month:02d}"


def temporal_series(
    signals: Sequence[SettledSignal],
    granularity: str,
    simulation: SimulationResult | None = None,
) -> list[TemporalBucket]:
    """Desempenho ao longo do tempo. Revela concentracao em poucas janelas.

    Agrupa pelo instante UTC canonico do kickoff — nao pela string crua —
    para que partidas com fusos diferentes caiam na janela certa.
    """
    resolvable = _resolvable(signals)
    groups: dict[str, list[SettledSignal]] = {}
    for s in resolvable:
        groups.setdefault(
            _period_key(s.signal.kickoff_utc, granularity), []
        ).append(s)

    equity_by_day = (
        {p.day: p.bankroll for p in simulation.equity} if simulation else {}
    )

    out: list[TemporalBucket] = []
    for key in sorted(groups):
        items = groups[key]
        wins = sum(1 for s in items if s.won)
        rets = [s.realized_return for s in items if s.realized_return is not None]
        profit = sum(
            s.profit for s in items if s.profit is not None
        )
        bankroll_end = None
        if equity_by_day:
            # ultimo dia coberto por esta janela
            days = sorted(d for d in equity_by_day if _same_window(d, key, granularity))
            if days:
                bankroll_end = equity_by_day[days[-1]]
        out.append(TemporalBucket(
            label=key,
            n=len(items),
            hit_rate=wins / len(items) if items else 0.0,
            avg_ev=mean([s.signal.ev for s in items]) if items else 0.0,
            realized_return=mean(rets) if rets else 0.0,
            profit=round(profit, 2),
            bankroll_end=bankroll_end,
        ))
    return out


def _same_window(day: str, key: str, granularity: str) -> bool:
    return _period_key(f"{day}T00:00:00Z", granularity) == key


def _segment_rows(
    dimension: str,
    signals: Sequence[SettledSignal],
    key_fn: Callable[[SettledSignal], str],
    min_n: int = 1,
) -> list[SegmentRow]:
    groups: dict[str, list[SettledSignal]] = {}
    for s in signals:
        groups.setdefault(key_fn(s), []).append(s)

    rows: list[SegmentRow] = []
    for name in sorted(groups):
        resolvable = _resolvable(groups[name])
        if len(resolvable) < min_n:
            continue
        wins = sum(1 for s in resolvable if s.won)
        ci = wilson_interval(wins, len(resolvable))
        rows.append(SegmentRow(
            dimension=dimension,
            segment=name,
            n=len(resolvable),
            observed_rate=wins / len(resolvable),
            avg_predicted=mean([s.signal.model_prob for s in resolvable]),
            avg_ev=mean([s.signal.ev for s in resolvable]),
            brier=brier_score(_pairs(groups[name])),
            ci_low=ci[0],
            ci_high=ci[1],
            sufficient=len(resolvable) >= MIN_SAMPLE,
        ))
    rows.sort(key=lambda r: r.n, reverse=True)
    return rows


def segment_rows(signals: Sequence[SettledSignal]) -> list[SegmentRow]:
    """Segmentacao multidimensional para descobrir onde o modelo funciona."""
    rows: list[SegmentRow] = []
    rows += _segment_rows("competicao", signals, lambda s: s.signal.competition or "—")
    rows += _segment_rows("temporada", signals, lambda s: s.signal.season or "—")
    rows += _segment_rows("mercado", signals, lambda s: s.signal.market)
    rows += _segment_rows("confianca", signals, lambda s: s.signal.confidence)
    rows += _segment_rows(
        "faixa_odd", signals,
        lambda s: _bucketize(s.signal.best_odd, ODD_EDGES)[0],
    )
    rows += _segment_rows(
        "faixa_probabilidade", signals,
        lambda s: _bucketize(s.signal.model_prob, PROB_EDGES)[0],
    )
    rows += _segment_rows(
        "faixa_ev", signals,
        lambda s: _bucketize(s.signal.ev, EV_EDGES)[0],
    )
    rows += _segment_rows(
        "mes", signals, lambda s: s.signal.kickoff_utc[:7],
    )
    return rows


def signal_rows(
    signals: Sequence[SettledSignal],
    limit: int | None = None,
) -> list[SignalRow]:
    """Registros detalhados para auditoria na UI."""
    out: list[SignalRow] = []
    for s in signals[:limit] if limit else signals:
        f = s.signal
        out.append(SignalRow(
            signal_id=f.signal_id,
            kickoff=f.kickoff,
            kickoff_utc=f.kickoff_utc,
            home=f.home,
            away=f.away,
            competition=f.competition,
            season=f.season,
            market=f.market,
            outcome=f.outcome,
            best_odd=f.best_odd,
            best_book=f.best_book,
            model_prob=f.model_prob,
            market_prob=f.market_prob,
            edge=f.edge,
            ev=f.ev,
            confidence=f.confidence,
            stake=f.stake,
            result=(
                f"{s.result_home_goals}-{s.result_away_goals}"
                if s.settled else "—"
            ),
            outcome_result=_outcome_label(s),
            realized_return=s.realized_return,
            profit=s.profit,
            rationale=f.rationale,
            n_prior_matches=f.n_prior_matches,
            lambda_home=f.lambda_home,
            lambda_away=f.lambda_away,
            home_attack=f.home_attack,
            home_defense=f.home_defense,
            away_attack=f.away_attack,
            away_defense=f.away_defense,
            league_goals=f.league_goals,
            odds_source=f.odds_source,
            odds_as_of=f.odds_as_of,
            model_version=f.model_version,
            config_hash=f.config_hash,
        ))
    return out


def compute_metrics(
    run: BacktestRun,
    simulation: SimulationResult | None = None,
    signal_limit: int | None = None,
) -> BacktestMetrics:
    """Calcula todas as metricas de uma execucao."""
    signals = run.signals
    from .evaluation import score_predictions
    all_predictions = [r for r in run.predictions if "Resultado Final (1X2)" in r["prediction"]["markets"]]
    prediction_metrics = score_predictions(
        [[r["prediction"]["markets"]["Resultado Final (1X2)"][o] for o in ("1", "X", "2")] for r in all_predictions],
        [0 if r["home_goals"] > r["away_goals"] else 1 if r["home_goals"] == r["away_goals"] else 2 for r in all_predictions]
    ) if all_predictions else {}
    return BacktestMetrics(
        prediction_metrics=prediction_metrics,
        aggregate=aggregate_metrics(signals),
        calibration=calibration_bins(signals),
        ev_buckets=ev_buckets(signals),
        temporal={
            "day": temporal_series(signals, "day", simulation),
            "week": temporal_series(signals, "week", simulation),
            "month": temporal_series(signals, "month", simulation),
        },
        segments=segment_rows(signals),
        signals=signal_rows(signals, signal_limit),
    )


def ev_monotonicity(buckets: Sequence[EvBucket]) -> bool:
    """True se o retorno realizado cresce com a faixa de EV.

    E uma ASSOCIACAO dentro deste dataset, nao causalidade: um numero
    maior de sinais na faixa tambem aumenta a estabilidade da estimativa.
    """
    usable = [b for b in buckets if b.sufficient]
    if len(usable) < 2:
        return False
    rets = [b.avg_realized_return for b in usable]
    return all(b > a for a, b in zip(rets, rets[1:]))


def confidence_spread(signals: Sequence[SettledSignal]) -> float:
    """Dispersao do retorno realizado — medida de variancia do resultado."""
    rets = [
        s.realized_return for s in _resolvable(signals)
        if s.realized_return is not None
    ]
    return pstdev(rets) if len(rets) > 1 else 0.0


#: Limiares de EV do Scanner, reexportados para a UI rotular as faixas.
SCANNER_EV_THRESHOLDS = {
    "forte": EV_FORTE,
    "media": EV_MEDIA,
    "fraca": EV_FRACA,
}
