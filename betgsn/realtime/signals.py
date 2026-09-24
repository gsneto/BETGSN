"""BETGSN :: realtime.signals — signal engine explicavel do terminal.

Um sinal NAO e uma aposta. E um FATO de mercado comprovado por
evidencia: quem moveu, para onde, quando, contra qual referencia.

Contrato de cada sinal:
  signal_id      identidade deterministica (tipo + escopo + evidencia)
  alpha_id       hipotese de origem (ver `alphas.py`)
  event/market/selection
  timestamp      instante em que o sinal foi criado
  observed_at    carimbo da ULTIMA observacao que sustenta o sinal
  reason         frase construida da evidencia (o WHY)
  evidence       dicionario com os numeros crus (o PROOF)
  market/fair/model/best/median prices — separados, nunca misturados
  freshness      estado de frescor da evidencia
  status         ACTIVE / STALE / EXPIRED (derivado da idade)
  production     sempre NO_BET aqui: sinal informativo, decisao humana

Regras de honestidade:
  - evento UNMATCHED nunca gera sinal;
  - sinal so existe enquanto a evidencia existir: condicao sumiu =
    SIGNAL_EXPIRED com motivo (nunca remocao silenciosa);
  - evidencia sem refresh por mais que `ttl_seconds` expira;
  - reconstruction: `signal_id` e derivado do CONTEUDO (fingerprint
    estavel: precos e carimbos, nunca idades derivadas), entao as
    mesmas entradas avaliadas em T reproduzem o mesmo sinal.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Mapping, Optional, Sequence

from .alphas import (
    SIGNAL_BEST_PRICE_GAP,
    SIGNAL_BOOKMAKER_LAG,
    SIGNAL_BOOKMAKER_LEAD,
    SIGNAL_BOOKMAKER_OUTLIER,
    SIGNAL_CONSENSUS_MOVE,
    SIGNAL_DISPERSION_SPIKE,
    SIGNAL_PRICE_REVERSAL,
    SIGNAL_RAPID_CONVERGENCE,
    SIGNAL_STALE_PRICE,
    default_alphas,
)
from .freshness import FreshnessState, FreshnessThresholds, age_seconds, parse_stamp
from .movement import DIRECTION_DOWN, DIRECTION_UP, LineMove, MarketMovement
from .views import EventView, MarketView, SelectionView

SIGNAL_ACTIVE = "ACTIVE"
SIGNAL_STALE = "STALE"
SIGNAL_EXPIRED = "EXPIRED"

PRODUCTION_NO_BET = "NO_BET"
EVIDENCE_OBSERVED = "OBSERVED"

ALPHA_BY_TYPE: dict[str, str] = {
    SIGNAL_STALE_PRICE: "bookmaker_microstructure",
    SIGNAL_BOOKMAKER_OUTLIER: "bookmaker_microstructure",
    SIGNAL_BOOKMAKER_LEAD: "bookmaker_microstructure",
    SIGNAL_BOOKMAKER_LAG: "bookmaker_microstructure",
    SIGNAL_CONSENSUS_MOVE: "movement",
    SIGNAL_PRICE_REVERSAL: "movement",
    SIGNAL_RAPID_CONVERGENCE: "movement",
    SIGNAL_DISPERSION_SPIKE: "movement",
    SIGNAL_BEST_PRICE_GAP: "line_shopping",
}


@dataclass(frozen=True)
class SignalRules:
    #: desvio minimo da mediana das DEMAIS casas para outlier (fracao)
    outlier_min_deviation: float = 0.05
    #: casas minimas para afirmar outlier/dispersao/consenso
    min_books: int = 3
    #: dispersao relativa (stdev/mediana) que caracteriza spike
    dispersion_spike_ratio: float = 0.04
    #: gap best-vs-median minimo (fracao)
    best_gap_min: float = 0.04
    #: janela (s) em que movimentos contam para lead/lag/consenso
    move_window_seconds: float = 600.0
    #: spread relativo maximo para considerar mercado convergido
    convergence_max_spread: float = 0.01
    #: TTL: sinal sem evidencia mais fresca que isso expira
    ttl_seconds: float = 1800.0
    #: idade da evidencia que degrada ACTIVE -> STALE
    stale_after_seconds: float = 900.0

    #: casas minimas na mesma direcao para CONSENSUS_MOVE
    consensus_min_books: int = 3


@dataclass(frozen=True)
class Signal:
    alpha_id: str
    signal_type: str
    event_key: str
    market: str
    selection: str
    signal_id: str
    timestamp: str
    observed_at: str
    reason: str
    evidence: dict
    market_price: Optional[float] = None
    fair_price: Optional[float] = None
    model_price: Optional[float] = None
    best_price: Optional[float] = None
    median: Optional[float] = None
    freshness: str = FreshnessState.UNKNOWN.value
    bookmakers: tuple[str, ...] = ()
    production: str = PRODUCTION_NO_BET
    evidence_status: str = EVIDENCE_OBSERVED
    status: str = SIGNAL_ACTIVE

    def to_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "alpha_id": self.alpha_id,
            "signal_type": self.signal_type,
            "event_key": self.event_key,
            "market": self.market,
            "selection": self.selection,
            "timestamp": self.timestamp,
            "observed_at": self.observed_at,
            "reason": self.reason,
            "evidence": self.evidence,
            "market_price": self.market_price,
            "fair_price": self.fair_price,
            "model_price": self.model_price,
            "best_price": self.best_price,
            "median": self.median,
            "freshness": self.freshness,
            "bookmakers": list(self.bookmakers),
            "status": self.status,
            "production": self.production,
            "evidence_status": self.evidence_status,
        }


@dataclass(frozen=True)
class SignalEvaluation:
    created: tuple[Signal, ...] = ()
    refreshed: tuple[Signal, ...] = ()
    expired: tuple[Signal, ...] = ()


def _signal_id(
    signal_type: str,
    event_key: str,
    market: str,
    selection: str,
    fingerprint: Sequence,
) -> str:
    identity = (
        f"{signal_type}|{event_key}|{market}|{selection}|{tuple(fingerprint)!r}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, identity).hex


def fair_odd_of(market_view: MarketView, selection: str) -> Optional[float]:
    probability = market_view.fair_probabilities.get(selection)
    if probability is None or probability <= 0.0:
        return None
    return round(1.0 / probability, 6)


def freshest_of(selection: SelectionView) -> str:
    """Estado de frescor do book mais recente da linha."""
    candidates = [
        b for b in selection.books if b.age_seconds is not None
    ]
    if not candidates:
        return FreshnessState.UNKNOWN.value
    freshest = min(candidates, key=lambda b: b.age_seconds)
    return freshest.freshness


class SignalEngine:
    """Avalia o estado de mercado e emite/expira sinais explicaveis."""

    def __init__(
        self,
        rules: SignalRules | None = None,
        thresholds: FreshnessThresholds | None = None,
        now: Optional[callable] = None,
    ) -> None:
        self.rules = rules or SignalRules()
        self.thresholds = thresholds or FreshnessThresholds()
        self._active: dict[str, Signal] = {}
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.created_count = 0
        self.expired_count = 0

    # ------------------------------------------------------------ entrada

    def evaluate(
        self,
        event_views: Sequence[EventView],
        movements: Sequence[MarketMovement] = (),
        model_prices: Mapping[str, float] | None = None,
    ) -> SignalEvaluation:
        """Avalia os eventos afetados; expira sinais cuja evidencia sumiu.

        `movements` traz os movimentos detectados neste tick por
        (evento, mercado). `model_prices` mapeia "event|market|selection"
        -> preco de modelo QUANDO existir snapshot; nunca e fabricado.
        """
        models = dict(model_prices or {})
        moves_by_key = {(m.event_key, m.market): m for m in movements}
        current: dict[str, Signal] = {}

        for view in event_views:
            if not view.matched:
                continue
            for market_view in view.markets:
                moves = moves_by_key.get((view.event_key, market_view.market))
                for selection in market_view.selections:
                    for signal in self._selection_signals(
                        view, market_view, selection, moves, models
                    ):
                        current[signal.signal_id] = signal

        created: list[Signal] = []
        refreshed: list[Signal] = []
        expired: list[Signal] = []
        affected = {v.event_key for v in event_views}

        for signal_id, previous in list(self._active.items()):
            if previous.event_key not in affected:
                continue
            if signal_id not in current:
                expired.append(self._expire(previous, "CONDITION_CLEARED"))

        for signal_id, signal in current.items():
            previous = self._active.get(signal_id)
            if previous is None:
                created.append(signal)
                self.created_count += 1
                self._active[signal_id] = signal
            else:
                #: mesmo conteudo (mesmo id): mantem o timestamp de criacao
                #: e renova o carimbo da evidencia — sinal persistente
                #: continua vivo enquanto o mercado continuar fluindo.
                updated = replace(
                    signal, timestamp=previous.timestamp
                )
                self._active[signal_id] = updated
                refreshed.append(updated)

        expired.extend(self._ttl_sweep())
        return SignalEvaluation(
            created=tuple(created),
            refreshed=tuple(refreshed),
            expired=tuple(expired),
        )

    # ------------------------------------------------------------ regras

    def _make(
        self,
        event_view: EventView,
        market_view: MarketView,
        selection: SelectionView,
        signal_type: str,
        reason: str,
        evidence: dict,
        observed_at: str,
        fingerprint: Sequence,
        model_prices: Mapping[str, float],
    ) -> Signal:
        key = (
            f"{event_view.event_key}|{market_view.market}|{selection.selection}"
        )
        return Signal(
            alpha_id=ALPHA_BY_TYPE[signal_type],
            signal_type=signal_type,
            event_key=event_view.event_key,
            market=market_view.market,
            selection=selection.selection,
            signal_id=_signal_id(
                signal_type,
                event_view.event_key,
                market_view.market,
                selection.selection,
                fingerprint,
            ),
            timestamp=self._now().isoformat(),
            observed_at=observed_at,
            reason=reason,
            evidence=evidence,
            market_price=selection.median,
            fair_price=fair_odd_of(market_view, selection.selection),
            model_price=model_prices.get(key),
            best_price=selection.best.price,
            median=selection.median,
            freshness=freshest_of(selection),
            bookmakers=tuple(b.bookmaker for b in selection.books),
        )

    def _selection_signals(
        self,
        event_view: EventView,
        market_view: MarketView,
        selection: SelectionView,
        moves: Optional[MarketMovement],
        models: Mapping[str, float],
    ) -> list[Signal]:
        found: list[Signal] = [
            *self._stale_price(event_view, market_view, selection, models),
            *self._outlier(event_view, market_view, selection, models),
            *self._dispersion_spike(event_view, market_view, selection, models),
            *self._best_gap(event_view, market_view, selection, models),
        ]
        if moves is not None:
            found.extend(
                self._movement_signals(
                    event_view, market_view, selection, moves, models
                )
            )
        return found

    def _stale_price(
        self,
        event_view: EventView,
        market_view: MarketView,
        selection: SelectionView,
        models: Mapping[str, float],
    ) -> list[Signal]:
        if selection.n_books < 2:
            return []
        fresh_books = [
            b for b in selection.books
            if b.age_seconds is not None
            and b.age_seconds <= self.thresholds.recent_seconds
        ]
        stale_books = [
            b for b in selection.books
            if b.age_seconds is not None
            and b.age_seconds > self.thresholds.stale_seconds
        ]
        if not stale_books or not fresh_books:
            return []
        signals = []
        for book in stale_books:
            fresh_list = ", ".join(
                f"{b.bookmaker} ({round(b.age_seconds)}s)" for b in fresh_books
            )
            reason = (
                f"{book.bookmaker} continua cotando {selection.selection} a "
                f"{book.price} com quote de {round(book.age_seconds)}s, "
                f"enquanto {fresh_list} tem quotes recentes: preco "
                f"possivelmente desatualizado, nao oportunidade."
            )
            signals.append(
                self._make(
                    event_view,
                    market_view,
                    selection,
                    SIGNAL_STALE_PRICE,
                    reason,
                    {
                        "stale_book": book.bookmaker,
                        "stale_age_seconds": round(book.age_seconds),
                        "stale_price": book.price,
                        "stale_timestamp": book.timestamp,
                        "fresh_books": [b.bookmaker for b in fresh_books],
                        "median": round(selection.median, 6),
                    },
                    observed_at=selection.last_update,
                    fingerprint=(book.bookmaker, book.price, book.timestamp),
                    model_prices=models,
                )
            )
        return signals

    def _outlier(
        self,
        event_view: EventView,
        market_view: MarketView,
        selection: SelectionView,
        models: Mapping[str, float],
    ) -> list[Signal]:
        if selection.n_books < self.rules.min_books:
            return []
        signals = []
        for book in selection.books:
            others_prices = [
                b.price for b in selection.books if b.bookmaker != book.bookmaker
            ]
            if len(others_prices) < self.rules.min_books - 1:
                continue
            median_others = statistics.median(others_prices)
            if median_others <= 0:
                continue
            deviation = (book.price - median_others) / median_others
            if abs(deviation) < self.rules.outlier_min_deviation:
                continue
            side = "ACIMA" if deviation > 0 else "ABAIXO"
            reason = (
                f"{book.bookmaker} cotando {selection.selection} a "
                f"{book.price}, {side} da mediana das demais casas "
                f"({round(median_others, 2)}) em "
                f"{round(abs(deviation) * 100, 1)}%: outlier observavel, "
                f"nao edge comprovado."
            )
            signals.append(
                self._make(
                    event_view,
                    market_view,
                    selection,
                    SIGNAL_BOOKMAKER_OUTLIER,
                    reason,
                    {
                        "outlier_book": book.bookmaker,
                        "outlier_price": book.price,
                        "median_others": round(median_others, 6),
                        "deviation_pct": round(abs(deviation), 6),
                        "direction": "UP" if deviation > 0 else "DOWN",
                        "outlier_timestamp": book.timestamp,
                    },
                    observed_at=book.timestamp,
                    fingerprint=(
                        book.bookmaker,
                        book.price,
                        round(median_others, 4),
                    ),
                    model_prices=models,
                )
            )
        return signals

    def _dispersion_spike(
        self,
        event_view: EventView,
        market_view: MarketView,
        selection: SelectionView,
        models: Mapping[str, float],
    ) -> list[Signal]:
        if (
            selection.n_books < self.rules.min_books
            or selection.dispersion is None
            or selection.median <= 0
        ):
            return []
        ratio = selection.dispersion / selection.median
        if ratio < self.rules.dispersion_spike_ratio:
            return []
        prices = {b.bookmaker: b.price for b in selection.books}
        reason = (
            f"Dispersao entre casas em {selection.selection}: "
            f"{round(ratio * 100, 1)}% da mediana "
            f"(stdev {round(selection.dispersion, 3)}, min "
            f"{min(prices.values())}, max {max(prices.values())}) — "
            f"mercado sem acordo neste instante."
        )
        return [
            self._make(
                event_view,
                market_view,
                selection,
                SIGNAL_DISPERSION_SPIKE,
                reason,
                {
                    "dispersion": round(selection.dispersion, 6),
                    "dispersion_ratio": round(ratio, 6),
                    "n_books": selection.n_books,
                    "prices": {k: round(v, 4) for k, v in prices.items()},
                },
                observed_at=selection.last_update,
                fingerprint=(
                    round(selection.dispersion, 4),
                    selection.n_books,
                ),
                model_prices=models,
            )
        ]

    def _best_gap(
        self,
        event_view: EventView,
        market_view: MarketView,
        selection: SelectionView,
        models: Mapping[str, float],
    ) -> list[Signal]:
        if (
            selection.n_books < self.rules.min_books
            or selection.best_vs_median is None
            or selection.median <= 0
        ):
            return []
        gap = selection.best_vs_median / selection.median
        if gap < self.rules.best_gap_min:
            return []
        reason = (
            f"Melhor preco de {selection.selection}: {selection.best.price} "
            f"em {selection.best.bookmaker}, "
            f"{round(gap * 100, 1)}% acima da mediana "
            f"({round(selection.median, 2)}): estrutura de line-shopping, "
            f"informacao de preco, nao selecao de aposta."
        )
        return [
            self._make(
                event_view,
                market_view,
                selection,
                SIGNAL_BEST_PRICE_GAP,
                reason,
                {
                    "best_book": selection.best.bookmaker,
                    "best_price": selection.best.price,
                    "best_timestamp": selection.best.timestamp,
                    "second_best": (
                        selection.second_best.to_dict()
                        if selection.second_best
                        else None
                    ),
                    "median": round(selection.median, 6),
                    "gap_pct": round(gap, 6),
                    "best_vs_second": selection.best_vs_second,
                },
                observed_at=selection.best.timestamp,
                fingerprint=(
                    selection.best.bookmaker,
                    selection.best.price,
                    round(selection.median, 4),
                ),
                model_prices=models,
            )
        ]

    def _movement_signals(
        self,
        event_view: EventView,
        market_view: MarketView,
        selection: SelectionView,
        moves: MarketMovement,
        models: Mapping[str, float],
    ) -> list[Signal]:
        selection_moves = [
            m for m in moves.moves if m.selection == selection.selection
        ]
        if not selection_moves:
            return []
        return [
            *self._consensus_move(
                event_view, market_view, selection, selection_moves, models
            ),
            *self._lead_and_lag(
                event_view, market_view, selection, selection_moves, models
            ),
            *self._reversal(
                event_view, market_view, selection, selection_moves, models
            ),
            *self._convergence(
                event_view, market_view, selection, selection_moves, models
            ),
        ]

    def _moves_in_window(self, moves: Sequence[LineMove]) -> list[LineMove]:
        now = self._now()
        result = []
        for move in moves:
            moment = parse_stamp(move.new_timestamp)
            if moment is None:
                continue
            if (now - moment).total_seconds() <= self.rules.move_window_seconds:
                result.append(move)
        return result

    def _consensus_move(
        self,
        event_view: EventView,
        market_view: MarketView,
        selection: SelectionView,
        moves: Sequence[LineMove],
        models: Mapping[str, float],
    ) -> list[Signal]:
        recent = self._moves_in_window(moves)
        up_books = {m.bookmaker for m in recent if m.direction == DIRECTION_UP}
        down_books = {
            m.bookmaker for m in recent if m.direction == DIRECTION_DOWN
        }
        direction = DIRECTION_UP if len(up_books) >= len(down_books) else DIRECTION_DOWN
        movers = [
            m
            for m in recent
            if m.direction == direction
        ]
        books = {m.bookmaker for m in movers}
        if len(books) < self.rules.consensus_min_books:
            return []
        detail = "; ".join(
            f"{m.bookmaker} {m.old_price}->{m.new_price} @{m.new_timestamp}"
            for m in movers
        )
        reason = (
            f"{len(books)} casas moveram {selection.selection} na mesma "
            f"direcao ({'subiu' if direction == DIRECTION_UP else 'caiu'}) "
            f"na janela de {round(self.rules.move_window_seconds)}s: "
            f"{detail}. Recomposicao de preco observada."
        )
        return [
            self._make(
                event_view,
                market_view,
                selection,
                SIGNAL_CONSENSUS_MOVE,
                reason,
                {
                    "direction": direction,
                    "books": sorted(books),
                    "moves": [m.to_dict() for m in movers],
                },
                observed_at=max(m.new_timestamp for m in movers),
                fingerprint=(
                    direction,
                    tuple(sorted((m.bookmaker, m.new_price) for m in movers)),
                ),
                model_prices=models,
            )
        ]

    def _lead_and_lag(
        self,
        event_view: EventView,
        market_view: MarketView,
        selection: SelectionView,
        moves: Sequence[LineMove],
        models: Mapping[str, float],
    ) -> list[Signal]:
        recent = self._moves_in_window(moves)
        if len(recent) < 2:
            return []
        ordered = sorted(recent, key=lambda m: m.new_timestamp)
        signals: list[Signal] = []
        first = ordered[0]
        followers = [
            m
            for m in ordered[1:]
            if m.bookmaker != first.bookmaker
            and m.direction == first.direction
        ]
        if not followers:
            return signals
        detail = "; ".join(
            f"{m.bookmaker} {m.old_price}->{m.new_price} @{m.new_timestamp}"
            for m in followers
        )
        reason = (
            f"{first.bookmaker} moveu {selection.selection} "
            f"{first.old_price}->{first.new_price} em "
            f"{first.new_timestamp} e {len(followers)} casa(s) seguiram "
            f"na mesma direcao depois: {detail}. "
            f"{first.bookmaker} liderou a reprecificacao."
        )
        signals.append(
            self._make(
                event_view,
                market_view,
                selection,
                SIGNAL_BOOKMAKER_LEAD,
                reason,
                {
                    "lead_book": first.bookmaker,
                    "lead_move": first.to_dict(),
                    "followers": [m.to_dict() for m in followers],
                },
                observed_at=max(m.new_timestamp for m in followers),
                fingerprint=(
                    first.bookmaker,
                    first.new_price,
                    tuple(sorted(m.bookmaker for m in followers)),
                ),
                model_prices=models,
            )
        )
        for follower in followers:
            lag_reason = (
                f"{follower.bookmaker} so moveu {selection.selection} "
                f"{follower.old_price}->{follower.new_price} em "
                f"{follower.new_timestamp}, DEPOIS de {first.bookmaker} "
                f"({first.new_timestamp}): atualizacao atrasada desta casa."
            )
            signals.append(
                self._make(
                    event_view,
                    market_view,
                    selection,
                    SIGNAL_BOOKMAKER_LAG,
                    lag_reason,
                    {
                        "lag_book": follower.bookmaker,
                        "lag_move": follower.to_dict(),
                        "lead_book": first.bookmaker,
                        "lead_timestamp": first.new_timestamp,
                    },
                    observed_at=follower.new_timestamp,
                    fingerprint=(
                        follower.bookmaker,
                        follower.new_price,
                        first.bookmaker,
                        first.new_timestamp,
                    ),
                    model_prices=models,
                )
            )
        return signals

    def _reversal(
        self,
        event_view: EventView,
        market_view: MarketView,
        selection: SelectionView,
        moves: Sequence[LineMove],
        models: Mapping[str, float],
    ) -> list[Signal]:
        by_book: dict[str, list[LineMove]] = {}
        for move in sorted(moves, key=lambda m: m.new_timestamp):
            by_book.setdefault(move.bookmaker, []).append(move)
        signals: list[Signal] = []
        for bookmaker, book_moves in by_book.items():
            if len(book_moves) < 2:
                continue
            last = book_moves[-1]
            previous = book_moves[-2]
            if last.direction == previous.direction:
                continue
            description = (
                "subiu e voltou a cair"
                if previous.direction == DIRECTION_UP
                else "caiu e voltou a subir"
            )
            reason = (
                f"{bookmaker} inverteu {selection.selection}: "
                f"{description} ({previous.old_price}->{previous.new_price} "
                f"em {previous.new_timestamp}, depois "
                f"{last.old_price}->{last.new_price} em "
                f"{last.new_timestamp}). Reversao observada."
            )
            signals.append(
                self._make(
                    event_view,
                    market_view,
                    selection,
                    SIGNAL_PRICE_REVERSAL,
                    reason,
                    {
                        "book": bookmaker,
                        "previous_move": previous.to_dict(),
                        "last_move": last.to_dict(),
                    },
                    observed_at=last.new_timestamp,
                    fingerprint=(
                        bookmaker,
                        previous.new_price,
                        last.new_price,
                    ),
                    model_prices=models,
                )
            )
        return signals

    def _convergence(
        self,
        event_view: EventView,
        market_view: MarketView,
        selection: SelectionView,
        moves: Sequence[LineMove],
        models: Mapping[str, float],
    ) -> list[Signal]:
        recent = self._moves_in_window(moves)
        moved_books = {m.bookmaker for m in recent}
        if len(moved_books) < 2 or selection.n_books < 2 or selection.median <= 0:
            return []
        prices = [b.price for b in selection.books]
        spread = (max(prices) - min(prices)) / selection.median
        if spread > self.rules.convergence_max_spread:
            return []
        reason = (
            f"Precos de {selection.selection} convergiram: spread entre "
            f"casas caiu para {round(spread * 100, 2)}% da mediana apos "
            f"movimentos recentes de {len(moved_books)} casas — mercado "
            f"sincronizando."
        )
        return [
            self._make(
                event_view,
                market_view,
                selection,
                SIGNAL_RAPID_CONVERGENCE,
                reason,
                {
                    "spread_pct": round(spread, 6),
                    "moved_books": sorted(moved_books),
                    "moves": [m.to_dict() for m in recent],
                },
                observed_at=max(m.new_timestamp for m in recent),
                fingerprint=(
                    round(spread, 6),
                    tuple(sorted(moved_books)),
                ),
                model_prices=models,
            )
        ]

    # ------------------------------------------------------------ ciclo

    def _expire(self, signal: Signal, cause: str) -> Signal:
        self.expired_count += 1
        self._active.pop(signal.signal_id, None)
        data = dict(signal.__dict__)
        data["reason"] = f"[EXPIRED:{cause}] {signal.reason}"
        return Signal(**data)

    def _ttl_sweep(self) -> list[Signal]:
        expired: list[Signal] = []
        now = self._now()
        for signal_id in list(self._active):
            signal = self._active[signal_id]
            age = age_seconds(signal.observed_at, now)
            if age is not None and age > self.rules.ttl_seconds:
                expired.append(self._expire(signal, "EVIDENCE_TTL"))
        return expired

    # ------------------------------------------------------------ leitura

    def active_signals(self, event_key: Optional[str] = None) -> list[Signal]:
        """Sinais ativos com status derivado da idade da evidencia."""
        now = self._now()
        out = [
            with_status(signal, now, self.rules)
            for signal in self._active.values()
            if event_key is None or signal.event_key == event_key
        ]
        out.sort(
            key=lambda s: (s.event_key, s.market, s.selection, s.signal_type)
        )
        return out

    def stats(self) -> dict:
        return {
            "active": len(self._active),
            "created_total": self.created_count,
            "expired_total": self.expired_count,
        }


def with_status(signal: Signal, now: datetime, rules: SignalRules) -> Signal:
    """ACTIVE/STALE/EXPIRED derivado da idade — nunca congelado."""
    age = age_seconds(signal.observed_at, now)
    if age is None or age > rules.ttl_seconds:
        status = SIGNAL_EXPIRED
    elif age > rules.stale_after_seconds:
        status = SIGNAL_STALE
    else:
        status = SIGNAL_ACTIVE
    data = dict(signal.__dict__)
    data["status"] = status
    return Signal(**data)
