"""BETGSN :: datalayer.canonical_bridge — ponte para os modelos canônicos.

A camada de dados fala em envelopes; o resto do BETGSN fala em objetos
canônicos (`canonical.CanonicalMatch`, `CanonicalOdds`, ...). Esta ponte
converte os registros das fontes reais no modelo canônico, preservando
proveniência e SEM inventar campos ausentes.

Regras:
  - odds sem carimbo de observação ficam com `timestamp=""` (nunca o
    kickoff): assim `CanonicalOdds.best_before` não as trata como
    disponíveis point-in-time;
  - xG ausente continua `UNAVAILABLE`;
  - estatística ausente continua `None`.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..canonical import (
    CanonicalMatch,
    CanonicalMatchStats,
    CanonicalOdds,
    CanonicalOddsLine,
    CanonicalXG,
    DataConfidence,
)
from ..football_data_uk import CsvMatch, UpcomingFixture
from ..xg_sources import XGStatus
from .envelope import DataEnvelope
from .source import Capabilities

__all__ = [
    "from_csv_match",
    "from_upcoming_fixture",
    "from_api_fixture",
    "odds_to_canonical",
    "envelope_to_canonical",
]


def odds_to_canonical(
    mapping: dict[str, dict[str, dict[str, float]]],
    *,
    match_key: str,
    source: str,
    retrieved_at: str = "",
) -> CanonicalOdds | None:
    """Converte `{mercado: {casa: {resultado: odd}}}` em `CanonicalOdds`."""
    lines: list[CanonicalOddsLine] = []
    for market, books in mapping.items():
        for book, outcomes in books.items():
            for outcome, odd in outcomes.items():
                lines.append(
                    CanonicalOddsLine(
                        bookmaker=book,
                        market=market,
                        outcome=outcome,
                        odd=float(odd),
                        timestamp="",  # CSV não publica carimbo de observação
                    )
                )
    if not lines:
        return None
    return CanonicalOdds(
        match_key=match_key,
        lines=tuple(lines),
        retrieved_at=retrieved_at,
        source=source,
    )


def _stats_from_csv(m: CsvMatch) -> tuple[CanonicalMatchStats, CanonicalMatchStats]:
    home = CanonicalMatchStats(
        shots=m.home_shots,
        shots_on_target=m.home_shots_on_target,
        corners=m.home_corners,
        yellow_cards=None,
        red_cards=None,
    )
    away = CanonicalMatchStats(
        shots=m.away_shots,
        shots_on_target=m.away_shots_on_target,
        corners=m.away_corners,
        yellow_cards=None,
        red_cards=None,
    )
    return home, away


def from_csv_match(m: CsvMatch, *, source: str = "football_data_uk") -> CanonicalMatch:
    """Converte um `CsvMatch` (resultado + odds + estatísticas) em canônico."""
    home_stats, away_stats = _stats_from_csv(m)
    odds = odds_to_canonical(
        m.odds_closing or m.odds_opening,
        match_key=m.match_key,
        source=source,
    )
    return CanonicalMatch(
        home=m.home,
        away=m.away,
        home_goals=m.home_goals,
        away_goals=m.away_goals,
        kickoff=m.kickoff,
        timezone=m.timezone,
        league=m.league,
        season=m.season,
        referee=m.referee,
        attendance=m.attendance,
        home_stats=home_stats,
        away_stats=away_stats,
        xg=CanonicalXG(status=XGStatus.UNAVAILABLE.value),
        odds=odds,
        source=source,
        confidence=DataConfidence.HIGH if m.home_shots is not None else DataConfidence.MEDIUM,
    )


def from_upcoming_fixture(
    f: UpcomingFixture, *, source: str = "football_data_uk"
) -> CanonicalMatch:
    """Converte um jogo futuro (sem placar) em canônico."""
    odds = odds_to_canonical(f.odds, match_key=f"{f.date}|{f.home}|{f.away}", source=source)
    return CanonicalMatch(
        home=f.home,
        away=f.away,
        home_goals=None,
        away_goals=None,
        kickoff=f.kickoff,
        timezone=f.timezone,
        league=f.league,
        odds=odds,
        source=source,
        confidence=DataConfidence.MEDIUM,
    )


def from_api_fixture(payload: dict[str, Any], *, source: str = "api_football") -> CanonicalMatch | None:
    """Converte um item de `/fixtures` da API-Football em canônico.

    Devolve None quando falta placar ou kickoff — melhor ausência do que
    um canônico incompleto fingindo ser resultado.
    """
    fixture = payload.get("fixture") or {}
    league = payload.get("league") or {}
    teams = payload.get("teams") or {}
    goals = payload.get("goals") or {}
    date = fixture.get("date")
    home = (teams.get("home") or {}).get("name")
    away = (teams.get("away") or {}).get("name")
    if not date or not home or not away:
        return None
    return CanonicalMatch(
        home=home,
        away=away,
        home_goals=goals.get("home"),
        away_goals=goals.get("away"),
        kickoff=date,
        league=league.get("name") or "",
        season=str(league.get("season") or ""),
        xg=CanonicalXG(status=XGStatus.UNAVAILABLE.value),
        source=source,
        confidence=DataConfidence.MEDIUM,
    )


def envelope_to_canonical(
    envelope: DataEnvelope, kind: str
) -> list[CanonicalMatch]:
    """Converte o `value` de um envelope em objetos canônicos.

    O tipo de cada registro decide o conversor. Registros desconhecidos são
    ignorados (nunca convertidos "de qualquer jeito").
    """
    out: list[CanonicalMatch] = []
    for record in _iter_records(envelope.value):
        if isinstance(record, CsvMatch):
            out.append(from_csv_match(record, source=envelope.source or "football_data_uk"))
        elif isinstance(record, UpcomingFixture):
            out.append(from_upcoming_fixture(record, source=envelope.source or "football_data_uk"))
        elif isinstance(record, dict) and kind == Capabilities.RESULTS:
            converted = from_api_fixture(record, source=envelope.source or "api_football")
            if converted is not None:
                out.append(converted)
    return out


def _iter_records(value: Any) -> Iterable[Any]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return value
    return (value,)
