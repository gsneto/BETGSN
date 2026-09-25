"""BETGSN :: market_audit — auditoria MARKET_RAW vs MARKET_FAIR por mercado.

Separa explicitamente três coisas que NUNCA podem ser confundidas:

    MARKET_RAW    probabilidade implícita bruta (1/odd) — contém a margem
    MARKET_FAIR   probabilidade sem margem (de-vig), com `complete_market`
    MODEL         probabilidade do modelo BETGSN (outro caminho)

Um mercado INCOMPLETO (falta outcome, ou o grupo não fecha) NÃO tem fair
válido: a auditoria marca `complete=False` e NÃO usa esse fair como se
fosse completo.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Sequence

from .line_shopping import market_analysis
from .odds_math import devig, market_overround
from .odds_normalize import NormalizedQuote

#: Mercados auditáveis hoje (cobertura observada no store).
AUDITED_MARKETS = (
    "Resultado Final (1X2)",
    "Total de Gols",
    "Ambas Marcam",
)


@dataclass
class MarketAudit:
    market: str
    n_lines: int
    n_complete: int
    n_incomplete: int
    mean_overround: float | None
    mean_book_count: float | None
    devig_method: str
    examples_raw: dict[str, float] = field(default_factory=dict)
    examples_fair: dict[str, float] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def complete_ratio(self) -> float:
        return self.n_complete / self.n_lines if self.n_lines else 0.0

    def to_dict(self) -> dict:
        return {
            "market": self.market,
            "n_lines": self.n_lines,
            "n_complete": self.n_complete,
            "n_incomplete": self.n_incomplete,
            "complete_ratio": round(self.complete_ratio, 4),
            "mean_overround": (
                round(self.mean_overround, 6)
                if self.mean_overround is not None else None
            ),
            "mean_book_count": (
                round(self.mean_book_count, 3)
                if self.mean_book_count is not None else None
            ),
            "devig_method": self.devig_method,
            "examples_raw": self.examples_raw,
            "examples_fair": self.examples_fair,
            "notes": list(self.notes),
        }


def audit_market(
    quotes: Sequence[NormalizedQuote],
    market: str,
    *,
    method: str = "multiplicative",
) -> MarketAudit:
    """Audita um mercado a partir das quotes observadas (PIT no chamador)."""
    usable = [q for q in quotes if q.market == market and q.usable]
    if not usable:
        return MarketAudit(
            market=market, n_lines=0, n_complete=0, n_incomplete=0,
            mean_overround=None, mean_book_count=None, devig_method=method,
            notes=("sem quotes utilizaveis",),
        )

    # agrupa por evento; cada evento é uma "linha" de mercado
    by_event: dict[str, list[NormalizedQuote]] = {}
    for q in usable:
        by_event.setdefault(q.event_id, []).append(q)

    overrounds: list[float] = []
    book_counts: list[int] = []
    n_complete = 0
    example_raw: dict[str, float] = {}
    example_fair: dict[str, float] = {}

    for event_id, group in by_event.items():
        analysis = market_analysis(group, market, method=method)
        if analysis is None:
            continue
        book_counts.append(
            max(analysis.n_books.values()) if analysis.n_books else 0
        )
        if analysis.complete:
            n_complete += 1
            if analysis.overround:
                overrounds.append(analysis.overround)
            if not example_raw:
                example_raw = {
                    sel: round(1.0 / odd, 6)
                    for sel, odd in analysis.medians.items()
                }
                example_fair = {
                    sel: round(p, 6)
                    for sel, p in analysis.fair_probabilities.items()
                }

    return MarketAudit(
        market=market,
        n_lines=len(by_event),
        n_complete=n_complete,
        n_incomplete=len(by_event) - n_complete,
        mean_overround=(
            statistics.fmean(overrounds) if overrounds else None
        ),
        mean_book_count=(
            statistics.fmean(book_counts) if book_counts else None
        ),
        devig_method=method,
        examples_raw=example_raw,
        examples_fair=example_fair,
        notes=(
            "MARKET_RAW = 1/odd (com margem); MARKET_FAIR = de-vig do grupo "
            "completo. Mercado incompleto não gera fair utilizável.",
        ),
    )


def audit_all_markets(
    quotes: Sequence[NormalizedQuote],
    markets: Sequence[str] = AUDITED_MARKETS,
    *,
    method: str = "multiplicative",
) -> dict[str, MarketAudit]:
    return {m: audit_market(quotes, m, method=method) for m in markets}


__all__ = ["MarketAudit", "audit_market", "audit_all_markets",
           "AUDITED_MARKETS"]
