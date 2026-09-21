"""Testes do gate de promocao de modelo.

O gate existe para impedir que um numero bonito numa amostra vire decisao
de producao. Estes testes verificam que ele bloqueia pelos motivos certos.
"""
from __future__ import annotations

import pytest

from betgsn.models import MODEL_STATUS, PRODUCTION_MODEL
from betgsn.models.promotion import (
    MAX_ACCEPTABLE_ECE,
    MIN_CONSISTENCY,
    MIN_LEAGUES,
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
