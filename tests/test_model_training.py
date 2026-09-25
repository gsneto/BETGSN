from dataclasses import replace

import pytest

from betgsn.model import HistoricalMatch


def _history():
    return [HistoricalMatch('A','B',2,1,kickoff=f'2020-01-{i%28+1:02d}',league='L')
            for i in range(120)] + [HistoricalMatch('C','D',1,1,kickoff='2020-01-02',league='S')]


def test_shrinkage_reduces_small_team_deviation():
    from betgsn.model_training import TrainingParams, fit_train_snapshot
    history = _history()+[HistoricalMatch('Tiny','B',5,0,kickoff='2020-01-20',league='L')]
    plain = fit_train_snapshot(history,train_start='2019-01-01',train_end='2020-03-01',params=TrainingParams())
    shrunk = fit_train_snapshot(history,train_start='2019-01-01',train_end='2020-03-01',params=TrainingParams(shrinkage=30))
    assert abs(shrunk.ratings[('L','Tiny')].attack-1) < abs(plain.ratings[('L','Tiny')].attack-1)


def test_small_league_fallback_and_future_invariance():
    from betgsn.model_training import TrainingParams, fit_train_snapshot
    params = TrainingParams(league_home=True)
    args = dict(train_start='2019-01-01',train_end='2020-03-01',params=params)
    a = fit_train_snapshot(_history(),**args)
    future = replace(_history()[0],kickoff='2021-01-01',home_goals=9)
    b = fit_train_snapshot(_history()+[future],**args)
    assert a.fingerprint == b.fingerprint
    assert a.home_by_league['S'] == a.global_home
    assert a.audit['home_fallback']['S'] == 'INSUFFICIENT_LEAGUE_SAMPLE'


def test_decay_and_missing_xg_do_not_invent_observations():
    from betgsn.model_training import TrainingParams, fit_train_snapshot
    a = fit_train_snapshot(_history(),train_start='2019-01-01',train_end='2020-03-01',
                           params=TrainingParams(decay_days=180,blend_weights=(.5,.25,0,.25)))
    assert a.audit['xg_matches'] == 0
    assert a.audit['effective_weights']['L'][3] == 0
    assert a.audit['n_matches'] == 121


def test_invalid_training_params_rejected():
    from betgsn.model_training import TrainingParams
    for kwargs in (dict(shrinkage=-1),dict(decay_days=0),dict(blend_weights=(1,1,1,1))):
        with pytest.raises(ValueError):
            TrainingParams(**kwargs)


def test_train_selector_is_invariant_to_future_labels():
    from betgsn.model_training import select_train_params
    kwargs = dict(train_start='2019-01-01',train_end='2020-03-01',enabled=('shrinkage','decay','home','blend'))
    a = select_train_params(_history(),**kwargs)
    b = select_train_params(_history()+[replace(_history()[0],kickoff='2021-01-01',home_goals=9)],**kwargs)
    assert a == b
