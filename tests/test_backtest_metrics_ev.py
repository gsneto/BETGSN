"""Testes das metricas de backtest: calibracao, EV e amostra insuficiente.

O ponto que precisa ficar provado: EV previsto alto NAO garante retorno
realizado positivo. As metricas tem que reportar o que aconteceu, nao o
que o modelo prometeu — e marcar amostra pequena como insuficiente.
"""
from __future__ import annotations

import pytest

from betgsn.backtest_engine import FrozenSignal, SettledSignal
from betgsn.backtest_metrics import (
    MIN_SAMPLE,
    aggregate_metrics,
    calibration_bins,
    ev_buckets,
    ev_monotonicity,
    wilson_interval,
)


def _frozen(index, prob, ev, odd, market="Resultado Final (1X2)", outcome="1"):
    return FrozenSignal(
        signal_id=f"s{index}",
        match_id=f"m{index}",
        competition="E0",
        season="2025",
        kickoff="2025-01-01 12:00",
        kickoff_utc=f"2025-01-{index % 28 + 1:02d}T12:00:00Z",
        home="A",
        away="B",
        market=market,
        outcome=outcome,
        best_odd=odd,
        best_book="book",
        median_odd=odd,
        fair_odd=1.0 / max(1e-9, prob),
        n_books=5,
        model_prob=prob,
        market_prob=1.0 / odd,
        edge=prob - 1.0 / odd,
        ev=ev,
        kelly=0.0,
        stake=10.0,
        stake_pct=0.01,
        expected_profit=10.0 * ev,
        confidence="MEDIA",
        rationale="",
        lambda_home=1.4,
        lambda_away=1.1,
        home_attack=1.1,
        home_defense=0.95,
        away_attack=1.0,
        away_defense=1.0,
        home_xg_for=None,
        away_xg_for=None,
        n_prior_matches=500,
        league_goals=2.6,
        attack_blend=0.0,
        odds_source="test",
        odds_as_of=None,
        model_version="BASELINE_V1",
        config_hash="x",
    )


def _settled(index, prob, ev, odd, won):
    return SettledSignal(
        signal=_frozen(index, prob, ev, odd),
        result_home_goals=2 if won else 0,
        result_away_goals=0 if won else 1,
        outcome_result="win" if won else "loss",
        realized_return=(odd - 1.0) if won else -1.0,
        profit=10.0 * ((odd - 1.0) if won else -1.0),
        settled=True,
    )


def test_calibration_recovers_observed_rate():
    signals = [_settled(i, 0.70, 0.05, 1.6, won=i < 70) for i in range(100)]
    bins = calibration_bins(signals)
    assert len(bins) == 1
    bin_ = bins[0]
    assert bin_.label == "70–75%"
    assert bin_.observed_rate == pytest.approx(0.70)
    assert bin_.avg_predicted == pytest.approx(0.70)
    assert bin_.sufficient is True


def test_sample_size_flag_respects_minimum():
    small = [_settled(i, 0.5, 0.03, 2.0, won=i % 2 == 0) for i in range(MIN_SAMPLE - 1)]
    large = [_settled(i, 0.5, 0.03, 2.0, won=i % 2 == 0) for i in range(MIN_SAMPLE)]
    assert aggregate_metrics(small).sample_sufficient is False
    assert aggregate_metrics(large).sample_sufficient is True


def test_high_predicted_ev_does_not_imply_profit():
    """EV alto previsto e retorno realizado negativo: as metricas reportam o real."""
    signals = [_settled(i, 0.6, 0.20, 2.0, won=False) for i in range(60)]
    metrics = aggregate_metrics(signals)
    assert metrics.avg_ev > 0
    assert metrics.avg_realized_return < 0
    assert metrics.ev_gap > 0


def test_ev_monotonicity_only_true_when_returns_grow():
    monotonic = (
        [_settled(i, 0.5, 0.01, 2.0, won=i < 10) for i in range(40)]
        + [_settled(i, 0.5, 0.03, 2.0, won=i < 20) for i in range(40)]
        + [_settled(i, 0.5, 0.08, 2.0, won=i < 30) for i in range(40)]
    )
    buckets = ev_buckets(monotonic)
    assert ev_monotonicity(buckets) is True

    inverted = (
        [_settled(i, 0.5, 0.01, 2.0, won=i < 30) for i in range(40)]
        + [_settled(i, 0.5, 0.03, 2.0, won=i < 20) for i in range(40)]
        + [_settled(i, 0.5, 0.08, 2.0, won=i < 10) for i in range(40)]
    )
    assert ev_monotonicity(ev_buckets(inverted)) is False


def test_wilson_interval_is_bounded_and_ordered():
    low, high = wilson_interval(0, 10)
    assert 0.0 <= low <= high <= 1.0
    low, high = wilson_interval(10, 10)
    assert high <= 1.0
    assert low < high
