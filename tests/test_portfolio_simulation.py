"""Testes de betgsn.portfolio.simulation — Monte Carlo, robustez e slippage."""
from __future__ import annotations

import pytest

from betgsn.portfolio.simulation import (
    DEFAULT_HAIRCUTS,
    PortfolioBet,
    PortfolioSimulation,
    RobustnessReport,
    apply_haircut,
    apply_slippage,
    effective_number_of_bets,
    model_error_simulation,
    simulate_portfolio,
    slippage_scenarios,
)

RUNS = 2000


def bet(
    label: str = "b1",
    probability: float = 0.6,
    odd: float = 1.9,
    stake: float = 10.0,
    **kwargs,
) -> PortfolioBet:
    return PortfolioBet(
        label=label, probability=probability, odd=odd, stake=stake, **kwargs
    )


def positive_ev_portfolio(n: int = 5) -> list[PortfolioBet]:
    # p=0.6, odd=1.9 -> EV = 0.14 por unidade apostada.
    return [
        bet(label=f"pos{i}", probability=0.6, odd=1.9, stake=10.0, match=f"m{i}")
        for i in range(n)
    ]


def negative_ev_portfolio(n: int = 5) -> list[PortfolioBet]:
    # p=0.4, odd=1.9 -> EV = -0.24 por unidade apostada.
    return [
        bet(label=f"neg{i}", probability=0.4, odd=1.9, stake=20.0, match=f"m{i}")
        for i in range(n)
    ]


# --------------------------------------------------------------- PortfolioBet


@pytest.mark.parametrize("bad_probability", [-0.01, 1.01, 2.0, -1.0])
def test_bet_rejects_probability_outside_unit_interval(bad_probability):
    with pytest.raises(ValueError):
        bet(probability=bad_probability)


@pytest.mark.parametrize("edge_probability", [0.0, 1.0])
def test_bet_accepts_probability_boundaries(edge_probability):
    assert bet(probability=edge_probability).probability == edge_probability


@pytest.mark.parametrize("bad_odd", [1.0, 0.99, 0.0, -1.5])
def test_bet_rejects_odd_not_above_one(bad_odd):
    with pytest.raises(ValueError):
        bet(odd=bad_odd)


def test_bet_rejects_negative_stake():
    with pytest.raises(ValueError):
        bet(stake=-1.0)


def test_bet_accepts_zero_stake():
    assert bet(stake=0.0).stake == 0.0


def test_bet_ev():
    assert bet(probability=0.6, odd=1.9).ev == pytest.approx(0.14)
    assert bet(probability=0.5, odd=2.0).ev == pytest.approx(0.0)
    assert bet(probability=0.4, odd=1.9).ev == pytest.approx(-0.24)


# --------------------------------------------------------------- validacao


def test_simulate_rejects_empty_bets():
    with pytest.raises(ValueError):
        simulate_portfolio([], runs=RUNS)


@pytest.mark.parametrize("bad_bankroll", [0.0, -1.0, -1000.0])
def test_simulate_rejects_non_positive_bankroll(bad_bankroll):
    with pytest.raises(ValueError):
        simulate_portfolio(positive_ev_portfolio(), initial_bankroll=bad_bankroll, runs=RUNS)


@pytest.mark.parametrize("bad_runs", [0, -1, -500])
def test_simulate_rejects_non_positive_runs(bad_runs):
    with pytest.raises(ValueError):
        simulate_portfolio(positive_ev_portfolio(), runs=bad_runs)


# --------------------------------------------------------------- determinismo


def test_same_seed_gives_identical_results():
    bets = positive_ev_portfolio()
    a = simulate_portfolio(bets, runs=RUNS, seed=123)
    b = simulate_portfolio(bets, runs=RUNS, seed=123)
    assert a.to_dict() == b.to_dict()


