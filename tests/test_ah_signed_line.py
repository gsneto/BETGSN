from datetime import datetime, timezone

import pytest

from betgsn.odds_normalize import NormalizedQuote
from betgsn.realtime.state import MarketState


@pytest.mark.parametrize('market,selection,line,accepted', [
    ('Handicap Asiatico','AH Casa -1.5',-1.5,True),
    ('Handicap Asiatico','AH Fora +1.5',1.5,True),
    ('Handicap Asiatico','AH Casa -0.25',-.25,True),
    ('Total de Gols','Over -1.5',-1.5,False),
    ('Handicap Asiatico','AH Casa -1.5',1.5,False),
    ('Handicap Asiatico','AH Casa -1.3',-1.3,False),
    ('Handicap Asiatico','AH Casa -1.5',float('nan'),False),
])
def test_market_state_signed_handicap(market,selection,line,accepted):
    q = NormalizedQuote('event','provider','soccer','L','A','B',
        '2026-01-02T12:00:00Z','Book',market,selection,2.,'2026-01-01T12:00:00Z',line)
    state = MarketState()
    affected, _, problems = state.apply([q], datetime(2026,1,1,12,tzinfo=timezone.utc))
    assert bool(affected) is accepted
    assert bool(problems) is not accepted
