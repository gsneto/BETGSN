"""BETGSN :: realtime.views — market view do terminal em tempo real.

Para cada (evento, mercado) o terminal mostra, por resultado:

  BOOK A   2.10   3.40   3.10     <- grade por casa (odds grid)
  BOOK B   2.08   3.45   3.12
  BEST     2.12   3.45   3.12     <- melhor preco e QUEM oferece
  MEDIAN   ...
  FAIR     ...                     <- devig da mediana entre casas

E ainda: numero de casas, dispersao (desvio-padrao entre casas),
probabilidade raw do mercado, overround, last update e freshness.

Fontes separadas, nunca misturadas (Fase 12):
  market_raw     mediana implicita entre casas (com margem)
  market_fair    devig da mediana (sem margem, metodo declarado)
  model          preco de modelo — so aparece quando existe snapshot
                 do pipeline; nunca inventado aqui.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from ..engine import market_groups
from ..odds_math import devig
from ..odds_normalize import NormalizedQuote
from .freshness import (
    FreshnessState,
    FreshnessThresholds,
    freshness_state,
    parse_stamp,
)
from .state import EventMeta, MarketState

#: Janela padrao de "jogo em andamento" apos o kickoff (relogio, nao
#: placar: o BETGSN nao captura odds in-play).
IN_PLAY_WINDOW = timedelta(hours=3)


@dataclass(frozen=True)
class BookPrice:
    """Preco de UMA casa para um resultado."""

    bookmaker: str
    price: float
    timestamp: str
    provider: str
    age_seconds: Optional[float] = None
    freshness: str = FreshnessState.UNKNOWN.value

    def to_dict(self) -> dict:
        return {
            "bookmaker": self.bookmaker,
            "price": round(self.price, 6),
            "timestamp": self.timestamp,
            "provider": self.provider,
            "age_seconds": (
                round(self.age_seconds, 1)
                if self.age_seconds is not None
                else None
            ),
            "freshness": self.freshness,
        }


@dataclass(frozen=True)
class SelectionView:
    """Estrutura de mercado de um resultado (line shopping)."""

    selection: str
    books: tuple[BookPrice, ...]
    best: BookPrice
    second_best: Optional[BookPrice]
    worst: BookPrice
    median: float
    mean: float
    n_books: int
    dispersion: Optional[float]
    best_vs_median: Optional[float]
    best_vs_second: Optional[float]
    last_update: str

    def to_dict(self) -> dict:
        return {
            "selection": self.selection,
            "books": [b.to_dict() for b in self.books],
            "best": self.best.to_dict(),
            "second_best": self.second_best.to_dict() if self.second_best else None,
            "worst": self.worst.to_dict(),
            "median": round(self.median, 6),
            "mean": round(self.mean, 6),
            "n_books": self.n_books,
            "dispersion": (
                round(self.dispersion, 6) if self.dispersion is not None else None
            ),
            "best_vs_median": (
                round(self.best_vs_median, 6)
                if self.best_vs_median is not None
                else None
            ),
            "best_vs_second": (
                round(self.best_vs_second, 6)
                if self.best_vs_second is not None
                else None
            ),
            "last_update": self.last_update,
        }


@dataclass(frozen=True)
class MarketView:
    """View completa de um mercado de um evento."""

    event_key: str
    market: str
    selections: tuple[SelectionView, ...]
    fair_probabilities: dict[str, float]
    overround: Optional[float]
    devig_method: str
    n_books: int
    last_update: str
    timestamp_span_seconds: float
    complete: bool

    def to_dict(self) -> dict:
        return {
            "event_key": self.event_key,
            "market": self.market,
            "selections": [s.to_dict() for s in self.selections],
            "fair_probabilities": {
                k: round(v, 8) for k, v in self.fair_probabilities.items()
            },
            "overround": (
                round(self.overround, 6) if self.overround is not None else None
            ),
            "devig_method": self.devig_method,
            "n_books": self.n_books,
            "last_update": self.last_update,
            "timestamp_span_seconds": round(self.timestamp_span_seconds, 1),
            "complete": self.complete,
        }


@dataclass(frozen=True)
class EventView:
    """Tudo o que o terminal sabe de um evento agora."""

    event_key: str
    home: str
    away: str
    kickoff: str
    league: str
    matched: bool
    status: str
    markets: tuple[MarketView, ...]
    last_update: str

    @property
    def label(self) -> str:
        return f"{self.home} vs {self.away}"

    def to_dict(self) -> dict:
        return {
            "event_key": self.event_key,
            "home": self.home,
            "away": self.away,
            "kickoff": self.kickoff,
            "league": self.league,
            "matched": self.matched,
            "match_status": self.status,
            "markets": [m.to_dict() for m in self.markets],
            "last_update": self.last_update,
        }


def match_status(kickoff: str, now: datetime | None = None) -> str:
    """PRE_MATCH / IN_PLAY / FINISHED pelo relogio — nunca pelo palpite."""
    moment = parse_stamp(kickoff)
    if moment is None:
        return "UNKNOWN"
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    if reference < moment:
        return "PRE_MATCH"
    if reference <= moment + IN_PLAY_WINDOW:
        return "IN_PLAY"
    return "FINISHED"


def build_selection_view(
    quotes: Sequence[NormalizedQuote],
    selection: str,
    thresholds: FreshnessThresholds,
    now: datetime | None = None,
) -> Optional[SelectionView]:
    """Estrutura de mercado de um resultado a partir das quotes atuais."""
    relevant = [q for q in quotes if q.selection == selection]
    if not relevant:
        return None

    latest: dict[str, NormalizedQuote] = {}
    for quote in relevant:
        current = latest.get(quote.bookmaker)
        if current is None or quote.timestamp >= current.timestamp:
            latest[quote.bookmaker] = quote

    books: list[BookPrice] = []
    for quote in latest.values():
        state, age = freshness_state(quote.timestamp, thresholds, now)
        books.append(
            BookPrice(
                bookmaker=quote.bookmaker,
                price=quote.price,
                timestamp=quote.timestamp,
                provider=quote.provider,
                age_seconds=age,
                freshness=state.value,
            )
        )
    books.sort(key=lambda b: b.price, reverse=True)

    prices = [b.price for b in books]
    best = books[0]
    second = books[1] if len(books) > 1 else None
    median = statistics.median(prices)
    stamps = [b.timestamp for b in books]

    return SelectionView(
        selection=selection,
        books=tuple(books),
        best=best,
        second_best=second,
        worst=books[-1],
        median=median,
        mean=statistics.fmean(prices),
        n_books=len(books),
        dispersion=statistics.stdev(prices) if len(prices) >= 2 else None,
        best_vs_median=best.price - median,
        best_vs_second=(best.price - second.price) if second else None,
        last_update=max(stamps),
    )


def build_market_view(
    quotes: Sequence[NormalizedQuote],
    event_key: str,
    market: str,
    thresholds: FreshnessThresholds,
    now: datetime | None = None,
    devig_method: str = "multiplicative",
) -> Optional[MarketView]:
    """View de um mercado: grade de casas, best/median/fair por resultado."""
    market_quotes = [q for q in quotes if q.market == market]
    if not market_quotes:
        return None

    selections: list[SelectionView] = []
    for selection in sorted({q.selection for q in market_quotes}):
        view = build_selection_view(market_quotes, selection, thresholds, now)
        if view is not None:
            selections.append(view)
    if not selections:
        return None

    medians = {s.selection: s.median for s in selections}
    fair: dict[str, float] = {}
    overround: float | None = None
    complete = False
    for group in market_groups(list(medians)):
        if not set(group) <= set(medians) or len(group) < 2:
            continue
        target = 2 if set(group) == {"1X", "12", "X2"} else 1
        group_odds = {sel: medians[sel] for sel in group}
        result = devig(group_odds, method=devig_method, complete=True)
        for sel, probability in result.fair_probabilities.items():
            fair[sel] = target * probability
        overround = result.overround
        complete = True

    all_stamps = [s.last_update for s in selections]
    bookmakers = {q.bookmaker for q in market_quotes}
    moments = [m for m in (parse_stamp(s) for s in all_stamps) if m is not None]
    span = (
        (max(moments) - min(moments)).total_seconds() if len(moments) >= 2 else 0.0
    )

    return MarketView(
        event_key=event_key,
        market=market,
        selections=tuple(selections),
        fair_probabilities=fair,
        overround=overround,
        devig_method=devig_method,
        n_books=len(bookmakers),
        last_update=max(all_stamps) if all_stamps else "",
        timestamp_span_seconds=span,
        complete=complete,
    )


def build_event_view(
    state: MarketState,
    event_key: str,
    thresholds: FreshnessThresholds,
    now: datetime | None = None,
    markets: Sequence[str] | None = None,
) -> Optional[EventView]:
    """View completa de um evento (todas as views de mercado)."""
    meta: Optional[EventMeta] = state.events.get(event_key)
    quotes = state.event_quotes(event_key)
    if meta is None or not quotes:
        return None

    wanted = list(markets) if markets else sorted({q.market for q in quotes})
    views: list[MarketView] = []
    for market in wanted:
        view = build_market_view(quotes, event_key, market, thresholds, now)
        if view is not None:
            views.append(view)

    return EventView(
        event_key=event_key,
        home=meta.home,
        away=meta.away,
        kickoff=meta.kickoff,
        league=meta.league,
        matched=meta.matched,
        status=match_status(meta.kickoff, now),
        markets=tuple(views),
        last_update=max((q.timestamp for q in quotes), default=""),
    )


def event_views(
    state: MarketState,
    thresholds: FreshnessThresholds,
    now: datetime | None = None,
) -> list[EventView]:
    """Views de todos os eventos conhecidos (board completo)."""
    out: list[EventView] = []
    for event_key in state.events:
        view = build_event_view(state, event_key, thresholds, now)
        if view is not None:
            out.append(view)
    out.sort(key=lambda v: (v.kickoff, v.event_key))
    return out
