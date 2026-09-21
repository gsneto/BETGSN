"""BETGSN :: models.robustness — onde o modelo melhora, e onde e so ruido.

Um numero agregado esconde o caso que importa: melhora concentrada em
poucos segmentos e piora no resto. Este modulo corta o resultado por liga,
temporada, mercado, faixa de odd, periodo, bookmaker e tamanho de amostra,
e da a CADA segmento um veredito explicito:

- `melhora_robusta`: IC 95% inteiramente acima de zero;
- `piora_robusta`: IC inteiramente abaixo de zero;
- `inconclusivo`: IC cruza zero — sem evidencia de diferenca;
- `amostra_insuficiente`: menos de `min_sample` observacoes.

Regra central: conclusao forte exige amostra suficiente E intervalo que
exclui zero. Sem as duas coisas, o veredito e "inconclusivo", nunca
"vantagem". O modulo nao escolhe o melhor segmento para reportar: reporta
todos, inclusive os que pioram.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

from ..evaluation import comparison_verdict, paired_bootstrap

#: Abaixo disso o segmento e reportado como AMOSTRA INSUFICIENTE.
MIN_SEGMENT_SAMPLE = 200

#: Faixas de odd usadas na segmentacao (mesmas do backtest_metrics).
ODDS_BANDS: tuple[tuple[str, float, float], ...] = (
    ("< 1.50", 0.0, 1.50),
    ("1.50-2.00", 1.50, 2.00),
    ("2.00-3.00", 2.00, 3.00),
    ("3.00-5.00", 3.00, 5.00),
    ("5.00+", 5.00, math.inf),
)

DIMENSIONS: tuple[str, ...] = (
    "liga", "temporada", "mercado", "faixa_odd", "periodo", "bookmaker",
)


@dataclass(frozen=True)
class Observation:
    """Uma partida avaliada: perda do baseline e do candidato.

    `baseline_loss` e `candidate_loss` sao perdas por observacao (ex.:
    LogLoss) na MESMA partida. Positivo em `delta` significa candidato
    melhor.
    """

    baseline_loss: float
    candidate_loss: float
    league: str = ""
    season: str = ""
    market: str = ""
    odd: float = 0.0
    period: str = ""
    bookmaker: str = ""

    @property
    def delta(self) -> float:
        return self.baseline_loss - self.candidate_loss


@dataclass(frozen=True)
class SegmentVerdict:
    dimension: str
    segment: str
    n: int
    mean_delta: float
    ci_low: float
    ci_high: float
    verdict: str
    sample_sufficient: bool

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "segment": self.segment,
            "n": self.n,
            "mean_delta": self.mean_delta,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "verdict": self.verdict,
            "sample_sufficient": self.sample_sufficient,
        }


def odds_band(odd: float) -> str:
    for label, lo, hi in ODDS_BANDS:
        if lo <= odd < hi:
            return label
    return ODDS_BANDS[-1][0]


def _key(observation: Observation, dimension: str) -> str:
    if dimension == "liga":
        return observation.league or "-"
    if dimension == "temporada":
        return observation.season or "-"
    if dimension == "mercado":
        return observation.market or "-"
    if dimension == "faixa_odd":
        return odds_band(observation.odd)
    if dimension == "periodo":
        return observation.period or "-"
    if dimension == "bookmaker":
        return observation.bookmaker or "-"
    raise ValueError(f"dimensao desconhecida: {dimension}")


def segment_verdicts(
    observations: Sequence[Observation],
    *,
    dimensions: Iterable[str] = DIMENSIONS,
    min_sample: int = MIN_SEGMENT_SAMPLE,
    resamples: int = 2000,
    seed: int = 6767,
    min_effect: float = 0.0,
) -> list[SegmentVerdict]:
    """Veredito por segmento, em cada dimensao pedida."""
    dimensions = tuple(dimensions)
    for dimension in dimensions:
        if dimension not in DIMENSIONS:
            raise ValueError(f"dimensao desconhecida: {dimension}")
    groups: dict[tuple[str, str], list[Observation]] = {}
    for observation in observations:
        for dimension in dimensions:
            groups.setdefault((dimension, _key(observation, dimension)), []).append(observation)

    verdicts: list[SegmentVerdict] = []
    for (dimension, segment), items in groups.items():
        verdicts.append(_verdict(dimension, segment, items, min_sample, resamples, seed, min_effect))
    verdicts.sort(key=lambda v: (v.dimension, -v.n, v.segment))
    return verdicts


def _verdict(dimension, segment, items, min_sample, resamples, seed, min_effect) -> SegmentVerdict:
    n = len(items)
    if n < min_sample:
        return SegmentVerdict(
            dimension, segment, n,
            float(np.mean([o.delta for o in items])) if items else 0.0,
            0.0, 0.0, "amostra_insuficiente", False,
        )
    base = [o.baseline_loss for o in items]
    cand = [o.candidate_loss for o in items]
    paired = paired_bootstrap(base, cand, resamples=resamples, seed=seed)
    return SegmentVerdict(
        dimension=dimension,
        segment=segment,
        n=n,
        mean_delta=paired["mean_diff"],
        ci_low=paired["ci_low"],
        ci_high=paired["ci_high"],
        verdict=comparison_verdict(paired, min_effect=min_effect),
        sample_sufficient=True,
    )


def robustness_summary(
    verdicts: Sequence[SegmentVerdict],
    *,
    min_effect: float = 0.0,
) -> dict:
    """Resumo honesto: quantos segmentos melhoram, pioram ou sao inconclusivos.

    O veredito geral so e positivo quando ha evidencia robusta em MAIS
    segmentos do que em sentido contrario. Qualquer outro caso e
    "inconclusivo" — mesmo com media agregada bonita.
    """
    sufficient = [v for v in verdicts if v.sample_sufficient]
    counts: dict[str, int] = {}
    for verdict in verdicts:
        counts[verdict.verdict] = counts.get(verdict.verdict, 0) + 1
    by_dimension: dict[str, dict[str, int]] = {}
    for verdict in verdicts:
        bucket = by_dimension.setdefault(verdict.dimension, {})
        bucket[verdict.verdict] = bucket.get(verdict.verdict, 0) + 1

    robust_up = counts.get("melhora_robusta", 0)
    robust_down = counts.get("piora_robusta", 0)
    if not sufficient:
        overall = "amostra_insuficiente"
    elif robust_up > robust_down and robust_up >= max(1, len(sufficient) // 2):
        overall = "melhora_robusta"
    elif robust_down > robust_up and robust_down >= max(1, len(sufficient) // 2):
        overall = "piora_robusta"
    else:
        overall = "inconclusivo"

    return {
        "n_segments": len(verdicts),
        "n_sufficient": len(sufficient),
        "n_insufficient": len(verdicts) - len(sufficient),
        "counts": counts,
        "by_dimension": by_dimension,
        "fraction_better_point_estimate": (
            sum(1 for v in sufficient if v.mean_delta > 0) / len(sufficient)
            if sufficient else None
        ),
        "fraction_robust_improvement": (
            robust_up / len(sufficient) if sufficient else None
        ),
        "overall_verdict": overall,
        "min_effect": min_effect,
        "note": (
            "Conclusao forte exige amostra suficiente e IC que exclui zero. "
            "Media agregada positiva com poucos segmentos robustos e "
            "idiossincrasia, nao vantagem."
        ),
    }


def sample_size_summary(verdicts: Sequence[SegmentVerdict]) -> dict:
    """Distribuicao dos vereditos por faixa de tamanho de amostra.

    Serve para responder a pergunta "as conclusoes fortes vieram de
    segmentos grandes ou pequenos?". Veredito robusto em segmento pequeno
    nao sustenta decisao.
    """
    buckets: dict[str, dict[str, int]] = {
        "<100": {}, "100-199": {}, "200-499": {}, "500-999": {}, "1000+": {},
    }
    def bucket_of(n: int) -> str:
        if n < 100:
            return "<100"
        if n < 200:
            return "100-199"
        if n < 500:
            return "200-499"
        if n < 1000:
            return "500-999"
        return "1000+"

    for verdict in verdicts:
        bucket = buckets[bucket_of(verdict.n)]
        bucket[verdict.verdict] = bucket.get(verdict.verdict, 0) + 1
    return buckets


def format_summary(summary: dict) -> str:
    """Tabela de texto do resumo de robustez."""
    lines = [
        f"Robustez: {summary['n_segments']} segmentos, "
        f"{summary['n_sufficient']} com amostra suficiente, "
        f"{summary['n_insufficient']} insuficientes",
    ]
    for name, count in sorted(summary["counts"].items()):
        lines.append(f"  {name}: {count}")
    frac = summary["fraction_better_point_estimate"]
    lines.append(
        "  melhor no ponto: "
        + (f"{frac:.0%}" if frac is not None else "n/d")
        + " | melhora robusta: "
        + (
            f"{summary['fraction_robust_improvement']:.0%}"
            if summary["fraction_robust_improvement"] is not None else "n/d"
        )
    )
    lines.append(f"  veredito geral: {summary['overall_verdict']}")
    return "\n".join(lines)
