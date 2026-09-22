"""BETGSN :: features.movement — Movimento de odds, estritamente point-in-time.

O movimento do preco carrega informacao: o mercado esta reprecificando algo.
Mas usar uma cotacao posterior ao instante da previsao e leakage puro — a
previsao "acertaria" porque ja viu o mercado corrigir.

Por isso toda funcao aqui recebe um cutoff e descarta o que vier depois.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from math import isfinite
from typing import Optional, Sequence

from ..timeutil import parse_kickoff, utc_key


@dataclass(frozen=True)
class PricePoint:
    """Uma cotacao observada num instante."""

    bookmaker: str
    market: str
    outcome: str
    odd: float
    timestamp: str

    def __post_init__(self) -> None:
        if not isfinite(self.odd) or self.odd <= 1.0:
            raise ValueError(f"odd precisa ser finita e > 1.0, recebi {self.odd!r}")
        if not self.timestamp:
            raise ValueError("cotacao exige timestamp")
        utc_key(self.timestamp)


def odds_before(
    points: Sequence[PricePoint], cutoff: str
) -> list[PricePoint]:
    """Cotacoes estritamente anteriores ao cutoff."""
    key = utc_key(cutoff)
    return [p for p in points if utc_key(p.timestamp) < key]


def odds_history_before(
    points: Sequence[PricePoint], cutoff: str, market: str, outcome: str
) -> list[PricePoint]:
    """Historico de uma linha especifica, ordenado, antes do cutoff."""
    visible = [
        p for p in odds_before(points, cutoff)
        if p.market == market and p.outcome == outcome
    ]
    return sorted(visible, key=lambda p: utc_key(p.timestamp))


def movement_features(
    points: Sequence[PricePoint],
    market: str,
    outcome: str,
    prediction_timestamp: str,
    kickoff: str,
) -> dict[str, Optional[float]]:
    """Features de movimento para UMA linha (mercado + resultado).

    Contrato temporal: `prediction_timestamp` e `kickoff` sao INSTANTES
    (idealmente em UTC canonico, "YYYY-MM-DDTHH:MM:SSZ"). O chamador que
    tem hora LOCAL da competicao (ex.: fixtures do football-data.co.uk)
    deve converter com o fuso da liga ANTES de chamar — uma hora local
    passada aqui seria tratada como UTC e deslocaria o cutoff.

    Devolve None — nunca zero — quando nao ha dado suficiente. Zero seria
    indistinguivel de "o preco nao se moveu", o que e uma afirmacao forte.

    O cutoff efetivo nunca ultrapassa o kickoff: mesmo que o chamador peca
    um instante posterior, so cotacoes pre-jogo entram. Isso fecha o
    caminho mais obvio de leakage.
    """
    effective_cutoff = min(utc_key(prediction_timestamp), utc_key(kickoff))
    empty: dict[str, Optional[float]] = {
        "opening_odds": None,
        "current_odds": None,
        "price_delta": None,
        "price_delta_pct": None,
        "implied_probability_delta": None,
        "minutes_since_open": None,
        "minutes_to_kickoff": None,
        "book_consensus_move": None,
        "book_dispersion": None,
        "best_price_move": None,
        "market_direction": None,
        "movement_velocity": None,
        "n_observations": None,
        "n_books": None,
    }

    history = odds_history_before(points, effective_cutoff, market, outcome)
    if not history:
        return empty

    cutoff_dt = parse_kickoff(effective_cutoff)
    kickoff_dt = parse_kickoff(kickoff)
    minutes_to_kickoff = (kickoff_dt - cutoff_dt).total_seconds() / 60.0

    first = history[0]
    last = history[-1]
    opening = first.odd
    current = last.odd

    per_book: dict[str, list[PricePoint]] = {}
    for point in history:
        per_book.setdefault(point.bookmaker, []).append(point)

    latest_per_book = [pts[-1].odd for pts in per_book.values()]
    first_per_book = [pts[0].odd for pts in per_book.values()]

    consensus_move = statistics.fmean(latest_per_book) - statistics.fmean(first_per_book)
    dispersion = (
        statistics.pstdev(latest_per_book) if len(latest_per_book) > 1 else None
    )
    best_move = max(latest_per_book) - max(first_per_book)

    minutes_since_open = (
        parse_kickoff(last.timestamp) - parse_kickoff(first.timestamp)
    ).total_seconds() / 60.0

    delta = current - opening
    velocity = delta / minutes_since_open if minutes_since_open > 0 else None

    if abs(delta) < 1e-9:
        direction = 0.0
    else:
        direction = 1.0 if delta > 0 else -1.0

    return {
        "opening_odds": round(opening, 4),
        "current_odds": round(current, 4),
        "price_delta": round(delta, 4),
        "price_delta_pct": round(delta / opening, 6),
        "implied_probability_delta": round(1.0 / current - 1.0 / opening, 6),
        "minutes_since_open": round(minutes_since_open, 2),
        "minutes_to_kickoff": round(minutes_to_kickoff, 2),
        "book_consensus_move": round(consensus_move, 4),
        "book_dispersion": (
            round(dispersion, 4) if dispersion is not None else None
        ),
        "best_price_move": round(best_move, 4),
        "market_direction": direction,
        "movement_velocity": (
            round(velocity, 8) if velocity is not None else None
        ),
        "n_observations": float(len(history)),
        "n_books": float(len(per_book)),
    }


def movement_feature_block(
    points: Sequence[PricePoint],
    prediction_timestamp: str,
    kickoff: str,
    market: str = "Resultado Final (1X2)",
    outcomes: Sequence[str] = ("1", "X", "2"),
) -> dict[str, Optional[float]]:
    """Bloco de features prefixado por resultado, pronto para o modelo."""
    prefix = {"1": "home", "X": "draw", "2": "away"}
    block: dict[str, Optional[float]] = {}
    for outcome in outcomes:
        tag = prefix.get(outcome, outcome.lower().replace(" ", "_"))
        features = movement_features(
            points, market, outcome, prediction_timestamp, kickoff
        )
        for name, value in features.items():
            block[f"movement_{tag}_{name}"] = value
    return block


#: Abaixo disso o consenso e fraco demais para ser tratado como mercado.
MIN_CONSENSUS_BOOKS = 3


def book_count(
    points: Sequence[PricePoint],
    market: str,
    outcome: str,
    cutoff: str,
) -> int:
    """Quantas casas tinham preco para a linha antes do cutoff."""
    return len(
        {
            p.bookmaker
            for p in odds_history_before(points, cutoff, market, outcome)
        }
    )


def consensus_limited(n_books: int, min_books: int = MIN_CONSENSUS_BOOKS) -> bool:
    """True quando o numero de casas nao sustenta um consenso de mercado."""
    return n_books < min_books


def latest_quote_age_minutes(
    points: Sequence[PricePoint], now: str
) -> Optional[float]:
    """Minutos desde a cotacao mais recente. None quando nao ha cotacao."""
    if not points:
        return None
    last = max(points, key=lambda p: utc_key(p.timestamp))
    delta = parse_kickoff(now) - parse_kickoff(last.timestamp)
    return delta.total_seconds() / 60.0


def is_stale(
    points: Sequence[PricePoint],
    now: str,
    max_age_minutes: float = 15.0,
) -> bool:
    """True quando nao ha cotacao ou a mais recente esta velha demais.

    Sem cotacao tambem e "stale": nao existe dado atual para apresentar.
    """
    age = latest_quote_age_minutes(points, now)
    if age is None:
        return True
    return age > max_age_minutes
