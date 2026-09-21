"""BETGSN :: features.odds — features de odds point-in-time.

Toda cotacao posterior ao instante da previsao (ou ao kickoff) e descartada.
Alem das estatisticas de preco, o bloco agora informa QUANTAS casas
sustentam o numero: com menos de `MIN_CONSENSUS_BOOKS` o consenso e
marcado como limitado, porque a mediana de uma ou duas casas nao e
consenso de mercado.
"""

from dataclasses import dataclass
from math import isfinite
from statistics import mean, median, pstdev

from ..engine import consensus_fair_probs, implied_prob, market_groups
from ..timeutil import utc_key

#: Abaixo disso o consenso e marcado como limitado.
MIN_CONSENSUS_BOOKS = 3


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


def _market_overround(medians: dict[str, float]) -> float | None:
    """Soma das implicitas das medianas de um grupo completo, se houver."""
    groups = [g for g in market_groups(list(medians)) if set(g) <= set(medians)]
    if not groups:
        return None
    group = max(groups, key=len)
    if len(group) < 2:
        return None
    return sum(implied_prob(medians[o]) for o in group)


def odds_features(quotes, prediction_timestamp, kickoff):
    cutoff = min(utc_key(prediction_timestamp), utc_key(kickoff))
    visible = sorted(
        [q for q in quotes if utc_key(q.timestamp) < cutoff and not q.closing],
        key=lambda q: utc_key(q.timestamp),
    )
    result = {}

    by_market: dict[str, dict[str, dict[str, float]]] = {}
    for quote in visible:
        by_market.setdefault(quote.market, {}).setdefault(quote.book, {})[
            quote.outcome
        ] = quote.odd

    for market, by_book in by_market.items():
        fair_probs = consensus_fair_probs(by_book)
        medians = {
            outcome: median(
                [odds[outcome] for odds in by_book.values() if outcome in odds]
            )
            for outcome in sorted({o for odds in by_book.values() for o in odds})
        }
        overround = _market_overround(medians)

        for outcome in medians:
            rows = [
                q
                for q in visible
                if (q.market, q.outcome) == (market, outcome)
            ]
            latest = {q.book: q for q in rows}
            odds = [q.odd for q in latest.values()]
            best_book, best_quote = max(latest.items(), key=lambda kv: kv[1].odd)
            best_odd = best_quote.odd
            book_count = len(latest)
            fair_probability = fair_probs.get(outcome)
            fair_odd = (
                1.0 / fair_probability
                if fair_probability is not None and fair_probability > 0
                else None
            )
            result[f"{market}_{outcome}"] = {
                "opening": rows[0].odd,
                "best": best_odd,
                "best_book": best_book,
                "mean": mean(odds),
                "median": median(odds),
                "dispersion": pstdev(odds),
                "movement": rows[-1].odd - rows[0].odd,
                "change_pct": rows[-1].odd / rows[0].odd - 1,
                "book_count": book_count,
                "consensus_limited": book_count < MIN_CONSENSUS_BOOKS,
                "overround": overround,
                "fair_probability": fair_probability,
                "fair_odd": fair_odd,
                "edge_vs_fair": (
                    best_odd * fair_probability - 1.0
                    if fair_probability is not None and 0.0 < fair_probability < 1.0
                    else None
                ),
            }
    return result
