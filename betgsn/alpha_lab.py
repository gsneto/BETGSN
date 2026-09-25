"""BETGSN :: alpha_lab — laboratório de alpha do terminal de mercado.

Objetivo: descobrir HONESTAMENTE se um sinal antecipa movimento de preço,
sem fabricar dado e sem escolher threshold olhando o teste.

Princípios
----------
1. Point-in-time: o estado do sinal em T usa SOMENTE quotes <= T; o
   "futuro" é medido com quotes > T (movimento posterior) e o fechamento
   com a última observação antes do kickoff.
2. Nenhuma lógica de sinal paralela: os thresholds vêm de `SignalRules`
   (produção) e as métricas de `signal_calibration` (mesmas fórmulas).
3. Amostra insuficiente -> INSUFFICIENT_DATA; efeito dentro do ruído ->
   NO_EVIDENCE; só uma janela -> FRAGILE; sobrevive -> VALIDATED.
4. Nada de ROI como único critério: o veredito usa n, intervalo de
   confiança, consistência por dimensão e (quando houver) CLV.

O que é mensurável hoje (kickoffs ainda no futuro): movimento POSTERIOR e
convergência. CLV/closing ficam BLOCKED até existir fechamento real.
"""
from __future__ import annotations

import statistics
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from .realtime.signal_calibration import (
    best_gap_ratios,
    dispersion_ratios,
    outlier_deviations,
)
from .realtime.signals import SignalRules
from .timeutil import parse_kickoff, utc_key

#: Horizontes de movimento posterior (segundos) para a observação forward.
FORWARD_HORIZONS = (300, 900, 1800, 3600)

#: Amostra mínima para um alpha sair de INSUFFICIENT_DATA.
MIN_ALPHA_SAMPLE = 100

#: Frequência mínima de consistência para não ser FRAGILE.
MIN_CONSISTENCY = 0.55

STATUS_RESEARCH = "RESEARCH"
STATUS_INSUFFICIENT = "INSUFFICIENT_DATA"
STATUS_EXPERIMENTAL = "EXPERIMENTAL"
STATUS_VALIDATED = "VALIDATED"
STATUS_FRAGILE = "FRAGILE"
STATUS_NO_EVIDENCE = "NO_EVIDENCE"
STATUS_REJECTED = "REJECTED"
STATUS_BLOCKED = "BLOCKED"


# --------------------------------------------------------------------------
# Registro de hipóteses
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AlphaSpec:
    """Uma hipótese de alpha, com requisitos e status DECLARADO."""

    alpha_id: str
    name: str
    version: str
    hypothesis: str
    signal_types: tuple[str, ...]
    markets: tuple[str, ...]
    required_books: int
    freshness_requirement: str
    data_requirements: tuple[str, ...]
    status: str = STATUS_RESEARCH

    def to_dict(self) -> dict:
        return {
            "alpha_id": self.alpha_id,
            "name": self.name,
            "version": self.version,
            "hypothesis": self.hypothesis,
            "signal_types": list(self.signal_types),
            "markets": list(self.markets),
            "required_books": self.required_books,
            "freshness_requirement": self.freshness_requirement,
            "data_requirements": list(self.data_requirements),
            "status": self.status,
        }


