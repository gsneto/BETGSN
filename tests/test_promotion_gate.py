"""Testes do gate de promocao de modelo.

O gate existe para impedir que um numero bonito numa amostra vire decisao
de producao. Estes testes verificam que ele bloqueia pelos motivos certos.
"""
from __future__ import annotations

import pytest

from betgsn.models import (
    MEASURED_ERROR_MARGIN,
    MODEL_STATUS,
    PRODUCTION_ELIGIBLE,
    PRODUCTION_MODEL,
)
from betgsn.models.promotion import (
    MAX_ACCEPTABLE_ECE,
    MIN_CONSISTENCY,
    MIN_LEAGUES,
    MIN_MEANINGFUL_IMPROVEMENT,
    MIN_SAMPLE_PER_SEGMENT,
    MIN_SEASONS,
    ModelStatus,
    PromotionDecision,
    SegmentResult,
    evaluate_promotion,
)


def _segment(league="E0", season="2025", n=300, logloss=0.95,
             brier=0.56, ece=0.02, base_logloss=0.99, base_brier=0.58,
             base_ece=0.02) -> SegmentResult:
    return SegmentResult(
        league=league, season=season, n_matches=n,
        metrics={"logloss": logloss, "brier": brier, "ece": ece},
        baseline_metrics={"logloss": base_logloss, "brier": base_brier,
                          "ece": base_ece},
    )


def _good_segments() -> list[SegmentResult]:
    """Conjunto que satisfaz todos os criterios."""
    return [
        _segment(league=lg, season=se)
        for se in ("2024", "2025")
        for lg in ("E0", "SP1")
    ]


# --------------------------------------------------------------- melhora

def test_improvement_positive_when_lower_is_better():
    seg = _segment(logloss=0.90, base_logloss=1.00)
    assert seg.improvement("logloss") == pytest.approx(0.10)


def test_improvement_negative_when_worse():
    seg = _segment(logloss=1.10, base_logloss=1.00)
    assert seg.improvement("logloss") == pytest.approx(-0.10)


def test_improvement_none_for_missing_metric():
    seg = _segment()
    assert seg.improvement("rps") is None


def test_improvement_none_when_baseline_is_zero():
    seg = SegmentResult("E0", "2025", 300, {"logloss": 0.5},
                        {"logloss": 0.0})
    assert seg.improvement("logloss") is None


# ------------------------------------------------------------- aprovacao

def test_full_evidence_promotes_to_validated():
    d = evaluate_promotion("Ensemble", _good_segments())
    assert d.recommended_status is ModelStatus.VALIDATED
    assert d.promoted
    assert d.blocking_failures == []


def test_promotion_never_jumps_to_production():
    """VALIDATED e o teto do gate. PRODUCTION e decisao humana."""
    d = evaluate_promotion("Ensemble", _good_segments())
    assert d.recommended_status is not ModelStatus.PRODUCTION


# ------------------------------------------------------------- bloqueios

def test_single_season_blocks_promotion():
    segs = [_segment(league=lg, season="2025") for lg in ("E0", "SP1")]
    d = evaluate_promotion("X", segs)
    assert d.recommended_status is ModelStatus.EXPERIMENTAL
    assert "multiplas_temporadas" in d.blocking_failures


def test_single_league_blocks_promotion():
    segs = [_segment(league="E0", season=se) for se in ("2024", "2025")]
    d = evaluate_promotion("X", segs)
    assert "multiplas_ligas" in d.blocking_failures


def test_small_sample_segments_are_discarded():
    segs = [
        _segment(league=lg, season=se, n=MIN_SAMPLE_PER_SEGMENT - 1)
        for se in ("2024", "2025") for lg in ("E0", "SP1")
    ]
    d = evaluate_promotion("X", segs)
    assert d.n_segments == 0
    assert "amostra_minima" in d.blocking_failures


def test_declared_leakage_blocks_everything():
    d = evaluate_promotion("X", _good_segments(), has_known_leakage=True)
    assert "sem_leakage_conhecido" in d.blocking_failures
    assert d.recommended_status is ModelStatus.EXPERIMENTAL


def test_no_real_improvement_blocks():
    segs = [
        _segment(league=lg, season=se, logloss=0.9899, base_logloss=0.99)
        for se in ("2024", "2025") for lg in ("E0", "SP1")
    ]
    d = evaluate_promotion("X", segs)
    assert "melhora_logloss" in d.blocking_failures


def test_inconsistent_improvement_blocks():
    """Media positiva concentrada numa liga nao promove."""
    segs = [
        _segment(league="E0", season="2024", logloss=0.70, base_logloss=0.99),
        _segment(league="SP1", season="2024", logloss=1.00, base_logloss=0.99),
        _segment(league="E0", season="2025", logloss=1.00, base_logloss=0.99),
        _segment(league="SP1", season="2025", logloss=1.00, base_logloss=0.99),
    ]
    d = evaluate_promotion("X", segs)
    assert d.consistency < MIN_CONSISTENCY
    assert "consistencia" in d.blocking_failures


def test_bad_calibration_blocks():
    segs = [
        _segment(league=lg, season=se, ece=MAX_ACCEPTABLE_ECE + 0.05)
        for se in ("2024", "2025") for lg in ("E0", "SP1")
    ]
    d = evaluate_promotion("X", segs)
    assert "calibracao_aceitavel" in d.blocking_failures


def test_secondary_degradation_blocks():
    segs = [
        _segment(league=lg, season=se, brier=0.70, base_brier=0.58)
        for se in ("2024", "2025") for lg in ("E0", "SP1")
    ]
    d = evaluate_promotion("X", segs, secondary_metrics=("brier",))
    assert "sem_degradacao_secundaria" in d.blocking_failures


