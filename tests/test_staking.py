"""Testes de `betgsn.staking` — alavancagem, Kelly e risco de ruina.

O que precisa ficar provado:
  - a fracao de Kelly esta correta para a vantagem validada;
  - o crescimento tem MAXIMO no Kelly e fica NEGATIVO acima do dobro;
  - a probabilidade de ruina cresce com a agressao;
  - o plano faseado respeita as faixas;
  - o disjuntor e a parada funcionam;
  - a incerteza da vantagem aumenta o risco (o ponto central).
"""

from __future__ import annotations

import math

import pytest

from betgsn.staking import (
    BETS_PER_YEAR,
    EDGE_ODD,
    EDGE_ROI,
    EDGE_SE,
    PLANS,
    Phase,
    StakingPlan,
    annual_growth,
    bet_variance,
    conservative_roi,
    decide_bet,
    drawdown_probability,
    flat_plan,
    full_kelly,
    growth_rate,
    kelly_table,
    recommend,
    simulate,
    win_prob,
)


# --------------------------------------------------------------------------
# Matematica basica
# --------------------------------------------------------------------------


def test_win_prob_from_roi():
    """roi = p*odd - 1  =>  p = (1+roi)/odd."""
    assert win_prob(0.016, 1.21) == pytest.approx(1.016 / 1.21)
    # sem vantagem, a prob e 1/odd
    assert win_prob(0.0, 2.0) == pytest.approx(0.5)


def test_win_prob_is_clamped():
    assert win_prob(10.0, 1.01) <= 0.99
    assert win_prob(-5.0, 100.0) >= 0.01


def test_bet_variance_positive():
    p = win_prob(EDGE_ROI, EDGE_ODD)
    assert bet_variance(p, EDGE_ODD) > 0


def test_full_kelly_matches_classic_formula():
    """f* = (b*p - q) / b — a solucao exata, nao a aproximacao EV/var."""
    from betgsn.engine import kelly_fraction

    p = win_prob(EDGE_ROI, EDGE_ODD)
    assert full_kelly() == pytest.approx(kelly_fraction(p, EDGE_ODD))


def test_full_kelly_is_plausible():
    """Com ROI de 1,6% a odd 1,21, Kelly fica perto de 7,6%."""
    fk = full_kelly()
    assert 0.06 < fk < 0.09, f"Kelly fora do esperado: {fk}"


def test_full_kelly_zero_without_edge():
    assert full_kelly(roi=0.0) == pytest.approx(0.0)
    assert full_kelly(roi=-0.05) == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Crescimento: o teto matematico
# --------------------------------------------------------------------------


def test_growth_is_maximized_at_full_kelly():
    """Kelly completo e o ponto de crescimento maximo."""
    fk = full_kelly()
    g_kelly = growth_rate(fk)
    for mult in (0.5, 0.75, 0.9, 1.1, 1.25, 1.5):
        assert growth_rate(fk * mult) < g_kelly, (
            f"crescimento em {mult}x Kelly deveria ser menor"
        )


def test_growth_is_positive_below_kelly():
    fk = full_kelly()
    for mult in (0.1, 0.25, 0.5, 0.75, 1.0):
        assert growth_rate(fk * mult) > 0


def test_growth_goes_negative_above_double_kelly():
    """Acima do dobro do Kelly, apostar mais faz PERDER mais rapido."""
    fk = full_kelly()
    assert growth_rate(fk * 2.0) < 0
    assert growth_rate(fk * 3.0) < 0


def test_growth_is_monotonic_after_the_peak():
    """Depois do pico, mais aposta = menos crescimento, sempre."""
    fk = full_kelly()
    vals = [growth_rate(fk * m) for m in (1.0, 1.25, 1.5, 1.75, 2.0)]
    assert vals == sorted(vals, reverse=True), f"nao monotonico: {vals}"


