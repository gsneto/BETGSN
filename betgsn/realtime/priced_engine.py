"""Fabrica PricedSignals a partir das views live com a MESMA política central.

Nenhum sinal aqui vira aposta automática; produção só libera com todos os
sete blocos GREEN, edge>=8%, EV>=8%, spread<=0.12 e execução medida.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from ..odds_normalize import NormalizedQuote
from ..priced_signals import (
    EXECUTION_WINDOW_SECONDS, PricedSignal, build_priced_signal,
)
from ..production_policy import GateBlock, ProductionGate, REQUIRED_BLOCKS
from ..timeutil import parse_kickoff, utc_key
from .views import EventView, MarketView, SelectionView


@dataclass(frozen=True)
class FairOverride:
    """Fair calibrado injetado por chave do live.

    A calibration_fingerprint identifica método + janela + n usados na
    calibração offline; sem esse selo o gate MARKET/PROVENANCE não sai
    de PENDING. A engine NUNCA infere fair — ausência é ausência.
    """
    prob: float
    window_id: str
    method: str
    n: int
    calibration_fingerprint: str

    def valid(self) -> bool:
        return (
            isinstance(self.prob, (int, float))
            and 0.0 < float(self.prob) < 1.0
            and bool(self.window_id) and bool(self.method)
            and isinstance(self.n, int) and self.n > 0
            and bool(self.calibration_fingerprint)
        )


def _compose_signal_gate(base: ProductionGate, *, market_ok: bool,
                         execution_ok: bool, provenance_ok: bool,
                         market_reasons: tuple[str, ...] = (),
                         execution_reasons: tuple[str, ...] = (),
                         provenance_reasons: tuple[str, ...] = ()
                         ) -> ProductionGate:
    """Gate por-sinal.

    - MARKET/EXECUTION/PROVENANCE viram GREEN somente com evidência no
      próprio sinal (nunca herdam GREEN do gate base para esses três).
    - Os demais blocos (MODEL/CLV/ROBUSTNESS/TEMPORAL) sempre herdam do
      base: evidência offline não pode ser fabricada por tick.
    """
    blocks = {name: base.blocks[name] for name in REQUIRED_BLOCKS}
    blocks["MARKET"] = GateBlock(
        "GREEN" if market_ok else "PENDING",
        () if market_ok else (market_reasons or ("fair_override ausente para a seleção",)),
    )
    blocks["EXECUTION"] = GateBlock(
        "GREEN" if execution_ok else "PENDING",
        () if execution_ok else (execution_reasons or ("execução medida indisponível",)),
    )
    blocks["PROVENANCE"] = GateBlock(
        "GREEN" if provenance_ok else "PENDING",
        () if provenance_ok else (provenance_reasons or ("selo de proveniência incompleto",)),
    )
    return ProductionGate(blocks, base.fingerprint)


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
        fair_override: Mapping[str, FairOverride] | None = None,
        executions: Mapping[str, tuple[float, str]] | None = None,
    ) -> list[PricedSignal]:
        """Materializa PricedSignals para as seleções do evento.

        `fair_override` mapeia `event_key|market|selection` -> FairOverride
        (calibração por janela/método/n + fingerprint). Quando presente,
        os blocos MARKET/PROVENANCE saem de PENDING para GREEN naquele
        sinal — nunca no gate global. Sem override o bloco continua
        PENDING (nada é inferido).

        `executions` mapeia `event_key|market|selection` -> (price, ts).
        Fill com selo temporal dentro da janela vira EXECUTED e libera
        EXECUTION/PROVENANCE; fora da janela vira EXPIRED e o bloco
        permanece PENDING.
        """
        if view is None or not view.matched:
            return []
        signals: list[PricedSignal] = []
        overrides = dict(fair_probs or {})
        fair_calib = dict(fair_override or {})
        fills = dict(executions or {})
        for market_view in view.markets:
            signals.extend(self._market_signals(
                view, market_view, decision_ts, models,
                overrides, fair_calib, fills,
            ))
        return signals

    def _market_signals(
        self,
        view: EventView,
        market_view: MarketView,
        decision_ts: str,
        models: Mapping[str, tuple[str, float]],
        overrides: Mapping[str, float],
        fair_calib: Mapping[str, FairOverride],
        fills: Mapping[str, tuple[float, str]],
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
            calib = fair_calib.get(key)
            if calib is not None and not calib.valid():
                calib = None
            fair_prob = None
            if calib is not None:
                fair_prob = float(calib.prob)
            if fair_prob is None:
                fair_prob = market_view.fair_probabilities.get(selection.selection)
            if fair_prob in (None, 0):
                fair_prob = overrides.get(key)
            model_pair = models.get(key)
            model_prob = model_pair[1] if model_pair else None
            model_fp = model_pair[0] if model_pair else ""
            ev = model_prob * selected.price - 1 if model_prob is not None else None
            spread = getattr(selection, "dispersion", None)
            fill = fills.get(key)
            executed_price = fill[0] if fill else None
            executed_at = fill[1] if fill else None
            # Composição do gate por-sinal (nunca fabrica evidência: só
            # promove blocos cujas condições live estão presentes).
            market_ok = calib is not None
            execution_ok = False
            if executed_price is not None and executed_at:
                try:
                    gap_s = (parse_kickoff(utc_key(executed_at))
                             - parse_kickoff(utc_key(decision_ts))).total_seconds()
                    execution_ok = 0 <= gap_s <= EXECUTION_WINDOW_SECONDS
                except Exception:
                    execution_ok = False
            provenance_ok = (
                market_ok
                and bool(selected.provider) and bool(selected.timestamp)
                and (execution_ok or fill is None)
            )
            reasons_market = () if market_ok else (
                "fair_override ausente para a seleção",)
            reasons_exec = () if execution_ok else (
                "sem executed_price+executed_at registrado",)
            reasons_prov = ()
            if not provenance_ok:
                if not market_ok:
                    reasons_prov = ("calibração ausente compromete proveniência",)
                elif not selected.provider or not selected.timestamp:
                    reasons_prov = ("quote sem provider/timestamp auditável",)
                else:
                    reasons_prov = ("fill registrada sem selo temporal",)
            gate_for_signal = _compose_signal_gate(
                self.gate,
                market_ok=market_ok, execution_ok=execution_ok,
                provenance_ok=provenance_ok,
                market_reasons=reasons_market,
                execution_reasons=reasons_exec,
                provenance_reasons=reasons_prov,
            )
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
                    gate=gate_for_signal,
                    model_fingerprint=model_fp,
                    executed_price=executed_price,
                    executed_at=executed_at,
                )
            except ValueError:
                # dropped quotes carry a reason inside build_priced_signal; no fake signal
                continue
            out.append(signal)
        return out


__all__ = ["PricedRealtimeEngine", "FairOverride"]
