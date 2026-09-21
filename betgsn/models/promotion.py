"""BETGSN :: models.promotion — Gate objetivo de promocao de modelo.

Um modelo NAO e promovido porque teve ROI alto numa amostra. ROI e a
metrica mais ruidosa que existe em apostas: uma sequencia de sorte em 300
apostas produz numeros excelentes sem nenhuma vantagem real.

O gate prioriza metricas probabilisticas (Brier, Log Loss, RPS, calibracao)
porque elas medem se a probabilidade esta certa, que e o que o sistema
realmente afirma. ROI e consequencia, nao evidencia primaria.

Todos os criterios sao explicitos e auditaveis. Nada de formula escondida.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence


class ModelStatus(str, Enum):
    EXPERIMENTAL = "EXPERIMENTAL"
    VALIDATED = "VALIDATED"
    PRODUCTION = "PRODUCTION"
    RETIRED = "RETIRED"


#: Amostra minima por liga/temporada para que o resultado conte como evidencia.
MIN_SAMPLE_PER_SEGMENT = 200
#: Numero minimo de temporadas distintas testadas.
MIN_SEASONS = 2
#: Numero minimo de ligas distintas testadas.
MIN_LEAGUES = 2
#: Melhora relativa minima na metrica primaria para ser considerada real.
MIN_RELATIVE_IMPROVEMENT = 0.005
#: Degradacao relativa maxima tolerada em qualquer metrica secundaria.
MAX_RELATIVE_DEGRADATION = 0.02
#: ECE acima disso significa probabilidade mal calibrada.
MAX_ACCEPTABLE_ECE = 0.05
#: Fracao minima dos segmentos em que o modelo precisa melhorar.
MIN_CONSISTENCY = 0.60

#: Metricas onde MENOR e melhor.
LOWER_IS_BETTER = ("brier", "logloss", "rps", "ece", "mce")


@dataclass(frozen=True)
class SegmentResult:
    """Resultado de um modelo num segmento (liga x temporada)."""

    league: str
    season: str
    n_matches: int
    metrics: dict[str, float]
    baseline_metrics: dict[str, float]

    def improvement(self, metric: str) -> Optional[float]:
        """Melhora relativa vs baseline. Positivo = melhor que baseline."""
        mine = self.metrics.get(metric)
        base = self.baseline_metrics.get(metric)
        if mine is None or base is None or base == 0:
            return None
        raw = (base - mine) / abs(base)
        return raw if metric in LOWER_IS_BETTER else -raw


@dataclass
class PromotionCriterion:
    """Um criterio individual, com resultado e justificativa legivel."""

    name: str
    passed: bool
    detail: str
    blocking: bool = True


@dataclass
class PromotionDecision:
    """Decisao completa e auditavel."""

    model: str
    current_status: ModelStatus
    recommended_status: ModelStatus
    criteria: list[PromotionCriterion] = field(default_factory=list)
    primary_metric: str = "logloss"
    n_segments: int = 0
    total_matches: int = 0
    consistency: float = 0.0

    @property
    def promoted(self) -> bool:
        return self.recommended_status != self.current_status

    @property
    def blocking_failures(self) -> list[str]:
        return [c.name for c in self.criteria if c.blocking and not c.passed]

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "current_status": self.current_status.value,
            "recommended_status": self.recommended_status.value,
            "promoted": self.promoted,
            "primary_metric": self.primary_metric,
            "n_segments": self.n_segments,
            "total_matches": self.total_matches,
            "consistency": round(self.consistency, 4),
            "blocking_failures": self.blocking_failures,
            "criteria": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "blocking": c.blocking,
                    "detail": c.detail,
                }
                for c in self.criteria
            ],
        }

    def summary(self) -> str:
        head = f"{self.model}: {self.current_status.value}"
        if self.promoted:
            head += f" -> {self.recommended_status.value}"
        else:
            head += " (mantido)"
        lines = [head]
        for c in self.criteria:
            mark = "OK " if c.passed else "FALHA"
            lines.append(f"  [{mark}] {c.name}: {c.detail}")
        return "\n".join(lines)


def evaluate_promotion(
    model: str,
    segments: Sequence[SegmentResult],
    current_status: ModelStatus = ModelStatus.EXPERIMENTAL,
    primary_metric: str = "logloss",
    secondary_metrics: Sequence[str] = ("brier", "rps"),
    has_known_leakage: bool = False,
) -> PromotionDecision:
    """Avalia se um modelo pode sair de EXPERIMENTAL.

    Retorna a decisao com cada criterio explicado. Um unico criterio
    bloqueante reprovado impede a promocao.
    """
    criteria: list[PromotionCriterion] = []

    usable = [s for s in segments if s.n_matches >= MIN_SAMPLE_PER_SEGMENT]
    total_matches = sum(s.n_matches for s in usable)
    leagues = {s.league for s in usable}
    seasons = {s.season for s in usable}

    criteria.append(PromotionCriterion(
        name="amostra_minima",
        passed=len(usable) > 0,
        detail=(
            f"{len(usable)} de {len(segments)} segmentos com >= "
            f"{MIN_SAMPLE_PER_SEGMENT} partidas ({total_matches} partidas uteis)"
        ),
    ))

    criteria.append(PromotionCriterion(
        name="multiplas_temporadas",
        passed=len(seasons) >= MIN_SEASONS,
        detail=f"{len(seasons)} temporada(s): {sorted(seasons)} (minimo {MIN_SEASONS})",
    ))

    criteria.append(PromotionCriterion(
        name="multiplas_ligas",
        passed=len(leagues) >= MIN_LEAGUES,
        detail=f"{len(leagues)} liga(s): {sorted(leagues)} (minimo {MIN_LEAGUES})",
    ))

    criteria.append(PromotionCriterion(
        name="sem_leakage_conhecido",
        passed=not has_known_leakage,
        detail=(
            "nenhum leakage declarado" if not has_known_leakage
            else "LEAKAGE DECLARADO: promocao impossivel"
        ),
    ))

    improvements = [
        s.improvement(primary_metric) for s in usable
        if s.improvement(primary_metric) is not None
    ]
    mean_improvement = (
        sum(improvements) / len(improvements) if improvements else None
    )
    criteria.append(PromotionCriterion(
        name=f"melhora_{primary_metric}",
        passed=(
            mean_improvement is not None
            and mean_improvement >= MIN_RELATIVE_IMPROVEMENT
        ),
        detail=(
            f"melhora media de {mean_improvement:.4%} (minimo "
            f"{MIN_RELATIVE_IMPROVEMENT:.2%})"
            if mean_improvement is not None
            else f"metrica {primary_metric} ausente nos segmentos"
        ),
    ))

    consistency = (
        sum(1 for i in improvements if i > 0) / len(improvements)
        if improvements else 0.0
    )
    criteria.append(PromotionCriterion(
        name="consistencia",
        passed=consistency >= MIN_CONSISTENCY,
        detail=(
            f"melhora em {consistency:.0%} dos segmentos "
            f"(minimo {MIN_CONSISTENCY:.0%})"
        ),
    ))

    degraded: list[str] = []
    for metric in secondary_metrics:
        values = [
            s.improvement(metric) for s in usable
            if s.improvement(metric) is not None
        ]
        if not values:
            continue
        mean_secondary = sum(values) / len(values)
        if mean_secondary < -MAX_RELATIVE_DEGRADATION:
            degraded.append(f"{metric} {mean_secondary:.2%}")
    criteria.append(PromotionCriterion(
        name="sem_degradacao_secundaria",
        passed=not degraded,
        detail=(
            "nenhuma metrica secundaria degradada alem de "
            f"{MAX_RELATIVE_DEGRADATION:.0%}"
            if not degraded else "degradacao: " + ", ".join(degraded)
        ),
    ))

    ece_values = [
        s.metrics["ece"] for s in usable if s.metrics.get("ece") is not None
    ]
    mean_ece = sum(ece_values) / len(ece_values) if ece_values else None
    criteria.append(PromotionCriterion(
        name="calibracao_aceitavel",
        passed=mean_ece is not None and mean_ece <= MAX_ACCEPTABLE_ECE,
        detail=(
            f"ECE medio {mean_ece:.4f} (maximo {MAX_ACCEPTABLE_ECE})"
            if mean_ece is not None else "ECE nao disponivel"
        ),
    ))

    all_passed = all(c.passed for c in criteria if c.blocking)
    recommended = (
        ModelStatus.VALIDATED
        if all_passed and current_status == ModelStatus.EXPERIMENTAL
        else current_status
    )

    return PromotionDecision(
        model=model,
        current_status=current_status,
        recommended_status=recommended,
        criteria=criteria,
        primary_metric=primary_metric,
        n_segments=len(usable),
        total_matches=total_matches,
        consistency=consistency,
    )