def default_alpha_registry() -> dict[str, AlphaSpec]:
    """As hipóteses do laboratório, com suporte de dados DECLARADO.

    `status` aqui é o suporte de DADOS (RESEARCH/EXPERIMENTAL/BLOCKED), não
    um veredito de performance — o veredito sai da avaliação.
    """
    return {
        spec.alpha_id: spec
        for spec in (
            AlphaSpec(
                alpha_id="bookmaker_outlier",
                name="Bookmaker Outlier",
                version="1.0",
                hypothesis=(
                    "Uma casa cotando fora da mediana das demais antecipa "
                    "movimento do mercado (lead) ou apenas reverte (ruído)."
                ),
                signal_types=("BOOKMAKER_OUTLIER",),
                markets=("Resultado Final (1X2)", "Total de Gols", "Ambas Marcam"),
                required_books=3,
                freshness_requirement="quote fresca na decisão",
                data_requirements=("odds multi-casa", "timestamp por quote"),
            ),
            AlphaSpec(
                alpha_id="dispersion_spike",
                name="Dispersion Spike",
                version="1.0",
                hypothesis=(
                    "Dispersão acima do normal entre casas converge depois "
                    "(mercado se reprecifica para um consenso)."
                ),
                signal_types=("DISPERSION_SPIKE",),
                markets=("Resultado Final (1X2)", "Total de Gols", "Ambas Marcam"),
                required_books=3,
                freshness_requirement="quote fresca na decisão",
                data_requirements=("odds multi-casa", "timestamp por quote"),
            ),
            AlphaSpec(
                alpha_id="best_price_gap",
                name="Best Price Gap",
                version="1.0",
                hypothesis=(
                    "O melhor preço acima da mediana persiste (line shopping "
                    "tem valor real) ou desaparece (preço fantasma)."
                ),
                signal_types=("BEST_PRICE_GAP",),
                markets=("Resultado Final (1X2)", "Total de Gols", "Ambas Marcam"),
                required_books=3,
                freshness_requirement="quote fresca na decisão",
                data_requirements=("odds multi-casa", "timestamp por quote"),
            ),
            AlphaSpec(
                alpha_id="consensus_move",
                name="Consensus Movement",
                version="1.0",
                hypothesis=(
                    "Quando N casas movem na mesma direção, o movimento "
                    "continua (momentum) ou reverte."
                ),
                signal_types=("CONSENSUS_MOVE",),
                markets=("Resultado Final (1X2)", "Total de Gols"),
                required_books=3,
                freshness_requirement="movimento recente (janela)",
                data_requirements=("historico de precos", "timestamps reais"),
            ),
            AlphaSpec(
                alpha_id="stale_price",
                name="Stale Price",
                version="1.0",
                hypothesis=(
                    "Uma casa com preço parado enquanto o resto atualiza "
                    "sinaliza preço desatualizado explorável."
                ),
                signal_types=("STALE_PRICE",),
                markets=("Resultado Final (1X2)", "Total de Gols"),
                required_books=2,
                freshness_requirement="exige ao menos uma casa fresca",
                data_requirements=("odds multi-casa", "timestamp por quote"),
            ),
            AlphaSpec(
                alpha_id="bookmaker_lead_lag",
                name="Bookmaker Lead/Lag",
                version="1.0",
                hypothesis=(
                    "Uma casa que move primeiro e seguida por outras "
                    "identifica liderança de preço (sharp anchor)."
                ),
                signal_types=("BOOKMAKER_LEAD", "BOOKMAKER_LAG"),
                markets=("Resultado Final (1X2)", "Total de Gols"),
                required_books=2,
                freshness_requirement="movimento recente (janela)",
                data_requirements=("historico de precos", "timestamps reais"),
            ),
            AlphaSpec(
                alpha_id="rapid_convergence",
                name="Rapid Convergence",
                version="1.0",
                hypothesis=(
                    "Casas que convergem rápido para o mesmo preço indicam "
                    "reprecificação concluída (sem follow-through)."
                ),
                signal_types=("RAPID_CONVERGENCE",),
                markets=("Resultado Final (1X2)", "Total de Gols"),
                required_books=2,
                freshness_requirement="movimento recente (janela)",
                data_requirements=("historico de precos", "timestamps reais"),
            ),
            AlphaSpec(
                alpha_id="price_reversal",
                name="Price Reversal",
                version="1.0",
                hypothesis=(
                    "Uma casa que inverte a direção do próprio movimento "
                    "sinaliza fim de fluxo (reversão)."
                ),
                signal_types=("PRICE_REVERSAL",),
                markets=("Resultado Final (1X2)", "Total de Gols"),
                required_books=1,
                freshness_requirement="movimento recente (janela)",
                data_requirements=("historico de precos", "timestamps reais"),
            ),
            AlphaSpec(
                alpha_id="market_residual",
                name="Model minus Market (Residual)",
                version="1.0",
                hypothesis=(
                    "A diferença entre probabilidade de modelo e fair do "
                    "mercado contém informação OOS."
                ),
                signal_types=(),
                markets=("Resultado Final (1X2)", "Total de Gols"),
                required_books=3,
                freshness_requirement="modelo + fair PIT",
                data_requirements=("modelo calibrado por janela", "fair PIT", "resultados"),
                status=STATUS_BLOCKED,
            ),
            AlphaSpec(
                alpha_id="favorite_longshot",
                name="Favorite-Longshot Bias",
                version="1.0",
                hypothesis=(
                    "Favoritos são subprecificados e longshots sobreprecificados "
                    "de forma sistemática."
                ),
                signal_types=(),
                markets=("Resultado Final (1X2)",),
                required_books=3,
                freshness_requirement="closing real",
                data_requirements=("resultados", "closing", "amostra ampla"),
                status=STATUS_BLOCKED,
            ),
            AlphaSpec(
                alpha_id="movement_vs_kickoff",
                name="Odds Movement vs Kickoff",
                version="1.0",
                hypothesis=(
                    "O movimento relativo ao tempo até o kickoff antecipa o "
                    "fechamento (drift pré-jogo)."
                ),
                signal_types=(),
                markets=("Resultado Final (1X2)", "Total de Gols"),
                required_books=3,
                freshness_requirement="closing real",
                data_requirements=("serie temporal densa", "closing real"),
                status=STATUS_BLOCKED,
            ),
            AlphaSpec(
                alpha_id="lineup_shock",
                name="Lineup Shock",
                version="0.1",
                hypothesis="Escalações inesperadas reprecificam o mercado.",
                signal_types=(),
                markets=(),
                required_books=3,
                freshness_requirement="escalação PIT",
                data_requirements=("fonte de escalação PIT",),
                status=STATUS_BLOCKED,
            ),
            AlphaSpec(
                alpha_id="live_goal_reaction",
                name="Live Goal Reaction",
                version="0.1",
                hypothesis="Reação do mercado a gols ao vivo.",
                signal_types=(),
                markets=(),
                required_books=3,
                freshness_requirement="in-play",
                data_requirements=("odds in-play",),
                status=STATUS_BLOCKED,
            ),
        )
    }


