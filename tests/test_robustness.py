"""Testes da analise de robustez por segmento.

A regra que precisa ficar provada: conclusao forte exige amostra suficiente
E intervalo que exclui zero. Segmento pequeno nunca gera veredito forte, e
melhora concentrada em poucos segmentos nao vira "melhora robusta".
"""
from __future__ import annotations

import pytest

from betgsn.models.robustness import (
    Observation,
    format_summary,
    odds_band,
    robustness_summary,
    sample_size_summary,
    segment_verdicts,
)


def _observations(n, delta, league, *, odd=2.5, market="1x2", season="2025"):
    return [
        Observation(
            baseline_loss=1.0, candidate_loss=1.0 - delta,
            league=league, season=season, market=market, odd=odd,
            period="2025-01", bookmaker="B1",
        )
        for _ in range(n)
    ]


def test_odds_band_boundaries():
    assert odds_band(1.20) == "< 1.50"
    assert odds_band(1.75) == "1.50-2.00"
    assert odds_band(2.50) == "2.00-3.00"
    assert odds_band(4.00) == "3.00-5.00"
    assert odds_band(9.00) == "5.00+"


def test_segment_verdicts_classify_by_evidence():
    observations = (
        _observations(300, 0.05, "A")
        + _observations(300, -0.05, "B")
        + _observations(50, 0.05, "C")
    )
    verdicts = segment_verdicts(observations, dimensions=("liga",), resamples=200)
    by_segment = {v.segment: v for v in verdicts}
    assert by_segment["A"].verdict == "melhora_robusta"
    assert by_segment["B"].verdict == "piora_robusta"
    assert by_segment["C"].verdict == "amostra_insuficiente"
    assert by_segment["C"].sample_sufficient is False


def test_segment_verdicts_reject_unknown_dimension():
    with pytest.raises(ValueError):
        segment_verdicts([], dimensions=("planeta",))


def test_summary_mixed_evidence_is_inconclusive():
    observations = _observations(300, 0.05, "A") + _observations(300, -0.05, "B")
    verdicts = segment_verdicts(observations, dimensions=("liga",), resamples=200)
    summary = robustness_summary(verdicts)
    assert summary["n_sufficient"] == 2
    assert summary["counts"]["melhora_robusta"] == 1
    assert summary["counts"]["piora_robusta"] == 1
    assert summary["overall_verdict"] == "inconclusivo"


def test_summary_all_robust_improvement():
    observations = _observations(300, 0.05, "A") + _observations(300, 0.04, "B")
    verdicts = segment_verdicts(observations, dimensions=("liga",), resamples=200)
    summary = robustness_summary(verdicts)
    assert summary["overall_verdict"] == "melhora_robusta"
    assert summary["fraction_robust_improvement"] == 1.0


def test_summary_without_sufficient_sample():
    observations = _observations(50, 0.05, "A") + _observations(30, 0.05, "B")
    verdicts = segment_verdicts(observations, dimensions=("liga",), resamples=100)
    summary = robustness_summary(verdicts)
    assert summary["n_sufficient"] == 0
    assert summary["overall_verdict"] == "amostra_insuficiente"
    assert summary["fraction_better_point_estimate"] is None


def test_sample_size_summary_buckets():
    observations = (
        _observations(300, 0.05, "A")
        + _observations(1200, 0.05, "B")
        + _observations(80, 0.05, "C")
    )
    verdicts = segment_verdicts(observations, dimensions=("liga",), resamples=100)
    buckets = sample_size_summary(verdicts)
    assert buckets["200-499"].get("melhora_robusta") == 1
    assert buckets["1000+"].get("melhora_robusta") == 1
    assert buckets["<100"].get("amostra_insuficiente") == 1


def test_multiple_dimensions_are_all_reported():
    observations = _observations(300, 0.05, "A", market="1x2") + _observations(
        300, -0.05, "A", market="btts"
    )
    verdicts = segment_verdicts(
        observations, dimensions=("liga", "mercado"), resamples=200,
    )
    dimensions = {v.dimension for v in verdicts}
    assert dimensions == {"liga", "mercado"}
    assert "Robustez" in format_summary(robustness_summary(verdicts))