def test_different_seed_gives_different_results():
    bets = positive_ev_portfolio()
    a = simulate_portfolio(bets, runs=RUNS, seed=123)
    b = simulate_portfolio(bets, runs=RUNS, seed=999)
    assert a.mean_final_bankroll != b.mean_final_bankroll


# --------------------------------------------------------------- resultados


def test_positive_ev_portfolio_grows():
    sim = simulate_portfolio(positive_ev_portfolio(), runs=RUNS, seed=7)

    assert sim.expected_log_growth > 0
    assert sim.probability_of_profit > 0.5
    assert sim.mean_final_bankroll > sim.initial_bankroll


def test_negative_ev_portfolio_shrinks():
    sim = simulate_portfolio(negative_ev_portfolio(), runs=RUNS, seed=7)

    assert sim.expected_log_growth < 0
    assert sim.mean_final_bankroll < sim.initial_bankroll


def test_simulation_reports_shape():
    bets = positive_ev_portfolio(3)
    sim = simulate_portfolio(bets, initial_bankroll=1000.0, runs=RUNS, seed=7)

    assert isinstance(sim, PortfolioSimulation)
    assert sim.runs == RUNS
    assert sim.n_bets == 3
    assert sim.total_staked == pytest.approx(30.0)
    assert sim.n_unique_matches == 3
    assert sim.p05 <= sim.p25 <= sim.p75 <= sim.p95
    assert 0.0 <= sim.probability_of_profit <= 1.0
    assert 0.0 <= sim.probability_of_ruin <= 1.0
    assert sim.probability_of_25pct_drawdown >= sim.probability_of_50pct_drawdown


def test_haircut_is_recorded_and_reduces_growth():
    bets = positive_ev_portfolio()
    clean = simulate_portfolio(bets, runs=RUNS, seed=7, haircut=0.0)
    cut = simulate_portfolio(bets, runs=RUNS, seed=7, haircut=0.10)

    assert clean.haircut == 0.0
    assert cut.haircut == 0.10
    assert cut.expected_log_growth < clean.expected_log_growth


def test_correlation_group_shares_the_draw():
    # Mesmo grupo: todas ganham juntas ou perdem juntas -> cauda mais larga.
    grouped = [
        bet(label=f"g{i}", probability=0.6, odd=1.9, stake=50.0,
            match=f"m{i}", correlation_group="same")
        for i in range(4)
    ]
    independent = [
        bet(label=f"i{i}", probability=0.6, odd=1.9, stake=50.0, match=f"m{i}")
        for i in range(4)
    ]

    g = simulate_portfolio(grouped, runs=RUNS, seed=7)
    i = simulate_portfolio(independent, runs=RUNS, seed=7)

    # Compartilhando o sorteio, o portfolio vira "tudo ou nada": ou as 4
    # vencem, ou as 4 perdem. A distribuicao fica mais larga nas duas pontas
    # e a diversificacao desaparece.
    assert g.p05 < i.p05
    assert (g.p95 - g.p05) > (i.p95 - i.p05)
    assert g.effective_number_of_bets == pytest.approx(1.0)
    assert i.effective_number_of_bets == pytest.approx(4.0)

    # Apenas dois desfechos possiveis quando o sorteio e compartilhado.
    assert g.median_final_bankroll in (g.p05, g.p95)


# --------------------------------------------------- effective_number_of_bets


def test_effective_number_of_bets_three_independent_matches():
    bets = [
        bet(label=f"b{i}", stake=10.0, match=f"match-{i}")
        for i in range(3)
    ]
    assert effective_number_of_bets(bets) == pytest.approx(3.0)


def test_effective_number_of_bets_same_correlation_group():
    bets = [
        bet(label=f"b{i}", stake=10.0, match=f"match-{i}", correlation_group="same")
        for i in range(3)
    ]
    assert effective_number_of_bets(bets) == pytest.approx(1.0)


