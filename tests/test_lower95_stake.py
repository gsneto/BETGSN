import pytest

from betgsn.staking import decide_bet
from betgsn.config import production_policy_fingerprint
from betgsn.production_policy import GateBlock, ProductionGate, REQUIRED_BLOCKS


def _gate(red=None):
    return ProductionGate({n:GateBlock('RED' if n==red else 'GREEN')
                           for n in REQUIRED_BLOCKS}, production_policy_fingerprint())


def test_stake_uses_lower95_not_point():
    result = decide_bet(.12,.01,2.,evidence_status='timestamped',n_bets=1000,
                        promotion_eligible=True, production_gate=_gate())
    # (.12 - 1.96*.01) / (2-1) * quarter Kelly.
    assert result.fraction == pytest.approx(.0251)


def test_missing_production_gate_blocks_even_trusted_prices():
    result = decide_bet(.12,.01,2.,evidence_status='timestamped',n_bets=1000,
                        promotion_eligible=True)
    assert result.action == 'NO_BET'
    assert result.fraction == 0


@pytest.mark.parametrize('red', REQUIRED_BLOCKS)
def test_each_red_blocks_staking(red):
    result = decide_bet(.12,.01,2.,evidence_status='timestamped',n_bets=1000,
                        promotion_eligible=True, production_gate=_gate(red))
    assert result.action == 'NO_BET'
    assert result.fraction == 0


@pytest.mark.parametrize('roi,se,odd', [(float('nan'),.01,2.),(.12,float('inf'),2.),
                                      (.12,.01,1.),(.12,.01,True)])
def test_invalid_financial_inputs_cannot_stake(roi,se,odd):
    with pytest.raises(ValueError):
        decide_bet(roi,se,odd, production_gate=_gate())
