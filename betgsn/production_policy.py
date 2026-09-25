"""Política operacional conjuntiva. Ausência de evidência nunca aprova."""
from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .config import production_policy_fingerprint, production_thresholds

REQUIRED_BLOCKS = ('MODEL', 'CLV', 'MARKET', 'EXECUTION', 'ROBUSTNESS',
                   'PROVENANCE', 'TEMPORAL')


def finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class GateBlock:
    status: str
    reasons: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'reasons', tuple(self.reasons))


@dataclass(frozen=True)
class ProductionGate:
    blocks: Mapping[str, GateBlock]
    fingerprint: str

    def __post_init__(self):
        object.__setattr__(self, 'blocks', MappingProxyType(dict(self.blocks)))

    @property
    def production_eligible(self) -> bool:
        return (
            self.fingerprint == production_policy_fingerprint()
            and set(self.blocks) == set(REQUIRED_BLOCKS)
            and all(isinstance(b, GateBlock) and b.status == 'GREEN'
                    for b in self.blocks.values())
        )


def selection_reasons(*, edge: float | None, ev: float | None,
                      spread: float | None, books_count: int, market: str,
                      gate: ProductionGate | None, quote_valid: bool) -> tuple[str, ...]:
    limits = production_thresholds()
    reasons = []
    if not finite_number(edge) or not limits['min_edge'] <= edge <= 1:
        reasons.append('EDGE_INSUFFICIENT_OR_INVALID')
    if not finite_number(ev) or ev < limits['min_ev']:
        reasons.append('EV_INSUFFICIENT_OR_INVALID')
    if not finite_number(spread) or not 0 <= spread <= limits['max_spread']:
        reasons.append('SPREAD_INVALID_OR_EXCESSIVE')
    if type(books_count) is not int or books_count < limits['min_books']:
        reasons.append('LIMITED_EVIDENCE')
    if market not in limits['markets']:
        reasons.append('RESEARCH_MARKET')
    if quote_valid is not True:
        reasons.append('QUOTE_NOT_AUDITABLE')
    if not isinstance(gate, ProductionGate) or not gate.production_eligible:
        reasons.append('NO_BET')
    return tuple(reasons)
