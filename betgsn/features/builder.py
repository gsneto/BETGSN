"""Builder monotônico: índice de disponibilidade, Elo incremental, snapshot auditável."""
from dataclasses import dataclass
from bisect import bisect_left
from collections import defaultdict
from ..temporal import result_time
from ..timeutil import utc_key
from .elo import Elo
from .form import form
from .rest import rest
from .h2h import h2h
from .strength import strength
from .xg import visible_xg


@dataclass(frozen=True)
class FeatureSnapshot:
    kickoff: str
    prediction_timestamp: str
    feature_time: str | None
    values: dict[str, float | None]
    version: str = "FEATURES_V1"
    xg_status: str = "UNAVAILABLE"


class FeatureBuilder:
    def __init__(self, matches, elo=None):
        indexed = sorted((result_time(m), utc_key(m.kickoff, m.timezone), i, m) for i, m in enumerate(matches))
        self.keys = [k for k, _, _, _ in indexed]
        self.matches = [m for _, _, _, m in indexed]
        self.elo = elo or Elo()
        self.entries = defaultdict(list)
        self.pre_ratings = {}
        self.position = 0
        self.last_cutoff = ""

    def build(self, fixture, prediction_timestamp=None):
        kickoff = utc_key(fixture.kickoff)
        cutoff = utc_key(prediction_timestamp or kickoff)
        if cutoff > kickoff or cutoff < self.last_cutoff:
            raise ValueError("builder requer previsões cronológicas anteriores ao kickoff")
        stop = bisect_left(self.keys, cutoff)
        while self.position < stop:
            m = self.matches[self.position]
            self.pre_ratings[id(m)] = (self.elo.rating(m.home), self.elo.rating(m.away))
            self.elo.update(m)
            self.entries[m.home].append(m)
            self.entries[m.away].append(m)
            self.position += 1
        self.last_cutoff = cutoff
        values = {}
        for prefix, team in (("home", fixture.home), ("away", fixture.away)):
            entries = sorted(self.entries[team], key=lambda m: utc_key(m.kickoff, m.timezone))
            safe = [visible_xg(m, cutoff) for m in entries]
            values[prefix + "_elo"] = self.elo.rating(team)
            for venue in ("home", "away"):
                values[f"{prefix}_elo_{venue}"] = self.elo.rating(team, venue)
            for window in (5, 10, 20):
                for venue in (None, "home", "away"):
                    for k, v in form(safe, team, cutoff, window, venue).items():
                        values[f"{prefix}_form_{window}_{venue or 'overall'}_{k}"] = v
            for k, v in rest(entries, cutoff).items():
                values[f"{prefix}_{k}"] = v
            for k, v in strength(entries, team, self.pre_ratings).items():
                values[f"{prefix}_{k}"] = v
        values["elo_difference"] = values["home_elo"] - values["away_elo"]
        for k, v in h2h(self.entries[fixture.home], fixture.home, fixture.away, cutoff).items():
            values["h2h_" + k] = v
        return FeatureSnapshot(kickoff, cutoff, self.keys[stop-1] if stop else None, values)
