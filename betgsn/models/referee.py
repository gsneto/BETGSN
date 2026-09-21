"""BETGSN :: models.referee — Perfil temporal de árbitros.

Constrói perfil por árbitro usando apenas histórico anterior ao jogo.
Nunca usa informação futura.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Sequence

@dataclass
class RefereeProfile:
    """Temporal profile of a referee built from historical data only."""
    name: str
    matches: int = 0
    total_fouls: int = 0
    total_yellow: int = 0
    total_red: int = 0
    total_corners: int = 0
    home_yellow: int = 0
    away_yellow: int = 0
    home_red: int = 0
    away_red: int = 0
    seasons: set = field(default_factory=set)
    leagues: set = field(default_factory=set)
    last_match: str = ""      # ISO 8601
    
    @property
    def avg_fouls(self) -> float:
        return self.total_fouls / self.matches if self.matches else 0.0
    
    @property
    def avg_yellow(self) -> float:
        return self.total_yellow / self.matches if self.matches else 0.0
    
    @property
    def avg_red(self) -> float:
        return self.total_red / self.matches if self.matches else 0.0
    
    @property
    def avg_cards(self) -> float:
        return (self.total_yellow + self.total_red) / self.matches if self.matches else 0.0
    
    @property
    def avg_corners(self) -> float:
        return self.total_corners / self.matches if self.matches else 0.0
    
    @property
    def home_card_ratio(self) -> float:
        """Fraction of cards given to home team."""
        total_home = self.home_yellow + self.home_red
        total_away = self.away_yellow + self.away_red
        total = total_home + total_away
        return total_home / total if total > 0 else 0.5
    
    @property
    def strictness(self) -> float:
        """Relative strictness (1.0 = league average)."""
        # This needs to be set externally after computing league average
        return 1.0
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "matches": self.matches,
            "avg_fouls": round(self.avg_fouls, 2),
            "avg_yellow": round(self.avg_yellow, 2),
            "avg_red": round(self.avg_red, 2),
            "avg_cards": round(self.avg_cards, 2),
            "avg_corners": round(self.avg_corners, 2),
            "home_card_ratio": round(self.home_card_ratio, 3),
            "seasons": sorted(self.seasons),
            "leagues": sorted(self.leagues),
        }


class RefereeEngine:
    """Builds temporal referee profiles from historical matches.
    
    Call update() for each match in chronological order.
    Call profile_before() to get the profile at a point in time.
    """
    
    def __init__(self) -> None:
        self._profiles: dict[str, RefereeProfile] = {}
        self._snapshots: list[tuple[str, str, RefereeProfile]] = []  # (cutoff, name, snapshot)
    
    def update(self, match) -> None:
        """Update referee profile with match data.
        
        match should have: referee, home_corners, away_corners,
        home_yellow, away_yellow, home_red, away_red, 
        home_fouls, away_fouls, kickoff, season, league
        """
        name = getattr(match, "referee", "")
        if not name or name.lower() in ("", "unknown", "n/a"):
            return
        
        if name not in self._profiles:
            self._profiles[name] = RefereeProfile(name=name)
        
        p = self._profiles[name]
        p.matches += 1
        
        # Cards
        hy = getattr(match, "home_yellow", None) or 0
        ay = getattr(match, "away_yellow", None) or 0
        hr = getattr(match, "home_red", None) or 0
        ar = getattr(match, "away_red", None) or 0
        p.total_yellow += hy + ay
        p.total_red += hr + ar
        p.home_yellow += hy
        p.away_yellow += ay
        p.home_red += hr
        p.away_red += ar
        
        # Corners
        hc = getattr(match, "home_corners", None) or 0
        ac = getattr(match, "away_corners", None) or 0
        p.total_corners += hc + ac
        
        # Fouls (if available)
        hf = getattr(match, "home_fouls", None) or 0
        af = getattr(match, "away_fouls", None) or 0
        p.total_fouls += hf + af
        
        # Metadata
        season = getattr(match, "season", "")
        league = getattr(match, "league", "")
        if season:
            p.seasons.add(season)
        if league:
            p.leagues.add(league)
        
        kickoff = getattr(match, "kickoff", "")
        if kickoff:
            p.last_match = kickoff
    
    def profile(self, name: str) -> Optional[RefereeProfile]:
        """Current profile (latest state)."""
        return self._profiles.get(name)
    
    def all_profiles(self) -> dict[str, RefereeProfile]:
        return dict(self._profiles)
    
    def league_averages(self) -> dict[str, float]:
        """Compute league-average referee stats."""
        if not self._profiles:
            return {}
        total_matches = sum(p.matches for p in self._profiles.values())
        if total_matches == 0:
            return {}
        return {
            "avg_cards": sum(p.avg_cards * p.matches for p in self._profiles.values()) / total_matches,
            "avg_fouls": sum(p.avg_fouls * p.matches for p in self._profiles.values()) / total_matches,
            "avg_corners": sum(p.avg_corners * p.matches for p in self._profiles.values()) / total_matches,
        }
    
    def strictness(self, name: str) -> float:
        """Referee strictness relative to league average."""
        p = self._profiles.get(name)
        if p is None or p.matches < 5:
            return 1.0  # neutral if insufficient data
        avgs = self.league_averages()
        league_cards = avgs.get("avg_cards", 4.0)
        if league_cards <= 0:
            return 1.0
        return p.avg_cards / league_cards
