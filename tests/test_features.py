from dataclasses import replace
import pytest
from betgsn.features import FeatureBuilder
from betgsn.features.elo import Elo
from betgsn.features.odds import OddsQuote, odds_features
from betgsn.model import Fixture, HistoricalMatch


def match(day, hg=2, ag=0):
    return HistoricalMatch("A", "B", hg, ag, kickoff=f"2025-01-{day:02d}T12:00:00Z")


def test_features_temporal_form_rest_h2h_elo():
    history = [match(1), match(5), match(9), match(20, 0, 9)]
    fx = Fixture("A", "B", kickoff="2025-01-13T12:00:00Z")
    s = FeatureBuilder(history).build(fx)
    altered = FeatureBuilder(history[:-1] + [match(20, 99, 99)]).build(fx)
    assert s == altered
    assert s.feature_time < s.kickoff
    assert s.values["home_form_5_overall_points"] == pytest.approx(3)
    assert s.values["home_days_since_last"] == 4
    assert s.values["home_matches_7d"] == 1
    assert s.values["h2h_n"] == 3
    assert s.values["elo_difference"] > 0
    assert s.values["home_form_5_overall_xg"] is None


def test_elo_updates_after_result_only():
    elo = Elo()
    elo.update(match(1))
    assert elo.rating("A") > 1500 > elo.rating("B")
    assert elo.rating("A") + elo.rating("B") == pytest.approx(3000)
    b = FeatureBuilder([match(1)])
    assert b.build(Fixture("A", "B", kickoff="2025-01-01T13:00:00Z")).values["elo_difference"] == 0


def test_odds_never_see_future_or_closing():
    q = OddsQuote("book", "1", 2, "2025-01-01T10:00:00Z")
    qs = [q, replace(q, odd=9, timestamp="2025-01-02T10:00:00Z"), replace(q, odd=20, closing=True)]
    f = odds_features(qs, "2025-01-01T11:00:00Z", "2025-01-03T12:00:00Z")
    assert f["1x2_1"]["best"] == 2


def test_builder_refuses_time_travel():
    b = FeatureBuilder([match(1)])
    b.build(Fixture("A", "B", kickoff="2025-01-10"))
    with pytest.raises(ValueError):
        b.build(Fixture("A", "B", kickoff="2025-01-05"))
