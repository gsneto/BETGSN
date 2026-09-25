import pytest


def test_execution_unknown_is_not_zero():
    from betgsn.priced_signals import execution_diagnostics
    result = execution_diagnostics(2.1,None,None)
    assert result['execution_status']=='UNKNOWN'
    assert result['absolute'] is None
    assert result['clv_after'] is None


def test_execution_gap_and_erosion():
    from betgsn.priced_signals import execution_diagnostics, execution_erosion
    row = execution_diagnostics(2.1,2.02,2.)
    assert row['absolute']==pytest.approx(-.08)
    assert row['relative']==pytest.approx(-.08/2.1)
    assert row['clv_before']==pytest.approx(.05)
    assert row['clv_after']==pytest.approx(.01)
    result = execution_erosion([row])
    assert result['status']=='EXECUTION_EROSION'
    assert result['ratio']==pytest.approx(.8)


def test_erosion_only_uses_paired_measurements():
    from betgsn.priced_signals import execution_diagnostics, execution_erosion
    rows = [execution_diagnostics(2.1,None,2.),execution_diagnostics(2.1,2.2,2.)]
    result = execution_erosion(rows)
    assert result['n']==1
    assert result['status']=='MEASURED'
    assert execution_erosion(rows[:1])['status']=='UNKNOWN'


def _quote(book='B1', price=2.1):
    from betgsn.odds_normalize import NormalizedQuote
    return NormalizedQuote('event','provider','soccer','L','A','B',
          '2026-01-02T12:00:00Z',book,'Total de Gols','Over 2.5',price,
          '2026-01-01T12:00:00Z',2.5)


def test_priced_signal_contract_and_null_execution():
    from betgsn.priced_signals import build_priced_signal
    q = _quote()
    signal = build_priced_signal(quote=q,selected_quote=q,model_prob=.6,fair_prob=.5,
             ev=.26,spread=.05,books=[q,_quote('B2',2.0),_quote('B3',2.05)],
             decision_timestamp='2026-01-01T12:00:01Z',gate=None,model_fingerprint='fit')
    payload = signal.to_dict()
    assert payload['execution_status']=='UNKNOWN'
    assert payload['executed_price'] is None
    assert payload['observed_price']==2.1
    assert payload['books_count']==3
    assert payload['production']=='NO_BET'
    assert payload['evidence_status']=='RESEARCH'
    assert payload['edge']==pytest.approx(.1)
    assert payload['provenance']['provider']=='provider'


def test_priced_signal_rejects_future_quote_and_cross_line():
    from dataclasses import replace
    from betgsn.priced_signals import build_priced_signal
    q = _quote()
    args = dict(quote=q, selected_quote=q,model_prob=.6,fair_prob=.5,ev=.26,
                spread=.05,books=[q],decision_timestamp='2026-01-01T11:59:00Z',
                gate=None,model_fingerprint='fit')
    with pytest.raises(ValueError,match='FUTURE'):
        build_priced_signal(**args)
    args['decision_timestamp']='2026-01-01T12:00:01Z'
    args['selected_quote']=replace(q,selection='Under 2.5')
    with pytest.raises(ValueError,match='MISMATCH'):
        build_priced_signal(**args)
