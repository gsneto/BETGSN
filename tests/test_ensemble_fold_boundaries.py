from betgsn.ml_walkforward import _fold_boundaries


def test_uniform_prediction_chunks():
    assert _fold_boundaries(list(range(40)), 4, 10) == [0, 10, 20, 30, 40]


def test_simultaneous_boundary_advances():
    times = list(range(20)) + [20] * 5 + list(range(25, 40))
    assert _fold_boundaries(times, 4, 5) == [0, 10, 20, 30, 40]
    times = list(range(9)) + [9] * 4 + list(range(13, 40))
    assert _fold_boundaries(times, 4, 5) == [0, 13, 20, 30, 40]


def test_tiny_last_prediction_chunk_rejected():
    times = list(range(2999)) + [2999] * 996 + list(range(3995, 4000))
    assert _fold_boundaries(times, 4, 100) is None


def test_training_chunk_not_subject_to_prediction_minimum():
    assert _fold_boundaries(list(range(7)), 2, 4) == [0, 3, 7]
