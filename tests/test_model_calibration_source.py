"""Calibração pós-modelo: fonte explícita e dados internos fora da amostra."""
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from betgsn import model_walkforward as wf


def test_harness_never_calibrates_model_on_market_odds():
    # Actual consumer must be a model-specific calibrator, not the market view.
    # Behavioral probe: intercepted source selection is forbidden at runtime.
    from unittest.mock import patch
    model = SimpleNamespace(n_matches=400, prob_1x2=lambda *a, **k: (.8, .1, .1))
    bets = [dict(d=f'{year}-06-01', home='A', away='B', oc='1',
                 res='win', odd=2., fair=.5, n_books=3, mkt='Resultado Final (1X2)')
            for year in (2020, 2021, 2022, 2023)]
    with patch.object(wf, '_select_calibration_method', side_effect=AssertionError('market source')):
        wf.run_model_walkforward(bets, [], wf.WalkForwardConfig(bootstrap_resamples=10),
                                 model_fn=lambda *a: model)


def _rows():
    from betgsn.model_calibration import CalibrationRow
    start = date(2020, 1, 1)
    return [CalibrationRow(.9 if i % 2 else .1, int(i % 5 != 0),
                           (start+timedelta(days=i)).isoformat(),
                           (start+timedelta(days=i+2)).isoformat(),
                           (start+timedelta(days=i-3)).isoformat(), '1x2')
            for i in range(240)]


@pytest.mark.parametrize('field,value', [('model_trained_until','2022-01-01'),
                                       ('p_model',float('nan'))])
def test_invalid_source_boundary_or_probability_rejected(field, value):
    from betgsn.model_calibration import fit_model_calibrator
    rows = _rows()
    rows[0] = replace(rows[0], **{field:value})
    with pytest.raises(ValueError):
        fit_model_calibrator(rows, train_end='2021-01-01', market='1x2')


def test_late_label_and_other_market_do_not_change_fit():
    from betgsn.model_calibration import fit_model_calibrator
    rows = _rows()
    a = fit_model_calibrator(rows, train_end='2021-01-01', market='1x2')
    extras = [replace(rows[0], label_available_at='2022-01-01'),
              replace(rows[0], market='ou')]
    b = fit_model_calibrator(rows+extras, train_end='2021-01-01', market='1x2')
    assert a.fingerprint == b.fingerprint
    assert a.n == 240
    assert set(a.audit['candidates']) == {'raw','platt','isotonic','temperature'}
    assert a.predict([.1,.9], '2021-02-01') == b.predict([.1,.9], '2021-02-01')
    with pytest.raises(ValueError):
        a.predict([.1], '2020-05-01')


@pytest.mark.parametrize('method', ['platt','isotonic','temperature'])
def test_each_method_serializable_and_finite(method):
    import json
    from betgsn.model_calibration import fit_parameters, apply_parameters
    rows = _rows()
    params = fit_parameters(rows, method)
    params = json.loads(json.dumps(params, allow_nan=False))
    ps = apply_parameters([.01,.5,.99], method, params)
    assert len(ps) == 3
    assert all(0 < p < 1 for p in ps)


def test_baseline_fit_excludes_late_published_result():
    from betgsn.model import HistoricalMatch
    matches = [HistoricalMatch('A','B',1,0,kickoff='2020-01-01') for _ in range(300)]
    late = HistoricalMatch('A','B',9,0,kickoff='2020-02-01',
                           result_available_at='2022-01-01')
    a = wf.fit_model_on_train(matches, '2021-01-01')
    b = wf.fit_model_on_train(matches+[late], '2021-01-01')
    assert a.n_matches == b.n_matches == 300


def test_missing_fair_in_middle_keeps_labels_aligned():
    model = SimpleNamespace(n_matches=400, prob_1x2=lambda *a, **k: (.8,.1,.1))
    bets = [dict(d=f'{year}-06-01', home='A',away='B',oc='1',res=res,
                 odd=2.,fair=fair,n_books=3,mkt='Resultado Final (1X2)')
            for year,res,fair in [(2020,'win',.5),(2023,'loss',.1),
                                   (2023,'loss',None),(2023,'win',.9)]]
    result = wf.run_model_walkforward(bets, [], wf.WalkForwardConfig(bootstrap_resamples=10),
                                     model_fn=lambda *a:model)
    assert result.market_fair.n == 2
    assert result.market_fair.brier == pytest.approx(.01)
    # One temporal block cannot support a block-bootstrap significance claim.
    assert result.paired_model_vs_fair is None
