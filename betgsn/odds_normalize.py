"""BETGSN :: odds_normalize — normalizacao canonica de odds multi-provider.

Este modulo e o UNICO parser de payloads de odds do BETGSN. Qualquer
provider (The Odds API, ParlayAPI, futuros) entra por aqui e sai no mesmo
formato, para que bookmakers, mercados e resultados possam ser comparados
sem matching incorreto.

Campos normalizados
-------------------
event, home_team, away_team, bookmaker, market, selection, price,
timestamp, provider.

Decisoes que evitam erro silencioso
-----------------------------------
  - `event_id` NAO depende do provider: e derivado de (mandante, visitante,
    kickoff) normalizados. Isso permite juntar o mesmo jogo visto por
    providers diferentes e fazer line shopping de verdade.
  - mercados desconhecidos sao DESCARTADOS, nunca renomeados para um
    mercado parecido. Um palpite de rotulo e pior que um dado ausente.
  - cotacoes depois do kickoff ficam marcadas (`pre_kickoff=False`) e sao
    excluidas pelo consumidor; nunca viram odd "atual".
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable, Mapping, Optional, Sequence

from .backtest_sources import normalize_team
from .timeutil import KickoffError, utc_key

#: Mapa canonico provider -> rotulo interno do BETGSN.
#: As chaves sao as da The Odds API; providers futuros devem reusar os
#: mesmos rotulos internos.
MARKET_MAP: dict[str, str] = {
    "h2h": "Resultado Final (1X2)",
    "totals": "Total de Gols",
    "btts": "Ambas Marcam",
    "spreads": "Handicap Asiatico",
}

#: Rotulos internos que este parser sabe produzir.
CANONICAL_MARKETS = tuple(sorted(set(MARKET_MAP.values())))


def _utc_or_raw(value: str) -> str:
    try:
        return utc_key(value)
    except KickoffError:
        return (value or "").strip()


def _line_label(point: object) -> str:
    """Rotulo de linha igual ao usado por `providers.odds_event_to_internal`."""
    return str(point)


def event_key(home_team: str, away_team: str, kickoff: str) -> str:
    """Chave de evento independente de provider (mandante|visitante|kickoff)."""
    return "|".join(
        (
            normalize_team(home_team),
            normalize_team(away_team),
            _utc_or_raw(kickoff),
        )
    )


@dataclass(frozen=True)
class NormalizedQuote:
    """Uma cotacao canonica de um bookmaker num instante."""

    event_id: str
    provider: str
    sport_key: str
    league: str
    home_team: str
    away_team: str
    kickoff: str
    bookmaker: str
    market: str
    selection: str
    price: float
    timestamp: str
    line: Optional[float] = None

    def __post_init__(self) -> None:
        if not isfinite(self.price) or self.price <= 1.0:
            raise ValueError(
                f"odd decimal precisa ser finita e > 1.0, recebi {self.price!r}"
            )
        if not self.timestamp:
            raise ValueError("cotacao de odds exige timestamp")
        if not self.kickoff:
            raise ValueError("cotacao de odds exige kickoff")
        if not self.bookmaker:
            raise ValueError("cotacao de odds exige bookmaker")

    @property
    def quote_key(self) -> tuple[str, str, str, str]:
        """Identidade da linha: (evento, casa, mercado, resultado)."""
        return (self.event_id, self.bookmaker, self.market, self.selection)

    @property
    def pre_kickoff(self) -> bool:
        """True quando a cotacao e estritamente anterior ao kickoff."""
        return _utc_or_raw(self.timestamp) < _utc_or_raw(self.kickoff)

    @property
    def usable(self) -> bool:
        """Cotacao aproveitavel: pre-jogo e com preco valido."""
        return self.pre_kickoff and isfinite(self.price) and self.price > 1.0


# --------------------------------------------------------------------------
# Resolucao de identidade: evento de provider -> event_key do FIXTURE
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FixtureMatch:
    """Resultado da resolucao de um evento de provider contra fixtures.

    status:
      - "MATCHED"   — exatamente um fixture casou; `match_key` e a
                      event_key DO FIXTURE (o fixture e o dono da chave
                      canonica que a API consome);
      - "AMBIGUOUS" — mais de um fixture casou; NAO casa (preferimos
                      ausencia a falso positivo);
      - "UNKNOWN"   — nenhum fixture casou; NAO casa.
    """

    status: str
    match_key: Optional[str] = None
    candidates: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "MATCHED" and bool(self.match_key)


class FixtureMatchIndex:
    """Indice deterministico de fixtures para casar eventos de provider.

    O casamento exige TODAS as condicoes — sem fuzzy, sem tolerancia:

      1. escopo: fixture da MESMA divisao FDUK que o sport key cobre;
      2. home exato apos alias (opcional, por divisao) + normalizacao;
      3. away exato apos alias + normalizacao;
      4. kickoff UTC exato (igualdade de string canonica `...Z`).

    Zero candidatos = UNKNOWN; mais de um = AMBIGUOUS. Em ambos os casos
    o resultado e NO MATCH — a observacao do provider e preservada sob a
    chave canonica do provider (dados nao se perdem), apenas nao e lida
    pela API ate existir fixture correspondente.

    Fixtures SEM horario publicado (`has_kickoff` falso) ficam fora do
    indice: sem instante nao existe casamento temporal possivel.
    """

    def __init__(
        self,
        fixtures: Sequence,
        aliases: Optional[Mapping[tuple[str, str], str]] = None,
    ) -> None:
        self._aliases: dict[tuple[str, str], str] = dict(aliases or {})
        # (divisao, kickoff_utc) -> {(norm_home, norm_away) -> [event_key]}
        self._by_division_kickoff: dict[
            tuple[str, str], dict[tuple[str, str], list[str]]
        ] = {}
        for fx in fixtures:
            kickoff_utc = self._fixture_kickoff_utc(fx)
            if kickoff_utc is None:
                continue  # sem horario publicado: fora do matching
            bucket = self._by_division_kickoff.setdefault(
                (fx.division, kickoff_utc), {}
            )
            names = (normalize_team(fx.home), normalize_team(fx.away))
            bucket.setdefault(names, []).append(fx.event_key)

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _fixture_kickoff_utc(fx) -> Optional[str]:
        """Kickoff UTC do fixture, ou None quando nao ha horario."""
        if not getattr(fx, "has_kickoff", True) or not getattr(fx, "time", ""):
            return None
        from .timeutil import utc_key

        try:
            return utc_key(fx.kickoff, fx.timezone)
        except (KickoffError, AttributeError, TypeError):
            return None

    # ------------------------------------------------------------ resolucao

    def resolve(
        self,
        home: str,
        away: str,
        kickoff_utc: str,
        divisions: Sequence[str],
    ) -> FixtureMatch:
        """Resolve (home, away, kickoff UTC) contra os fixtures indexados.

        `divisions` e o escopo: as divisoes FDUK que o sport key cobre.
        Escopo vazio (sport key sem divisao conhecida) devolve UNKNOWN —
        sem escopo nao ha casamento seguro.

        Contam-se FIXTURES candidatos, nao chaves distintas: mais de um
        fixture no escopo (duplicata de CSV, mesmo confronto em outra
        competicao) e AMBIGUOUS — preferimos ausencia a falso positivo,
        mesmo que as chaves coincidam.
        """
        kickoff = _utc_or_raw((kickoff_utc or "").strip())
        candidates: list[str] = []
        for division in divisions:
            bucket = self._by_division_kickoff.get((division, kickoff))
            if not bucket:
                continue
            home_fduk = self._aliases.get((division, (home or "").strip()), home)
            away_fduk = self._aliases.get((division, (away or "").strip()), away)
            names = (normalize_team(home_fduk or ""), normalize_team(away_fduk or ""))
            candidates.extend(bucket.get(names, ()))

        if len(candidates) == 1:
            return FixtureMatch("MATCHED", match_key=candidates[0], candidates=1)
        if len(candidates) > 1:
            return FixtureMatch("AMBIGUOUS", candidates=len(candidates))
        return FixtureMatch("UNKNOWN", candidates=0)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "provider": self.provider,
            "sport_key": self.sport_key,
            "league": self.league,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "kickoff": self.kickoff,
            "bookmaker": self.bookmaker,
            "market": self.market,
            "selection": self.selection,
            "price": round(self.price, 6),
            "timestamp": self.timestamp,
            "line": self.line,
        }


def _selection_for(
    market: str,
    outcome_name: str,
    point: object,
    home_team: str,
    away_team: str,
) -> Optional[tuple[str, Optional[float]]]:
    """(resultado canonico, linha) ou None quando nao ha mapeamento seguro."""
    name = (outcome_name or "").strip()
    if market == "Resultado Final (1X2)":
        if normalize_team(name) == normalize_team(home_team):
            return "1", None
        if normalize_team(name) == normalize_team(away_team):
            return "2", None
        return "X", None
    if market == "Total de Gols":
        if point is None:
            return None
        side = "Over" if name.lower() == "over" else "Under"
        return f"{side} {_line_label(point)}", float(point)
    if market == "Ambas Marcam":
        return ("BTTS Sim" if name.lower() == "yes" else "BTTS Nao"), None
    if market == "Handicap Asiatico":
        if point is None:
            return None
        if normalize_team(name) == normalize_team(home_team):
            return f"AH Casa {point:+g}", float(point)
        if normalize_team(name) == normalize_team(away_team):
            return f"AH Fora {point:+g}", float(point)
        return None
    return None


def iter_event_quotes(event: Mapping):
    """Parser unico de payload: gera (casa, mercado, resultado, odd, linha).

    Nao depende de kickoff nem de timestamp — e a primitiva reutilizada
    tanto pela normalizacao temporal quanto pela conversao de formato.
    """
    home = str(event.get("home_team") or event.get("home") or "").strip()
    away = str(event.get("away_team") or event.get("away") or "").strip()
    if not home or not away:
        return
    for book in event.get("bookmakers", []) or []:
        bookmaker = str(book.get("title") or book.get("key") or "").strip()
        if not bookmaker:
            continue
        for market in book.get("markets", []) or []:
            market_label = MARKET_MAP.get(str(market.get("key", "")))
            if market_label is None:
                continue
            for outcome in market.get("outcomes", []) or []:
                mapped = _selection_for(
                    market_label,
                    str(outcome.get("name", "")),
                    outcome.get("point"),
                    home,
                    away,
                )
                if mapped is None:
                    continue
                selection, line = mapped
                try:
                    price = float(outcome["price"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not isfinite(price) or price <= 1.0:
                    continue
                yield bookmaker, market_label, selection, price, line


def event_teams(event: Mapping) -> tuple[str, str]:
    home = str(event.get("home_team") or event.get("home") or "").strip()
    away = str(event.get("away_team") or event.get("away") or "").strip()
    return home, away


def event_kickoff(event: Mapping) -> str:
    return str(
        event.get("commence_time") or event.get("kickoff") or event.get("date") or ""
    ).strip()


def normalize_event(
    event: Mapping,
    provider: str,
    fetched_at: str,
    sport_key: str = "",
    league: str = "",
) -> list[NormalizedQuote]:
    """Converte um evento cru de provider em cotacoes canonicas.

    Devolve lista vazia quando o evento nao tem mandante/visitante/kickoff
    utilizaveis — nunca inventa um jogo.
    """
    home, away = event_teams(event)
    kickoff = event_kickoff(event)
    if not home or not away or not kickoff:
        return []

    try:
        kickoff_key = utc_key(kickoff)
    except KickoffError:
        return []

    timestamp = str(event.get("timestamp") or fetched_at or "").strip()
    if not timestamp:
        return []

    event_id = event_key(home, away, kickoff_key)
    league = league or str(event.get("league") or event.get("sport_title") or "")
    quotes: list[NormalizedQuote] = []
    for bookmaker, market_label, selection, price, line in iter_event_quotes(event):
        quotes.append(
            NormalizedQuote(
                event_id=event_id,
                provider=provider,
                sport_key=sport_key,
                league=league,
                home_team=home,
                away_team=away,
                kickoff=kickoff_key,
                bookmaker=bookmaker,
                market=market_label,
                selection=selection,
                price=price,
                timestamp=timestamp,
                line=line,
            )
        )
    return quotes


def grouped_from_event(event: Mapping) -> dict[str, dict[str, dict[str, float]]]:
    """{mercado: {casa: {resultado: odd}}} a partir de um evento cru.

    Conversao de FORMATO, sem semantica temporal: e o que mantem
    `providers.odds_event_to_internal` e este modulo com um unico parser.
    """
    out: dict[str, dict[str, dict[str, float]]] = {}
    for bookmaker, market_label, selection, price, _line in iter_event_quotes(event):
        out.setdefault(market_label, {}).setdefault(bookmaker, {})[selection] = price
    return out


def normalize_events(
    events: Iterable[Mapping],
    provider: str,
    fetched_at: str,
    sport_key: str = "",
) -> list[NormalizedQuote]:
    out: list[NormalizedQuote] = []
    for event in events:
        out.extend(normalize_event(event, provider, fetched_at, sport_key))
    return out


def dedupe_quotes(quotes: Sequence[NormalizedQuote]) -> list[NormalizedQuote]:
    """Uma cotacao por linha: mantem a MAIS RECENTE de cada casa.

    Se o provider repetir a mesma linha em snapshots diferentes, so a
    ultima interessa. O desempate usa (timestamp, provider, bookmaker)
    para ser deterministico.
    """
    latest: dict[tuple[str, str, str, str], NormalizedQuote] = {}
    for quote in quotes:
        if not quote.usable:
            continue
        key = quote.quote_key
        current = latest.get(key)
        if current is None or (
            _utc_or_raw(quote.timestamp),
            quote.provider,
        ) >= (
            _utc_or_raw(current.timestamp),
            current.provider,
        ):
            latest[key] = quote
    return sorted(latest.values(), key=lambda q: (q.event_id, q.market, q.selection, q.bookmaker))


def usable_quotes(quotes: Sequence[NormalizedQuote]) -> list[NormalizedQuote]:
    return [q for q in quotes if q.usable]


def group_by_event(
    quotes: Sequence[NormalizedQuote],
) -> dict[str, list[NormalizedQuote]]:
    out: dict[str, list[NormalizedQuote]] = {}
    for quote in quotes:
        out.setdefault(quote.event_id, []).append(quote)
    return out


def group_by_market(
    quotes: Sequence[NormalizedQuote],
) -> dict[str, dict[str, dict[str, float]]]:
    """{mercado: {casa: {resultado: odd}}} — o formato interno do BETGSN."""
    out: dict[str, dict[str, dict[str, float]]] = {}
    for quote in quotes:
        if not quote.usable:
            continue
        out.setdefault(quote.market, {}).setdefault(quote.bookmaker, {})[
            quote.selection
        ] = quote.price
    return out


def bookmakers(quotes: Sequence[NormalizedQuote]) -> list[str]:
    return sorted({q.bookmaker for q in quotes})


def book_count(
    quotes: Sequence[NormalizedQuote], market: str, selection: str
) -> int:
    return len(
        {
            q.bookmaker
            for q in quotes
            if q.market == market and q.selection == selection and q.usable
        }
    )
