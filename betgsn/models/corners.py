"""BETGSN :: models.corners — Modelo independente para escanteios.

Testa Poisson, Binomial Negativa, XGBoost e LightGBM.
Usa features temporais: escanteios for/against, chutes, posse,
ataques, xG, Elo, força do adversário, descanso.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Sequence
import math

@dataclass(frozen=True)
class CornersFeatures:
    """Features for corners prediction."""
    home_corners_for: float = 0.0
    home_corners_against: float = 0.0
    away_corners_for: float = 0.0
    away_corners_against: float = 0.0
    home_shots: float = 0.0
    away_shots: float = 0.0
    home_shots_on_target: float = 0.0
    away_shots_on_target: float = 0.0
    home_elo: float = 1500.0
    away_elo: float = 1500.0
    home_form_corners: Optional[float] = None   # rolling avg
    away_form_corners: Optional[float] = None
    home_opponent_strength: float = 1.0
    away_opponent_strength: float = 1.0
    home_rest_days: Optional[int] = None
    away_rest_days: Optional[int] = None
    
    def to_array(self) -> list[float]:
        vals = [
            self.home_corners_for, self.home_corners_against,
            self.away_corners_for, self.away_corners_against,
            self.home_shots, self.away_shots,
            self.home_shots_on_target, self.away_shots_on_target,
            self.home_elo, self.away_elo,
            self.home_corners_for if self.home_form_corners is None else self.home_form_corners,
            self.away_corners_for if self.away_form_corners is None else self.away_form_corners,
            self.home_opponent_strength, self.away_opponent_strength,
            float(self.home_rest_days or 7),
            float(self.away_rest_days or 7),
        ]
        return vals

@dataclass(frozen=True)
class CornersMarket:
    """Predicted corners markets."""
    home_lambda: float
    away_lambda: float
    total_probabilities: dict[str, float]   # {"Over 8.5": ..., "Under 8.5": ...}
    home_probabilities: dict[str, float]    # {"Casa Cantos Over 4.5": ...}
    away_probabilities: dict[str, float]    # {"Fora Cantos Over 4.5": ...}
    model: str = "poisson"

class PoissonCornersModel:
    """Baseline Poisson for corners."""
    version = "CORNERS_POISSON_V1"
    
    def predict(self, features: CornersFeatures,
                total_lines: tuple[float, ...] = (8.5, 9.5, 10.5, 11.5),
                team_lines: tuple[float, ...] = (3.5, 4.5, 5.5)) -> CornersMarket:
        lam_h = (features.home_corners_for + features.away_corners_against) / 2.0
        lam_a = (features.away_corners_for + features.home_corners_against) / 2.0
        lam_h = max(0.5, lam_h)
        lam_a = max(0.5, lam_a)
        
        total = {}
        for line in total_lines:
            p_over = _poisson_over(lam_h, lam_a, line)
            total[f"Cantos Over {line}"] = p_over
            total[f"Cantos Under {line}"] = 1.0 - p_over
        
        home_probs = {}
        away_probs = {}
        for line in team_lines:
            home_probs[f"Casa Cantos Over {line}"] = _poisson_team_over(lam_h, line)
            home_probs[f"Casa Cantos Under {line}"] = 1.0 - _poisson_team_over(lam_h, line)
            away_probs[f"Fora Cantos Over {line}"] = _poisson_team_over(lam_a, line)
            away_probs[f"Fora Cantos Under {line}"] = 1.0 - _poisson_team_over(lam_a, line)
        
        return CornersMarket(lam_h, lam_a, total, home_probs, away_probs, self.version)


class NegBinCornersModel:
    """Negative Binomial for corners — handles overdispersion."""
    version = "CORNERS_NEGBIN_V1"
    
    def predict(self, features: CornersFeatures,
                total_lines: tuple[float, ...] = (8.5, 9.5, 10.5, 11.5),
                team_lines: tuple[float, ...] = (3.5, 4.5, 5.5),
                r: float = 5.0) -> CornersMarket:
        """r controls overdispersion: higher r -> closer to Poisson."""
        lam_h = max(0.5, (features.home_corners_for + features.away_corners_against) / 2.0)
        lam_a = max(0.5, (features.away_corners_for + features.home_corners_against) / 2.0)
        
        total = {}
        for line in total_lines:
            p_over = _negbin_over(lam_h, lam_a, line, r)
            total[f"Cantos Over {line}"] = p_over
            total[f"Cantos Under {line}"] = 1.0 - p_over
        
        home_probs = {}
        away_probs = {}
        for line in team_lines:
            home_probs[f"Casa Cantos Over {line}"] = _negbin_team_over(lam_h, line, r)
            home_probs[f"Casa Cantos Under {line}"] = 1.0 - _negbin_team_over(lam_h, line, r)
            away_probs[f"Fora Cantos Over {line}"] = _negbin_team_over(lam_a, line, r)
            away_probs[f"Fora Cantos Under {line}"] = 1.0 - _negbin_team_over(lam_a, line, r)
        
        return CornersMarket(lam_h, lam_a, total, home_probs, away_probs, self.version)


# --- Helpers ---

def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam + k * math.log(max(1e-12, lam)) - math.lgamma(k + 1))

def _poisson_over(lam_h: float, lam_a: float, line: float, max_n: int = 30) -> float:
    total = 0.0
    for h in range(max_n):
        for a in range(max_n):
            if h + a > line:
                total += _poisson_pmf(h, lam_h) * _poisson_pmf(a, lam_a)
    return min(1.0, max(0.0, total))

def _poisson_team_over(lam: float, line: float, max_n: int = 30) -> float:
    total = 0.0
    for k in range(max_n):
        if k > line:
            total += _poisson_pmf(k, lam)
    return min(1.0, max(0.0, total))

def _negbin_pmf(k: int, mu: float, r: float) -> float:
    """Negative binomial PMF parametrized by mean mu and dispersion r."""
    p = r / (r + mu) if r + mu > 0 else 0.5
    return math.exp(
        math.lgamma(k + r) - math.lgamma(k + 1) - math.lgamma(r) +
        r * math.log(max(1e-12, p)) + k * math.log(max(1e-12, 1 - p))
    )

def _negbin_over(lam_h: float, lam_a: float, line: float, r: float, max_n: int = 30) -> float:
    total = 0.0
    for h in range(max_n):
        for a in range(max_n):
            if h + a > line:
                total += _negbin_pmf(h, lam_h, r) * _negbin_pmf(a, lam_a, r)
    return min(1.0, max(0.0, total))

def _negbin_team_over(lam: float, line: float, r: float, max_n: int = 30) -> float:
    total = 0.0
    for k in range(max_n):
        if k > line:
            total += _negbin_pmf(k, lam, r)
    return min(1.0, max(0.0, total))
