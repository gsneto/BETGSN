from betgsn.benchmark import block_ci, financial_scenario


def test_block_bootstrap_and_missing_financial_data():
    assert block_ci([1,1],["2025-01-01","2025-02-01"]) == [1,1]
    assert block_ci([1],["2025-01-01"]) is None
    result=financial_scenario([],[],"1x2")
    assert result["clv"] is None
    assert result["yield"] is None
    assert result["n_bets"] == 0
