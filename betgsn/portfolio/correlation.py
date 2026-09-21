"""BETGSN :: portfolio.correlation — Correlação entre mercados e jogos.

Dentro do mesmo jogo, muitos mercados são correlacionados pela matriz de
placares. Entre jogos diferentes, assume independência como baseline.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Sequence
import math

@dataclass(frozen=True)
class CorrelationEstimate:
    """Estimativa de correlação entre dois eventos."""
    event_a: str
    event_b: str
    correlation: float        # [-1, 1]
    source: str               # "score_matrix", "historical", "assumed_independent"
    sample_size: int = 0
    confidence: str = "LOW"   # LOW, MEDIUM, HIGH
    
    @property
    def independent(self) -> bool:
        return abs(self.correlation) < 0.05


def intra_match_joint_prob(
    score_matrix: list[list[float]],
    condition_a: callable,
    condition_b: callable,
) -> float:
    """Joint probability of two events in the same match from score matrix.
    
    condition_a and condition_b are functions (home_goals, away_goals) -> bool.
    
    Example:
        # P(Home Win AND Over 2.5)
        intra_match_joint_prob(matrix,
            lambda h, a: h > a,
            lambda h, a: h + a > 2.5)
    """
    total = 0.0
    for h in range(len(score_matrix)):
        for a in range(len(score_matrix[h])):
            if condition_a(h, a) and condition_b(h, a):
                total += score_matrix[h][a]
    return total


def intra_match_correlation(
    score_matrix: list[list[float]],
    condition_a: callable,
    condition_b: callable,
) -> CorrelationEstimate:
    """Correlation between two events within the same match."""
    p_a = sum(score_matrix[h][a] for h in range(len(score_matrix))
              for a in range(len(score_matrix[h])) if condition_a(h, a))
    p_b = sum(score_matrix[h][a] for h in range(len(score_matrix))
              for a in range(len(score_matrix[h])) if condition_b(h, a))
    p_ab = intra_match_joint_prob(score_matrix, condition_a, condition_b)
    
    if p_a <= 0 or p_a >= 1 or p_b <= 0 or p_b >= 1:
        return CorrelationEstimate("a", "b", 0.0, "score_matrix", confidence="LOW")
    
    # Phi coefficient (Matthews correlation)
    num = p_ab - p_a * p_b
    den = math.sqrt(p_a * (1 - p_a) * p_b * (1 - p_b))
    corr = num / den if den > 1e-12 else 0.0
    
    return CorrelationEstimate(
        "a", "b", round(corr, 4), "score_matrix", confidence="HIGH"
    )


def inter_match_correlation() -> CorrelationEstimate:
    """Between different matches: assume independence as baseline."""
    return CorrelationEstimate(
        "match_1", "match_2", 0.0, "assumed_independent",
        confidence="MEDIUM"
    )


# Common intra-match conditions for quick access
def home_win(h: int, a: int) -> bool:
    return h > a

def draw(h: int, a: int) -> bool:
    return h == a

def away_win(h: int, a: int) -> bool:
    return h < a

def over(line: float):
    def check(h: int, a: int) -> bool:
        return h + a > line
    return check

def under(line: float):
    def check(h: int, a: int) -> bool:
        return h + a < line
    return check

def btts_yes(h: int, a: int) -> bool:
    return h > 0 and a > 0

def btts_no(h: int, a: int) -> bool:
    return h == 0 or a == 0
