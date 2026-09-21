import pytest
from betgsn.model import HistoricalMatch, fit_ratings, expected_goals
from betgsn.xg_sources import XGObservation, XGStatus, attach_xg


def test_missing_xg_never_becomes_goals():
    m = HistoricalMatch("A", "B", 4, 1, kickoff="2025-01-01")
    r = fit_ratings([m], ["A", "B"])
    assert r["A"].xg_for is None
    assert r["A"].xg_status == "UNAVAILABLE"
    assert expected_goals(r["A"], r["B"], 3, attack_blend=.5) == expected_goals(r["A"], r["B"], 3, attack_blend=0)


def test_real_xg_keeps_source_and_zero():
    m = HistoricalMatch("A", "B", 4, 1)
    obs = XGObservation(0, 1.7, 1.7, 0, XGStatus.REAL, "provider", "2025-01-02")
    m = attach_xg(m, obs)
    r = fit_ratings([m], ["A", "B"])
    assert r["A"].xg_for == 0
    assert r["A"].xg_status == "REAL"
    assert m.home_xg_against == 1.7


def test_xg_validation():
    with pytest.raises(ValueError):
        XGObservation(home_xg=3)
    with pytest.raises(ValueError):
        XGObservation(home_xg=float("nan"), status=XGStatus.REAL, source="test")
