"""Testes de temporal leakage.

Cada teste aqui ataca uma forma concreta de vazamento de informacao futura:

- resultado futuro no treino;
- odds/feature futura disponivel antes do kickoff;
- ajuste (calibracao/ensemble) usando o proprio conjunto de teste;
- janelas walk-forward sobrepostas;
- tuning no conjunto de teste.

Nao basta "ter cuidado": a disciplina temporal precisa falhar de forma
visivel quando e violada.
"""
from __future__ import annotations

import numpy as np
import pytest

from betgsn.backtest_data import HistoricalCorpus
from betgsn.features import FeatureBuilder
from betgsn.model import Fixture, HistoricalMatch
from betgsn.models.base import TemporalBatch, separated
from betgsn.models.calibration import TemporalCalibrator
from betgsn.models.ensemble import WeightedEnsemble
from betgsn.models.walk_forward import WalkForwardWindow, indices, windows
from betgsn.temporal import result_time
from betgsn.timeutil import parse_kickoff, utc_key


def _match(home, away, hg, ag, kickoff, **kwargs):
    return HistoricalMatch(home, away, hg, ag, kickoff=kickoff,
                           league=kwargs.pop("league", "L"),
                           season=kwargs.pop("season", "S"), **kwargs)


# ------------------------------------------------------------------ batch


def test_temporal_batch_requires_sorted_disjoint_and_aligned():
    with pytest.raises(ValueError):
        TemporalBatch([[0.0]], [0], ())
    with pytest.raises(ValueError):
        TemporalBatch([[0.0], [1.0]], [0], ("2025-01-01", "2025-01-02"))
    with pytest.raises(ValueError):
        TemporalBatch([[0.0], [1.0]], [0, 1], ("2025-01-02", "2025-01-01"))
    TemporalBatch([[0.0], [1.0]], [0, 1], ("2025-01-01", "2025-01-02"))


def test_train_validation_must_be_temporally_disjoint():
    train = TemporalBatch([[0.0]], [0], ("2025-01-01",))
    validation = TemporalBatch([[0.0]], [0], ("2025-01-02",))
    separated(train, validation)
    with pytest.raises(ValueError):
        separated(validation, train)
    same_day = TemporalBatch([[0.0]], [0], ("2025-01-01",))
    with pytest.raises(ValueError):
        separated(train, same_day)


# ------------------------------------------------------ calibracao/ensemble


def _probabilities():
    return np.array([[.9, .05, .05], [.05, .9, .05], [.05, .05, .9]] * 20)


def test_calibrator_cannot_fit_or_predict_on_its_own_test_set():
    p = _probabilities()
    y = [0, 1, 2] * 20
    times = ["2024-01-01"] * 60
    calibrator = TemporalCalibrator("platt").fit(p, y, times, "2023-01-01")
    with pytest.raises(ValueError):
        calibrator.fit(p, y, times, "2024-01-01")
    with pytest.raises(ValueError):
        calibrator.predict_proba(p, "2024-01-01")
    assert calibrator.predict_proba(p, "2025-01-01").shape == p.shape


def test_ensemble_cannot_fit_or_predict_on_its_own_test_set():
    good = _probabilities()
    bad = np.full((60, 3), 1 / 3)
    ensemble = WeightedEnsemble().fit(
        {"good": good, "bad": bad}, [0, 1, 2] * 20, ["2024-01-01"] * 60, "2023-01-01",
    )
    with pytest.raises(ValueError):
        ensemble.predict_proba({"good": good, "bad": bad}, "2024-01-01")
    with pytest.raises(ValueError):
        WeightedEnsemble().fit(
            {"good": good, "bad": bad}, [0, 1, 2] * 20, ["2024-01-01"] * 60,
            "2024-01-01",
        )


# --------------------------------------------------------- walk-forward


def test_walk_forward_test_windows_never_overlap():
    fold_windows = list(windows("2020-01-01", "2029-01-01", 365, 180, 180))
    assert len(fold_windows) >= 3
    for earlier, later in zip(fold_windows, fold_windows[1:]):
        assert parse_kickoff(earlier.test_end) <= parse_kickoff(later.validation_end)
        assert parse_kickoff(earlier.test_end) <= parse_kickoff(later.test_end)


def test_indices_excludes_result_not_yet_available():
    window = WalkForwardWindow("2020-01-01", "2021-01-01", "2022-01-01", "2023-01-01")
    train, validation, test = indices(
        window, ["2022-06-01"], ["2024-01-01"],
    )
    assert (train, validation, test) == ([], [], [])