# ------------------------------------------------------------- relatorio

def test_decision_serializes_every_criterion():
    d = evaluate_promotion("Ensemble", _good_segments())
    payload = d.to_dict()
    assert payload["model"] == "Ensemble"
    assert payload["recommended_status"] == "VALIDATED"
    names = {c["name"] for c in payload["criteria"]}
    assert "melhora_logloss" in names
    assert "consistencia" in names
    assert "sem_leakage_conhecido" in names
    assert all("detail" in c and c["detail"] for c in payload["criteria"])


def test_summary_is_human_readable():
    d = evaluate_promotion("Ensemble", _good_segments())
    text = d.summary()
    assert "Ensemble" in text
    assert "VALIDATED" in text


def test_empty_segments_never_promote():
    d = evaluate_promotion("X", [])
    assert d.recommended_status is ModelStatus.EXPERIMENTAL
    assert d.n_segments == 0


# --------------------------------------------------------------- registro

def test_production_model_is_still_baseline():
    """Ensemble validado NAO virou producao automaticamente."""
    assert PRODUCTION_MODEL == "BASELINE_V1"
    assert MODEL_STATUS["BASELINE_V1"] == "PRODUCTION"


def test_ensemble_is_validated_not_production():
    assert MODEL_STATUS["Ensemble"] == "VALIDATED"


def test_boosters_remain_experimental():
    assert MODEL_STATUS["XGBoost"] == "EXPERIMENTAL"
    assert MODEL_STATUS["LightGBM"] == "EXPERIMENTAL"


# --------------------------------------------------- criterios quantitativos


def test_single_segment_cannot_estimate_noise():
    """Com um segmento so, a dispersao do efeito e desconhecida."""
    d = evaluate_promotion("X", [_segment(league="E0", season="2025")])
    assert "efeito_acima_do_ruido" in d.blocking_failures


def test_improvement_inside_dispersion_blocks():
    """Media positiva puxada por um segmento sorteado nao e vantagem."""
    segs = [
        _segment(league="E0", season="2024", logloss=0.70, base_logloss=0.99),
        _segment(league="SP1", season="2024", logloss=0.99, base_logloss=0.99),
        _segment(league="E0", season="2025", logloss=0.99, base_logloss=0.99),
        _segment(league="SP1", season="2025", logloss=0.99, base_logloss=0.99),
    ]
    d = evaluate_promotion("X", segs)
    assert "efeito_acima_do_ruido" in d.blocking_failures
    assert d.improvement_t_stat < 2.0


def test_consistent_improvement_passes_noise_criterion():
    d = evaluate_promotion("X", _good_segments())
    assert "efeito_acima_do_ruido" not in d.blocking_failures
    assert d.improvement_t_stat == float("inf")


def test_improvement_ci_overrides_segment_t_stat():
    passing = evaluate_promotion("X", _good_segments(), improvement_ci=(0.001, 0.02))
    assert "efeito_acima_do_ruido" not in passing.blocking_failures
    failing = evaluate_promotion("X", _good_segments(), improvement_ci=(-0.001, 0.02))
    assert "efeito_acima_do_ruido" in failing.blocking_failures


def test_total_sample_minimum_blocks():
    d = evaluate_promotion(
        "X", [_segment(league="E0", season="2025", n=MIN_SAMPLE_PER_SEGMENT)],
    )
    assert "amostra_total_minima" in d.blocking_failures


def test_tuning_on_test_blocks_promotion():
    d = evaluate_promotion("X", _good_segments(), tuned_on_test=True)
    assert "sem_tuning_no_teste" in d.blocking_failures
    assert d.recommended_status is ModelStatus.EXPERIMENTAL


def test_declared_walk_forward_windows_are_required():
    blocked = evaluate_promotion("X", _good_segments(), n_windows=1)
    assert "evidencia_oos_janelas" in blocked.blocking_failures
    approved = evaluate_promotion("X", _good_segments(), n_windows=2)
    assert "evidencia_oos_janelas" not in approved.blocking_failures


def test_clv_and_drawdown_when_declared():
    clv_blocked = evaluate_promotion(
        "X", _good_segments(), clv={"mean": -0.01, "ci_low": -0.02},
    )
    assert "clv_nao_negativo" in clv_blocked.blocking_failures
    drawdown_blocked = evaluate_promotion("X", _good_segments(), max_drawdown=0.80)
    assert "drawdown_aceitavel" in drawdown_blocked.blocking_failures


def test_small_effect_is_validated_but_not_production_eligible():
    """O caso do Ensemble real: real na direcao, abaixo da margem de erro."""
    segs = [
        _segment(league=lg, season=se, logloss=0.95, base_logloss=0.99)
        for se in ("2024", "2025") for lg in ("E0", "SP1")
    ]
    d = evaluate_promotion("Ensemble", segs)
    assert d.recommended_status is ModelStatus.VALIDATED
    assert d.below_meaningful_margin is True
    assert d.production_eligible is False
    assert d.mean_improvement < MIN_MEANINGFUL_IMPROVEMENT


def test_meaningful_effect_is_production_eligible():
    segs = [
        _segment(league=lg, season=se, logloss=0.90, base_logloss=0.99)
        for se in ("2024", "2025") for lg in ("E0", "SP1")
    ]
    d = evaluate_promotion("X", segs)
    assert d.production_eligible is True
    assert d.mean_improvement >= MIN_MEANINGFUL_IMPROVEMENT


def test_registry_separates_validated_from_production_eligible():
    assert MODEL_STATUS["Ensemble"] == "VALIDATED"
    assert PRODUCTION_ELIGIBLE["Ensemble"] is False
    assert MEASURED_ERROR_MARGIN == 0.05
    assert PRODUCTION_MODEL == "BASELINE_V1"
