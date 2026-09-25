"""BETGSN :: realtime.state — memoria de mercado do engine em tempo real.

Estado incremental: a cada captura, apenas as chaves afetadas sao
recomputadas (views, movimento, sinais) — nada de recalcular o mundo
a cada quote (Fase 30).

Estrutura
---------
- `latest[(event, market, selection, book)] = quote` — o preco mais
  recente de cada casa para cada linha (identidade: uma quote por casa
  por linha; timestamp posterior substitui anterior EM MEMORIA — o
  snapshot store continua append-only e guarda TODAS);
- `history[(...)]` — deque limitada de (timestamp, preco) para
  deteccao de movimento;
- `events[event_key]` — metadados do evento (times, kickoff, liga e a
  flag `matched`: evento casado com fixture segue adiante; sem casamento
  fica UNMATCHED, visivel no terminal, NUNCA em sinais).

Protecao contra future leakage
------------------------------
Quote com carimbo no futuro (alem da tolerancia de skew) e recusada e
reportada como problema de qualidade de dados — nunca entra em estado,
nunca gera sinal.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from ..odds_normalize import NormalizedQuote
from ..timeutil import parse_kickoff
from ..markets import validate_selection_line

#: Profundidade maxima do historico por linha (casa x linha).
HISTORY_DEPTH = 64

#: Nenhuma tolerancia de futuro: um carimbo posterior a `now` e recusado.
#: Aceitar skew positivo abriria look-ahead (uma quote futura entrando no
#: sinal decidido em T). Um relogio de provider adiantado e tratado como
#: problema de dados (FUTURE_TIMESTAMP), nunca como validade assumida.
MAX_CLOCK_SKEW_SECONDS = 0.0


def _parse(value: str) -> datetime | None:
    try:
        return parse_kickoff(value)
    except Exception:  # noqa: BLE001 - carimbo ilegivel e problema de dados
        return None


@dataclass
class EventMeta:
    """Metadados de um evento visto pelo engine."""

    event_key: str
    home: str
    away: str
    kickoff: str
    league: str = ""
    sport_key: str = ""
    #: False quando o evento NAO foi casado com fixture. Unmatched e
    #: explicito no terminal e NUNCA entra em sinais.
    matched: bool = True
    first_seen: str = ""
    last_seen: str = ""


@dataclass(frozen=True)
class QuoteProblem:
    """Problema de qualidade de uma quote — nada desaparece silencioso."""

    reason: str
    provider: str
    event_id: str
    bookmaker: str
    market: str
    selection: str
    timestamp: str
    price: float | None = None

    def to_dict(self) -> dict:
        return {
            "reason": self.reason,
            "provider": self.provider,
            "event_id": self.event_id,
            "bookmaker": self.bookmaker,
            "market": self.market,
            "selection": self.selection,
            "timestamp": self.timestamp,
            "price": self.price,
        }


#: Chave de linha: (evento, mercado, resultado, casa).
LineKey = tuple[str, str, str, str]


class MarketState:
    """Memoria de mercado incremental, confinada a thread do engine."""

    def __init__(self, history_depth: int = HISTORY_DEPTH) -> None:
        self.latest: dict[LineKey, NormalizedQuote] = {}
        self.history: dict[LineKey, deque[tuple[str, float]]] = {}
        self.events: dict[str, EventMeta] = {}
        self.history_depth = history_depth
        self.problems: deque[QuoteProblem] = deque(maxlen=1024)
        self.problems_count = 0

    # ------------------------------------------------------------ ingestao

    def apply(
        self,
        quotes: Sequence[NormalizedQuote],
        now: datetime | None = None,
        matched_keys: Iterable[str] | None = None,
    ) -> tuple[set[str], set[LineKey], list[QuoteProblem]]:
        """Incorpora quotes; devolve (eventos afetados, linhas afetadas,
        problemas de qualidade detectados).

        `matched_keys` e o conjunto de event_ids casados com fixtures;
        eventos fora dele entram com `matched=False` (UNMATCHED).
        """
        reference = now or datetime.now(timezone.utc)
        #: None = chamador nao sabe (assume casado); set() vazio = NADA
        #: casado — distincção necessaria para o engine, onde "nenhum
        #: evento casou" deve marcar todos como UNMATCHED.
        matched_set = (
            None if matched_keys is None else set(matched_keys)
        )
        affected_events: set[str] = set()
        affected_lines: set[LineKey] = set()
        problems: list[QuoteProblem] = []

        for quote in quotes:
            problem = self._validate(quote, reference)
            if problem is not None:
                problems.append(problem)
                self.problems.append(problem)
                self.problems_count += 1
                continue

            matched = (
                True
                if matched_set is None
                else quote.event_id in matched_set
            )
            self._register_event(quote, matched)
            key: LineKey = (
                quote.event_id,
                quote.market,
                quote.selection,
                quote.bookmaker,
            )
            history = self.history.setdefault(
                key, deque(maxlen=self.history_depth)
            )
            last = history[-1] if history else None
            if last is None or last != (quote.timestamp, quote.price):
                history.append((quote.timestamp, quote.price))

            current = self.latest.get(key)
            if current is None or quote.timestamp >= current.timestamp:
                self.latest[key] = quote

            affected_events.add(quote.event_id)
            affected_lines.add(key)

        return affected_events, affected_lines, problems

    def _validate(
        self, quote: NormalizedQuote, now: datetime
    ) -> QuoteProblem | None:
        stamp = _parse(quote.timestamp)
        if stamp is None:
            return QuoteProblem(
                reason="MISSING_OR_INVALID_TIMESTAMP",
                provider=quote.provider,
                event_id=quote.event_id,
                bookmaker=quote.bookmaker,
                market=quote.market,
                selection=quote.selection,
                timestamp=quote.timestamp,
                price=quote.price,
            )
        if (stamp - now) > timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
            return QuoteProblem(
                reason="FUTURE_TIMESTAMP",
                provider=quote.provider,
                event_id=quote.event_id,
                bookmaker=quote.bookmaker,
                market=quote.market,
                selection=quote.selection,
                timestamp=quote.timestamp,
                price=quote.price,
            )
        line_problem = validate_selection_line(quote.market, quote.selection, quote.line)
        if line_problem:
            return QuoteProblem(
                reason=line_problem,
                provider=quote.provider,
                event_id=quote.event_id,
                bookmaker=quote.bookmaker,
                market=quote.market,
                selection=quote.selection,
                timestamp=quote.timestamp,
                price=quote.price,
            )
        return None

    def _register_event(self, quote: NormalizedQuote, matched: bool) -> None:
        meta = self.events.get(quote.event_id)
        if meta is None:
            meta = EventMeta(
                event_key=quote.event_id,
                home=quote.home_team,
                away=quote.away_team,
                kickoff=quote.kickoff,
                league=quote.league,
                sport_key=quote.sport_key,
                matched=matched,
                first_seen=quote.timestamp,
            )
            self.events[quote.event_id] = meta
        meta.last_seen = quote.timestamp

    # ------------------------------------------------------------ leitura

    def event_quotes(self, event_key: str) -> list[NormalizedQuote]:
        return [
            quote
            for (event, _m, _s, _b), quote in self.latest.items()
            if event == event_key
        ]

    def market_quotes(self, event_key: str, market: str) -> list[NormalizedQuote]:
        return [
            quote
            for (event, m, _s, _b), quote in self.latest.items()
            if event == event_key and m == market
        ]

    def history_for(self, key: LineKey) -> list[tuple[str, float]]:
        return list(self.history.get(key, ()))

    def stats(self) -> dict:
        matched = sum(1 for meta in self.events.values() if meta.matched)
        return {
            "events": len(self.events),
            "events_matched": matched,
            "events_unmatched": len(self.events) - matched,
            "lines": len(self.latest),
            "problems": self.problems_count,
        }
