"""BETGSN :: canonical — Modelos canônicos independentes de API.

Toda fonte de dados (API-Football, football-data.co.uk, The Odds API, etc.)
deve ser convertida para estes formatos antes de entrar no pipeline.
Nenhum modelo do sistema depende diretamente do JSON de uma API.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DataConfidence(str, Enum):
    HIGH = "HIGH"           # multiple consistent sources
    MEDIUM = "MEDIUM"       # single reliable source
    LOW = "LOW"             # estimated or single unreliable source
    CONFLICT = "CONFLICT"   # sources disagree


@dataclass(frozen=True)
class CanonicalTeam:
    name: str
    canonical_name: str  # normalized, accent-stripped
    country: str = ""
    league: str = ""
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalOddsLine:
    """One odds line for one outcome from one bookmaker at one point in time."""
    bookmaker: str
    market: str           # e.g. "Resultado Final (1X2)"
    outcome: str          # e.g. "1", "Over 2.5"
    odd: float
    timestamp: str = ""   # ISO 8601 UTC when this price was observed
    is_opening: bool = False
    is_closing: bool = False


@dataclass(frozen=True)
class CanonicalOdds:
    """All odds for one match, organized by market."""
    match_key: str        # date|home|away canonical key
    lines: tuple[CanonicalOddsLine, ...] = ()
    retrieved_at: str = ""
    source: str = ""

    def by_market(self) -> dict[str, dict[str, dict[str, float]]]:
        """Returns {market: {bookmaker: {outcome: odd}}} for pipeline consumption."""
        result: dict[str, dict[str, dict[str, float]]] = {}
        for line in self.lines:
            result.setdefault(line.market, {}).setdefault(line.bookmaker, {})[line.outcome] = line.odd
        return result

    def best_before(self, cutoff: str) -> "CanonicalOdds":
        """Returns odds observed strictly before the cutoff."""
        filtered = tuple(l for l in self.lines if l.timestamp and l.timestamp < cutoff)
        return CanonicalOdds(self.match_key, filtered, self.retrieved_at, self.source)


@dataclass(frozen=True)
class CanonicalMatchStats:
    """Per-team statistics for a match."""
    shots: Optional[int] = None
    shots_on_target: Optional[int] = None
    corners: Optional[int] = None
    fouls: Optional[int] = None
    yellow_cards: Optional[int] = None
    red_cards: Optional[int] = None
    possession_pct: Optional[float] = None
    passes: Optional[int] = None
    pass_accuracy_pct: Optional[float] = None
    offsides: Optional[int] = None
    tackles: Optional[int] = None
    interceptions: Optional[int] = None
    saves: Optional[int] = None
    blocked_shots: Optional[int] = None
    free_kicks: Optional[int] = None

    @property
    def cards_total(self) -> Optional[int]:
        y = self.yellow_cards
        r = self.red_cards
        if y is None and r is None:
            return None
        return (y or 0) + (r or 0)


@dataclass(frozen=True)
class CanonicalXG:
    """Expected goals observation with provenance."""
    home_xg: Optional[float] = None
    away_xg: Optional[float] = None
    status: str = "UNAVAILABLE"  # REAL, ESTIMATED, UNAVAILABLE
    source: str = ""
    available_at: str = ""       # when this data became available (ISO 8601)


@dataclass(frozen=True)
class CanonicalMatch:
    """A match in canonical form — the central data object."""
    home: str
    away: str
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None
    kickoff: str = ""            # ISO 8601 UTC
    timezone: str = "UTC"
    league: str = ""
    division: str = ""
    season: str = ""
    matchday: int = 0
    referee: str = ""
    attendance: Optional[int] = None
    # stats
    home_stats: Optional[CanonicalMatchStats] = None
    away_stats: Optional[CanonicalMatchStats] = None
    # xG
    xg: CanonicalXG = field(default_factory=CanonicalXG)
    # odds
    odds: Optional[CanonicalOdds] = None
    # provenance
    source: str = ""
    retrieved_at: str = ""
    result_available_at: str = ""
    confidence: DataConfidence = DataConfidence.MEDIUM

    @property
    def finished(self) -> bool:
        return self.home_goals is not None and self.away_goals is not None

    @property
    def match_key(self) -> str:
        return f"{self.kickoff[:10]}|{self.home}|{self.away}"


@dataclass(frozen=True)
class CanonicalReferee:
    """Referee profile built from historical data."""
    name: str
    matches: int = 0
    avg_fouls: float = 0.0
    avg_yellow_cards: float = 0.0
    avg_red_cards: float = 0.0
    avg_total_cards: float = 0.0
    home_card_ratio: float = 0.5  # fraction of cards given to home team
    seasons: tuple[str, ...] = ()
    leagues: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalInjury:
    """A player injury/absence."""
    player: str
    team: str
    type: str = ""        # "injury", "suspension", "international", "other"
    reason: str = ""
    status: str = ""      # "out", "doubtful", "questionable"
    expected_return: str = ""  # ISO date or empty
    source: str = ""
    retrieved_at: str = ""


@dataclass(frozen=True)
class CanonicalLineup:
    """Starting lineup for one team in a match."""
    team: str
    formation: str = ""
    starters: tuple[str, ...] = ()
    substitutes: tuple[str, ...] = ()
    coach: str = ""
    source: str = ""
    retrieved_at: str = ""


@dataclass(frozen=True)
class ProviderRecord:
    """Audit record for a data retrieval."""
    provider: str
    field: str
    value: str
    retrieved_at: str
    source_timestamp: str = ""
    confidence: DataConfidence = DataConfidence.MEDIUM