def test_annual_growth_scales_with_bets():
    fk = full_kelly()
    g1 = annual_growth(fk, bets=100)
    g2 = annual_growth(fk, bets=200)
    assert g2 == pytest.approx(g1 * 2)


def test_growth_never_exceeds_kelly_ceiling():
    """O teto de crescimento e modesto — nao da para exagerar."""
    fk = full_kelly()
    anual = math.expm1(annual_growth(fk)) * 100
    assert anual < 25.0, f"crescimento anual irreal: {anual}%"
    assert anual > 5.0, f"crescimento anual baixo demais: {anual}%"


def test_growth_handles_total_loss_fraction():
    """Fracao >= 1 quebra a banca: crescimento -infinito."""
    assert growth_rate(1.0) == float("-inf")
    assert growth_rate(1.5) == float("-inf")


# --------------------------------------------------------------------------
# Risco de ruina
# --------------------------------------------------------------------------


def test_drawdown_probability_known_values():
    """Formula classica: P(alfa) = alfa ** ((2-f)/f)."""
    # Kelly completo: 50% de chance de cair a metade
    assert drawdown_probability(1.0, 0.5) == pytest.approx(0.5)
    # meio Kelly: 12,5%
    assert drawdown_probability(0.5, 0.5) == pytest.approx(0.125)
    # quarto de Kelly: ~0,78%
    assert drawdown_probability(0.25, 0.5) == pytest.approx(0.5 ** 7)


def test_drawdown_probability_increases_with_aggression():
    vals = [drawdown_probability(m, 0.5) for m in (0.25, 0.5, 1.0, 1.5)]
    assert vals == sorted(vals), "mais agressao deveria aumentar o risco"


def test_drawdown_probability_deep_loss():
    assert drawdown_probability(1.0, 0.10) == pytest.approx(0.10)
    assert drawdown_probability(0.5, 0.10) == pytest.approx(0.001)
    assert drawdown_probability(0.25, 0.10) == pytest.approx(0.1 ** 7)


def test_drawdown_probability_bounds():
    assert drawdown_probability(0.0, 0.5) == 0.0
    assert drawdown_probability(2.0, 0.5) == 1.0
    assert drawdown_probability(5.0, 0.5) == 1.0


def test_kelly_table_is_ordered_and_sane():
    rows = kelly_table()
    assert len(rows) >= 6
    # o pico de crescimento esta em 1x Kelly
    peak = max(rows, key=lambda r: r["annual_pct"])
    assert peak["kelly_multiple"] == pytest.approx(1.0)
    # risco cresce com a agressao
    risks = [r["p_ruin"] for r in rows]
    assert risks == sorted(risks)
    # acima do otimo o crescimento cai
    above = [r for r in rows if r["kelly_multiple"] > 1.0]
    assert all(r["annual_pct"] < peak["annual_pct"] for r in above)


# --------------------------------------------------------------------------
# Planos
# --------------------------------------------------------------------------


def test_flat_plan_applies_everywhere():
    plan = flat_plan("x", 0.02)
    assert plan.fraction_for(100.0, 1000.0) == 0.02
    assert plan.fraction_for(100000.0, 1000.0) == 0.02
    assert plan.max_fraction == 0.02


def test_phased_plan_respects_thresholds():
    plan = StakingPlan("t", (
        Phase(until_multiple=2.0, fraction=0.05),
        Phase(until_multiple=5.0, fraction=0.03),
        Phase(until_multiple=1e9, fraction=0.015),
    ))
    start = 1000.0
    assert plan.fraction_for(1000.0, start) == 0.05    # 1.0x
    assert plan.fraction_for(1999.0, start) == 0.05    # < 2x
    assert plan.fraction_for(2000.0, start) == 0.03    # 2x
    assert plan.fraction_for(4999.0, start) == 0.03
    assert plan.fraction_for(5000.0, start) == 0.015   # 5x
    assert plan.fraction_for(99999.0, start) == 0.015