def test_indices_keeps_match_whose_result_was_known_in_time():
    window = WalkForwardWindow("2020-01-01", "2021-01-01", "2022-01-01", "2023-01-01")
    train, validation, test = indices(
        window, ["2022-06-01"], ["2022-06-03"],
    )
    assert test == [0]


# ------------------------------------------------------ features/corpus


def test_features_do_not_change_when_future_matches_are_added():
    past = [
        _match("A", "B", 1, 0, "2025-01-01"),
        _match("B", "C", 2, 1, "2025-01-05"),
    ]
    future = [_match("A", "C", 3, 0, "2025-02-01")]
    fixture = Fixture("A", "B", "L", "2025-01-10")
    without_future = FeatureBuilder(past).build(fixture)
    with_future = FeatureBuilder(past + future).build(fixture)
    assert without_future.values == with_future.values


def test_feature_time_never_reaches_the_cutoff():
    matches = [_match("A", "B", 1, 0, "2025-01-01"), _match("A", "C", 0, 0, "2025-01-20")]
    snapshot = FeatureBuilder(matches).build(Fixture("A", "B", "L", "2025-01-10"))
    assert snapshot.feature_time is not None
    assert utc_key(snapshot.feature_time) < utc_key(snapshot.kickoff)


def test_corpus_uses_publication_time_not_kickoff():
    matches = [_match("A", "B", 1, 0, "2025-01-01"), _match("C", "D", 2, 0, "2025-01-02")]
    corpus = HistoricalCorpus(matches)
    # resultado da primeira publica em 2025-01-03; da segunda em 2025-01-04
    assert [m.home for m in corpus.available_before("2025-01-04T00:00:00Z")] == ["A"]
    # partidas simultaneas ao cutoff ficam de fora
    assert [m.home for m in corpus.matches_before("2025-01-02")] == ["A"]


def test_result_time_embargo_and_guard():
    match = _match("A", "B", 1, 0, "2025-01-01")
    assert result_time(match) == "2025-01-03T00:00:00Z"
    impossible = _match("A", "B", 1, 0, "2025-01-01",
                        result_available_at="2024-12-31")
    with pytest.raises(ValueError):
        result_time(impossible)


def test_corpus_rejects_matches_without_kickoff():
    with pytest.raises(ValueError):
        HistoricalCorpus([_match("A", "B", 1, 0, "")])


# ------------------------------------------------------ xG embargo (D.1)


def test_estimated_xg_with_future_publication_is_embargoed():
    """ESTIMATED + `xg_available_at` futuro: xG nao pode vazar antes.

    A regra vale para qualquer status != UNAVAILABLE com data de
    publicacao declarada: observacao publicada DEPOIS do corte e
    removida (strip para UNAVAILABLE), mesmo sendo estimada.
    """
    m = _match("A", "B", 1, 0, "2025-01-01",
               home_xg=1.4, away_xg=0.8, xg_status="ESTIMATED",
               xg_source="demo", xg_available_at="2025-01-10")
    corpus = HistoricalCorpus([m])
    # Resultado ja publicado (embargo de 2 dias ok), mas xG ainda nao.
    got = corpus.available_before("2025-01-05T00:00:00Z")
    assert len(got) == 1
    assert got[0].xg_status == "UNAVAILABLE"
    assert got[0].home_xg is None and got[0].away_xg is None
    # Apos a publicacao declarada do xG, a observacao aparece.
    got = corpus.available_before("2025-01-11T00:00:00Z")
    assert got[0].xg_status == "ESTIMATED"
    assert got[0].home_xg == 1.4


def test_estimated_xg_without_publication_keeps_demo_semantics():
    """ESTIMATED sem `xg_available_at`: semantica demo preservada.

    Nao ha prova de publicacao, mas tambem nao ha data para inventar:
    o comportamento de demonstracao (visivel) e mantido. REAL sem data
    continua removido — status REAL exige prova.
    """
    demo = _match("A", "B", 1, 0, "2025-01-01",
                  home_xg=1.4, away_xg=0.8, xg_status="ESTIMATED",
                  xg_source="demo")
    real_no_proof = _match("C", "D", 1, 0, "2025-01-01",
                           home_xg=2.0, away_xg=1.0, xg_status="REAL",
                           xg_source="provider")
    corpus = HistoricalCorpus([demo, real_no_proof])
    got = {m.home: m for m in corpus.available_before("2025-01-05T00:00:00Z")}
    assert got["A"].xg_status == "ESTIMATED"
    assert got["A"].home_xg == 1.4
    assert got["C"].xg_status == "UNAVAILABLE"
    assert got["C"].home_xg is None