# --------------------------------------------------------------------------
# Observação prospectiva de um sinal
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ForwardObservation:
    """Um sinal observado + como o preço se moveu DEPOIS (PIT)."""

    signal_type: str
    alpha_id: str
    event_key: str
    market: str
    selection: str
    bookmaker: str
    signal_timestamp: str
    observed_at: str
    decision_price: float
    median_price: float
    best_price: float
    book_count: int
    dispersion_ratio: float
    deviation: float          # do book de referência vs mediana das demais
    direction: str            # UP | DOWN | FLAT (direção implícita do sinal)
    seconds_to_kickoff: float
    # movimento posterior da MEDIANA (mercado) por horizonte
    market_move: dict[int, float | None]
    # movimento posterior do PREÇO DE REFERÊNCIA (a casa sinalizada)
    reference_move: dict[int, float | None]
    closing_price: float | None
    closing_move: float | None
    #: True quando o mercado se moveu NA DIREÇÃO que o sinal implica.
    market_followed: bool | None
    #: True quando a dispersão/gap caiu depois (convergência).
    converged: bool | None
    provider: str = ""
    league: str = ""

    def to_dict(self) -> dict:
        return {
            "signal_type": self.signal_type,
            "alpha_id": self.alpha_id,
            "event_key": self.event_key,
            "market": self.market,
            "selection": self.selection,
            "bookmaker": self.bookmaker,
            "signal_timestamp": self.signal_timestamp,
            "observed_at": self.observed_at,
            "decision_price": round(self.decision_price, 6),
            "median_price": round(self.median_price, 6),
            "best_price": round(self.best_price, 6),
            "book_count": self.book_count,
            "dispersion_ratio": round(self.dispersion_ratio, 6),
            "deviation": round(self.deviation, 6),
            "direction": self.direction,
            "seconds_to_kickoff": round(self.seconds_to_kickoff, 1),
            "market_move": {
                str(k): (round(v, 6) if v is not None else None)
                for k, v in self.market_move.items()
            },
            "reference_move": {
                str(k): (round(v, 6) if v is not None else None)
                for k, v in self.reference_move.items()
            },
            "closing_price": (
                round(self.closing_price, 6) if self.closing_price is not None else None
            ),
            "closing_move": (
                round(self.closing_move, 6) if self.closing_move is not None else None
            ),
            "market_followed": self.market_followed,
            "converged": self.converged,
            "provider": self.provider,
            "league": self.league,
        }


def _pct_move(old: float, new: float) -> float | None:
    if old is None or new is None or old <= 0:
        return None
    return (new - old) / old


