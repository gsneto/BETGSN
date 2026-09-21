"""Testes da comparacao honesta entre modelos.

O que precisa ficar provado:

- as perdas por observacao batem com as metricas agregadas;
- o bootstrap pareado distingue melhora real de ruido;
- um efeito real porem pequeno NAO e chamado de vantagem robusta;
- o block bootstrap recusa a hipotese de independencia quando ha poucos
  blocos, em vez de devolver um IC estreito e falso.
"""
from __future__ import annotations

import numpy as np
import pytest

from betgsn.evaluation import (
    brier_terms,
    block_bootstrap_ci,
    compare_models,
    comparison_verdict,
    logloss_terms,
    min_detectable_effect,
    paired_bootstrap,
    rps_terms,
    score_predictions,
)


def _uniform(n, k=3):
    return np.full((n, k), 1.0 / k)


def _onehot(k, index, n):
    row = np.zeros(k)
    row[index] = 0.8
    row[(index + 1) % k] = 0.1
    row[(index + 2) % k] = 0.1
    return np.tile(row, (n, 1))


# --------------------------------------------------------------- perdas


def test_per_observation_losses_match_aggregate():
    p = _onehot(3, 0, 40)
    y = [0] * 40
    assert logloss_terms(p, y).mean() == pytest.approx(-np.log(0.8))
    assert brier_terms(p, y).mean() == pytest.approx(0.06)
    assert rps_terms(p, y).mean() == pytest.approx(0.025)


def test_per_observation_losses_reject_bad_input():
    with pytest.raises(ValueError):
        logloss_terms(_uniform(3), [0, 1])
    with pytest.raises(ValueError):
        logloss_terms(_uniform(3), [0, 1, 9])


# --------------------------------------------------------- bootstrap


def test_paired_bootstrap_detects_zero_difference():
    a = np.zeros(100)
    paired = paired_bootstrap(a, a, resamples=500)
    assert paired["mean_diff"] == 0.0
    assert paired["ci_low"] <= 0 <= paired["ci_high"]
    assert not paired["distinguishable"]


def test_paired_bootstrap_detects_consistent_improvement():
    rng = np.random.default_rng(1)
    baseline = 1.0 + rng.normal(0, 0.05, 400)
    candidate = baseline - 0.20
    paired = paired_bootstrap(baseline, candidate, resamples=1000)
    assert paired["mean_diff"] == pytest.approx(0.20, abs=1e-9)
    assert paired["ci_low"] > 0
    assert paired["distinguishable"]
    assert paired["p_value"] < 0.01


def test_block_bootstrap_requires_two_blocks():
    with pytest.raises(ValueError):
        block_bootstrap_ci([1.0, 2.0, 3.0], ["2025-01"] * 3)
    result = block_bootstrap_ci(
        [1.0, 2.0, 3.0, 4.0], ["2025-01", "2025-01", "2025-02", "2025-02"],
        resamples=200,
    )
    assert result["n_blocks"] == 2
    assert result["low"] <= result["high"]


def test_paired_bootstrap_requires_aligned_samples():
    with pytest.raises(ValueError):
        paired_bootstrap([1.0, 2.0], [1.0])


# ----------------------------------------------------------- veredito


def test_comparison_verdict_maps_interval_to_words():
    assert comparison_verdict({"ci_low": 0.01, "ci_high": 0.05}) == "melhora_robusta"
    assert comparison_verdict({"ci_low": -0.05, "ci_high": -0.01}) == "piora_robusta"
    assert comparison_verdict({"ci_low": -0.01, "ci_high": 0.05}) == "inconclusivo"
    assert (
        comparison_verdict({"ci_low": 0.001, "ci_high": 0.004}, min_effect=0.01)
        == "melhora_pequena"
    )
    assert (
        comparison_verdict({"ci_low": 0.01, "ci_high": 0.05}, min_effect=0.02)
        == "melhora_pequena"
    )


# ------------------------------------------------------ compare_models


def test_compare_models_robust_improvement():
    baseline = _uniform(400)
    candidate = _onehot(3, 0, 400)
    result = compare_models(baseline, candidate, [0] * 400, resamples=500)
    assert result["verdict"] == "melhora_robusta"
    assert result["relative_improvement"] > 0


def test_compare_models_robust_degradation():
    baseline = _onehot(3, 0, 400)
    candidate = _uniform(400)
    result = compare_models(baseline, candidate, [0] * 400, resamples=500)
    assert result["verdict"] == "piora_robusta"


def test_compare_models_identical_is_inconclusive():
    baseline = _uniform(200)
    result = compare_models(baseline, baseline.copy(), [0] * 200, resamples=300)
    assert result["verdict"] == "inconclusivo"
    assert result["mean_diff"] == 0.0


def test_compare_models_real_but_small_effect_is_not_robust():
    """Efeito real, porem abaixo da margem, nao vira 'vantagem robusta'."""
    baseline = np.tile([0.40, 0.30, 0.30], (400, 1))
    candidate = np.tile([0.41, 0.295, 0.295], (400, 1))
    result = compare_models(
        baseline, candidate, [0] * 400, resamples=500, min_effect=0.05,
    )
    assert result["mean_diff"] > 0
    assert result["verdict"] == "melhora_pequena"
    assert not result["meets_min_effect"]


def test_compare_models_block_bootstrap_by_month():
    baseline = _uniform(300)
    candidate = _onehot(3, 0, 300)
    blocks = ["2025-01"] * 100 + ["2025-02"] * 100 + ["2025-03"] * 100
    result = compare_models(
        baseline, candidate, [0] * 300, resamples=400, block_keys=blocks,
    )
    assert result["n_blocks"] == 3
    assert result["verdict"] == "melhora_robusta"


def test_compare_models_rejects_unknown_metric():
    with pytest.raises(ValueError):
        compare_models(_uniform(10), _uniform(10), [0] * 10, metric="auc")


# -------------------------------------------------------- utilidades


def test_min_detectable_effect_shrinks_with_sample():
    small = min_detectable_effect(100, 1.0)
    large = min_detectable_effect(10000, 1.0)
    assert large < small
    with pytest.raises(ValueError):
        min_detectable_effect(0, 1.0)


def test_aggregate_metrics_still_work():
    metrics = score_predictions(_onehot(3, 0, 30), [0] * 30)
    assert set(metrics) >= {"brier", "logloss", "rps", "ece", "n"}
