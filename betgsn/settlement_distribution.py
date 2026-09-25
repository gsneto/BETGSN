"""Distribuição de liquidação, incluindo split-stake de quartos de linha."""
from dataclasses import dataclass
import math

from .backtest_data import MatchResult, settle_outcome
from .markets import validate_selection_line
from .production_policy import finite_number


@dataclass(frozen=True)
class SettlementDistribution:
    win: float = 0.
    half_win: float = 0.
    push: float = 0.
    half_loss: float = 0.
    loss: float = 0.

    @property
    def settled_exposure(self):
        return self.win+self.half_win/2+self.loss+self.half_loss/2

    @property
    def effective_probability(self):
        mass = self.settled_exposure
        return (self.win+self.half_win/2)/mass if mass else None

    def expected_return(self, price):
        if not finite_number(price) or price<=1:
            raise ValueError('invalid price')
        return (self.win+self.half_win/2)*(price-1)-self.loss-self.half_loss/2


def settlement_for(market, selection, result):
    """Mesmo settlement legado para linhas inteiras/meias; quarter divide stake."""
    if market not in ('Handicap Asiatico','Total de Gols'):
        return settle_outcome(market,selection,result)
    error = validate_selection_line(market,selection,None)
    if error:
        raise ValueError(error)
    prefix, raw = selection.rsplit(' ',1)
    line = float(raw)
    if (line*2).is_integer():
        return settle_outcome(market,selection,result)
    lo = math.floor(line*2)/2
    legs = [settle_outcome(market,f'{prefix} {x:+g}',result) for x in (lo,lo+.5)]
    if legs[0]==legs[1]:
        return legs[0]
    if set(legs)=={'win','push'}:
        return 'half_win'
    if set(legs)=={'loss','push'}:
        return 'half_loss'
    raise ValueError('inconsistent split settlement')


def distribution_for(matrix, market, selection):
    totals = dict(win=0.,half_win=0.,push=0.,half_loss=0.,loss=0.)
    for home,row in enumerate(matrix.grid):
        for away,probability in enumerate(row):
            state = settlement_for(market,selection,MatchResult(home,away))
            if state not in totals:
                raise ValueError('unsupported settlement')
            totals[state] += probability
    return SettlementDistribution(**totals)
