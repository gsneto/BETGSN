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

import math
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence
from ..config import production_thresholds
from ..production_policy import ProductionGate, finite_number


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

# --------------------------------------------------------------------------
# Criterios adicionados pela validacao quantitativa (Agente QUANT)
# --------------------------------------------------------------------------
#
# Dois problemas que o gate antigo nao capturava:
#
# 1. Efeito dentro do ruido. Uma melhora media de +0,5% pode ser
#    indistinguivel de zero quando a dispersao entre segmentos e grande. O
#    gate antigo aprovava qualquer media positiva acima de 0,5%, sem olhar
#    o erro-padrao. `MIN_NOISE_T_STAT` exige que a media seja grande em
#    relacao ao proprio erro: sem isso, "vantagem" e sorte amostral.
#
# 2. Efeito real, mas irrelevante. A margem de erro medida do experimento e
#    de ~5%. Um ganho de 0,56% e real na direcao, mas MENOR que a margem:
#    nao sustenta promocao a producao. Por isso a promocao a VALIDATED nao
#    implica elegibilidade para producao.

#: Media minima do efeito para ser considerada estatisticamente fora do ruido.
MIN_NOISE_T_STAT = 2.0
#: Margem de erro medida (~5%). Efeito abaixo disso nao e elegivel a producao.
MIN_MEANINGFUL_IMPROVEMENT = 0.05
#: Amostra total minima (partidas) para a evidencia contar.
MIN_TOTAL_MATCHES = 400
#: Janelas walk-forward minimas quando a evidencia OOS e declarada.
MIN_WINDOWS = production_thresholds()['min_windows']
#: Drawdown maximo tolerado quando a evidencia financeira e declarada.
MAX_ACCEPTABLE_DRAWDOWN = 0.50
#: Segmentos minimos para estimar a dispersao entre segmentos.
MIN_SEGMENTS_FOR_NOISE = 3

#: Amostra minima de linhas apostadas para CLV DECLARADO contar como
#: evidencia (M7). Abaixo disso a media de CLV e ruido puro: com ~30
#: linhas o erro-padrao tipico (~1-2pp) ainda e da ordem do proprio CLV,
#: e a media nao se distingue de zero. CLV declarado com amostra menor
#: reprova o criterio — evidencia insuficiente declarada e declaracao
#: que falhou, nao ausencia que passa.
MIN_CLV_SAMPLE = production_thresholds()['min_clv_sample']


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
    mean_improvement: Optional[float] = None
    improvement_t_stat: Optional[float] = None
    min_meaningful_improvement: float = MIN_MEANINGFUL_IMPROVEMENT
    production_eligible: bool = False

    @property
    def promoted(self) -> bool:
        return self.recommended_status != self.current_status

    @property
    def blocking_failures(self) -> list[str]:
        return [c.name for c in self.criteria if c.blocking and not c.passed]

    @property
    def below_meaningful_margin(self) -> bool:
        """Efeito real, porem menor que a margem de erro medida (~5%)."""
        return (
            self.mean_improvement is not None
            and self.mean_improvement < self.min_meaningful_improvement
        )

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
            "mean_improvement": (
                None if self.mean_improvement is None
                else round(self.mean_improvement, 6)
            ),
            "improvement_t_stat": (
                None if self.improvement_t_stat is None
                or not math.isfinite(self.improvement_t_stat)
                else round(self.improvement_t_stat, 4)
            ),
            "improvement_t_stat_infinite": bool(
                self.improvement_t_stat is not None
                and math.isinf(self.improvement_t_stat)
            ),
            "min_meaningful_improvement": self.min_meaningful_improvement,
            "below_meaningful_margin": self.below_meaningful_margin,
            "production_eligible": self.production_eligible,
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
        if self.below_meaningful_margin:
            lines.append(
                "  NOTA: efeito real porem abaixo da margem de erro "
                f"({self.min_meaningful_improvement:.2%}). Nao elegivel a producao."
            )
        elif self.production_eligible:
            lines.append("  Elegivel a producao (decisao humana, nao automatica).")
        return "\n".join(lines)


