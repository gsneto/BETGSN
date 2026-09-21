"""BETGSN :: models.cards — Modelo independente para cartões.

Usa features temporais: cartões for/against, faltas, tackles,
árbitro, casa/fora, rolling 5/10/20, agressividade do adversário.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import math

@dataclass(frozen=True)
class CardsFeatures:
    """Features for cards prediction."""
    home_cards_for: float = 0.0
    home_cards_against: float = 0.0
    away_cards_for: float = 0.0
    away_cards_against: float = 0.0
    home_fouls: float = 0.0
    away_fouls: float = 0.0
    referee_avg_cards: Optional[float] = None
    referee_avg_fouls: Optional[float] = None
    referee_home_bias: Optional[float] = None  # fraction of cards to home
    home_elo: float = 1500.0
    away_elo: float = 1500.0
    home_form_cards: Optional[float] = None
    away_form_cards: Optional[float] = None
    home_opponent_aggression: float = 0.0  # opponent's historical card rate
    away_opponent_aggression: float = 0.0
    home_rest_days: Optional[int] = None
    away_rest_days: Optional[int] = None
    elo_difference: float = 0.0
    
    def to_array(self) -> list[float]:
        return [
            self.home_cards_for, self.home_cards_against,
            self.away_cards_for, self.away_cards_against,
            self.home_fouls, self.away_fouls,
            self.referee_avg_cards if self.referee_avg_cards is not None else 4.0,
            self.referee_avg_fouls if self.referee_avg_fouls is not None else 24.0,
            self.referee_home_bias if self.referee_home_bias is not None else 0.5,
            self.home_elo, self.away_elo,
            self.home_cards_for if self.home_form_cards is None else self.home_form_cards,
            self.away_cards_for if self.away_form_cards is None else self.away_form_cards,
            self.home_opponent_aggression, self.away_opponent_aggression,
            float(self.home_rest_days or 7),
            float(self.away_rest_days or 7),
            self.elo_difference,
        ]

@dataclass(frozen=True)
class CardsMarket:
    """Predicted cards markets."""
    home_lambda: float
    away_lambda: float
    total_probabilities: dict[str, float]  
    home_probabilities: dict[str, float]   
    away_probabilities: dict[str, float]   
    model: str = "poisson"

class PoissonCardsModel:
    """Baseline Poisson for total cards."""
    version = "CARDS_POISSON_V1"
    
    def predict(self, features: CardsFeatures,
                total_lines: tuple[float, ...] = (2.5, 3.5, 4.5, 5.5),
                team_lines: tuple[float, ...] = (1.5, 2.5),
                ref_adjustment: float = 1.0) -> CardsMarket:
        lam_h = max(0.3, (features.home_cards_for + features.away_cards_against) / 2.0)
        lam_a = max(0.3, (features.away_cards_for + features.home_cards_against) / 2.0)
        
        # Referee adjustment
        if features.referee_avg_cards is not None:
            league_avg = (features.home_cards_for + features.home_cards_against +
                         features.away_cards_for + features.away_cards_against) / 2.0
            if league_avg > 0:
                ref_factor = features.referee_avg_cards / league_avg
                lam_h *= ref_factor * ref_adjustment
                lam_a *= ref_factor * ref_adjustment
        
        total = {}
        for line in total_lines:
            p = _poisson_over_sum(lam_h, lam_a, line)
            total[f"Cartoes Over {line}"] = p
            total[f"Cartoes Under {line}"] = 1.0 - p
        
        home_probs = {}
        away_probs = {}
        for line in team_lines:
            home_probs[f"Casa Cartoes Over {line}"] = _poisson_team_over(lam_h, line)
            home_probs[f"Casa Cartoes Under {line}"] = 1.0 - _poisson_team_over(lam_h, line)
            away_probs[f"Fora Cartoes Over {line}"] = _poisson_team_over(lam_a, line)
            away_probs[f"Fora Cartoes Under {line}"] = 1.0 - _poisson_team_over(lam_a, line)
        
        return CardsMarket(lam_h, lam_a, total, home_probs, away_probs, self.version)

class NegBinCardsModel:
    """Negative Binomial for cards."""
    version = "CARDS_NEGBIN_V1"
    
    def predict(self, features: CardsFeatures,
                total_lines: tuple[float, ...] = (2.5, 3.5, 4.5, 5.5),
                team_lines: tuple[float, ...] = (1.5, 2.5),
                r: float = 3.0) -> CardsMarket:
        lam_h = max(0.3, (features.home_cards_for + features.away_cards_against) / 2.0)
        lam_a = max(0.3, (features.away_cards_for + features.home_cards_against) / 2.0)
        
        if features.referee_avg_cards is not None:
            league_avg = (features.home_cards_for + features.home_cards_against +
                         features.away_cards_for + features.away_cards_against) / 2.0
            if league_avg > 0:
                lam_h *= features.referee_avg_cards / league_avg
                lam_a *= features.referee_avg_cards / league_avg
        
        total = {}
        for line in total_lines:
            p = _negbin_over_sum(lam_h, lam_a, line, r)
            total[f"Cartoes Over {line}"] = p
            total[f"Cartoes Under {line}"] = 1.0 - p
        
        home_probs = {}
        away_probs = {}
        for line in team_lines:
            home_probs[f"Casa Cartoes Over {line}"] = _negbin_team_over(lam_h, line, r)
            home_probs[f"Casa Cartoes Under {line}"] = 1.0 - _negbin_team_over(lam_h, line, r)
            away_probs[f"Fora Cartoes Over {line}"] = _negbin_team_over(lam_a, line, r)
            away_probs[f"Fora Cartoes Under {line}"] = 1.0 - _negbin_team_over(lam_a, line, r)
        
        return CardsMarket(lam_h, lam_a, total, home_probs, away_probs, self.version)


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam + k * math.log(max(1e-12, lam)) - math.lgamma(k + 1))

def _poisson_over_sum(lam_h: float, lam_a: float, line: float, max_n: int = 20) -> float:
    total = 0.0
    for h in range(max_n):
        for a in range(max_n):
            if h + a > line:
                total += _poisson_pmf(h, lam_h) * _poisson_pmf(a, lam_a)
    return min(1.0, max(0.0, total))

def _poisson_team_over(lam: float, line: float, max_n: int = 20) -> float:
    total = 0.0
    for k in range(max_n):
        if k > line:
            total += _poisson_pmf(k, lam)
    return min(1.0, max(0.0, total))

def _negbin_pmf(k: int, mu: float, r: float) -> float:
    p = r / (r + mu) if r + mu > 0 else 0.5
    return math.exp(
        math.lgamma(k + r) - math.lgamma(k + 1) - math.lgamma(r) +
        r * math.log(max(1e-12, p)) + k * math.log(max(1e-12, 1 - p))
    )

def _negbin_over_sum(lam_h: float, lam_a: float, line: float, r: float, max_n: int = 20) -> float:
    total = 0.0
    for h in range(max_n):
        for a in range(max_n):
            if h + a > line:
                total += _negbin_pmf(h, lam_h, r) * _negbin_pmf(a, lam_a, r)
    return min(1.0, max(0.0, total))

def _negbin_team_over(lam: float, line: float, r: float, max_n: int = 20) -> float:
    total = 0.0
    for k in range(max_n):
        if k > line:
            total += _negbin_pmf(k, lam, r)
    return min(1.0, max(0.0, total))