def _window(
    series: Sequence[tuple[str, str, float]],
    stamps: Sequence[str],
    after: str,
    cutoff: str,
) -> Sequence[tuple[str, str, float]]:
    """Fatia (after, cutoff] por bisect.

    `stamps` são as chaves canônicas UTC (já ordenadas lexicograficamente
    == cronologicamente). `after`/`cutoff` são normalizados aqui.
    """
    import bisect

    lo = bisect.bisect_right(stamps, utc_key(after))
    hi = bisect.bisect_right(stamps, utc_key(cutoff))
    return series[lo:hi]


def _price_at(
    series: Sequence[tuple[str, str, float]],
    book: str,
    cutoff: str,
    *,
    after: str,
    stamps: Sequence[str] | None = None,
) -> float | None:
    """Último preço da casa com `after < timestamp <= cutoff`."""
    if stamps is None:
        stamps = [utc_key(r[0]) for r in series]
    found: tuple[str, float] | None = None
    for stamp, bk, price in _window(series, stamps, after, cutoff):
        if bk != book:
            continue
        if found is None or utc_key(stamp) >= utc_key(found[0]):
            found = (stamp, price)
    return found[1] if found else None


def _median_at(
    series: Sequence[tuple[str, str, float]],
    cutoff: str,
    *,
    after: str,
    stamps: Sequence[str] | None = None,
) -> float | None:
    """Mediana entre casas no instante mais recente em (after, cutoff]."""
    if stamps is None:
        stamps = [utc_key(r[0]) for r in series]
    latest: dict[str, tuple[str, float]] = {}
    for stamp, bk, price in _window(series, stamps, after, cutoff):
        cur = latest.get(bk)
        if cur is None or utc_key(stamp) >= utc_key(cur[0]):
            latest[bk] = (stamp, price)
    if not latest:
        return None
    return statistics.median(p for _s, p in latest.values())


