"""Testes da validacao walk-forward e da ablacao de ensemble.

O harness tem que provar tres coisas:

- monta janelas sem sobreposicao e descarta as pequenas demais;
- reprova configuracao com leakage ANTES de reportar metrica;
- so chama de vantagem quando o IC pareado exclui zero.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from betgsn.models.validation import (
    Fold,
    build_folds,
    ensemble_ablation,
    leakage_audit,
    walk_forward_validate,
    weight_stability,
)
from betgsn.models.walk_forward import WalkForwardWindow


def _timeline(n, step_days=3):
    base = datetime(2020, 1, 1)
    kicks, available = [], []
    for i in range(n):
        kick = base + timedelta(days=step_days * i)
        kicks.append(kick.strftime("%Y-%m-%d"))
        available.append((kick + timedelta(days=2)).strftime("%Y-%m-%d"))
    return kicks, available


# ------------------------------------------------------------------ folds


def test_build_folds_are_disjoint_and_ordered():
    kicks, available = _timeline(600)
    folds = build_folds(
        kicks, available, "2020-01-01", "2025-01-01",
        train_days=365, validation_days=180, test_days=180,
        min_train=50, min_validation=20, min_test=20,
    )
    assert len(folds) >= 3
    seen_test: set[int] = set()
    for fold in folds:
        assert not (set(fold.train) & set(fold.test))
        assert not (set(fold.validation) & set(fold.test))
        assert not (set(fold.train) & set(fold.validation))
        assert not (seen_test & set(fold.test))
        seen_test |= set(fold.test)


def test_build_folds_discards_small_windows():
    kicks, available = _timeline(40)
    folds = build_folds(
        kicks, available, "2020-01-01", "2025-01-01",
        min_train=200, min_validation=50, min_test=50,
    )
    assert folds == []


def test_build_folds_requires_aligned_inputs():
    with pytest.raises(ValueError):
        build_folds(["2020-01-01"], [], "2020-01-01", "2021-01-01")


# ------------------------------------------------------------- leakage


def test_leakage_audit_detects_overlapping_blocks():
    window = WalkForwardWindow("2020-01-01", "2021-01-01", "2022-01-01", "2023-01-01")
    fold = Fold(0, window, (0, 1), (2,), (1,))
    violations = leakage_audit([fold], ["2020-01-01"] * 3, ["2020-01-03"] * 3)
    assert any("sobrepostos" in v for v in violations)


def test_leakage_audit_detects_index_reused_across_windows():
    window = WalkForwardWindow("2020-01-01", "2021-01-01", "2022-01-01", "2023-01-01")
    folds = [Fold(0, window, (0,), (1,), (2,)), Fold(1, window, (0,), (1,), (2,))]
    violations = leakage_audit(folds, ["2020-01-01"] * 3, ["2020-01-03"] * 3)
    assert any("teste das janelas" in v for v in violations)


def test_leakage_audit_accepts_clean_folds():
    kicks, available = _timeline(600)
    folds = build_folds(
        kicks, available, "2020-01-01", "2025-01-01",
        train_days=365, validation_days=180, test_days=180,
        min_train=50, min_validation=20, min_test=20,
    )
    assert leakage_audit(folds, kicks, available) == []


# --------------------------------------------------- walk_forward_validate


def _synthetic(n=600, seed=0):
    kicks, available = _timeline(n)
    feature = np.random.default_rng(seed).integers(0, 3, n)
    y = feature.copy()

    def candidate(train, validation, test):
        out = []
        for i in test:
            row = np.full(3, 0.05)
            row[feature[i]] = 0.9
            out.append(row)
        return np.array(out)

    def baseline(test):
        return np.full((len(test), 3), 1 / 3)

    return kicks, available, y, candidate, baseline


def test_walk_forward_reports_robust_improvement_and_is_clean():
    kicks, available, y, candidate, baseline = _synthetic()
    result = walk_forward_validate(
        candidate, baseline, y, kicks, available, "2020-01-01", "2025-01-01",
        train_days=365, validation_days=180, test_days=180,
        min_train=50, min_validation=20, min_test=20, resamples=300,
    )
    assert result.clean
    assert result.leakage == []
    assert result.stability == 1.0
    assert result.verdict == "melhora_robusta"
    assert result.paired["ci_low"] > 0
    assert result.aggregate["candidate"]["logloss"] < result.aggregate["baseline"]["logloss"]


def test_walk_forward_declares_tuning_on_test():
    kicks, available, y, candidate, baseline = _synthetic()
    result = walk_forward_validate(
        candidate, baseline, y, kicks, available, "2020-01-01", "2025-01-01",
        train_days=365, validation_days=180, test_days=180,
        min_train=50, min_validation=20, min_test=20, resamples=200,
        tuned_on_test=True,
    )
    assert not result.clean
    assert result.to_dict()["tuned_on_test"] is True


def test_walk_forward_identical_models_is_inconclusive():
    kicks, available, y, candidate, baseline = _synthetic()
    result = walk_forward_validate(
        lambda tr, va, te: baseline(te), baseline, y, kicks, available,
        "2020-01-01", "2025-01-01", train_days=365, validation_days=180,
        test_days=180, min_train=50, min_validation=20, min_test=20, resamples=200,
    )
    assert result.verdict == "inconclusivo"


def test_walk_forward_without_folds_is_safe():
    result = walk_forward_validate(
        lambda tr, va, te: np.ones((len(te), 3)) / 3,
        lambda te: np.ones((len(te), 3)) / 3,
        [0, 1], ["2020-01-01", "2020-01-02"], ["2020-01-03", "2020-01-04"],
        "2020-01-01", "2021-01-01", min_train=1000,
    )
    assert result.aggregate == {"n_folds": 0}
    assert result.verdict == "inconclusivo"


# --------------------------------------------------------------- ablacao


def test_ensemble_ablation_separates_helpful_from_useless_member():
    good = np.tile([0.9, 0.05, 0.05], (60, 1))
    useless = np.full((60, 3), 1 / 3)
    y = [0] * 60
    report = ensemble_ablation(
        {"good": good, "useless": useless}, y,
        weights={"good": 0.8, "useless": 0.2},
    )
    members = {m["name"]: m for m in report["members"]}
    assert members["good"]["helps"] is True
    assert members["good"]["contribution"] > 0
    assert members["useless"]["helps"] is False
    assert 1.0 < report["effective_members"] < 2.0


def test_ensemble_ablation_requires_two_members():
    with pytest.raises(ValueError):
        ensemble_ablation({"only": np.full((5, 3), 1 / 3)}, [0] * 5)


def test_weight_stability_flags_unstable_member():
    stable_report = weight_stability([[0.8, 0.2], [0.78, 0.22], [0.82, 0.18]])
    assert [m["stable"] for m in stable_report["members"]] == [True, True]
    unstable_report = weight_stability([[0.9, 0.1], [0.1, 0.9]])
    assert [m["stable"] for m in unstable_report["members"]] == [False, False]
    with pytest.raises(ValueError):
        weight_stability([])