def test_phased_plan_decreases_fraction():
    plan = recommend()
    fracs = [p.fraction for p in plan.phases]
    assert fracs == sorted(fracs, reverse=True), "as fracoes devem diminuir"


def test_recommended_plan_has_protections():
    plan = recommend()
    assert plan.drawdown_cut > 0, "precisa de disjuntor"
    assert plan.stop_loss > 0, "precisa de parada"
    assert plan.max_fraction < full_kelly(), (
        "o plano nao pode comecar acima do Kelly"
    )


# --------------------------------------------------------------------------
# Simulacao
# --------------------------------------------------------------------------


def test_simulation_is_deterministic():
    plan = PLANS["moderado"]
    a = simulate(plan, years=1.0, n_paths=2000)
    b = simulate(plan, years=1.0, n_paths=2000)
    assert a == b


def test_more_aggression_means_more_ruin():
    """O ponto central: agressao compra crescimento com ruina."""
    cons = simulate(PLANS["conservador"], years=3.0, n_paths=8000)
    agre = simulate(PLANS["agressivo"], years=3.0, n_paths=8000)
    assert agre.median_multiple > cons.median_multiple
    assert agre.p_halve > cons.p_halve


def test_overkelly_is_worse_than_moderate():
    """Apostar 15% (o que quem persegue retorno faz) e PIOR que 4%."""
    mod = simulate(PLANS["agressivo"], years=3.0, n_paths=8000)
    over = simulate(PLANS["sobrekelly"], years=3.0, n_paths=8000)
    assert over.median_multiple < mod.median_multiple, (
        "sobre-apostar deveria render MENOS, nao mais"
    )
    assert over.p_ruin > mod.p_ruin
    assert over.p_ruin > 0.10, "sobre-Kelly deveria ter ruina alta"


def test_full_kelly_has_high_drawdown_risk():
    """Kelly cheio da o maximo crescimento e paga caro por isso."""
    r = simulate(PLANS["kelly"], years=3.0, n_paths=8000)
    assert r.p_halve > 0.10
    assert r.p_ruin > 0.0


def test_stop_loss_bounds_the_loss():
    """A parada impede perda catastrofica."""
    sem = StakingPlan("sem", (Phase(1e9, 0.10),))
    com = StakingPlan("com", (Phase(1e9, 0.10),), stop_loss=0.50)
    a = simulate(sem, years=2.0, n_paths=8000)
    b = simulate(com, years=2.0, n_paths=8000)
    assert b.p_ruin <= a.p_ruin
    assert b.p5 >= a.p5, "a parada deveria melhorar o pior caso"


def test_drawdown_cut_reduces_variance():
    sem = StakingPlan("sem", (Phase(1e9, 0.05),))
    com = StakingPlan("com", (Phase(1e9, 0.05),), drawdown_cut=0.25)
    a = simulate(sem, years=3.0, n_paths=8000)
    b = simulate(com, years=3.0, n_paths=8000)
    assert b.p_halve <= a.p_halve


def test_edge_uncertainty_increases_risk():
    """Sem incerteza na vantagem, o risco e bem menor.

    Este e o ponto que torna a alavancagem perigosa: a vantagem estimada
    pode ser menor que a real, e apostar pela estimativa pontual vira
    sobre-aposta.

    Detalhe contraintuitivo: a MEDIA pode ate SUBIR com a incerteza. Os
    caminhos de sorte (vantagem alta) sao amplificados e puxam a media
    para cima — efeito loteria. Mas o que voce VIVE nao e a media: e a
    mediana e o pior caso. Esses pioram.
    """
    plan = PLANS["kelly"]
    certo = simulate(plan, years=3.0, n_paths=8000, edge_se=0.0)
    incerto = simulate(plan, years=3.0, n_paths=8000, edge_se=EDGE_SE)

    # o risco de ruina sobe
    assert incerto.p_ruin > certo.p_ruin
    # o pior caso (percentil 5) piora
    assert incerto.p5 < certo.p5
    # a chance de cair a metade sobe
    assert incerto.p_halve > certo.p_halve
    # e a mediana nao melhora
    assert incerto.median_multiple <= certo.median_multiple * 1.01