def test_effective_number_of_bets_empty_and_zero_stake():
    assert effective_number_of_bets([]) == 0.0
    assert effective_number_of_bets([bet(stake=0.0)]) == 0.0


def test_effective_number_of_bets_reflects_concentration():
    concentrated = [
        bet(label="big", stake=90.0, match="m1"),
        bet(label="small", stake=10.0, match="m2"),
    ]
    value = effective_number_of_bets(concentrated)
    assert 1.0 < value < 2.0  # nem 1 aposta, nem 2 iguais


def test_simulation_exposes_effective_number_of_bets():
    bets = [
        bet(label=f"b{i}", stake=10.0, match=f"m{i}", correlation_group="same")
        for i in range(3)
    ]
    sim = simulate_portfolio(bets, runs=500, seed=7)
    assert sim.effective_number_of_bets == pytest.approx(1.0)


# --------------------------------------------------------------- haircut


def test_apply_haircut_reduces_multiplicatively():
    assert apply_haircut(0.60, 0.10) == pytest.approx(0.54)
    assert apply_haircut(0.50, 0.02) == pytest.approx(0.49)
    assert apply_haircut(0.60, 0.0) == pytest.approx(0.60)


@pytest.mark.parametrize("bad_haircut", [-0.01, 1.0, 1.5, -1.0])
def test_apply_haircut_rejects_out_of_range(bad_haircut):
    with pytest.raises(ValueError):
        apply_haircut(0.5, bad_haircut)


def test_apply_haircut_stays_within_unit_interval():
    assert 0.0 <= apply_haircut(1.0, 0.99) <= 1.0
    assert apply_haircut(0.0, 0.5) == 0.0


# --------------------------------------------------------------- robustez


def test_model_error_simulation_one_scenario_per_haircut():
    haircuts = (0.0, 0.02, 0.05, 0.10)
    report = model_error_simulation(
        positive_ev_portfolio(), runs=RUNS, haircuts=haircuts, seed=7
    )

    assert isinstance(report, RobustnessReport)
    assert len(report.scenarios) == len(haircuts)
    assert report.haircuts == haircuts
    assert [s.haircut for s in report.scenarios] == list(haircuts)


def test_model_error_growth_decreases_monotonically():
    haircuts = (0.0, 0.02, 0.05, 0.10, 0.20)
    report = model_error_simulation(
        positive_ev_portfolio(), runs=RUNS, haircuts=haircuts, seed=7
    )

    growths = [s.expected_log_growth for s in report.scenarios]
    assert growths == sorted(growths, reverse=True)
    assert all(a > b for a, b in zip(growths, growths[1:]))


def test_model_error_default_haircuts():
    report = model_error_simulation(positive_ev_portfolio(), runs=500, seed=7)
    assert report.haircuts == DEFAULT_HAIRCUTS


def test_robustness_score_one_when_all_survive():
    report = model_error_simulation(
        positive_ev_portfolio(), runs=RUNS, haircuts=(0.0, 0.01, 0.02), seed=7
    )
    assert all(s.expected_log_growth > 0 for s in report.scenarios)
    assert report.robustness_score == 1.0
    assert report.breakeven_haircut is None


def test_robustness_score_zero_when_none_survive():
    report = model_error_simulation(
        negative_ev_portfolio(), runs=RUNS, haircuts=(0.0, 0.05, 0.10), seed=7
    )
    assert all(s.expected_log_growth <= 0 for s in report.scenarios)
    assert report.robustness_score == 0.0


def test_robustness_score_empty_report():
    assert RobustnessReport().robustness_score == 0.0
    assert RobustnessReport().breakeven_haircut is None


def test_breakeven_haircut_is_first_non_positive():
    report = model_error_simulation(
        negative_ev_portfolio(), runs=RUNS, haircuts=(0.0, 0.05, 0.10), seed=7
    )
    assert report.breakeven_haircut == 0.0


