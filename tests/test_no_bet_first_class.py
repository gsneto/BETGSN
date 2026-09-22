"""I-13 — NO_BET e primeira classe: nunca vira aposta.

O contrato testado aqui atravessa as camadas:

    decisao (staking.BetDecision)
      -> portfolio (enforce_decision / decide_portfolio)
      -> exposicao (check_exposure sobre stakes efetivas)

Regras:

- BET                         -> stake positiva (fracao de banca > 0);
- NO_BET                      -> stake zero, SEM excecao;
- NO_BET por falta de evidencia -> stake zero;
- NO_BET por promotion gate   -> stake zero (o gate bloqueia a
  elegibilidade, a evidencia continua exploratoria, o Quant responde
  NO_BET);
- NO_BET chegando ao portfolio -> nenhuma exposicao criada.
"""
from __future__ import annotations

import pytest

from betgsn.models.promotion import ModelStatus, SegmentResult, evaluate_promotion
from betgsn.portfolio.policy import decide_portfolio, enforce_decision
from betgsn.portfolio.risk import check_exposure, ExposureLimits
from betgsn.portfolio.simulation import PortfolioBet
from betgsn.staking import BetDecision, decide_bet


def _bets(n=5, stake=10.0):
    return [
        PortfolioBet(f"bet{i}", 0.55, 1.9, stake, f"m{i}",
                     league=f"L{i}", market="1x2")
        for i in range(n)
    ]


def _bet_decision():
    return decide_bet(0.016, 0.0054, 1.21,
                      evidence_status="timestamped", n_bets=6748)


def _no_bet_decision(reason="teste"):
    return BetDecision(action="NO_BET", reason=reason)


# ------------------------------------------------- contrato do dataclass


def test_bet_decision_rejects_no_bet_with_positive_fraction():
    """NO_BET com stake e inconsistencia: o construtor recusa."""
    with pytest.raises(ValueError, match="NO_BET nao cria stake"):
        BetDecision(action="NO_BET", reason="x", fraction=0.05)


def test_bet_decision_rejects_unknown_action():
    with pytest.raises(ValueError, match="BET ou NO_BET"):
        BetDecision(action="TALVEZ", reason="x")


def test_bet_decision_allows_bet_with_fraction():
    d = BetDecision(action="BET", reason="ok", fraction=0.02)
    assert d.should_bet is True
    assert d.fraction > 0


def test_with_zero_stake_normalizes_transported_no_bet():
    """Rede de seguranca para decisao que chegou de fora inconsistente."""
    # o construtor recusa fraction>0; um dado transportado (desserializado)
    # pode chegar inconsistente — simulamos exatamente isso
    transported = BetDecision(action="NO_BET", reason="x")
    object.__setattr__(transported, "fraction", 0.03)
    fixed = transported.with_zero_stake()
    assert fixed.action == "NO_BET"
    assert fixed.fraction == 0.0


def test_bet_path_has_positive_stake():
    """O caminho positivo continua positivo: o hardening nao quebra BET."""
    d = _bet_decision()
    assert d.action == "BET"
    assert d.fraction > 0
    assert d.should_bet is True


# ------------------------------------------------- enforce_decision


def test_enforce_decision_zeros_all_stakes_on_no_bet():
    bets = _bets(stake=25.0)
    effective = enforce_decision(bets, _no_bet_decision())
    assert len(effective) == len(bets)
    assert all(b.stake == 0.0 for b in effective)
    # os objetos originais nao sao mutados
    assert all(b.stake == 25.0 for b in bets)


def test_enforce_decision_keeps_stakes_on_bet():
    bets = _bets(stake=25.0)
    effective = enforce_decision(bets, _bet_decision())
    assert [b.stake for b in effective] == [25.0] * len(bets)


def test_enforce_decision_without_decision_is_passthrough():
    bets = _bets()
    assert enforce_decision(bets, None) == list(bets)


# ------------------------------------------------- decide_portfolio


def test_portfolio_respects_no_bet_from_quant():
    """NO_BET do Quant interrompe o portfolio: nem simulacao roda."""
    decision = decide_portfolio(
        _bets(), evidence_status="timestamped", runs=2000,
        decision=_no_bet_decision("evidencia insuficiente"),
    )
    assert decision.action == "NO_BET"
    assert decision.fraction_of_bankroll == 0.0
    assert decision.should_bet is False
    assert "decisao_do_quant" in decision.reason
    names = {c[0] for c in decision.checks}
    assert "decisao_do_quant" in names


