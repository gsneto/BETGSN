"""BETGSN :: line_shopping — melhor preco, consenso, comparabilidade e arbitragem.

O valor nao esta so na previsao: esta em pegar a MELHOR odd para o mesmo
resultado entre varias casas. Este modulo responde tres perguntas:

  1. Qual o melhor preco, de qual casa, e quando foi observado?
  2. O consenso do mercado (mediana entre casas, sem margem) concorda?
  3. As odds comparadas foram observadas no MESMO momento?

Regra de honestidade temporal
-----------------------------
Comparar uma odd de agora com outra de 40 minutos atras produz um "melhor
preco" que talvez nunca tenha existido. Por isso todo resultado carrega
`comparable` e a dispersao temporal. Se `comparable=False`, o consumidor
deve tratar a comparacao como indicativa, nunca como oportunidade certa.

Regra dos poucos bookmakers
---------------------------
Com menos de `MIN_CONSENSUS_BOOKS` (3) casas, o "consenso" e fraco. O
resultado e marcado com `consensus_limited=True` e o numero de casas fica
explicito (`n_books`). Nunca se inventa uma terceira casa para completar
um consenso.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional, Sequence

from .engine import market_groups, scan_arbitrage
from .odds_math import MarketProbabilities, devig, edge_vs_fair
from .odds_normalize import NormalizedQuote
from .timeutil import KickoffError, parse_kickoff

#: Abaixo disso o consenso e marcado como limitado.
MIN_CONSENSUS_BOOKS = 3

#: Duas odds com mais que isso de diferenca nao sao "do mesmo momento".
DEFAULT_MAX_TIMESTAMP_SPAN_SECONDS = 900.0


@dataclass(frozen=True)
class PriceOption:
    bookmaker: str
    price: float
    timestamp: str
    provider: str

    def to_dict(self) -> dict:
        return {
            "bookmaker": self.bookmaker,
            "price": round(self.price, 6),
            "timestamp": self.timestamp,
            "provider": self.provider,
        }


@dataclass(frozen=True)
class LineShoppingResult:
    """Melhor preco de uma linha (evento + mercado + resultado)."""

    event_id: str
    market: str
    selection: str
    options: tuple[PriceOption, ...]
    best: PriceOption
    worst: PriceOption
    median_odd: float
    n_books: int
    consensus_limited: bool
    timestamp_span_seconds: float
    comparable: bool
    fair_probability: Optional[float] = None
    fair_odd: Optional[float] = None
    edge: Optional[float] = None
    overround: Optional[float] = None
    devig_method: str = "multiplicative"
    warnings: tuple[str, ...] = ()

    @property
    def book_count(self) -> int:
        return self.n_books

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "market": self.market,
            "selection": self.selection,
            "best": self.best.to_dict(),
            "worst": self.worst.to_dict(),
            "median_odd": round(self.median_odd, 6),
            "n_books": self.n_books,
            "book_count": self.n_books,
            "consensus_limited": self.consensus_limited,
            "timestamp_span_seconds": round(self.timestamp_span_seconds, 3),
            "comparable": self.comparable,
            "fair_probability": (
                round(self.fair_probability, 8)
                if self.fair_probability is not None
                else None
            ),
            "fair_odd": round(self.fair_odd, 6) if self.fair_odd is not None else None,
            "edge": round(self.edge, 6) if self.edge is not None else None,
            "overround": round(self.overround, 6) if self.overround is not None else None,
            "devig_method": self.devig_method,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class MarketAnalysis:
    """Consenso sem margem de um mercado completo."""

    market: str
    medians: dict[str, float]
    probabilities: MarketProbabilities
    complete: bool
    n_books: dict[str, int]

    @property
    def overround(self) -> float:
        return self.probabilities.overround

    @property
    def fair_probabilities(self) -> dict[str, float]:
        return self.probabilities.fair_probabilities


def _timestamp_span_seconds(options: Sequence[PriceOption]) -> float:
    stamps = []
    for option in options:
        try:
            stamps.append(parse_kickoff(option.timestamp))
        except KickoffError:
            continue
    if len(stamps) < 2:
        return 0.0
    return abs((max(stamps) - min(stamps)).total_seconds())


def _median(values: Sequence[float]) -> float:
    return statistics.median(values) if values else 0.0


def market_analysis(
    quotes: Sequence[NormalizedQuote],
    market: str,
    method: str = "multiplicative",
) -> Optional[MarketAnalysis]:
    """Consenso sem margem de um mercado, agrupando linhas completas.

    Usa a MEDIANA entre casas (robusta a uma casa atrasada) e remove a
    margem por grupo. `complete` so e True quando o grupo cobre o mercado
    inteiro — sem isso, normalizar e uma aproximacao local.
    """
    usable = [q for q in quotes if q.market == market and q.usable]
    if not usable:
        return None

    by_selection: dict[str, list[float]] = {}
    books_by_selection: dict[str, set[str]] = {}
    for quote in usable:
        by_selection.setdefault(quote.selection, []).append(quote.price)
        books_by_selection.setdefault(quote.selection, set()).add(quote.bookmaker)

    medians = {sel: _median(prices) for sel, prices in by_selection.items()}
    if not medians:
        return None

    fair: dict[str, float] = {}
    overround = 0.0
    complete = False
    groups = [g for g in market_groups(list(medians)) if set(g) <= set(medians)]
    for group in groups:
        target = 2 if set(group) == {"1X", "12", "X2"} else 1
        group_odds = {sel: medians[sel] for sel in group}
        result = devig(group_odds, method=method, complete=True)
        for sel, probability in result.fair_probabilities.items():
            fair[sel] = target * probability
        if len(group) >= 2:
            overround = result.overround
            complete = True

    probabilities = MarketProbabilities(
        fair_probabilities=fair,
        overround=overround,
        method=method,
        complete=complete,
    )
    return MarketAnalysis(
        market=market,
        medians=medians,
        probabilities=probabilities,
        complete=complete,
        n_books={sel: len(books) for sel, books in books_by_selection.items()},
    )


def line_shop(
    quotes: Sequence[NormalizedQuote],
    market: str,
    selection: str,
    event_id: Optional[str] = None,
    min_books: int = MIN_CONSENSUS_BOOKS,
    max_timestamp_span_seconds: float = DEFAULT_MAX_TIMESTAMP_SPAN_SECONDS,
    devig_method: str = "multiplicative",
) -> Optional[LineShoppingResult]:
    """Melhor preco de uma linha, com consenso e aviso de comparabilidade."""
    relevant = [
        q
        for q in quotes
        if q.usable
        and q.market == market
        and q.selection == selection
        and (event_id is None or q.event_id == event_id)
    ]
    if not relevant:
        return None

    # uma cotacao por casa: a mais recente
    latest: dict[str, NormalizedQuote] = {}
    for quote in relevant:
        current = latest.get(quote.bookmaker)
        if current is None or quote.timestamp >= current.timestamp:
            latest[quote.bookmaker] = quote

    options = tuple(
        sorted(
            (
                PriceOption(q.bookmaker, q.price, q.timestamp, q.provider)
                for q in latest.values()
            ),
            key=lambda o: o.price,
            reverse=True,
        )
    )
    best = options[0]
    worst = options[-1]
    n_books = len(options)
    span = _timestamp_span_seconds(options)
    comparable = span <= max_timestamp_span_seconds

    warnings: list[str] = []
    if n_books < min_books:
        warnings.append("CONSENSUS_LIMITED")
    if not comparable:
        warnings.append("TIMESTAMPS_INCOMPARABLE")

    scope = [
        q
        for q in quotes
        if q.usable and q.market == market and (event_id is None or q.event_id == event_id)
    ]
    analysis = market_analysis(scope, market, method=devig_method)
    fair_probability = analysis.fair_probabilities.get(selection) if analysis else None
    fair_odd = (
        1.0 / fair_probability
        if fair_probability is not None and fair_probability > 0
        else None
    )
    edge = (
        edge_vs_fair(best.price, fair_probability)
        if fair_probability is not None and 0.0 < fair_probability < 1.0
        else None
    )
    if analysis is not None and not analysis.complete:
        warnings.append("MARKET_INCOMPLETE")

    return LineShoppingResult(
        event_id=event_id or relevant[0].event_id,
        market=market,
        selection=selection,
        options=options,
        best=best,
        worst=worst,
        median_odd=_median([o.price for o in options]),
        n_books=n_books,
        consensus_limited=n_books < min_books,
        timestamp_span_seconds=span,
        comparable=comparable,
        fair_probability=fair_probability,
        fair_odd=fair_odd,
        edge=edge,
        overround=analysis.overround if analysis else None,
        devig_method=devig_method,
        warnings=tuple(warnings),
    )


def line_shopping_report(
    quotes: Sequence[NormalizedQuote],
    min_books: int = MIN_CONSENSUS_BOOKS,
    max_timestamp_span_seconds: float = DEFAULT_MAX_TIMESTAMP_SPAN_SECONDS,
    devig_method: str = "multiplicative",
) -> list[LineShoppingResult]:
    """Todas as linhas comparaveis, melhores primeiro (maior edge)."""
    keys = sorted({(q.event_id, q.market, q.selection) for q in quotes if q.usable})
    results: list[LineShoppingResult] = []
    for event_id, market, selection in keys:
        result = line_shop(
            quotes,
            market,
            selection,
            event_id=event_id,
            min_books=min_books,
            max_timestamp_span_seconds=max_timestamp_span_seconds,
            devig_method=devig_method,
        )
        if result is not None:
            results.append(result)
    results.sort(
        key=lambda r: (r.edge if r.edge is not None else -1.0, r.best.price),
        reverse=True,
    )
    return results


# --------------------------------------------------------------------------
# Arbitragem
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ArbitrageLeg:
    selection: str
    bookmaker: str
    price: float
    stake: float
    payout: float
    timestamp: str
    provider: str

    def to_dict(self) -> dict:
        return {
            "selection": self.selection,
            "bookmaker": self.bookmaker,
            "price": round(self.price, 6),
            "stake": round(self.stake, 2),
            "payout": round(self.payout, 2),
            "timestamp": self.timestamp,
            "provider": self.provider,
        }


@dataclass(frozen=True)
class ArbitrageOpportunity:
    event_id: str
    market: str
    margin: float
    total_implied: float
    legs: tuple[ArbitrageLeg, ...]
    n_books: int
    comparable: bool
    timestamp_span_seconds: float
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "market": self.market,
            "margin": round(self.margin, 6),
            "total_implied": round(self.total_implied, 6),
            "legs": [leg.to_dict() for leg in self.legs],
            "n_books": self.n_books,
            "comparable": self.comparable,
            "timestamp_span_seconds": round(self.timestamp_span_seconds, 3),
            "warnings": list(self.warnings),
        }


def detect_arbitrage(
    quotes: Sequence[NormalizedQuote],
    min_books: int = 2,
    max_timestamp_span_seconds: float = DEFAULT_MAX_TIMESTAMP_SPAN_SECONDS,
    total_stake: float = 1000.0,
) -> list[ArbitrageOpportunity]:
    """Detecta arbitragem usando a MELHOR odd de cada resultado.

    Um arb exige que a soma das implicitas das melhores odds seja < 1. Se
    as odds vierem de instantes incompatíveis, a oportunidade e marcada
    com `comparable=False`: pode ser so defasagem de dado, nao lucro.
    """
    by_event: dict[str, list[NormalizedQuote]] = {}
    for quote in quotes:
        if quote.usable:
            by_event.setdefault(quote.event_id, []).append(quote)

    found: list[ArbitrageOpportunity] = []
    for event_id, event_quotes in sorted(by_event.items()):
        markets = sorted({q.market for q in event_quotes})
        for market in markets:
            market_quotes = [q for q in event_quotes if q.market == market]
            selections = sorted({q.selection for q in market_quotes})
            groups = [g for g in market_groups(selections) if set(g) <= set(selections)]
            for group in groups:
                if len(group) < 2:
                    continue
                best_by_selection: dict[str, NormalizedQuote] = {}
                for selection in group:
                    candidates = [q for q in market_quotes if q.selection == selection]
                    if not candidates:
                        best_by_selection = {}
                        break
                    best_by_selection[selection] = max(candidates, key=lambda q: q.price)
                if len(best_by_selection) < 2:
                    continue
                books = {q.bookmaker for q in best_by_selection.values()}
                if len(books) < min_books:
                    continue

                odds_by_book: dict[str, dict[str, float]] = {}
                for selection, quote in best_by_selection.items():
                    odds_by_book.setdefault(quote.bookmaker, {})[selection] = quote.price
                result = scan_arbitrage(odds_by_book, total_stake=total_stake)
                if not result.arbitrage:
                    continue

                options = [
                    PriceOption(q.bookmaker, q.price, q.timestamp, q.provider)
                    for q in best_by_selection.values()
                ]
                span = _timestamp_span_seconds(options)
                comparable = span <= max_timestamp_span_seconds
                warnings = () if comparable else ("TIMESTAMPS_INCOMPARABLE",)

                legs: list[ArbitrageLeg] = []
                for leg in result.legs:
                    source = best_by_selection[leg.outcome]
                    legs.append(
                        ArbitrageLeg(
                            selection=leg.outcome,
                            bookmaker=leg.book,
                            price=leg.odd,
                            stake=leg.stake,
                            payout=leg.payout,
                            timestamp=source.timestamp,
                            provider=source.provider,
                        )
                    )
                found.append(
                    ArbitrageOpportunity(
                        event_id=event_id,
                        market=market,
                        margin=result.margin,
                        total_implied=sum(1.0 / l.price for l in legs),
                        legs=tuple(legs),
                        n_books=len(books),
                        comparable=comparable,
                        timestamp_span_seconds=span,
                        warnings=warnings,
                    )
                )
    found.sort(key=lambda a: a.margin, reverse=True)
    return found