def test_breakeven_haircut_partial_survival():
    # EV fino: sobrevive sem corte, quebra com corte grande.
    bets = [
        bet(label=f"t{i}", probability=0.53, odd=1.95, stake=25.0, match=f"m{i}")
        for i in range(6)
    ]
    haircuts = (0.0, 0.01, 0.02, 0.03, 0.05, 0.10, 0.20, 0.30)
    report = model_error_simulation(bets, runs=RUNS, haircuts=haircuts, seed=7)

    breakeven = report.breakeven_haircut
    survivors = [s for s in report.scenarios if s.expected_log_growth > 0]

    if breakeven is None:
        assert len(survivors) == len(report.scenarios)
        assert report.robustness_score == 1.0
    else:
        assert breakeven in haircuts
        failing = [s.haircut for s in report.scenarios if s.expected_log_growth <= 0]
        assert breakeven == min(failing)
        assert 0.0 <= report.robustness_score <= 1.0


def test_robustness_report_to_dict():
    report = model_error_simulation(
        positive_ev_portfolio(), runs=500, haircuts=(0.0, 0.05), seed=7
    )
    payload = report.to_dict()

    assert payload["haircuts_tested"] == [0.0, 0.05]
    assert len(payload["scenarios"]) == 2
    assert "robustness_definition" in payload
    assert 0.0 <= payload["robustness_score"] <= 1.0


# --------------------------------------------------------------- slippage


def test_apply_slippage_reduces_odd():
    assert apply_slippage(2.00, 0.02) == pytest.approx(1.96)
    assert apply_slippage(2.00, 0.0) == pytest.approx(2.00)


def test_apply_slippage_never_returns_one_or_less():
    result = apply_slippage(1.01, 0.99)
    assert result > 1.0
    assert result == pytest.approx(1.0001)


@pytest.mark.parametrize("bad_slippage", [-0.01, 1.0, 2.0])
def test_apply_slippage_rejects_out_of_range(bad_slippage):
    with pytest.raises(ValueError):
        apply_slippage(2.0, bad_slippage)


def test_apply_slippage_output_is_always_valid_odd():
    # O resultado precisa continuar sendo construivel como PortfolioBet.
    for slip in (0.0, 0.5, 0.9, 0.99):
        degraded = apply_slippage(1.01, slip)
        assert bet(odd=degraded).odd == degraded


def test_slippage_scenarios_mean_ev_decreases():
    slippages = (0.0, 0.01, 0.02, 0.05)
    results = slippage_scenarios(
        positive_ev_portfolio(), slippages=slippages, runs=RUNS, seed=7
    )

    assert len(results) == len(slippages)
    assert [r["slippage"] for r in results] == list(slippages)

    evs = [r["mean_ev"] for r in results]
    assert all(a > b for a, b in zip(evs, evs[1:]))


def test_slippage_scenarios_growth_decreases():
    results = slippage_scenarios(
        positive_ev_portfolio(), slippages=(0.0, 0.05, 0.10), runs=RUNS, seed=7
    )
    growths = [r["expected_log_growth"] for r in results]
    assert all(a > b for a, b in zip(growths, growths[1:]))


def test_slippage_scenarios_keys():
    results = slippage_scenarios(
        positive_ev_portfolio(2), slippages=(0.0,), runs=500, seed=7
    )
    assert set(results[0]) == {
        "slippage",
        "mean_ev",
        "expected_log_growth",
        "median_final_bankroll",
        "probability_of_profit",
    }


def test_slippage_scenarios_preserves_bet_metadata():
    # Se os metadados fossem perdidos, a concentracao seria recalculada errado.
    bets = [
        bet(label=f"b{i}", stake=10.0, match=f"m{i}", correlation_group="same")
        for i in range(3)
    ]
    results = slippage_scenarios(bets, slippages=(0.0,), runs=500, seed=7)
    assert len(results) == 1
    assert results[0]["mean_ev"] == pytest.approx(bets[0].ev)
