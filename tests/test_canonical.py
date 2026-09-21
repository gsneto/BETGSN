"""Testes do modelo canônico de dados."""
import pytest
from betgsn.canonical import (
    CanonicalMatch, CanonicalOdds, CanonicalOddsLine,
    CanonicalMatchStats, CanonicalXG, CanonicalReferee,
    DataConfidence, ProviderRecord,
)

def test_canonical_match_basic():
    m = CanonicalMatch(home="Arsenal", away="Chelsea", home_goals=2, away_goals=1)
    assert m.finished
    assert m.home == "Arsenal"

def test_canonical_match_not_finished():
    m = CanonicalMatch(home="Arsenal", away="Chelsea")
    assert not m.finished

def test_canonical_odds_by_market():
    lines = (
        CanonicalOddsLine("Pinnacle", "1X2", "1", 1.90),
        CanonicalOddsLine("Pinnacle", "1X2", "X", 3.40),
        CanonicalOddsLine("Pinnacle", "1X2", "2", 4.20),
        CanonicalOddsLine("Bet365", "1X2", "1", 1.85),
    )
    odds = CanonicalOdds("2026-09-20|Arsenal|Chelsea", lines)
    by_market = odds.by_market()
    assert "1X2" in by_market
    assert by_market["1X2"]["Pinnacle"]["1"] == 1.90
    assert by_market["1X2"]["Bet365"]["1"] == 1.85

def test_canonical_odds_best_before():
    lines = (
        CanonicalOddsLine("Pin", "1X2", "1", 1.90, timestamp="2026-09-19T10:00:00Z"),
        CanonicalOddsLine("Pin", "1X2", "1", 1.85, timestamp="2026-09-20T14:00:00Z"),
    )
    odds = CanonicalOdds("key", lines)
    before = odds.best_before("2026-09-20T00:00:00Z")
    assert len(before.lines) == 1
    assert before.lines[0].odd == 1.90

def test_match_stats_cards_total():
    s = CanonicalMatchStats(yellow_cards=3, red_cards=1)
    assert s.cards_total == 4
    s2 = CanonicalMatchStats()
    assert s2.cards_total is None

def test_xg_status():
    xg = CanonicalXG(home_xg=1.5, away_xg=0.8, status="REAL", source="provider")
    assert xg.status == "REAL"
    xg2 = CanonicalXG()
    assert xg2.status == "UNAVAILABLE"

def test_data_confidence_enum():
    assert DataConfidence.HIGH == "HIGH"
    assert DataConfidence.CONFLICT == "CONFLICT"

def test_match_key():
    m = CanonicalMatch(home="Arsenal", away="Chelsea", kickoff="2026-09-20T14:00:00Z")
    assert m.match_key == "2026-09-20|Arsenal|Chelsea"
