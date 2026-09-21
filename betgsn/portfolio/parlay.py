"""BETGSN :: portfolio.parlay — Cálculo de múltiplas (parlays/accumulators).

Calcula probabilidade conjunta, EV, Kelly e risco para combinações de apostas.
Respeita correlação intra-jogo quando disponível.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Sequence
import math

@dataclass(frozen=True)
class ParlayLeg:
    """Uma perna de uma múltipla."""
    match: str
    market: str
    outcome: str
    model_prob: float
    odd: float
    bookmaker: str
    ev: float = 0.0
    edge: float = 0.0
    correlation_group: str = ""  # matches in same group may be correlated
    
    @property
    def implied_prob(self) -> float:
        return 1.0 / self.odd if self.odd > 1.0 else 1.0

@dataclass(frozen=True)
class ParlayResult:
    """Resultado calculado de uma múltipla."""
    legs: tuple[ParlayLeg, ...]
    n_legs: int
    combined_odd: float
    joint_probability: float    # from model
    implied_probability: float  # from odds
    ev: float
    edge: float
    kelly: float
    stake: float = 0.0
    payout: float = 0.0
    expected_profit: float = 0.0
    correlation_adjustment: float = 1.0  # multiplier applied to joint prob
    category: str = ""           # "2-leg", "3-leg", etc.
    risk_score: float = 0.0     # 0-1, higher = riskier
    
    @property
    def n_matches(self) -> int:
        return len({leg.match for leg in self.legs})
    
    @property
    def all_same_match(self) -> bool:
        return self.n_matches == 1

def calculate_parlay(
    legs: Sequence[ParlayLeg],
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.10,
    stake_cap: float = 0.02,
    joint_prob_override: Optional[float] = None,
) -> ParlayResult:
    """Calculate a parlay/accumulator.
    
    If joint_prob_override is given (e.g. from score matrix), uses that
    instead of assuming independence.
    """
    if not legs:
        raise ValueError("parlay precisa de ao menos 1 perna")
    
    combined_odd = 1.0
    for leg in legs:
        combined_odd *= leg.odd
    
    implied = 1.0 / combined_odd if combined_odd > 1 else 1.0
    
    if joint_prob_override is not None:
        joint = joint_prob_override
        corr_adj = joint / max(1e-12, math.prod(l.model_prob for l in legs))
    else:
        joint = math.prod(l.model_prob for l in legs)
        corr_adj = 1.0
    
    ev = joint * combined_odd - 1.0
    edge = joint - implied
    
    # Kelly for the parlay
    b = combined_odd - 1.0
    kelly = max(0.0, (b * joint - (1.0 - joint)) / b) if b > 0 else 0.0
    kelly_used = min(kelly * kelly_fraction, stake_cap)
    stake_amount = round(bankroll * kelly_used, 2)
    
    # Risk score: combination of n_legs, combined_odd magnitude, and edge
    risk = min(1.0, len(legs) / 10.0 + (1.0 / combined_odd) * 0.3)
    
    return ParlayResult(
        legs=tuple(legs),
        n_legs=len(legs),
        combined_odd=round(combined_odd, 4),
        joint_probability=joint,
        implied_probability=implied,
        ev=ev,
        edge=edge,
        kelly=kelly,
        stake=stake_amount,
        payout=round(stake_amount * combined_odd, 2),
        expected_profit=round(stake_amount * ev, 2),
        correlation_adjustment=corr_adj,
        category=f"{len(legs)}-leg",
        risk_score=round(risk, 3),
    )

def best_parlays(
    signals: Sequence[ParlayLeg],
    max_legs: int = 4,
    min_legs: int = 2,
    max_parlays: int = 20,
    min_ev: float = 0.0,
    max_same_match: int = 2,
    bankroll: float = 1000.0,
) -> list[ParlayResult]:
    """Generate and rank the best parlays from available signals.
    
    Uses a greedy approach (not exhaustive combinatorics for large N).
    Limits same-match legs and filters by EV.
    """
    from itertools import combinations
    
    if len(signals) < min_legs:
        return []
    
    results = []
    # Generate combinations up to max_legs
    for n in range(min_legs, min(max_legs + 1, len(signals) + 1)):
        for combo in combinations(signals, n):
            # Check same-match constraint
            match_counts: dict[str, int] = {}
            for leg in combo:
                match_counts[leg.match] = match_counts.get(leg.match, 0) + 1
            if any(c > max_same_match for c in match_counts.values()):
                continue
            
            result = calculate_parlay(combo, bankroll=bankroll)
            if result.ev >= min_ev:
                results.append(result)
    
    # Sort by EV descending
    results.sort(key=lambda r: r.ev, reverse=True)
    return results[:max_parlays]