def _add_seconds(stamp: str, seconds: int) -> str:
    from datetime import timedelta

    return (parse_kickoff(stamp) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def observe_signal(
    *,
    signal_type: str,
    alpha_id: str,
    event_key: str,
    market: str,
    selection: str,
    bookmaker: str,
    signal_timestamp: str,
    kickoff: str,
    series: Sequence[tuple[str, str, float]],
    decision_price: float,
    median_price: float,
    best_price: float,
    book_count: int,
    dispersion_ratio: float,
    deviation: float,
    direction: str,
    closing_price: float | None,
    provider: str = "",
    league: str = "",
) -> ForwardObservation:
    """Monta a observação forward de um sinal, só com quotes posteriores."""
    stamps = [utc_key(r[0]) for r in series]
    market_move: dict[int, float | None] = {}
    reference_move: dict[int, float | None] = {}
    for horizon in FORWARD_HORIZONS:
        cutoff = _add_seconds(signal_timestamp, horizon)
        future_median = _median_at(series, cutoff, after=signal_timestamp,
                                   stamps=stamps)
        future_ref = _price_at(series, bookmaker, cutoff, after=signal_timestamp,
                               stamps=stamps)
        market_move[horizon] = _pct_move(median_price, future_median)
        reference_move[horizon] = _pct_move(decision_price, future_ref)

    # "followed": o mercado se moveu na direção implícita (UP se a casa
    # cotava acima, DOWN se abaixo) em algum horizonte.
    market_followed: bool | None = None
    if direction in ("UP", "DOWN"):
        sign = 1.0 if direction == "UP" else -1.0
        moves = [m for m in market_move.values() if m is not None]
        if moves:
            # usa o primeiro horizonte com dado (5m)
            first = market_move.get(FORWARD_HORIZONS[0])
            if first is None:
                first = moves[0]
            market_followed = (first * sign) > 0

    # "converged": a dispersão caiu no primeiro horizonte com dado.
    converged: bool | None = None
    horizon_15 = _add_seconds(signal_timestamp, 900)
    future_median_5 = _median_at(series, horizon_15, after=signal_timestamp,
                                 stamps=stamps)
    if future_median_5 is not None:
        future_prices = latest_prices_at_cutoff(
            series, horizon_15, after=signal_timestamp, stamps=stamps)
        if len(future_prices) >= 2:
            future_disp = (
                statistics.pstdev(list(future_prices.values()))
                / statistics.median(list(future_prices.values()))
            )
            converged = future_disp < dispersion_ratio

    closing_move = _pct_move(median_price, closing_price)
    return ForwardObservation(
        signal_type=signal_type,
        alpha_id=alpha_id,
        event_key=event_key,
        market=market,
        selection=selection,
        bookmaker=bookmaker,
        signal_timestamp=signal_timestamp,
        observed_at=signal_timestamp,
        decision_price=decision_price,
        median_price=median_price,
        best_price=best_price,
        book_count=book_count,
        dispersion_ratio=dispersion_ratio,
        deviation=deviation,
        direction=direction,
        seconds_to_kickoff=(
            parse_kickoff(kickoff) - parse_kickoff(signal_timestamp)
        ).total_seconds(),
        market_move=market_move,
        reference_move=reference_move,
        closing_price=closing_price,
        closing_move=closing_move,
        market_followed=market_followed,
        converged=converged,
        provider=provider,
        league=league,
    )


def latest_prices_at_cutoff(
    series: Sequence[tuple[str, str, float]], cutoff: str, *, after: str = "",
    stamps: Sequence[str] | None = None,
) -> dict[str, float]:
    if stamps is None:
        stamps = [utc_key(r[0]) for r in series]
    lo = bisect_right(stamps, utc_key(after)) if after else 0
    hi = bisect_right(stamps, utc_key(cutoff))
    latest: dict[str, tuple[str, float]] = {}
    for stamp, bk, price in series[lo:hi]:
        cur = latest.get(bk)
        if cur is None or utc_key(stamp) >= utc_key(cur[0]):
            latest[bk] = (stamp, price)
    return {bk: p for bk, (_s, p) in latest.items()}


# --------------------------------------------------------------------------
# Avaliação estatística
# --------------------------------------------------------------------------


def _bootstrap_ci(values: Sequence[float], *, seed: int = 7,
                  iterations: int = 2000, alpha: float = 0.05) -> tuple[float, float]:
    """IC bootstrap da média. Sem scipy: RNG determinístico."""
    import random

    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(clean)
    means = []
    for _ in range(iterations):
        sample = [clean[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int((alpha / 2) * iterations)]
    hi = means[int((1 - alpha / 2) * iterations) - 1]
    return (lo, hi)


@dataclass
class AlphaEvaluation:
    alpha_id: str
    signal_type: str
    n: int
    #: "directional" (segue/não segue) ou "convergence" (converge/não converge)
    metric_kind: str
    primary_metric: float | None
    primary_label: str
    ci_low: float | None
    ci_high: float | None
    mean_market_move_5m: float | None
    mean_market_move_60m: float | None
    mean_closing_move: float | None
    consistency: dict[str, float]
    status: str
    limitations: list[str] = field(default_factory=list)
    by_market: dict[str, dict] = field(default_factory=dict)
    by_league: dict[str, dict] = field(default_factory=dict)
    by_book: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "alpha_id": self.alpha_id,
            "signal_type": self.signal_type,
            "n": self.n,
            "metric_kind": self.metric_kind,
            "primary_metric": self.primary_metric,
            "primary_label": self.primary_label,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "mean_market_move_5m": self.mean_market_move_5m,
            "mean_market_move_60m": self.mean_market_move_60m,
            "mean_closing_move": self.mean_closing_move,
            "consistency": self.consistency,
            "status": self.status,
            "limitations": list(self.limitations),
            "by_market": self.by_market,
            "by_league": self.by_league,
            "by_book": self.by_book,
        }


def _dimension_stats(obs: Sequence[ForwardObservation], key) -> dict[str, dict]:
    groups: dict[str, list[ForwardObservation]] = {}
    for o in obs:
        groups.setdefault(str(key(o)), []).append(o)
    out: dict[str, dict] = {}
    for name, rows in sorted(groups.items()):
        follows = [o.market_followed for o in rows if o.market_followed is not None]
        convs = [o.converged for o in rows if o.converged is not None]
        moves = [
            o.market_move.get(FORWARD_HORIZONS[0]) for o in rows
            if o.market_move.get(FORWARD_HORIZONS[0]) is not None
        ]
        signed = []
        for o in rows:
            first = o.market_move.get(FORWARD_HORIZONS[0])
            if first is None or o.direction not in ("UP", "DOWN"):
                continue
            signed.append(first if o.direction == "UP" else -first)
        out[name] = {
            "n": len(rows),
            "follow_rate": (
                sum(1 for f in follows if f) / len(follows) if follows else None
            ),
            "converged_rate": (
                sum(1 for c in convs if c) / len(convs) if convs else None
            ),
            "mean_move_5m": (sum(moves) / len(moves)) if moves else None,
            "signed_move_5m": (sum(signed) / len(signed)) if signed else None,
        }
    return out


def _bootstrap_ci_binary(values: Sequence[bool], *, seed: int = 7,
                         iterations: int = 2000, alpha: float = 0.05
                         ) -> tuple[float, float]:
    """IC bootstrap da proporção (Wald-ish via reamostragem)."""
    import random

    clean = [bool(v) for v in values]
    if len(clean) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(clean)
    rates = []
    for _ in range(iterations):
        hits = sum(clean[rng.randrange(n)] for _ in range(n))
        rates.append(hits / n)
    rates.sort()
    return (rates[int((alpha / 2) * iterations)],
            rates[int((1 - alpha / 2) * iterations) - 1])


def _consistency_side(
    dim: dict[str, dict], key: str, *, reference: float, above: bool
) -> float:
    """Fração de subgrupos (n>=10) no MESMO lado da referência que o efeito
    geral. Mede ESTABILIDADE do efeito, não a sua direção.
    """
    values = [d.get(key) for d in dim.values()
              if d.get(key) is not None and d["n"] >= 10]
    if not values:
        return float("nan")
    same = sum(1 for v in values if (v > reference) == above)
    return same / len(values)


def evaluate_alpha(
    observations: Sequence[ForwardObservation],
    *,
    min_sample: int = MIN_ALPHA_SAMPLE,
) -> AlphaEvaluation:
    """Avalia um alpha com honestidade estatística.

    Métrica primária por família:
      - direcional (OUTLIER): move do mercado ASSINADO pela direção implícita
        (o mercado seguiu a casa ou não). IC bootstrap da média.
      - convergência (DISPERSION_SPIKE, BEST_PRICE_GAP): fração de vezes em
        que a dispersão caiu no horizonte. IC bootstrap da proporção.

    Veredito:
      - n < min_sample -> INSUFFICIENT_DATA
      - IC cruza o nulo (0 para média; 0.5 para proporção) -> NO_EVIDENCE
      - consistência por dimensão baixa -> FRAGILE
      - senão -> VALIDATED (candidato a mais validação, nunca produção)
    """
    n = len(observations)
    limitations: list[str] = []
    if n == 0:
        return AlphaEvaluation(
            alpha_id="", signal_type="", n=0, metric_kind="none",
            primary_metric=None, primary_label="", ci_low=None, ci_high=None,
            mean_market_move_5m=None, mean_market_move_60m=None,
            mean_closing_move=None, consistency={}, status=STATUS_INSUFFICIENT,
            limitations=["sem observações"],
        )

    alpha_id = observations[0].alpha_id
    signal_type = observations[0].signal_type
    directional = observations[0].direction in ("UP", "DOWN")
    moves_5m = [
        o.market_move.get(FORWARD_HORIZONS[0]) for o in observations
        if o.market_move.get(FORWARD_HORIZONS[0]) is not None
    ]
    moves_60m = [
        o.market_move.get(3600) for o in observations
        if o.market_move.get(3600) is not None
    ]
    closing_moves = [o.closing_move for o in observations if o.closing_move is not None]

    if n < min_sample:
        return AlphaEvaluation(
            alpha_id=alpha_id, signal_type=signal_type, n=n,
            metric_kind="directional" if directional else "convergence",
            primary_metric=None, primary_label="", ci_low=None, ci_high=None,
            mean_market_move_5m=None, mean_market_move_60m=None,
            mean_closing_move=None, consistency={}, status=STATUS_INSUFFICIENT,
            limitations=[f"amostra {n} < {min_sample}: INSUFFICIENT_DATA"],
        )

    by_market = _dimension_stats(observations, lambda o: o.market)
    by_league = _dimension_stats(observations, lambda o: o.league or "?")
    by_book = _dimension_stats(observations, lambda o: o.bookmaker)

    if directional:
        metric_kind = "directional"
        primary_label = "signed_market_move_5m"
        signed = []
        for o in observations:
            first = o.market_move.get(FORWARD_HORIZONS[0])
            if first is None:
                continue
            signed.append(first if o.direction == "UP" else -first)
        if not signed:
            return AlphaEvaluation(
                alpha_id=alpha_id, signal_type=signal_type, n=n,
                metric_kind=metric_kind, primary_metric=None,
                primary_label=primary_label, ci_low=None, ci_high=None,
                mean_market_move_5m=None, mean_market_move_60m=None,
                mean_closing_move=None, consistency={},
                status=STATUS_INSUFFICIENT,
                limitations=["sem movimento posterior mensurável"],
            )
        primary = sum(signed) / len(signed)
        ci_low, ci_high = _bootstrap_ci(signed)
        above = primary > 0
        consistency = {
            "market": _consistency_side(by_market, "signed_move_5m",
                                        reference=0.0, above=above),
            "league": _consistency_side(by_league, "signed_move_5m",
                                        reference=0.0, above=above),
            "book": _consistency_side(by_book, "signed_move_5m",
                                      reference=0.0, above=above),
        }
        crosses_null = ci_low <= 0 <= ci_high
    else:
        metric_kind = "convergence"
        primary_label = "converged_rate"
        conv = [o.converged for o in observations if o.converged is not None]
        if not conv:
            return AlphaEvaluation(
                alpha_id=alpha_id, signal_type=signal_type, n=n,
                metric_kind=metric_kind, primary_metric=None,
                primary_label=primary_label, ci_low=None, ci_high=None,
                mean_market_move_5m=None, mean_market_move_60m=None,
                mean_closing_move=None, consistency={},
                status=STATUS_INSUFFICIENT,
                limitations=["sem horizonte posterior para medir convergência"],
            )
        primary = sum(1 for c in conv if c) / len(conv)
        ci_low, ci_high = _bootstrap_ci_binary(conv)
        above = primary > 0.5
        consistency = {
            "market": _consistency_side(by_market, "converged_rate",
                                        reference=0.5, above=above),
            "league": _consistency_side(by_league, "converged_rate",
                                        reference=0.5, above=above),
            "book": _consistency_side(by_book, "converged_rate",
                                      reference=0.5, above=above),
        }
        crosses_null = ci_low <= 0.5 <= ci_high

    if crosses_null:
        status = STATUS_NO_EVIDENCE
        limitations.append(
            f"efeito dentro do IC95% [{ci_low:.4f}, {ci_high:.4f}] — "
            "não distinguível do nulo"
        )
    elif any(v == v and v < MIN_CONSISTENCY for v in consistency.values()):
        status = STATUS_FRAGILE
        limitations.append(
            f"efeito não consistente entre dimensões (< {MIN_CONSISTENCY:.0%})"
        )
    else:
        status = STATUS_VALIDATED
        limitations.append(
            "candidato a mais validação; NÃO é produção e não implica ROI"
        )

    if not closing_moves:
        limitations.append("CLV/closing indisponível (kickoffs futuros)")

    return AlphaEvaluation(
        alpha_id=alpha_id, signal_type=signal_type, n=n,
        metric_kind=metric_kind, primary_metric=primary,
        primary_label=primary_label,
        ci_low=(ci_low if ci_low == ci_low else None),
        ci_high=(ci_high if ci_high == ci_high else None),
        mean_market_move_5m=(sum(moves_5m) / len(moves_5m)) if moves_5m else None,
        mean_market_move_60m=(sum(moves_60m) / len(moves_60m)) if moves_60m else None,
        mean_closing_move=(
            sum(closing_moves) / len(closing_moves) if closing_moves else None
        ),
        consistency=consistency, status=status, limitations=limitations,
        by_market=by_market, by_league=by_league, by_book=by_book,
    )


def evaluate_all(
    observations: Sequence[ForwardObservation],
    *,
    min_sample: int = MIN_ALPHA_SAMPLE,
) -> dict[str, AlphaEvaluation]:
    by_signal: dict[str, list[ForwardObservation]] = {}
    for o in observations:
        by_signal.setdefault(o.signal_type, []).append(o)
    return {
        signal_type: evaluate_alpha(rows, min_sample=min_sample)
        for signal_type, rows in sorted(by_signal.items())
    }


__all__ = [
    "AlphaSpec",
    "default_alpha_registry",
    "ForwardObservation",
    "observe_signal",
    "evaluate_alpha",
    "evaluate_all",
    "AlphaEvaluation",
    "FORWARD_HORIZONS",
    "MIN_ALPHA_SAMPLE",
    "STATUS_RESEARCH",
    "STATUS_INSUFFICIENT",
    "STATUS_EXPERIMENTAL",
    "STATUS_VALIDATED",
    "STATUS_FRAGILE",
    "STATUS_NO_EVIDENCE",
    "STATUS_REJECTED",
    "STATUS_BLOCKED",
]
