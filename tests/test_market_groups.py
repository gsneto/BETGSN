import pytest
from betgsn.engine import evaluate_market, consensus_fair_probs


def test_devig_separates_total_lines():
    odds={"book":{"Over 1.5":1.5,"Under 1.5":3,"Over 2.5":2,"Under 2.5":2}}
    p=consensus_fair_probs(odds)
    assert p["Over 1.5"] == pytest.approx(2/3)
    assert p["Over 2.5"] == .5
    assert sum(p.values()) == 2


def test_double_chance_sum_two_and_incomplete_market():
    p=consensus_fair_probs({"book":{"1X":1.5,"12":1.5,"X2":1.5}})
    assert sum(p.values()) == pytest.approx(2)
    assert evaluate_market({"book":{"Over 2.5":2}},{"Over 2.5":.6}) == []


@pytest.mark.parametrize("outcomes", [{"1": 2, "2": 3}, {"1X": 1.5, "12": 1.5}])
def test_incomplete_three_way_group_is_not_normalized(outcomes):
    assert consensus_fair_probs({"book": outcomes}) == {}


def test_handicap_zero_and_separate_lines():
    p = consensus_fair_probs({"book": {
        "AH Casa +0": 2, "AH Fora +0": 2,
        "AH Casa -1.5": 3, "AH Fora +1.5": 1.5,
    }})
    assert p["AH Casa +0"] == .5
    assert p["AH Casa -1.5"] == pytest.approx(1 / 3)
    assert sum(p.values()) == pytest.approx(2)