def test_simulation_without_edge_loses():
    """Sem vantagem real, qualquer fracao perde (a margem da casa)."""
    r = simulate(PLANS["moderado"], years=3.0, n_paths=4000, edge=-0.02)
    assert r.p_profit < 0.30
    assert r.median_multiple < 1.0


def test_simulation_metrics_are_consistent():
    r = simulate(PLANS["faseado_disjuntor"], years=3.0, n_paths=4000)
    assert 0.0 <= r.p_profit <= 1.0
    assert 0.0 <= r.p_halve <= 1.0
    assert 0.0 <= r.p_ruin <= 1.0
    assert r.p5 <= r.median_multiple <= r.p95
    assert r.p_ruin <= r.p_halve


def test_simulation_years_affect_growth():
    um = simulate(PLANS["moderado"], years=1.0, n_paths=4000)
    tres = simulate(PLANS["moderado"], years=3.0, n_paths=4000)
    assert tres.median_multiple > um.median_multiple


def test_bets_per_year_default_is_realistic():
    assert BETS_PER_YEAR >= 100


# --------------------------------------------------------------------------
# NO BET
# --------------------------------------------------------------------------


def test_conservative_roi_is_below_point_estimate():
    assert conservative_roi(EDGE_ROI, EDGE_SE) < EDGE_ROI
    assert conservative_roi(EDGE_ROI, 0.0) == EDGE_ROI
    with pytest.raises(ValueError):
        conservative_roi(0.01, -0.001)


def test_no_bet_when_evidence_is_exploratory():
    """Preco sem timestamp de publicacao nao sustenta dinheiro real."""
    decision = decide_bet(
        EDGE_ROI, EDGE_SE, EDGE_ODD, evidence_status="exploratory", n_bets=6748,
    )
    assert decision.action == "NO_BET"
    assert not decision.should_bet
    assert decision.fraction == 0.0
    assert "evidencia_confiavel" in decision.reason


def test_no_bet_when_lower_bound_is_not_positive():
    decision = decide_bet(
        0.001, 0.01, 2.0, evidence_status="timestamped", n_bets=5000,
    )
    assert decision.action == "NO_BET"
    assert "limite_inferior_positivo" in decision.reason


def test_no_bet_when_sample_is_too_small():
    decision = decide_bet(
        0.05, 0.005, 2.0, evidence_status="timestamped", n_bets=100,
    )
    assert decision.action == "NO_BET"
    assert "amostra_suficiente" in decision.reason


def test_bet_when_evidence_supports_it():
    decision = decide_bet(
        EDGE_ROI, EDGE_SE, EDGE_ODD, evidence_status="timestamped", n_bets=6748,
    )
    assert decision.action == "BET"
    assert decision.conservative_roi > 0
    assert 0 < decision.fraction <= 0.05


def test_bet_decision_serializes_checks():
    decision = decide_bet(
        EDGE_ROI, EDGE_SE, EDGE_ODD, evidence_status="timestamped", n_bets=6748,
    )
    payload = decision.to_dict()
    names = {c["name"] for c in payload["checks"]}
    assert names == {
        "evidencia_confiavel", "limite_inferior_positivo",
        "amostra_suficiente", "ruina_toleravel",
    }
    assert all(c["detail"] for c in payload["checks"])


def test_no_bet_plan_never_stakes():
    plan = PLANS["no_bet"]
    assert plan.fraction_for(1000.0, 1000.0) == 0.0
    assert plan.max_fraction == 0.0
    result = simulate(plan, years=1.0, n_paths=500)
    assert result.median_multiple == pytest.approx(1.0)
    assert result.p_ruin == 0.0
