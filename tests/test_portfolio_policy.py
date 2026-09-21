"""Testes da politica de portfolio: BET vs NO BET.

O ponto central: quando a evidencia e exploratoria (preco sem timestamp de
publicacao), quando o EV conservador nao e positivo ou quando a exposicao
estoura os limites, a resposta tem que ser NO_BET — e a decisao precisa
registrar o motivo.
"""
from __future__ import annotations

import pytest

from betgsn.portfolio.policy import decide_portfolio
from betgsn.portfolio.risk import ExposureLimits
from betgsn.portfolio.simulation import PortfolioBet


def _bets(n=10, probability=0.55, odd=1.9, stake=10.0):
    return [
        PortfolioBet(f"bet{i}", probability, odd, stake,
                     match=f"m{i}", league=f"L{i}", market="1x2")
        for i in range(n)
    ]


def test_exploratory_evidence_forces_no_bet():
    decision = decide_portfolio(_bets(), evidence_status="exploratory", runs=2000)
    assert decision.action == "NO_BET"
    assert "evidencia_confiavel" in decision.reason
    assert decision.should_bet is False


def test_trusted_positive_ev_portfolio_is_approved():
    decision = decide_portfolio(
        _bets(), evidence_status="timestamped", runs=4000,
    )
    assert decision.action == "BET"
    assert decision.expected_log_growth > 0
    assert decision.fraction_of_bankroll > 0


def test_negative_ev_forces_no_bet():
    decision = decide_portfolio(
        _bets(probability=0.50), evidence_status="timestamped", runs=2000,
    )
    assert decision.action == "NO_BET"
    assert "ev_conservador_positivo" in decision.reason


def test_exposure_violation_forces_no_bet():
    bets = _bets(n=1, stake=300.0)
    decision = decide_portfolio(
        bets, evidence_status="timestamped", runs=2000,
        limits=ExposureLimits(max_total_exposure=0.25),
    )
    assert decision.action == "NO_BET"
    assert "exposicao_dentro_dos_limites" in decision.reason


def test_empty_portfolio_forces_no_bet():
    decision = decide_portfolio([], evidence_status="timestamped", runs=1000)
    assert decision.action == "NO_BET"
    assert "carteira_nao_vazia" in decision.reason


def test_decision_serializes_every_check():
    decision = decide_portfolio(
        _bets(), evidence_status="timestamped", runs=2000,
    )
    payload = decision.to_dict()
    assert payload["action"] == "BET"
    names = {c["name"] for c in payload["checks"]}
    assert names == {
        "evidencia_confiavel", "carteira_nao_vazia", "ev_conservador_positivo",
        "crescimento_positivo", "ruina_toleravel", "drawdown_toleravel",
        "exposicao_dentro_dos_limites",
    }
    assert all(c["detail"] for c in payload["checks"])


def test_high_ruin_tolerance_can_block():
    """Aposta agressiva o bastante para estourar a tolerancia de ruina."""
    bets = [
        PortfolioBet(f"bet{i}", 0.52, 2.0, 200.0, match=f"m{i}", league=f"L{i}")
        for i in range(5)
    ]
    decision = decide_portfolio(
        bets, evidence_status="timestamped", runs=4000, max_ruin=0.0,
        limits=ExposureLimits(max_total_exposure=1.0, max_single_stake=1.0,
                              max_match_exposure=1.0, max_league_exposure=1.0),
    )
    assert decision.action == "NO_BET"
    assert "ruina_toleravel" in decision.reason
