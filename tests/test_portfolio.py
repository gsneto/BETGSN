"""Testes do motor de portfólio: correlação, parlays e risco."""
import pytest
import math

from betgsn.portfolio.correlation import (
    intra_match_joint_prob,
    intra_match_correlation,
    inter_match_correlation,
    home_win, draw, away_win, over, under, btts_yes, btts_no,
)
from betgsn.portfolio.parlay import (
    ParlayLeg, ParlayResult, calculate_parlay, best_parlays,
)
from betgsn.portfolio.risk import (
    ExposureLimits, check_exposure, expected_log_growth, risk_of_ruin,
)


# --------------------------------------------------------------------------
# Correlation
# --------------------------------------------------------------------------

def _uniform_matrix(n=9):
    """Uniform score matrix for testing."""
    total = n * n
    return [[1.0 / total] * n for _ in range(n)]

def _realistic_matrix():
    """Simple realistic 5x5 matrix (home advantage)."""
    raw = [
        [0.07, 0.06, 0.04, 0.02, 0.01],
        [0.09, 0.08, 0.05, 0.03, 0.01],
        [0.06, 0.07, 0.04, 0.02, 0.01],
        [0.03, 0.04, 0.03, 0.01, 0.01],
        [0.01, 0.02, 0.01, 0.01, 0.00],
    ]
    total = sum(sum(row) for row in raw)
    return [[c / total for c in row] for row in raw]

def test_joint_prob_sums_correctly():
    m = _uniform_matrix()
    # P(any outcome) = 1
    assert intra_match_joint_prob(m, lambda h, a: True, lambda h, a: True) == pytest.approx(1.0, abs=1e-6)

def test_joint_prob_home_win_and_over():
    m = _realistic_matrix()
    p = intra_match_joint_prob(m, home_win, over(1.5))
    # Home win AND over 1.5 means home scores >= 2 or away scores >= 1
    assert 0 < p < 1

def test_correlation_independent_events():
    m = _uniform_matrix()
    # In uniform distribution, everything is independent
    c = intra_match_correlation(m, home_win, over(2.5))
    assert abs(c.correlation) < 0.1

def test_correlation_same_event_is_one():
    m = _realistic_matrix()
    c = intra_match_correlation(m, home_win, home_win)
    assert c.correlation == pytest.approx(1.0, abs=0.01)

def test_inter_match_is_independent():
    c = inter_match_correlation()
    assert c.correlation == 0.0
    assert c.source == "assumed_independent"

def test_btts_and_over_positive_correlation():
    m = _realistic_matrix()
    c = intra_match_correlation(m, btts_yes, over(2.5))
    assert c.correlation > 0  # BTTS yes and Over 2.5 should be positively correlated


# --------------------------------------------------------------------------
# Parlays
# --------------------------------------------------------------------------

def _leg(match="A vs B", outcome="1", prob=0.6, odd=1.8) -> ParlayLeg:
    return ParlayLeg(match=match, market="1X2", outcome=outcome,
                     model_prob=prob, odd=odd, bookmaker="Test",
                     ev=prob*odd-1, edge=prob-1/odd)

def test_single_leg_parlay():
    result = calculate_parlay([_leg()])
    assert result.n_legs == 1
    assert result.combined_odd == pytest.approx(1.8)
    assert result.joint_probability == pytest.approx(0.6)

def test_two_leg_parlay():
    legs = [_leg("A vs B"), _leg("C vs D", prob=0.7, odd=1.5)]
    result = calculate_parlay(legs)
    assert result.n_legs == 2
    assert result.combined_odd == pytest.approx(1.8 * 1.5)
    assert result.joint_probability == pytest.approx(0.6 * 0.7)

def test_parlay_with_override():
    legs = [_leg("A vs B"), _leg("A vs B", outcome="Over 2.5", prob=0.5, odd=2.0)]
    result = calculate_parlay(legs, joint_prob_override=0.35)
    assert result.joint_probability == pytest.approx(0.35)
    assert result.correlation_adjustment != 1.0

def test_parlay_ev():
    leg = _leg(prob=0.6, odd=1.8)
    result = calculate_parlay([leg])
    assert result.ev == pytest.approx(0.6 * 1.8 - 1.0)

def test_best_parlays_filters():
    legs = [
        _leg("A vs B", prob=0.6, odd=1.8),
        _leg("C vs D", prob=0.7, odd=1.5),
        _leg("E vs F", prob=0.5, odd=2.1),
    ]
    results = best_parlays(legs, max_legs=3, min_legs=2, min_ev=-1.0)
    assert len(results) > 0
    assert all(r.n_legs >= 2 for r in results)

def test_best_parlays_same_match_limit():
    legs = [
        _leg("A vs B", "1", 0.6, 1.8),
        _leg("A vs B", "Over 2.5", 0.5, 2.0),
        _leg("A vs B", "BTTS Sim", 0.55, 1.9),
    ]
    results = best_parlays(legs, max_same_match=2, min_ev=-10.0)
    # 3-leg parlays with all same match should be excluded (max_same_match=2)
    three_leg = [r for r in results if r.n_legs == 3]
    assert len(three_leg) == 0

def test_empty_signals():
    assert best_parlays([], min_legs=2) == []


# --------------------------------------------------------------------------
# Risk
# --------------------------------------------------------------------------

def test_exposure_within_limits():
    stakes = [
        {"match": "A vs B", "league": "PL", "stake": 10, "type": "single"},
        {"match": "C vs D", "league": "PL", "stake": 15, "type": "single"},
    ]
    report = check_exposure(stakes, 1000, ExposureLimits())
    assert report.within_limits
    assert report.total_exposure == 25

def test_exposure_violation():
    stakes = [
        {"match": "A vs B", "league": "PL", "stake": 300, "type": "single"},
    ]
    report = check_exposure(stakes, 1000, ExposureLimits(max_total_exposure=0.25))
    assert not report.within_limits
    assert len(report.violations) > 0

def test_expected_log_growth_positive_edge():
    # Edge exists: prob=0.6, odd=1.8 -> EV=+8%
    g = expected_log_growth(0.6, 1.8, 0.05)
    assert g > 0

def test_expected_log_growth_no_edge():
    # Fair odds: prob=0.5, odd=2.0 -> EV=0
    g = expected_log_growth(0.5, 2.0, 0.05)
    assert g < 0  # minus vig from fractional sizing

def test_expected_log_growth_overbetting():
    # Overbetting (fraction too large) should give negative growth
    g = expected_log_growth(0.6, 1.8, 0.90)
    # g can be very negative
    assert g < expected_log_growth(0.6, 1.8, 0.05)

def test_risk_of_ruin_no_edge():
    r = risk_of_ruin(0.5, 2.0, 0.05)
    assert r == 1.0  # guaranteed ruin with no edge

def test_risk_of_ruin_with_edge():
    r = risk_of_ruin(0.6, 1.8, 0.05)
    assert 0 < r < 1.0

def test_risk_of_ruin_increases_with_fraction():
    r1 = risk_of_ruin(0.6, 1.8, 0.02)
    r2 = risk_of_ruin(0.6, 1.8, 0.10)
    assert r2 > r1
