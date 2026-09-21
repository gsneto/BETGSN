from betgsn.models.walk_forward import windows, indices


def test_rolling_expanding_and_embargo():
    expanding=list(windows("2020-01-01","2025-01-01",365,180,180))
    rolling=list(windows("2020-01-01","2025-01-01",365,180,180,False))
    assert expanding[0].train_start == expanding[1].train_start
    assert rolling[1].train_start > rolling[0].train_start
    window=expanding[0]
    train,val,test=indices(window,["2020-12-29","2020-12-30"],["2020-12-30","2022-01-01"])
    assert train == [0]
    assert val == test == []
