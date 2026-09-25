"""BETGSN :: market_dataset — estado de mercado PIT e fingerprint reprodutível.

Constrói, a partir das observações do store, o estado observável de uma
linha (evento + mercado + resultado) num instante T, usando SOMENTE quotes
com `timestamp <= T`. Nenhum dado futuro entra — é a base de todo
experimento do Alpha Lab.

Por instante T, o estado expõe:

    best_price      maior preço entre as casas (line shopping)
    second_best     segundo maior (auditoria do best)
    median_price    mediana entre casas (consenso robusto)
    worst_price     menor preço
    dispersion      desvio-padrão entre casas
    book_count      número de casas distintas
    best_book       casa do best_price (identidade auditável)

O `dataset_fingerprint` resume a amostra (eventos, linhas, janela temporal,
casas) para que um experimento seja reproduzível.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .timeutil import utc_key


@dataclass(frozen=True)
class MarketStatePIT:
    """Estado observável de uma linha num instante, só com quotes <= T."""

    event_key: str
    market: str
    selection: str
    timestamp: str
    best_price: float
    second_best: float | None
    median_price: float
    worst_price: float
    dispersion: float
    book_count: int
    best_book: str
    worst_book: str

    @property
    def best_vs_median(self) -> float:
        return self.best_price - self.median_price

    @property
    def best_gap_ratio(self) -> float:
        return (self.best_price - self.median_price) / self.median_price

    @property
    def dispersion_ratio(self) -> float:
        return self.dispersion / self.median_price if self.median_price > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "event_key": self.event_key,
            "market": self.market,
            "selection": self.selection,
            "timestamp": self.timestamp,
            "best_price": round(self.best_price, 6),
            "second_best": (
                round(self.second_best, 6) if self.second_best is not None else None
            ),
            "median_price": round(self.median_price, 6),
            "worst_price": round(self.worst_price, 6),
            "dispersion": round(self.dispersion, 6),
            "book_count": self.book_count,
            "best_book": self.best_book,
            "worst_book": self.worst_book,
        }


def state_from_prices(
    *,
    event_key: str,
    market: str,
    selection: str,
    timestamp: str,
    prices_by_book: Mapping[str, float],
) -> MarketStatePIT | None:
    """Estado a partir de {casa: preço}. None se não houver casa."""
    usable = {
        str(book): float(price)
        for book, price in prices_by_book.items()
        if price is not None and price > 1.0
    }
    if not usable:
        return None
    ordered = sorted(usable.items(), key=lambda kv: (-kv[1], kv[0]))
    prices = [p for _b, p in ordered]
    best_book, best_price = ordered[0]
    worst_book, worst_price = ordered[-1]
    return MarketStatePIT(
        event_key=event_key,
        market=market,
        selection=selection,
        timestamp=timestamp,
        best_price=best_price,
        second_best=prices[1] if len(prices) >= 2 else None,
        median_price=statistics.median(prices),
        worst_price=worst_price,
        dispersion=statistics.pstdev(prices) if len(prices) >= 2 else 0.0,
        book_count=len(prices),
        best_book=best_book,
        worst_book=worst_book,
    )


def latest_prices_at(
    series: Sequence[tuple[str, str, float]],
    cutoff: str,
) -> dict[str, float]:
    """Último preço por casa com `timestamp <= cutoff`.

    `series` é uma sequência de (timestamp, bookmaker, price) ordenada ou
    não; devolve {casa: preço}. PIT estrito: nada com carimbo posterior.
    """
    limit = utc_key(cutoff)
    latest: dict[str, tuple[str, float]] = {}
    for stamp, book, price in series:
        if utc_key(stamp) > limit:
            continue
        current = latest.get(book)
        if current is None or utc_key(stamp) >= utc_key(current[0]):
            latest[book] = (stamp, price)
    return {book: price for book, (_s, price) in latest.items()}


def dataset_fingerprint(
    *,
    event_keys: Iterable[str],
    markets: Iterable[str],
    bookmakers: Iterable[str],
    n_observations: int,
    period: tuple[str, str],
) -> str:
    """Resumo reprodutível da amostra de um experimento."""
    payload = {
        "events": sorted(set(event_keys)),
        "markets": sorted(set(markets)),
        "books": sorted(set(bookmakers)),
        "n_observations": int(n_observations),
        "period": list(period),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


__all__ = [
    "MarketStatePIT",
    "state_from_prices",
    "latest_prices_at",
    "dataset_fingerprint",
]
