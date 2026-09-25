import pytest
from betgsn.model import ScoreMatrix
from betgsn.backtest_data import MatchResult, settle_outcome


def test_integer_total_is_push():
    assert settle_outcome('Total de Gols','Under 2',MatchResult(1,1)) == 'push'
    assert settle_outcome('Total de Gols','Over 2',MatchResult(1,1)) == 'push'


@pytest.mark.parametrize('selection,expected', [('AH Casa -0.25',-.5),
                                               ('AH Casa +0.25',.5),
                                               ('AH Casa 0',0)])
def test_quarter_handicap_draw(selection,expected):
    from betgsn.settlement_distribution import distribution_for
    d = distribution_for(ScoreMatrix((0.,0.),[[1.]],0),'Handicap Asiatico',selection)
    assert d.expected_return(2.) == expected
    assert sum((d.win,d.half_win,d.push,d.half_loss,d.loss)) == 1


def test_totals_quarter_push_mass():
    from betgsn.settlement_distribution import distribution_for
    grid = [[0.,0.],[0.,1.]]
    d = distribution_for(ScoreMatrix((1.,1.),grid,1),'Total de Gols','Over 2.25')
    assert d.half_loss == 1
    assert d.settled_exposure == .5
    assert d.effective_probability == 0