def test_no_bet_decision_overrides_even_perfect_portfolio():
    """Nenhuma verificacao local transforma NO_BET em BET."""
    bets = [
        PortfolioBet(f"bet{i}", 0.60, 1.9, 5.0, f"m{i}", league=f"L{i}")
        for i in range(5)
    ]
    decision = decide_portfolio(
        bets, evidence_status="timestamped", runs=2000,
        limits=ExposureLimits(max_total_exposure=1.0, max_single_stake=1.0,
                              max_match_exposure=1.0, max_league_exposure=1.0),
        decision=_no_bet_decision(),
    )
    assert decision.action == "NO_BET"
    assert decision.fraction_of_bankroll == 0.0


def test_bet_from_quant_does_not_approve_portfolio_alone():
    """BET do Quant nao aprova sozinho: a evidencia ainda e verificada."""
    decision = decide_portfolio(
        _bets(), evidence_status="exploratory", runs=1000,
        decision=_bet_decision(),
    )
    assert decision.action == "NO_BET"
    assert "evidencia_confiavel" in decision.reason


def test_bet_from_quant_with_trusted_evidence_still_bet():
    decision = decide_portfolio(
        _bets(), evidence_status="timestamped", runs=2000,
        decision=_bet_decision(),
    )
    assert decision.action == "BET"
    assert decision.fraction_of_bankroll > 0


# ------------------------------------------------- exposicao


def test_no_bet_creates_no_exposure():
    """NO_BET chegando ao portfolio: nenhuma exposicao e criada."""
    bets = _bets(stake=100.0)
    effective = enforce_decision(bets, _no_bet_decision())
    stakes = [
        {"match": b.match, "league": b.league, "stake": b.stake,
         "type": "single"}
        for b in effective
    ]
    report = check_exposure(stakes, 1000.0, ExposureLimits())
    assert report.total_exposure == 0.0
    assert report.total_exposure_pct == 0.0
    assert report.within_limits
    assert all(v == 0.0 for v in report.by_match.values())


def test_bet_exposure_is_positive():
    bets = _bets(stake=10.0)
    effective = enforce_decision(bets, _bet_decision())
    stakes = [
        {"match": b.match, "league": b.league, "stake": b.stake,
         "type": "single"}
        for b in effective
    ]
    report = check_exposure(stakes, 1000.0, ExposureLimits())
    assert report.total_exposure == 50.0


# ------------------------------------------------- cadeia promotion gate


def _segment(league, season, logloss, base_logloss, n=300):
    return SegmentResult(
        league=league, season=season, n_matches=n,
        metrics={"logloss": logloss, "brier": 0.2, "rps": 0.15,
                 "ece": 0.03},
        baseline_metrics={"logloss": base_logloss, "brier": 0.21,
                          "rps": 0.16, "ece": 0.03},
    )


def test_no_bet_by_promotion_gate_yields_zero_stake():
    """Gate bloqueia -> evidencia continua exploratoria -> NO_BET -> zero.

    A cadeia real: o promotion gate nao promove (efeito abaixo da
    margem), o status de evidencia NAO vira "validated", o Quant decide
    NO_BET sobre evidencia exploratoria e o portfolio fica sem
    exposicao.
    """
    # efeito real (~1%) mas abaixo da margem medida (~5%)
    segments = [
        _segment(lg, se, 0.990, 1.000)
        for se in ("2024", "2025") for lg in ("E0", "SP1")
    ]
    gate = evaluate_promotion("Candidato", segments)
    assert gate.recommended_status is not ModelStatus.PRODUCTION
    assert gate.production_eligible is False

    # modelo nao promovido: a evidencia das odds continua exploratoria
    evidence_status = "validated" if gate.production_eligible else "exploratory"
    assert evidence_status == "exploratory"

    quant = decide_bet(0.016, 0.0054, 1.21,
                       evidence_status=evidence_status, n_bets=6748)
    assert quant.action == "NO_BET"
    assert quant.fraction == 0.0

    effective = enforce_decision(_bets(stake=100.0), quant)
    assert all(b.stake == 0.0 for b in effective)
    stakes = [{"match": b.match, "league": b.league, "stake": b.stake,
               "type": "single"} for b in effective]
    assert check_exposure(stakes, 1000.0, ExposureLimits()).total_exposure == 0.0


def test_no_bet_by_lack_of_evidence_yields_zero_stake():
    """NO_BET por falta de evidencia: mesmo caminho, mesmo resultado."""
    quant = decide_bet(0.016, 0.0054, 1.21,
                       evidence_status="exploratory", n_bets=6748)
    assert quant.action == "NO_BET"
    assert "evidencia_confiavel" in quant.reason
    assert quant.fraction == 0.0
    assert all(b.stake == 0.0 for b in enforce_decision(_bets(), quant))
