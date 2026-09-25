"""BETGSN :: alpha_replay — replay point-in-time do store para o Alpha Lab.

Reconstrói, em instantes de decisão ao longo da série observada, o estado
de mercado PIT e avalia os sinais com os MESMOS thresholds de produção
(`SignalRules`). Para cada sinal emitido, mede o movimento POSTERIOR do
mercado e o fechamento (quando existir).

Nada aqui usa dado futuro para DECIDIR: o estado em T usa quotes <= T; o
"futuro" só é lido para MEDIR o que aconteceu depois.
"""
from __future__ import annotations

import statistics
from typing import Iterable, Sequence

from .alpha_lab import (
    FORWARD_HORIZONS,
    ForwardObservation,
    observe_signal,
)
from .realtime.signals import SignalRules
from .timeutil import parse_kickoff, utc_key

SIGNAL_OUTLIER = "BOOKMAKER_OUTLIER"
SIGNAL_DISPERSION = "DISPERSION_SPIKE"
SIGNAL_BEST_GAP = "BEST_PRICE_GAP"
SIGNAL_CONSENSUS = "CONSENSUS_MOVE"

ALPHA_OF = {
    SIGNAL_OUTLIER: "bookmaker_outlier",
    SIGNAL_DISPERSION: "dispersion_spike",
    SIGNAL_BEST_GAP: "best_price_gap",
    SIGNAL_CONSENSUS: "consensus_move",
}


def _line_series(
    observations: Sequence,
) -> dict[tuple[str, str], list[tuple[str, str, float]]]:
    """{(mercado, resultado): [(timestamp, casa, preço)] ordenado}."""
    series: dict[tuple[str, str], list[tuple[str, str, float]]] = {}
    for o in observations:
        series.setdefault((o.market, o.outcome), []).append(
            (o.timestamp, o.bookmaker, float(o.odd))
        )
    for rows in series.values():
        rows.sort(key=lambda r: r[0])
    return series


def _latest_by_book(
    rows: Sequence[tuple[str, str, float]], cutoff: str
) -> dict[str, float]:
    limit = utc_key(cutoff)
    latest: dict[str, tuple[str, float]] = {}
    for stamp, book, price in rows:
        if utc_key(stamp) > limit:
            continue
        cur = latest.get(book)
        if cur is None or utc_key(stamp) >= utc_key(cur[0]):
            latest[book] = (stamp, price)
    return {b: p for b, (_s, p) in latest.items()}


def _closing_price(rows: Sequence[tuple[str, str, float]], kickoff: str,
                   window_minutes: float) -> float | None:
    """Mediana do último preço por casa dentro da janela do kickoff."""
    ko = parse_kickoff(kickoff)
    latest: dict[str, tuple[float, float]] = {}
    for stamp, book, price in rows:
        minutes = (ko - parse_kickoff(stamp)).total_seconds() / 60.0
        if not (0 < minutes <= window_minutes):
            continue
        cur = latest.get(book)
        if cur is None or minutes < cur[0]:
            latest[book] = (minutes, price)
    if not latest:
        return None
    return statistics.median(p for _m, p in latest.values())


def _decision_instants(
    rows: Sequence[tuple[str, str, float]],
    *,
    stride_seconds: int,
) -> list[str]:
    """Instantes de decisão: timestamps distintos amostrados por stride."""
    stamps = sorted({r[0] for r in rows})
    if not stamps:
        return []
    out: list[str] = []
    last: float | None = None
    for stamp in stamps:
        moment = parse_kickoff(stamp).timestamp()
        if last is None or moment - last >= stride_seconds:
            out.append(stamp)
            last = moment
    return out


def replay_signals(
    store,
    *,
    markets: Iterable[str] | None = None,
    max_matches: int | None = None,
    decision_stride_seconds: int = 900,
    closing_window_minutes: float = 120.0,
    rules: SignalRules | None = None,
) -> list[ForwardObservation]:
    """Gera observações prospectivas dos sinais sobre o store real."""
    rules = rules or SignalRules()
    wanted = set(markets) if markets else None

    # agrupa observações por partida
    by_match: dict[str, list] = {}
    for obs in store.observations_for_markets(sorted(wanted) if wanted else None):
        by_match.setdefault(obs.match_key, []).append(obs)

    match_keys = sorted(by_match)
    if max_matches is not None:
        match_keys = match_keys[:max_matches]

    out: list[ForwardObservation] = []
    for match_key in match_keys:
        observations = by_match[match_key]
        if not observations:
            continue
        kickoff = observations[0].kickoff
        league = ""
        series = _line_series(observations)

        for (market, selection), rows in series.items():
            if len(rows) < rules.min_books:
                continue
            provider = ""
            for o in observations:
                if o.market == market and o.outcome == selection and o.provider:
                    provider = o.provider
                    break

            for instant in _decision_instants(rows, stride_seconds=decision_stride_seconds):
                prices = _latest_by_book(rows, instant)
                if len(prices) < rules.min_books:
                    continue
                ordered = sorted(prices.items(), key=lambda kv: (-kv[1], kv[0]))
                price_list = [p for _b, p in ordered]
                median = statistics.median(price_list)
                if median <= 0:
                    continue
                best_book, best_price = ordered[0]
                dispersion = statistics.pstdev(price_list)
                dispersion_ratio = dispersion / median
                best_gap_ratio = (best_price - median) / median

                closing = _closing_price(rows, kickoff, closing_window_minutes)
                base = dict(
                    event_key=match_key, market=market, selection=selection,
                    signal_timestamp=instant, kickoff=kickoff, series=rows,
                    median_price=median, best_price=best_price,
                    book_count=len(prices), dispersion_ratio=dispersion_ratio,
                    closing_price=closing, provider=provider, league=league,
                )

                # BEST_PRICE_GAP (sem direção: mede persistência/convergência)
                if best_gap_ratio >= rules.best_gap_min:
                    out.append(observe_signal(
                        signal_type=SIGNAL_BEST_GAP, alpha_id=ALPHA_OF[SIGNAL_BEST_GAP],
                        bookmaker=best_book, decision_price=best_price,
                        deviation=best_price - median, direction="FLAT", **base,
                    ))

                # DISPERSION_SPIKE (sem direção)
                if dispersion_ratio >= rules.dispersion_spike_ratio:
                    out.append(observe_signal(
                        signal_type=SIGNAL_DISPERSION,
                        alpha_id=ALPHA_OF[SIGNAL_DISPERSION],
                        bookmaker=best_book, decision_price=median,
                        deviation=dispersion, direction="FLAT", **base,
                    ))

                # BOOKMAKER_OUTLIER (com direção implícita por casa)
                if len(prices) >= rules.min_books:
                    for book, price in prices.items():
                        others = [p for b, p in prices.items() if b != book]
                        if len(others) < rules.min_books - 1:
                            continue
                        med_others = statistics.median(others)
                        if med_others <= 0:
                            continue
                        deviation = (price - med_others) / med_others
                        if abs(deviation) < rules.outlier_min_deviation:
                            continue
                        direction = "UP" if price > med_others else "DOWN"
                        out.append(observe_signal(
                            signal_type=SIGNAL_OUTLIER,
                            alpha_id=ALPHA_OF[SIGNAL_OUTLIER],
                            bookmaker=book, decision_price=price,
                            deviation=deviation, direction=direction, **base,
                        ))
    return out


__all__ = ["replay_signals", "SIGNAL_OUTLIER", "SIGNAL_DISPERSION",
           "SIGNAL_BEST_GAP", "SIGNAL_CONSENSUS", "ALPHA_OF"]
