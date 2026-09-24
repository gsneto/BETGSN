"""BETGSN :: realtime.movement — deteccao de movimento em tempo real.

Cada movimento e um FATO explicavel:

  OLD PRICE -> NEW PRICE -> TIMESTAMP -> BOOK -> DIRECTION -> MAGNITUDE

Nada de linguagem enganosa: "rapid" significa que o intervalo entre as
duas observacoes foi curto (configuravel), nao que "o mercado ficou
nervoso". Classificacoes de nivel de mercado (consenso, divergencia,
reversao) sao computadas a partir dos movimentos POR CASA — nunca de
uma casa so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Sequence

from ..odds_normalize import NormalizedQuote
from .freshness import parse_stamp
from .state import LineKey, MarketState

DIRECTION_UP = "UP"
DIRECTION_DOWN = "DOWN"


@dataclass(frozen=True)
class LineMove:
    """Movimento de UMA linha (evento, mercado, resultado, casa)."""

    event_key: str
    market: str
    selection: str
    bookmaker: str
    old_price: float
    new_price: float
    old_timestamp: str
    new_timestamp: str
    provider: str

    @property
    def direction(self) -> str:
        return DIRECTION_UP if self.new_price > self.old_price else DIRECTION_DOWN

    @property
    def magnitude(self) -> float:
        return round(abs(self.new_price - self.old_price), 6)

    @property
    def magnitude_pct(self) -> float:
        if self.old_price <= 0:
            return 0.0
        return round(
            abs(self.new_price - self.old_price) / self.old_price, 6
        )

    @property
    def elapsed_seconds(self) -> Optional[float]:
        old = parse_stamp(self.old_timestamp)
        new = parse_stamp(self.new_timestamp)
        if old is None or new is None:
            return None
        return (new - old).total_seconds()

    def to_dict(self) -> dict:
        return {
            "event_key": self.event_key,
            "market": self.market,
            "selection": self.selection,
            "bookmaker": self.bookmaker,
            "old_price": round(self.old_price, 6),
            "new_price": round(self.new_price, 6),
            "old_timestamp": self.old_timestamp,
            "new_timestamp": self.new_timestamp,
            "provider": self.provider,
            "direction": self.direction,
            "magnitude": self.magnitude,
            "magnitude_pct": self.magnitude_pct,
            "elapsed_seconds": self.elapsed_seconds,
        }


@dataclass
class MarketMovement:
    """Movimentos agregados de um (evento, mercado) neste tick."""

    event_key: str
    market: str
    moves: list[LineMove] = field(default_factory=list)

    @property
    def books_moved(self) -> set[str]:
        return {m.bookmaker for m in self.moves}

    @property
    def last_move_at(self) -> str:
        return max((m.new_timestamp for m in self.moves), default="")

    def moves_for(self, selection: str) -> list[LineMove]:
        return [m for m in self.moves if m.selection == selection]

    def to_dict(self) -> dict:
        return {
            "event_key": self.event_key,
            "market": self.market,
            "moves": [m.to_dict() for m in self.moves],
            "books_moved": sorted(self.books_moved),
            "last_move_at": self.last_move_at,
        }


class MovementEngine:
    """Detecta movimentos novos por linha, incrementalmente.

    Rastreia o indice da ultima entrada do historico ja processada por
    linha: um movimento so existe quando o (timestamp, preco) mudou de
    fato. Quote repetida (mesmo preco, mesmo carimbo) NAO gera movimento;
    mesmo carimbo com preco diferente TAMPOUCO (observacao duplicada com
    inconsistencia nao e movimento, e problema de dados). Ao ver uma
    linha pela primeira vez com historico acumulado (ex.: restart do
    engine), emite os movimentos REAIS da historia — reproduzivel, e o
    event bus deduplica os repetidos.
    """

    def __init__(self, min_magnitude_pct: float = 0.0) -> None:
        self._processed: dict[LineKey, int] = {}
        self.min_magnitude_pct = min_magnitude_pct

    def process(
        self,
        state: MarketState,
        affected_lines: Sequence[LineKey],
    ) -> list[MarketMovement]:
        """Processa as linhas afetadas; devolve movimentos por mercado."""
        by_market: dict[tuple[str, str], MarketMovement] = {}
        for key in affected_lines:
            history = state.history_for(key)
            if not history:
                continue
            event_key, market, selection, bookmaker = key
            last_index = self._processed.get(key, -1)
            if last_index >= len(history):
                #: deque evictou entradas: ponteiro invalidado, comeca do fim
                last_index = len(history) - 1
            for i in range(max(last_index, 0) + 1, len(history)):
                old_stamp, old_price = history[i - 1]
                new_stamp, new_price = history[i]
                if old_price == new_price:
                    continue
                quote = state.latest.get(key)
                move = LineMove(
                    event_key=event_key,
                    market=market,
                    selection=selection,
                    bookmaker=bookmaker,
                    old_price=old_price,
                    new_price=new_price,
                    old_timestamp=old_stamp,
                    new_timestamp=new_stamp,
                    provider=quote.provider if quote else "",
                )
                if move.magnitude_pct < self.min_magnitude_pct:
                    continue
                summary = by_market.setdefault(
                    (event_key, market),
                    MarketMovement(event_key=event_key, market=market),
                )
                summary.moves.append(move)
            self._processed[key] = len(history) - 1
        return list(by_market.values())

    def reset(self) -> None:
        self._processed.clear()
