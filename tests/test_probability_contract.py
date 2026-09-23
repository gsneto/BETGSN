"""Contrato tipado de fontes de probabilidade — MARKET vs MODEL separados.

O que esta suíte prova:

  - market_raw / market_fair / model vivem em CAMPOS distintos e nenhum
    preenche outro (construção explícita);
  - invariantes: probabilidades em (0,1), implied == 1/odd;
  - model_minus_market só existe com as duas pontas — None nunca é
    zero fabricado;
  - breakdown_from_bet NUNCA usa market como model (e vice-versa);
  - breakdown_from_signal idem.
"""

from __future__ import annotations

import pytest

from betgsn.probability_contract import (
    ProbabilityBreakdown,
    breakdown_from_bet,
    breakdown_from_signal,
)


# ==========================================================================
# invariantes
# ==========================================================================


def test_valid_breakdown_roundtrip():
    b = ProbabilityBreakdown(
        market_raw_probability=0.80, market_fair_probability=0.82,
        model_probability=0.78, implied_probability=0.80,
        chosen_odd=1.25, bookmaker="Pinnacle",
        timestamp="2030-01-01T09:55:00Z", origin="test",
    )
    assert b.model_minus_market == pytest.approx(-0.02)
    assert b.model_minus_fair == pytest.approx(-0.04)
    assert b.fair_minus_raw == pytest.approx(0.02)
    d = b.to_dict()
    assert d["market_raw_probability"] == 0.80
    assert d["model_minus_market"] == pytest.approx(-0.02)


def test_probability_out_of_range_raises():
    with pytest.raises(ValueError, match="market_raw"):
        ProbabilityBreakdown(market_raw_probability=1.5)
    with pytest.raises(ValueError, match="model"):
        ProbabilityBreakdown(model_probability=0.0)
    with pytest.raises(ValueError, match="market_fair"):
        ProbabilityBreakdown(market_fair_probability=-0.1)


def test_implied_must_mirror_chosen_odd():
    with pytest.raises(ValueError, match="implied"):
        ProbabilityBreakdown(
            market_raw_probability=0.5, implied_probability=0.9,
            chosen_odd=2.0,
        )


def test_chosen_odd_must_be_valid():
    with pytest.raises(ValueError, match="chosen_odd"):
        ProbabilityBreakdown(chosen_odd=0.9)


# ==========================================================================
# model_minus_market: None != 0
# ==========================================================================


def test_model_minus_market_none_when_either_side_missing():
    only_model = ProbabilityBreakdown(model_probability=0.5)
    only_market = ProbabilityBreakdown(market_raw_probability=0.5)
    assert only_model.model_minus_market is None
    assert only_market.model_minus_market is None
    assert only_model.model_minus_fair is None


def test_model_minus_market_zero_is_legitimate():
    both = ProbabilityBreakdown(
        market_raw_probability=0.5, model_probability=0.5)
    assert both.model_minus_market == 0.0


# ==========================================================================
# construção a partir de linhas canônicas — sem cruzamento de fontes
# ==========================================================================


def test_breakdown_from_bet_fills_each_source_from_its_own_field():
    bet = {
        "odd": 1.25, "fair": 0.82, "p_model": 0.78,
        "d": "2030-02-02", "lg": "E0",
    }
    b = breakdown_from_bet(bet)
    assert b.market_raw_probability == pytest.approx(1.0 / 1.25)
    assert b.market_fair_probability == pytest.approx(0.82)
    assert b.model_probability == pytest.approx(0.78)
    assert b.chosen_odd == 1.25
    assert b.origin == "canonical_bet+model"


def test_breakdown_from_bet_without_model_leaves_model_none():
    """Sem p_model no dicionário, model fica None — nunca preenchido
    com market (a pergunta 'o que o modelo acha' fica sem resposta)."""
    bet = {"odd": 1.25, "fair": 0.82, "d": "2030-02-02"}
    b = breakdown_from_bet(bet)
    assert b.model_probability is None
    assert b.model_minus_market is None
    assert b.market_raw_probability is not None
    assert b.origin == "canonical_bet"


def test_breakdown_from_bet_invalid_model_probability_raises():
    bet = {"odd": 1.25, "fair": 0.82, "p_model": 1.5}
    with pytest.raises(ValueError, match="model"):
        breakdown_from_bet(bet)


def test_breakdown_from_bet_without_fair():
    bet = {"odd": 2.0}
    b = breakdown_from_bet(bet)
    assert b.market_fair_probability is None
    assert b.market_raw_probability == 0.5
    assert b.fair_minus_raw is None


# ==========================================================================
# construção a partir de sinais
# ==========================================================================


def test_breakdown_from_signal_sources_separate():
    signal = {
        "best_odd": 2.10, "fair_odd": 2.00, "model_prob": 0.45,
        "best_book": "Pinnacle", "kickoff": "2030-01-01T12:00:00Z",
    }
    b = breakdown_from_signal(signal)
    assert b.market_raw_probability == pytest.approx(1.0 / 2.10)
    assert b.market_fair_probability == pytest.approx(0.50)
    assert b.model_probability == pytest.approx(0.45)
    assert b.bookmaker == "Pinnacle"
    assert b.timestamp == "2030-01-01T12:00:00Z"
    assert b.origin == "signal"


def test_breakdown_from_signal_without_model_and_fair():
    signal = {"best_odd": 2.0, "best_book": "Bet365"}
    b = breakdown_from_signal(signal)
    assert b.model_probability is None
    assert b.market_fair_probability is None
    assert b.market_raw_probability == 0.5
    assert b.model_minus_market is None


# ==========================================================================
# separação estrutural: market nunca "vira" model
# ==========================================================================


def test_market_and_model_are_distinct_fields_not_interchangeable():
    """O contrato não oferece caminho para converter uma fonte na outra:
    model_minus_market existe exatamente para medir a diferença SEM
    fundi-las."""
    b = ProbabilityBreakdown(
        market_raw_probability=0.80, market_fair_probability=0.79,
        model_probability=0.60,
    )
    # as três fontes permanecem independentes e legíveis
    assert (b.market_raw_probability, b.market_fair_probability,
            b.model_probability) == (0.80, 0.79, 0.60)
    # a diferença é medida, nunca aplicada
    assert b.model_minus_market == pytest.approx(-0.20)
    assert b.market_raw_probability == 0.80  # inalterada
