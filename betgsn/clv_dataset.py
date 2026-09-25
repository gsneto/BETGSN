"""BETGSN :: clv_dataset — dataset de CLV com referências de fechamento.

Constrói, a partir do ciclo de vida REAL do store, um dataset auditável:

    signal/event/market/selection
    observed_price (mediana na decisão)
    selected_price (quando existir no sinal — não fabricado)
    closing_price (mediana das casas na janela)
    closing_source (consensus_median | bookmaker_close | exchange_close)
    reference_closes (Pinnacle/Betfair/bet365 quando observados)
    signal_timestamp / closing_timestamp
    clv
    time_to_kickoff / freshness / n_books

Referências de fechamento (separadas, nunca substituídas em silêncio):

    bookmaker_close   Pinnacle (sharp de referência)
    exchange_close    Betfair (exchange)
    consensus_close   mediana entre casas (o que o store já calcula)

`PENDING`/`NO_CLOSE`/`INVALID`/`MISMATCH` NÃO entram como CLV válido — só
`CLOSED` com `closing_odd` e `closing_timestamp` reais.

Progresso: `closed / MIN_CLV_SAMPLE` (200). Nunca finge progresso.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass, field
from typing import Sequence

from .config import production_thresholds
from .odds_snapshots import OddsSnapshotStore

#: Casas de referência (sharp anchor / exchange). Presença observada, não
#: presumida — sem observação, a referência é ausente.
BOOKMAKER_REFERENCE = "Pinnacle"
EXCHANGE_REFERENCE = "Betfair"


def clv_progress(store: OddsSnapshotStore | None = None) -> dict:
    """Progresso do CLV rumo ao alvo: closed/pending/no_close/invalid/200.

    Usa `clv_state_counts` (uma passada) — o sweep completo continua sendo
    a fonte autoritativa do relatório; aqui o alvo é o monitor rápido.
    """
    from .models.promotion import MIN_CLV_SAMPLE

    store = store or OddsSnapshotStore()
    counts = store.clv_state_counts()
    by_state = counts["by_state"]
    closed = int(by_state.get("CLOSED", 0))
    target = int(production_thresholds()["min_clv_sample"])
    return {
        "target": target,
        "closed": closed,
        "pending": int(by_state.get("PENDING", 0)),
        "no_close": int(by_state.get("NO_CLOSE", 0)),
        "invalid": int(by_state.get("INVALID", 0)),
        "mismatch": int(by_state.get("MISMATCH", 0)),
        "n_entries": int(counts["n_entries"]),
        "remaining": max(0, target - closed),
        "status": (
            "CLV_INSUFFICIENT_DATA" if closed < target else "CLV_READY"
        ),
        "note": (
            "CLV válido = CLOSED + closing_odd + closing_timestamp reais. "
            "PENDING/NO_CLOSE/INVALID/MISMATCH não contam."
        ),
    }


@dataclass
class ClvDatasetRow:
    event_key: str
    market: str
    selection: str
    observed_price: float
    selected_price: float | None
    closing_price: float | None
    closing_source: str
    closing_bookmaker: str
    reference_closes: dict[str, float]
    signal_timestamp: str
    closing_timestamp: str
    clv: float | None
    time_to_kickoff: float | None
    n_books: int
    state: str

    def to_dict(self) -> dict:
        return {
            "event_key": self.event_key,
            "market": self.market,
            "selection": self.selection,
            "observed_price": round(self.observed_price, 6),
            "selected_price": (
                round(self.selected_price, 6)
                if self.selected_price is not None else None
            ),
            "closing_price": (
                round(self.closing_price, 6)
                if self.closing_price is not None else None
            ),
            "closing_source": self.closing_source,
            "closing_bookmaker": self.closing_bookmaker,
            "reference_closes": {
                k: round(v, 6) for k, v in self.reference_closes.items()
            },
            "signal_timestamp": self.signal_timestamp,
            "closing_timestamp": self.closing_timestamp,
            "clv": round(self.clv, 6) if self.clv is not None else None,
            "time_to_kickoff": (
                round(self.time_to_kickoff, 1)
                if self.time_to_kickoff is not None else None
            ),
            "n_books": self.n_books,
            "state": self.state,
        }


def _reference_closes(
    store: OddsSnapshotStore, event_key: str, market: str, outcome: str,
) -> dict[str, float]:
    by_book = store.closing_by_book(event_key, market, outcome)
    out: dict[str, float] = {}
    if BOOKMAKER_REFERENCE in by_book:
        out["bookmaker_close"] = by_book[BOOKMAKER_REFERENCE][0]
    if EXCHANGE_REFERENCE in by_book:
        out["exchange_close"] = by_book[EXCHANGE_REFERENCE][0]
    if by_book:
        out["consensus_close"] = statistics.median(
            odd for odd, _ts, _m in by_book.values()
        )
    return out


def clv_dataset(
    store: OddsSnapshotStore | None = None,
    *,
    only_closed: bool = False,
) -> dict:
    """Dataset de CLV com fingerprint, a partir do ciclo de vida real."""
    from .timeutil import parse_kickoff

    store = store or OddsSnapshotStore()
    sweep = store.clv_lifecycle_sweep()
    rows: list[ClvDatasetRow] = []
    for lc in sweep.lifecycles:
        state = lc.state
        if only_closed and state != "CLOSED":
            continue
        entry = lc.entry
        result = lc.result
        refs = _reference_closes(
            store, entry.match_key, entry.market, entry.outcome
        )
        closing_price = result.closing_odd if result else None
        closing_ts = result.closing_timestamp if result else ""
        source = "consensus_median" if closing_price is not None else ""
        if result is not None and result.closing_bookmaker:
            if result.closing_bookmaker == BOOKMAKER_REFERENCE:
                source = "bookmaker_close"
            elif result.closing_bookmaker == EXCHANGE_REFERENCE:
                source = "exchange_close"
        ttk = None
        try:
            ttk = (
                parse_kickoff(entry.kickoff)
                - parse_kickoff(entry.entry_timestamp)
            ).total_seconds()
        except Exception:  # noqa: BLE001
            ttk = None
        rows.append(ClvDatasetRow(
            event_key=entry.match_key,
            market=entry.market,
            selection=entry.outcome,
            observed_price=entry.entry_odd,
            selected_price=None,
            closing_price=closing_price,
            closing_source=source,
            closing_bookmaker=(result.closing_bookmaker if result else ""),
            reference_closes=refs,
            signal_timestamp=entry.entry_timestamp,
            closing_timestamp=closing_ts,
            clv=(result.clv_percentage if result else None),
            time_to_kickoff=ttk,
            n_books=entry.entry_n_books,
            state=state,
        ))

    fingerprint = hashlib.sha256(json.dumps({
        "n": len(rows),
        "events": sorted({r.event_key for r in rows}),
        "states": {s: sum(1 for r in rows if r.state == s)
                   for s in sorted({r.state for r in rows})},
    }, sort_keys=True).encode()).hexdigest()[:16]

    return {
        "kind": "clv_dataset",
        "fingerprint": fingerprint,
        "progress": clv_progress(store),
        "rows": [r.to_dict() for r in rows],
    }


__all__ = [
    "clv_progress",
    "clv_dataset",
    "ClvDatasetRow",
    "BOOKMAKER_REFERENCE",
    "EXCHANGE_REFERENCE",
]
