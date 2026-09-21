from dataclasses import dataclass
from math import isfinite
from statistics import mean, median, pstdev
from ..timeutil import utc_key


@dataclass(frozen=True)
class OddsQuote:
    book: str
    outcome: str
    odd: float
    timestamp: str
    market: str = "1x2"
    closing: bool = False

    def __post_init__(self):
        if not isfinite(self.odd) or self.odd <= 1:
            raise ValueError("odd inválida")
        utc_key(self.timestamp)


def odds_features(quotes, prediction_timestamp, kickoff):
    cutoff = min(utc_key(prediction_timestamp), utc_key(kickoff))
    visible = sorted([q for q in quotes if utc_key(q.timestamp) < cutoff and not q.closing],
                     key=lambda q: utc_key(q.timestamp))
    result = {}
    for market, outcome in sorted({(q.market, q.outcome) for q in visible}):
        rows = [q for q in visible if (q.market, q.outcome) == (market, outcome)]
        latest = {q.book: q for q in rows}
        odds = [q.odd for q in latest.values()]
        result[f"{market}_{outcome}"] = {"opening": rows[0].odd, "best": max(odds),
            "mean": mean(odds), "median": median(odds), "dispersion": pstdev(odds),
            "movement": rows[-1].odd - rows[0].odd,
            "change_pct": rows[-1].odd / rows[0].odd - 1}
    return result
