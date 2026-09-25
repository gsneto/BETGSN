import pytest
from betgsn.models.promotion import evaluate_promotion


def _criterion(clv):
    result = evaluate_promotion('test', [], clv=clv)
    return next(c for c in result.criteria if c.name=='clv_nao_negativo')


@pytest.mark.parametrize('change', [dict(n=199),dict(median=0),dict(mean=0),
    dict(positive_rate=.549),dict(prospective=False),dict(mean=None),dict(n=None),
    dict(median=float('nan')),dict(closed_only=False)])
def test_clv_every_requirement_blocks(change):
    clv = dict(n=200,mean=.01,median=.01,positive_rate=.55,n_positive=110,
               prospective=True,closed_only=True,ci_low=.001)
    clv.update(change)
    c = _criterion(clv)
    assert c.blocking and not c.passed


def test_missing_clv_is_blocking():
    c = _criterion(None)
    assert c.blocking and not c.passed


def test_exact_clv_boundary_passes():
    c = _criterion(dict(n=200,mean=.01,median=.01,positive_rate=.55,n_positive=110,
                        prospective=True,closed_only=True,ci_low=.001))
    assert c.passed


def test_promotion_requires_complete_operational_blocks():
    from betgsn.models.promotion import SegmentResult
    segments = [SegmentResult(l,s,300,{'logloss':.4,'brier':.1,'ece':.01},
                             {'logloss':.6,'brier':.2,'ece':.02})
                for l in ('A','B') for s in ('2024','2025')]
    result = evaluate_promotion('test',segments,n_windows=24,
        clv=dict(n=200,mean=.01,median=.01,positive_rate=.55,n_positive=110,
                 prospective=True,closed_only=True,ci_low=.001))
    assert not result.production_eligible