def evaluate_promotion(
    model: str,
    segments: Sequence[SegmentResult],
    current_status: ModelStatus = ModelStatus.EXPERIMENTAL,
    primary_metric: str = "logloss",
    secondary_metrics: Sequence[str] = ("brier", "rps"),
    has_known_leakage: bool = False,
    *,
    improvement_ci: tuple[float, float] | None = None,
    clv: dict | None = None,
    max_drawdown: float | None = None,
    n_windows: int | None = None,
    tuned_on_test: bool = False,
    min_meaningful_improvement: float = MIN_MEANINGFUL_IMPROVEMENT,
    calibration: dict | None = None,
    production_gate: ProductionGate | None = None,
) -> PromotionDecision:
    """Avalia se um modelo pode sair de EXPERIMENTAL.

    Retorna a decisao com cada criterio explicado. Um unico criterio
    bloqueante reprovado impede a promocao.

    Evidencia opcional (quando informada, entra como criterio bloqueante):

    - `improvement_ci`: IC 95% da melhora media (ex.: bootstrap pareado).
      Se informado, substitui o teste t entre segmentos.
    - `clv`: {"mean": float, "ci_low": float, "ci_high": float,
      "n": int (opcional), "prospective": bool (opcional)}. CLV
      prospectivo: a unica evidencia de que o preco batido no momento da
      decisao venceu o fechamento. Contrato (M7):
        * `n`, quando informado, e o numero de linhas COM CLV valido
          (ex.: `CLVCoverage.bets_with_clv`). Abaixo de MIN_CLV_SAMPLE
          o criterio REPROVA: amostra insuficiente nao passa
          silenciosamente.
        * `prospective`, quando informado e False, REPROVA: CLV
          retrospectivo (entrada reconstruida depois do fechamento) nao
          e evidencia prospectiva — e validacao disfarçada.
        * sem `n`/`prospective` o criterio avalia mean/ci como antes
          (compatibilidade com chamadores que nao os medem).
    - `max_drawdown`: pior queda da banca no periodo (fracao, ex.: 0.35).
    - `n_windows`: numero de janelas walk-forward OOS testadas.
    - `tuned_on_test`: declarar True quando hiperparametro foi ajustado no
      conjunto de teste. Bloqueia a promocao.
    - `calibration` (canal de calibracao por janela): {"mean_ece": float,
      "n_segments": int, "insufficient_segments": int,
      "min_sample_per_segment": int, "method": str}. E o ECE CALIBRADO
      medido por janela walk-forward (calibrador ajustado no TRAIN,
      congelado no TEST), onde cada janela tem amostra que sustenta a
      medicao. Quando informado, o criterio `calibracao_aceitavel` usa
      este canal em vez da media de ECE raspada dos segmentos (ligas x
      temporadas pequenos produzem ECE instavel — amostra insuficiente
      nao e miscalibracao). O LIMITE NAO MUDA (MAX_ACCEPTABLE_ECE), e as
      janelas insuficientes sao declaradas no detalhe, nunca escondidas.
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
    if calibration is not None:
        # Canal de calibracao por janela walk-forward: medicao onde a
        # amostra sustenta o estimador (ver docstring). O limite e o
        # MESMO do caminho por segmento — o que muda e a qualidade da
        # medicao, nunca o criterio.
        mean_ece = calibration.get("mean_ece")
        cal_passed = (
            mean_ece is not None and mean_ece <= MAX_ACCEPTABLE_ECE
        )
        n_cal = int(calibration.get("n_segments", 0))
        n_ins = int(calibration.get("insufficient_segments", 0))
        min_sample = int(calibration.get("min_sample_per_segment", 0))
        method = str(calibration.get("method", "n/d"))
        if mean_ece is None:
            cal_detail = "ECE calibrado indisponivel (nenhuma janela com amostra suficiente)"
        else:
            cal_detail = (
                f"ECE calibrado {mean_ece:.4f} (maximo {MAX_ACCEPTABLE_ECE}) "
                f"em {n_cal} janela(s) walk-forward com >= {min_sample} "
                f"apostas (metodo {method})"
                + (f"; {n_ins} janela(s) INSUFFICIENT_DATA excluida(s)" if n_ins else "")
            )
    else:
        mean_ece = sum(ece_values) / len(ece_values) if ece_values else None
        cal_passed = mean_ece is not None and mean_ece <= MAX_ACCEPTABLE_ECE
        cal_detail = (
            f"ECE medio {mean_ece:.4f} (maximo {MAX_ACCEPTABLE_ECE})"
            if mean_ece is not None else "ECE nao disponivel"
        )
    criteria.append(PromotionCriterion(
        name="calibracao_aceitavel", passed=cal_passed, detail=cal_detail,
    ))

    # ---- criterios quantitativos adicionados --------------------------------

    criteria.append(PromotionCriterion(
        name="amostra_total_minima",
        passed=total_matches >= MIN_TOTAL_MATCHES,
        detail=f"{total_matches} partidas uteis (minimo {MIN_TOTAL_MATCHES})",
    ))

    t_stat = _t_stat(improvements)
    if improvement_ci is not None:
        noise_passed = improvement_ci[0] > 0
        noise_detail = (
            f"IC 95% da melhora [{improvement_ci[0]:+.4%}, {improvement_ci[1]:+.4%}] "
            + ("exclui zero" if noise_passed else "cruza zero: indistinguivel de zero")
        )
    elif len(improvements) >= MIN_SEGMENTS_FOR_NOISE:
        noise_passed = t_stat is not None and t_stat >= MIN_NOISE_T_STAT
        noise_detail = (
            f"t entre segmentos {t_stat:.2f} (minimo {MIN_NOISE_T_STAT}) em "
            f"{len(improvements)} segmentos"
        )
    else:
        noise_passed = False
        noise_detail = (
            f"{len(improvements)} segmento(s): insuficiente para estimar o ruido "
            f"(minimo {MIN_SEGMENTS_FOR_NOISE}); melhora media nao e evidencia"
        )
    criteria.append(PromotionCriterion(
        name="efeito_acima_do_ruido", passed=noise_passed, detail=noise_detail,
    ))

    margin_passed = (
        mean_improvement is not None
        and mean_improvement >= min_meaningful_improvement
    )
    # Nao bloqueante de proposito: VALIDATED mede evidencia estatistica.
    # Elegibilidade a producao exige efeito maior que a margem de erro.
    criteria.append(PromotionCriterion(
        name="efeito_acima_da_margem",
        passed=margin_passed,
        blocking=False,
        detail=(
            f"melhora media {mean_improvement:+.4%} vs margem "
            f"{min_meaningful_improvement:.2%}; "
            + ("elegivel a producao" if margin_passed
               else "ABAIXO da margem: nao elegivel a producao")
            if mean_improvement is not None
            else "melhora media indisponivel"
        ),
    ))

    criteria.append(PromotionCriterion(
        name="sem_tuning_no_teste",
        passed=not tuned_on_test,
        detail=(
            "nenhum ajuste declarado no conjunto de teste"
            if not tuned_on_test
            else "AJUSTE NO CONJUNTO DE TESTE DECLARADO: promocao impossivel"
        ),
    ))

    if n_windows is None:
        criteria.append(PromotionCriterion(
            name="evidencia_oos_janelas", passed=True, blocking=False,
            detail="janelas walk-forward nao declaradas (evidencia OOS nao avaliada)",
        ))
    else:
        criteria.append(PromotionCriterion(
            name="evidencia_oos_janelas",
            passed=n_windows >= MIN_WINDOWS,
            detail=f"{n_windows} janela(s) walk-forward (minimo {MIN_WINDOWS})",
        ))

    if clv is None:
        criteria.append(PromotionCriterion(
            name="clv_nao_negativo", passed=False, blocking=True,
            detail="CLV nao informado (odds sem timestamp de publicacao)",
        ))
    else:
        clv_mean = clv.get("mean")
        clv_low = clv.get("ci_low")
        clv_passed = (finite_number(clv_mean) and clv_mean > 0
                      and finite_number(clv_low) and clv_low > 0)
        detail = (
            (f"CLV medio {clv_mean:+.4%}" if finite_number(clv_mean) else 'CLV indisponivel')
            + (f", IC low {clv_low:+.4%}" if finite_number(clv_low) else "")
        )
        # M7: CLV declarado precisa ser prospectivo e amostrado. Sem
        # isso, uma media positiva sobre meia duzia de linhas entraria
        # como "evidencia" — e CLV reconstruido depois do fechamento
        # seria validacao retrospectiva disfarçada de prospectiva.
        prospective = clv.get("prospective")
        if prospective is False:
            clv_passed = False
            detail += "; RETROSPECTIVO: entrada apos o fechamento, nao " \
                      "e evidencia prospectiva"
        elif prospective is None:
            # Fase D: a chave nao foi declarada. O pass/fail original e
            # PRESERVADO (retrocompatibilidade), mas a ausencia fica
            # explicita — CLV sem proveniencia declarada pode ser
            # retrospectivo e o leitor do relatorio precisa saber.
            detail += "; PROVENANCIA NAO DECLARADA: informe " \
                      "'prospective' para classificar o CLV"
        n_clv = clv.get("n")
        median = clv.get('median')
        positive = clv.get('n_positive')
        rate = clv.get('positive_rate')
        strict_passed = (
            type(n_clv) is int and n_clv >= MIN_CLV_SAMPLE
            and finite_number(median) and median > 0
            and type(positive) is int and 0 <= positive <= n_clv
            and positive*100 >= 55*n_clv
            and finite_number(rate) and .55 <= rate <= 1
            and abs(rate-positive/n_clv) <= 1e-6
            and prospective is True and clv.get('closed_only') is True
        )
        clv_passed = bool(clv_passed and strict_passed)
        if not strict_passed:
            detail += '; evidencia insuficiente: exige CLOSED prospectivo n>=200, mediana>0, beat-close>=55%'
        if clv_passed and n_clv is not None:
            if int(n_clv) < MIN_CLV_SAMPLE:
                clv_passed = False
                detail += (
                    f"; amostra insuficiente: {int(n_clv)} linha(s) com CLV "
                    f"valido (minimo {MIN_CLV_SAMPLE})"
                )
            else:
                detail += f"; n={int(n_clv)} linhas com CLV valido"
        elif n_clv is None:
            detail += "; amostra nao informada"
        criteria.append(PromotionCriterion(
            name="clv_nao_negativo", passed=clv_passed, detail=detail,
        ))

    if max_drawdown is None:
        criteria.append(PromotionCriterion(
            name="drawdown_aceitavel", passed=True, blocking=False,
            detail="drawdown nao informado",
        ))
    else:
        criteria.append(PromotionCriterion(
            name="drawdown_aceitavel",
            passed=max_drawdown <= MAX_ACCEPTABLE_DRAWDOWN,
            detail=(
                f"drawdown {max_drawdown:.1%} (maximo "
                f"{MAX_ACCEPTABLE_DRAWDOWN:.0%})"
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
        mean_improvement=mean_improvement,
        improvement_t_stat=t_stat,
        min_meaningful_improvement=min_meaningful_improvement,
        production_eligible=bool(all_passed and margin_passed
                                 and isinstance(production_gate, ProductionGate)
                                 and production_gate.production_eligible),
    )


def _t_stat(improvements: Sequence[float]) -> Optional[float]:
    """t da melhora media entre segmentos. None se nao ha dispersao utilizavel.

    Com todos os segmentos identicos (desvio zero) a media e exata: t e
    infinito quando positiva e -infinito quando negativa.
    """
    if len(improvements) < 2:
        return None
    mean = statistics.fmean(improvements)
    stdev = statistics.pstdev(improvements)
    if stdev == 0:
        return math.inf if mean > 0 else (-math.inf if mean < 0 else 0.0)
    return mean / (stdev / math.sqrt(len(improvements)))
