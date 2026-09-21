"""Elo incremental com estados casa/fora; atualizações só após disponibilidade."""
from dataclasses import dataclass, field


@dataclass
class Elo:
    initial: float = 1500
    k: float = 20
    home_advantage: float = 65
    overall: dict = field(default_factory=dict)
    home: dict = field(default_factory=dict)
    away: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.k <= 0:
            raise ValueError("K deve ser positivo")

    def rating(self, team, venue=None):
        table = self.overall if venue is None else self.home if venue == "home" else self.away
        return table.get(team, self.initial)

    def update(self, match):
        actual = 1 if match.home_goals > match.away_goals else .5 if match.home_goals == match.away_goals else 0
        for ht, at in ((self.overall, self.overall), (self.home, self.away)):
            h, a = ht.get(match.home, self.initial), at.get(match.away, self.initial)
            expected = 1 / (1 + 10 ** ((a - h - self.home_advantage) / 400))
            delta = self.k * (actual - expected)
            ht[match.home], at[match.away] = h + delta, a - delta
