"""BETGSN :: portfolio.risk — Gestão de risco e limites de exposição.

Controla exposição total, por jogo, por liga, e por correlação.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Sequence

@dataclass
class ExposureLimits:
    """Limites de exposição configuráveis."""
    max_total_exposure: float = 0.25        # fraction of bankroll
    max_match_exposure: float = 0.05        # max on one match
    max_league_exposure: float = 0.10       # max on one league
    max_correlated_exposure: float = 0.08   # max on correlated events
    max_single_stake: float = 0.02          # max single bet
    max_parlay_stake: float = 0.01          # max parlay bet
    max_daily_bets: int = 50
    max_concurrent_parlays: int = 10
    
    def validate(self) -> list[str]:
        errors = []
        if self.max_total_exposure <= 0 or self.max_total_exposure > 1:
            errors.append("max_total_exposure deve estar em (0, 1]")
        if self.max_single_stake > self.max_total_exposure:
            errors.append("max_single_stake não pode exceder max_total_exposure")
        return errors

@dataclass(frozen=True)
class ExposureReport:
    """Relatório de exposição do portfólio atual."""
    total_exposure: float
    total_exposure_pct: float
    by_match: dict[str, float] = field(default_factory=dict)
    by_league: dict[str, float] = field(default_factory=dict)
    n_bets: int = 0
    n_parlays: int = 0
    violations: list[str] = field(default_factory=list)
    
    @property
    def within_limits(self) -> bool:
        return len(self.violations) == 0

def check_exposure(
    stakes: list[dict],  # [{"match": ..., "league": ..., "stake": ..., "type": ...}]
    bankroll: float,
    limits: ExposureLimits,
) -> ExposureReport:
    """Check if current portfolio is within exposure limits."""
    total = sum(s["stake"] for s in stakes)
    by_match: dict[str, float] = {}
    by_league: dict[str, float] = {}
    n_parlays = 0
    
    for s in stakes:
        match = s.get("match", "unknown")
        league = s.get("league", "unknown")
        by_match[match] = by_match.get(match, 0) + s["stake"]
        by_league[league] = by_league.get(league, 0) + s["stake"]
        if s.get("type") == "parlay":
            n_parlays += 1
    
    violations = []
    total_pct = total / bankroll if bankroll > 0 else 0
    
    if total_pct > limits.max_total_exposure:
        violations.append(
            f"exposição total {total_pct:.1%} > limite {limits.max_total_exposure:.1%}"
        )
    
    for match, exp in by_match.items():
        pct = exp / bankroll
        if pct > limits.max_match_exposure:
            violations.append(f"exposição {match}: {pct:.1%} > {limits.max_match_exposure:.1%}")
    
    for league, exp in by_league.items():
        pct = exp / bankroll
        if pct > limits.max_league_exposure:
            violations.append(f"exposição {league}: {pct:.1%} > {limits.max_league_exposure:.1%}")
    
    for s in stakes:
        if s.get("type") == "single" and s["stake"] / bankroll > limits.max_single_stake:
            violations.append(f"stake individual {s['stake']:.2f} > limite")
        if s.get("type") == "parlay" and s["stake"] / bankroll > limits.max_parlay_stake:
            violations.append(f"stake parlay {s['stake']:.2f} > limite")
    
    if n_parlays > limits.max_concurrent_parlays:
        violations.append(f"parlays {n_parlays} > limite {limits.max_concurrent_parlays}")
    
    return ExposureReport(
        total_exposure=total,
        total_exposure_pct=total_pct,
        by_match=by_match,
        by_league=by_league,
        n_bets=len(stakes),
        n_parlays=n_parlays,
        violations=violations,
    )

def expected_log_growth(prob: float, odd: float, fraction: float) -> float:
    """Expected log growth per bet (Kelly criterion foundation).
    
    g = p * ln(1 + f*b) + (1-p) * ln(1 - f)
    where f = fraction of bankroll, b = odd - 1
    """
    import math
    if fraction <= 0 or fraction >= 1:
        return -float("inf") if fraction >= 1 else 0.0
    b = odd - 1.0
    if b <= 0:
        return -float("inf")
    return prob * math.log(1 + fraction * b) + (1 - prob) * math.log(1 - fraction)

def risk_of_ruin(
    prob: float, 
    odd: float, 
    fraction: float,
    ruin_level: float = 0.1,
) -> float:
    """Analytical approximation of probability of ever reaching ruin_level of peak.
    
    Uses the formula: P(ruin) ≈ ruin_level^(2*edge/(variance*fraction))
    Valid only for fraction <= Kelly.
    """
    import math
    edge = prob * odd - 1.0
    if edge <= 0:
        return 1.0
    b = odd - 1.0
    variance = prob * b * b + (1 - prob) * 1.0  # per-unit variance
    exponent = 2 * edge / (variance * fraction) if variance * fraction > 0 else float("inf")
    return ruin_level ** exponent if exponent < 100 else 0.0
