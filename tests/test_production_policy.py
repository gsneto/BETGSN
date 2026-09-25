"""Produção falha fechada; fixtures de gates não são evidência econômica."""
import math

import pytest

from betgsn import config
from betgsn.signals import Confidence, classify


def test_legacy_call_cannot_claim_strong_without_edge_or_gate():
    assert classify(.20, 3, .10) != Confidence.FORTE


def test_strong_rejects_spread_above_twelve_points():
    assert classify(.20, 3, .12001) != Confidence.FORTE


def _policy():
    from betgsn import production_policy
    return production_policy


def _green():
    p = _policy()
    return p.ProductionGate(
        {n: p.GateBlock('GREEN', ()) for n in p.REQUIRED_BLOCKS},
        config.production_policy_fingerprint(),
    )


def test_every_red_unknown_missing_or_stale_fingerprint_blocks():
    p = _policy()
    green = _green()
    assert green.production_eligible
    for name in p.REQUIRED_BLOCKS:
        for status in ('RED', 'UNKNOWN', 'green', ''):
            blocks = dict(green.blocks, **{name: p.GateBlock(status, ())})
            assert not p.ProductionGate(blocks, green.fingerprint).production_eligible
        blocks = dict(green.blocks)
        del blocks[name]
        assert not p.ProductionGate(blocks, green.fingerprint).production_eligible
    assert not p.ProductionGate(green.blocks, 'old-policy').production_eligible


@pytest.mark.parametrize('field,value', [
    ('edge', .07999), ('ev', .07999), ('spread', .12001),
    ('edge', None), ('ev', None), ('spread', None),
    ('edge', math.nan), ('ev', math.inf), ('spread', -1),
    ('edge', True), ('books_count', True), ('books_count', 2),
    ('books_count', 3.5), ('market', 'Ambas Marcam'), ('quote_valid', False),
])
def test_each_selection_requirement_is_independent(field, value):
    p = _policy()
    args = dict(edge=.08, ev=.08, spread=.12, books_count=3,
                market='Handicap Asiatico', gate=_green(), quote_valid=True)
    assert p.selection_reasons(**args) == ()
    args[field] = value
    assert p.selection_reasons(**args)


def test_classify_strong_requires_valid_operational_context():
    gate = _green()
    assert classify(.08, 3, .12, edge=.08, market='Total de Gols',
                    production_gate=gate, quote_valid=True) == Confidence.FORTE
    assert classify(.08, 3, .12, edge=.07999, market='Total de Gols',
                    production_gate=gate, quote_valid=True) != Confidence.FORTE


def test_gate_defensively_copies_mutable_input():
    p = _policy()
    blocks = dict(_green().blocks)
    gate = p.ProductionGate(blocks, config.production_policy_fingerprint())
    blocks['MODEL'] = p.GateBlock('RED', ('failed',))
    assert gate.production_eligible
    with pytest.raises(TypeError):
        gate.blocks['MODEL'] = blocks['MODEL']


def test_policy_requires_explicit_fingerprint_review():
    assert config.production_thresholds() == {
        'version': 1, 'min_edge': .08, 'min_ev': .08, 'max_spread': .12,
        'min_books': 3, 'max_ev_gap': .03, 'min_clv_sample': 200,
        'min_beat_close': .55, 'min_windows': 3, 'max_execution_erosion': .5,
        'markets': ['Handicap Asiatico', 'Total de Gols'],
    }
    # Digest reviewed before evaluation; no dynamic expected fingerprint.
    assert config.production_policy_fingerprint() == '7b0a4d8837f6e54b48693c22d8c678ccccbb553dbf96816f4442e5e2c78edef3'
