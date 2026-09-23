"""BETGSN :: fixtures_odds_api — fallback de fixtures futuras (The Odds API).

Por que este modulo existe
--------------------------
O football-data.co.uk publica o `fixtures.csv` apenas da RODADA CORRENTE
(atualizacao as sextas e as tercas). Na janela entre rodadas — tipicamente
de domingo a terca — o arquivo nao contem nenhum jogo futuro, o corte
temporal do `real_signals` rejeita tudo (corretamente) e a tela SINAIS
devolve 503.

Este modulo e o FALLBACK: busca os jogos futuros com odds multi-casa na
The Odds API (a mesma fonte ja usada pela captura de odds) e grava um
cache local (`fixtures/oddsapi.json`) que o
`FootballDataClient.load_fixtures()` mescla com o CSV.

Contrato de dados — identico ao do fluxo real, sem excecoes:
  - somente eventos com kickoff verificavel (`commence_time` UTC);
    sem kickoff o evento e DESCARTADO — nunca se fabrica 00:00;
  - sem odds de nenhuma casa o evento e DESCARTADO — fixture sem odds
    nao produz sinal e nao serve para nada no fluxo REAL;
  - sport key sem divisao FDUK verificada e DESCARTADO — a liga nunca
    e adivinhada;
  - nomes de times sao resolvidos pela tabela versionada
    `team_aliases.json` (com provenance); sem alias, o nome do provider
    permanece e o jogo honestamente cai no contador `skipped_no_rating`;
  - o CSV do football-data.co.uk continua sendo a fonte PRIMARIA:
    evento duplicado (mesma `event_key`) fica com o CSV;
  - o cache guarda o evento CRU — a conversao roda na leitura, entao
    aliases novos passam a valer sem gastar credito de API;
  - o filtro temporal do `real_signals` NAO e alterado: evento do
    fallback que ja passou continua sendo rejeitado.

Custo: ~1 credito por sport key (h2h, regiao eu). Sao 21 chaves
mapeadas — ver `providers.DIVISION_TO_SPORT_KEY`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .football_data_uk import (
    UpcomingFixture,
    _best_odds,
    league_label,
)
from .timeutil import KickoffError, now_utc, utc_key

__all__ = [
    "CACHE_FILENAME",
    "SOURCE_LABEL",
    "OddsApiFixturesReport",
    "event_to_upcoming_fixture",
    "merge_fixtures",
    "has_future_fixture",
    "oddsapi_cache_path",
    "load_cached_fixtures",
    "read_cache_meta",
    "fetch_odds_api_fixtures",
]

#: Nome do cache em disco, dentro do diretorio de fixtures do cliente.
CACHE_FILENAME = "oddsapi.json"

#: Rotulo de proveniencia das fixtures vindas deste fallback.
SOURCE_LABEL = "the_odds_api"

#: Regiao consultada no fallback. `eu` inclui a Pinnacle e casas do
#: continente; e o mesmo escopo usado pela captura periodica.
FALLBACK_REGIONS = "eu"

#: Mercado consultado: so 1X2. E o minimo para o consenso multi-casa do
#: `signals` (MIN_BOOKS); mercados de linha ficam para o CSV quando
#: disponivel.
FALLBACK_MARKETS = "h2h"


@dataclass
class OddsApiFixturesReport:
    """Relatorio de uma execucao do fallback (auditoria)."""

    fetched_at: str = ""
    fetched_sports: int = 0
    failed_sports: int = 0
    events: int = 0
    fixtures: int = 0
    dropped_no_kickoff: int = 0
    dropped_no_odds: int = 0
    #: times que ficaram com o nome do provider (sem alias) — vao para o
    #: contador honesto skipped_no_rating do snapshot
    unresolved_teams: list[str] = field(default_factory=list)
    credits: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    wrote_cache: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "fetched_at": self.fetched_at,
            "fetched_sports": self.fetched_sports,
            "failed_sports": self.failed_sports,
            "events": self.events,
            "fixtures": self.fixtures,
            "dropped_no_kickoff": self.dropped_no_kickoff,
            "dropped_no_odds": self.dropped_no_odds,
            "unresolved_teams": self.unresolved_teams,
            "credits": self.credits,
            "errors": self.errors[:20],
            "wrote_cache": self.wrote_cache,
        }


# --------------------------------------------------------------------------
# Conversao pura: evento cru -> UpcomingFixture
# --------------------------------------------------------------------------


def event_to_upcoming_fixture(
    event: Mapping[str, Any],
    aliases: Mapping[tuple[str, str], str] | None = None,
) -> UpcomingFixture | None:
    """Converte um evento da The Odds API em `UpcomingFixture`.

    Funcao pura: sem rede, sem env, sem relogio. Devolve None em TODO
    caso em que faltaria dado — kickoff ausente/ilegivel, sem odds, ou
    sport key sem divisao FDUK verificada. Nunca inventa campo.
    """
    from .odds_normalize import grouped_from_event
    from .providers import SPORT_KEY_TO_DIVISIONS

    sport_key = str(event.get("sport_key") or "").strip()
    divisions = SPORT_KEY_TO_DIVISIONS.get(sport_key)
    if not divisions or len(divisions) != 1:
        # sem divisao FDUK unica nao existe liga confiavel: descarta
        return None
    division = divisions[0]

    home = str(event.get("home_team") or "").strip()
    away = str(event.get("away_team") or "").strip()
    commence = str(event.get("commence_time") or "").strip()
    if not home or not away or not commence:
        return None
    try:
        kickoff = utc_key(commence)
    except (KickoffError, ValueError):
        return None

    odds = grouped_from_event(event)
    if not odds:
        return None

    alias_map = dict(aliases or {})
    home = alias_map.get((division, home), home)
    away = alias_map.get((division, away), away)

    best: dict[str, dict[str, float]] = {}
    who: dict[str, dict[str, str]] = {}
    for market, books in odds.items():
        b, w = _best_odds(books)
        best[market] = b
        who[market] = w

    return UpcomingFixture(
        division=division,
        league=league_label(division),
        date=kickoff[:10],
        time=kickoff[11:16],
        timezone="UTC",
        home=home,
        away=away,
        odds=odds,
        best_odds=best,
        best_books=who,
        source=SOURCE_LABEL,
    )


def merge_fixtures(
    primary: Sequence[UpcomingFixture],
    secondary: Sequence[UpcomingFixture],
) -> list[UpcomingFixture]:
    """Mescla fixtures de duas fontes; a PRIMARIA vence duplicatas.

    Duplicata = mesma `event_key` (confronto + instante UTC). Fixtures
    sem horario publicado tem event_key "" e nunca colidem.
    """
    seen = {f.event_key for f in primary if f.event_key}
    out = list(primary)
    for fx in secondary:
        if fx.event_key and fx.event_key in seen:
            continue
        seen.add(fx.event_key)
        out.append(fx)
    return out


def has_future_fixture(
    fixtures: Sequence[UpcomingFixture], now: str | None = None
) -> bool:
    """True quando existe ao menos uma fixture com kickoff futuro.

    Mesma regra do corte temporal do `real_signals`: exige horario
    publicado (`has_kickoff`) e instante ESTITAMENTE posterior ao agora.
    Fixture sem horario nunca conta como futura.
    """
    moment = now or now_utc()
    for fx in fixtures:
        if not fx.has_kickoff:
            continue
        try:
            if utc_key(fx.kickoff, fx.timezone) > moment:
                return True
        except KickoffError:
            continue
    return False


# --------------------------------------------------------------------------
# Cache em disco
# --------------------------------------------------------------------------


def oddsapi_cache_path(fixtures_dir: Path | str) -> Path:
    return Path(fixtures_dir) / CACHE_FILENAME


def _read_cache_payload(fixtures_dir: Path | str) -> dict[str, Any] | None:
    path = oddsapi_cache_path(fixtures_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_cached_fixtures(fixtures_dir: Path | str) -> list[UpcomingFixture]:
    """Fixtures do cache local do fallback. Vazio se ausente/corrompido.

    A conversao roda AQUI, na leitura: o cache guarda o evento cru para
    que aliases novos valham sem nova chamada de API. Tabela de aliases
    invalida levanta `TeamAliasError` — erro de contrato de dados e
    explicito, nao silenciado.
    """
    from .team_aliases import load_team_aliases

    payload = _read_cache_payload(fixtures_dir)
    if payload is None:
        return []
    events = payload.get("events")
    if not isinstance(events, list):
        return []
    aliases = load_team_aliases()
    out: list[UpcomingFixture] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        fx = event_to_upcoming_fixture(event, aliases)
        if fx is not None:
            out.append(fx)
    return out


def read_cache_meta(fixtures_dir: Path | str) -> dict[str, Any] | None:
    """Metadados do cache (inventario), sem converter eventos."""
    payload = _read_cache_payload(fixtures_dir)
    if payload is None:
        return None
    events = payload.get("events")
    return {
        "available": True,
        "source": "The Odds API",
        "events": len(events) if isinstance(events, list) else 0,
        "fetched_at": str(payload.get("fetched_at") or ""),
    }


# --------------------------------------------------------------------------
# Fetch: busca na The Odds API e grava o cache
# --------------------------------------------------------------------------


def fetch_odds_api_fixtures(
    client: Any,
    divisions: Sequence[str] | None = None,
    provider: Any | None = None,
) -> OddsApiFixturesReport:
    """Busca fixtures futuras com odds e grava o cache local.

    `client` e um `FootballDataClient` (so `fixtures_dir` e `root` sao
    usados). `divisions` restringe as ligas (default: todas as divisoes
    com sport key verificada). `provider` permite injetar um adapter
    falso em teste.

    Falha alto e explicito: sem chave, o relatorio carrega o erro e
    NADA e gravado. Chave invalida (401/403/402) aborta as demais
    ligas — nao ha por que queimar as outras chamadas. O cache so e
    gravado quando ao menos uma liga respondeu.
    """
    from .providers import (
        FAILURE_AUTH,
        FAILURE_FORBIDDEN,
        FAILURE_NO_CREDITS,
        OddsApiProvider,
        ProviderError,
        parse_credit_headers,
        sport_keys_for_divisions,
    )
    from .team_aliases import load_team_aliases

    report = OddsApiFixturesReport(fetched_at=now_utc())

    if provider is None:
        provider = OddsApiProvider.from_env()
    if provider is None:
        report.errors.append(
            "The Odds API sem chave (BETGSN_ODDS_API_KEY); fallback indisponivel"
        )
        return report

    if divisions:
        sport_keys, _unmapped = sport_keys_for_divisions(divisions)
    else:
        from .providers import DIVISION_TO_SPORT_KEY

        sport_keys = list(DIVISION_TO_SPORT_KEY.values())

    events: list[dict] = []
    credits: dict[str, int] = {}
    for sport_key in sport_keys:
        try:
            body, headers = provider.live_odds_with_meta(
                sport_key,
                regions=FALLBACK_REGIONS,
                markets=FALLBACK_MARKETS,
            )
        except ProviderError as exc:
            report.failed_sports += 1
            report.errors.append(f"{sport_key}: {exc.kind} — {exc}")
            if exc.kind in (FAILURE_AUTH, FAILURE_FORBIDDEN, FAILURE_NO_CREDITS):
                break  # chave/quota: as demais ligas vao falhar igual
            continue
        report.fetched_sports += 1
        if isinstance(body, list):
            events.extend(e for e in body if isinstance(e, dict))
        parsed = parse_credit_headers(headers)
        if parsed:
            credits = parsed

    report.events = len(events)
    report.credits = credits

    if not report.fetched_sports:
        return report  # nenhuma liga respondeu: nao grava cache vazio

    aliases = load_team_aliases()
    fixtures: list[UpcomingFixture] = []
    unresolved: list[str] = []
    for event in events:
        fx = event_to_upcoming_fixture(event, aliases)
        if fx is None:
            if not str(event.get("commence_time") or "").strip() or not _valid_kickoff(
                event
            ):
                report.dropped_no_kickoff += 1
            else:
                report.dropped_no_odds += 1
            continue
        for side, provider_name in (
            ("home", str(event.get("home_team") or "").strip()),
            ("away", str(event.get("away_team") or "").strip()),
        ):
            division = fx.division
            if provider_name and (division, provider_name) not in aliases:
                if provider_name not in unresolved:
                    unresolved.append(provider_name)
        fixtures.append(fx)

    report.fixtures = len(fixtures)
    report.unresolved_teams = unresolved

    payload = {
        "kind": "oddsapi_fixtures",
        "fetched_at": report.fetched_at,
        "regions": FALLBACK_REGIONS,
        "markets": FALLBACK_MARKETS,
        "n_events": len(events),
        "events": events,
        "credits": credits,
    }
    cache_path = oddsapi_cache_path(client.fixtures_dir)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        report.errors.append(f"falha ao gravar cache {cache_path.name}: {exc}")
        return report
    report.wrote_cache = True

    # trilha de auditoria no manifesto (mesma do import FDUK)
    from .football_data_uk import _append_manifest

    try:
        _append_manifest(client.root / "manifest.json", "fixtures_oddsapi", report)
    except OSError:
        pass  # manifesto e auditoria: falha nao derruba o import
    return report


def _valid_kickoff(event: Mapping[str, Any]) -> bool:
    from .timeutil import is_valid_kickoff

    return is_valid_kickoff(str(event.get("commence_time") or "").strip())
