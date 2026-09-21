"""Testes dos modelos de escanteios e cartões."""
import pytest
import math

from betgsn.models.corners import (
    CornersFeatures, PoissonCornersModel, NegBinCornersModel,
)
from betgsn.models.cards import (
    CardsFeatures, PoissonCardsModel, NegBinCardsModel,
)
from betgsn.models.referee import RefereeProfile, RefereeEngine


# --------------------------------------------------------------------------
# Corners
# --------------------------------------------------------------------------

def test_poisson_corners_probabilities_sum():
    model = PoissonCornersModel()
    features = CornersFeatures(
        home_corners_for=5.5, home_corners_against=4.5,
        away_corners_for=5.0, away_corners_against=5.0,
    )
    result = model.predict(features, total_lines=(9.5,), team_lines=(4.5,))
    assert result.total_probabilities["Cantos Over 9.5"] + \
           result.total_probabilities["Cantos Under 9.5"] == pytest.approx(1.0, abs=0.01)

def test_negbin_corners_probabilities_sum():
    model = NegBinCornersModel()
    features = CornersFeatures(
        home_corners_for=5.5, home_corners_against=4.5,
        away_corners_for=5.0, away_corners_against=5.0,
    )
    result = model.predict(features, total_lines=(9.5,), team_lines=(4.5,))
    assert result.total_probabilities["Cantos Over 9.5"] + \
           result.total_probabilities["Cantos Under 9.5"] == pytest.approx(1.0, abs=0.01)

def test_corners_higher_lambda_means_more_corners():
    model = PoissonCornersModel()
    low = CornersFeatures(home_corners_for=3.0, home_corners_against=3.0,
                          away_corners_for=3.0, away_corners_against=3.0)
    high = CornersFeatures(home_corners_for=7.0, home_corners_against=7.0,
                           away_corners_for=7.0, away_corners_against=7.0)
    r_low = model.predict(low, total_lines=(9.5,))
    r_high = model.predict(high, total_lines=(9.5,))
    assert r_high.total_probabilities["Cantos Over 9.5"] > \
           r_low.total_probabilities["Cantos Over 9.5"]


# --------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------

def test_poisson_cards_probabilities_sum():
    model = PoissonCardsModel()
    features = CardsFeatures(
        home_cards_for=2.0, home_cards_against=2.0,
        away_cards_for=2.0, away_cards_against=2.0,
    )
    result = model.predict(features, total_lines=(3.5,), team_lines=(1.5,))
    assert result.total_probabilities["Cartoes Over 3.5"] + \
           result.total_probabilities["Cartoes Under 3.5"] == pytest.approx(1.0, abs=0.01)

def test_cards_referee_adjustment():
    model = PoissonCardsModel()
    base = CardsFeatures(
        home_cards_for=2.0, home_cards_against=2.0,
        away_cards_for=2.0, away_cards_against=2.0,
    )
    strict = CardsFeatures(
        home_cards_for=2.0, home_cards_against=2.0,
        away_cards_for=2.0, away_cards_against=2.0,
        referee_avg_cards=6.0,  # strict ref
    )
    r_base = model.predict(base, total_lines=(4.5,))
    r_strict = model.predict(strict, total_lines=(4.5,))
    assert r_strict.total_probabilities["Cartoes Over 4.5"] > \
           r_base.total_probabilities["Cartoes Over 4.5"]


# --------------------------------------------------------------------------
# Referee
# --------------------------------------------------------------------------

class MockMatch:
    def __init__(self, referee="M Oliver", home_yellow=2, away_yellow=1,
                 home_red=0, away_red=0, home_corners=5, away_corners=4,
                 home_fouls=10, away_fouls=12, kickoff="2026-01-01",
                 season="2526", league="PL"):
        self.referee = referee
        self.home_yellow = home_yellow
        self.away_yellow = away_yellow
        self.home_red = home_red
        self.away_red = away_red
        self.home_corners = home_corners
        self.away_corners = away_corners
        self.home_fouls = home_fouls
        self.away_fouls = away_fouls
        self.kickoff = kickoff
        self.season = season
        self.league = league

def test_referee_engine_basic():
    engine = RefereeEngine()
    engine.update(MockMatch())
    p = engine.profile("M Oliver")
    assert p is not None
    assert p.matches == 1
    assert p.avg_yellow == 3.0
    assert p.avg_cards == 3.0

def test_referee_engine_multiple_matches():
    engine = RefereeEngine()
    engine.update(MockMatch(home_yellow=2, away_yellow=2))
    engine.update(MockMatch(home_yellow=3, away_yellow=1))
    p = engine.profile("M Oliver")
    assert p.matches == 2
    assert p.avg_yellow == 4.0  # (4 + 4) / 2

def test_referee_engine_unknown():
    engine = RefereeEngine()
    assert engine.profile("Unknown") is None

def test_referee_home_card_ratio():
    engine = RefereeEngine()
    engine.update(MockMatch(home_yellow=3, away_yellow=1, home_red=0, away_red=0))
    p = engine.profile("M Oliver")
    assert p.home_card_ratio == pytest.approx(0.75)

def test_referee_strictness():
    engine = RefereeEngine()
    for _ in range(10):
        engine.update(MockMatch(referee="Strict", home_yellow=3, away_yellow=3))
        engine.update(MockMatch(referee="Lenient", home_yellow=1, away_yellow=1))
    assert engine.strictness("Strict") > engine.strictness("Lenient")

def test_referee_no_update_for_empty_name():
    engine = RefereeEngine()
    engine.update(MockMatch(referee=""))
    assert len(engine.all_profiles()) == 0
