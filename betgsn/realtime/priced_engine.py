"""Fabrica PricedSignals a partir das views live com a MESMA política central.

Nenhum sinal aqui vira aposta automática; produção só libera com todos os
sete blocos GREEN, edge>=8%, EV>=8%, spread<=0.12 e execução medida.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from ..odds_normalize import NormalizedQuote
from ..priced_signals import PricedSignal, build_priced_signal
from ..production_policy import ProductionGate
from ..timeutil import parse_kickoff, utc_key
from .views import EventView, MarketView, SelectionView


def _books_from_view(selection: SelectionView, decision_ts: str) -> list[NormalizedQuote]:
    limit = utc_key(decision_ts)
    quotes: list[NormalizedQuote] = []
    for book in selection.books:
        if book.timestamp and utc_key(book.timestamp) <= limit:
            quotes.append(_quote_from(book, selection))
    return quotes


def _quote_from(book, selection: SelectionView) -> NormalizedQuote:
    return NormalizedQuote(
        event_id=selection._event_key,
        provider=book.provider,
        sport_key=selection._sport_key,
        league=selection._league,
        home_team=selection._home,
        away_team=selection._away,
        kickoff=selection._kickoff,
        bookmaker=book.bookmaker,
        market=selection._market,
        selection=selection.selection,
        price=book.price,
        timestamp=book.timestamp,
        line=selection._line,
    )


@dataclass
class PricedRealtimeEngine:
    """Ligação fina entre views live e a fábrica de PricedSignal.

    O gate operacional é passado por dependência. Modelos são consultados
    por chave `event|market|selection` e nunca inferidos: `(fingerprint,
    prob)` obrigatório. Modelo ausente marca `MODEL_UNAVAILABLE`.
    """

    gate: ProductionGate

    def priced(
        self,
        view: EventView | None,
        *,
        decision_ts: str,
        models: Mapping[str, tuple[str, float]],
        fair_probs: Mapping[str, float] | None = None,
    ) -> list[PricedSignal]:
        if view is None or not view.matched:
            return []
        signals: list[PricedSignal] = []
        overrides = dict(fair_probs or {})
        for market_view in view.markets:
            signals.extend(self._market_signals(view, market_view, decision_ts, models, overrides))
        return signals

    def _market_signals(
        self,
        view: EventView,
        market_view: MarketView,
        decision_ts: str,
        models: Mapping[str, tuple[str, float]],
        overrides: Mapping[str, float],
    ) -> list[PricedSignal]:
        out: list[PricedSignal] = []
        for selection in market_view.selections:
            # attach context needed by _quote_from without mutating public dataclass
            object.__setattr__(selection, "_event_key", view.event_key)
            object.__setattr__(selection, "_sport_key", getattr(view, "sport_key", ""))
            object.__setattr__(selection, "_league", view.league)
            object.__setattr__(selection, "_home", view.home)
            object.__setattr__(selection, "_away", view.away)
            object.__setattr__(selection, "_kickoff", view.kickoff)
            object.__setattr__(selection, "_market", market_view.market)
            object.__setattr__(selection, "_line", getattr(selection, "line", None))

            books = _books_from_view(selection, decision_ts)
            if not books:
                continue
            selected = max(books, key=lambda q: (q.price, q.timestamp))
            key = f"{view.event_key}|{market_view.market}|{selection.selection}"
            fair_prob = market_view.fair_probabilities.get(selection.selection)
            if fair_prob in (None, 0):
                fair_prob = overrides.get(key)
            model_pair = models.get(key)
            model_prob = model_pair[1] if model_pair else None
            model_fp = model_pair[0] if model_pair else ""
            ev = model_prob * selected.price - 1 if model_prob is not None else None
            spread = getattr(selection, "dispersion", None)
            try:
                signal = build_priced_signal(
                    quote=selected,
                    selected_quote=selected,
                    model_prob=model_prob,
                    fair_prob=fair_prob,
                    ev=ev,
                    spread=spread,
                    books=books,
                    decision_timestamp=decision_ts,
                    gate=self.gate,
                    model_fingerprint=model_fp,
                )
            except ValueError:
                # dropped quotes carry a reason inside build_priced_signal; no fake signal
                continue
            out.append(signal)
        return out


__all__ = ["PricedRealtimeEngine"]
