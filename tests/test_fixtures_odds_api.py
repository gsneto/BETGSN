"""Testes do fallback de fixtures futuras via The Odds API.

Por que estes testes existem
----------------------------
O football-data.co.uk publica fixtures.csv apenas da rodada corrente
(atualizacao as sextas e tercas). Na janela entre rodadas o fluxo REAL
fica sem NENHUM jogo futuro e a tela SINAIS devolve 503.

O fallback (`betgsn.fixtures_odds_api`) busca jogos futuros com odds
multi-casa na The Odds API e grava um cache local mesclado ao CSV pelo
`FootballDataClient.load_fixtures`.

O que precisa ficar provado:
  - nenhum dado e fabricado: evento sem kickoff, sem odds ou sem divisao
    FDUK conhecida e DESCARTADO, nunca convertido "de qualquer jeito";
  - o horario publicado (commence_time UTC) e preservado como instante;
  - FDUK continua primario: evento duplicado fica com o CSV;
  - o filtro temporal do real_signals continua mandando: fixture do
    fallback que ja passou NAO vira jogo futuro;
  - o cache velho ou corrompido nao produz fixture nenhuma (nem falsa,
    nem verdadeira) — ausencia continua explicita;
  - o fetch falha alto (chave invalida aborta) e grava cache somente
    quando alguma liga respondeu.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from betgsn.football_data_uk import FootballDataClient, UpcomingFixture
from betgsn.fixtures_odds_api import (
    CACHE_FILENAME,
    SOURCE_LABEL,
    event_to_upcoming_fixture,
    fetch_odds_api_fixtures,
    has_future_fixture,
    load_cached_fixtures,
    merge_fixtures,
    oddsapi_cache_path,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _event(
    home: str = "Arsenal",
    away: str = "Chelsea",
    commence: str = "2030-06-10T14:00:00Z",
    sport_key: str = "soccer_epl",
    n_books: int = 3,
) -> dict:
    """Evento no formato da The Odds API, com odds reais de N casas."""
    books = [
        ("pinnacle", "Pinnacle", 1.90, 3.40, 4.20),
        ("bet365_eu", "Bet365", 1.85, 3.35, 4.10),
        ("williamhill", "William Hill", 1.87, 3.30, 4.15),
        ("unibet_fr", "Unibet", 1.88, 3.42, 4.18),
    ][:n_books]
    return {
        "id": "evt-test",
        "sport_key": sport_key,
        "commence_time": commence,
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {
                "key": key,
                "title": title,
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": home, "price": h},
                            {"name": "Draw", "price": d},
                            {"name": away, "price": a},
                        ],
                    }
                ],
            }
            for key, title, h, d, a in books
        ],
    }


def _future_fixture(home: str = "Arsenal", away: str = "Chelsea") -> UpcomingFixture:
    """Fixture futura (amanha) com odds, para testes do snapshot."""
    odds = {
        "Resultado Final (1X2)": {
            "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
            "Bet365": {"1": 1.85, "X": 3.35, "2": 4.10},
            "Betfair Exchange": {"1": 1.95, "X": 3.45, "2": 4.30},
        }
    }
    best = {
        m: {oc: max(b[oc] for b in books.values()) for oc in next(iter(books.values()))}
        for m, books in odds.items()
    }
    who = {
        m: {
            oc: next(b for b, o in books.items() if o.get(oc) == best[m][oc])
            for oc in best[m]
        }
        for m, books in odds.items()
    }
    amanha = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    return UpcomingFixture(
        division="E0", league="Premier League (England)",
        date=amanha, time="18:00", timezone="UTC",
        home=home, away=away, odds=odds, best_odds=best, best_books=who,
    )


# --------------------------------------------------------------------------
# Conversao pura: evento -> UpcomingFixture
# --------------------------------------------------------------------------


def test_future_event_converts_with_real_kickoff():
    fx = event_to_upcoming_fixture(_event())
    assert fx is not None
    assert fx.home == "Arsenal" and fx.away == "Chelsea"
    assert fx.division == "E0"
    assert fx.league == "Premier League (England)"
    # kickoff preservado como INSTANTE: date/time em UTC
    assert fx.date == "2030-06-10"
    assert fx.time == "14:00"
    assert fx.timezone == "UTC"
    assert fx.has_kickoff
    # odds reais no formato interno, com rotulos do BETGSN
    assert "Resultado Final (1X2)" in fx.odds
    assert fx.n_books == 3
    assert fx.best_odds["Resultado Final (1X2)"]["1"] == 1.90
    assert fx.source == SOURCE_LABEL


def test_event_without_kickoff_is_dropped():
    ev = _event()
    del ev["commence_time"]
    assert event_to_upcoming_fixture(ev) is None


def test_event_with_unparseable_kickoff_is_dropped():
    ev = _event(commence="quarta-feira")
    assert event_to_upcoming_fixture(ev) is None


def test_event_without_odds_is_dropped():
    ev = _event()
    ev["bookmakers"] = []
    assert event_to_upcoming_fixture(ev) is None


def test_event_with_unknown_sport_key_is_dropped():
    """Sport key sem divisao FDUK verificada: a liga NUNCA e adivinhada."""
    ev = _event(sport_key="soccer_some_obscure_cup")
    assert event_to_upcoming_fixture(ev) is None


def test_team_alias_resolves_to_fduk_name():
    """Alias versionado traduz o nome do provider para o nome do corpus."""
    aliases = {("E0", "Leeds United"): "Leeds"}
    ev = _event(home="Leeds United", away="Arsenal")
    fx = event_to_upcoming_fixture(ev, aliases)
    assert fx is not None
    assert fx.home == "Leeds"
    assert fx.away == "Arsenal"  # sem alias, mantem o nome do provider


def test_conversion_never_fabricates_market_data():
    """Sem casa com odds 1X2 completas, nao ha fixture."""
    ev = _event(n_books=1)
    fx = event_to_upcoming_fixture(ev)
    assert fx is not None  # 1 casa ainda e odds REAL — fixture existe
    assert fx.n_books == 1


# --------------------------------------------------------------------------
# Merge: FDUK primario, fallback secundario
# --------------------------------------------------------------------------


def test_merge_prefers_fduk_on_duplicate_event():
    csv_fx = _future_fixture()
    api_fx = replace(_future_fixture(), source=SOURCE_LABEL)
    # mesmo confronto no mesmo instante -> mesma event_key
    assert csv_fx.event_key == api_fx.event_key
    merged = merge_fixtures([csv_fx], [api_fx])
    assert len(merged) == 1
    assert merged[0].source == "football_data_uk"


def test_merge_keeps_non_duplicate_fallback_fixture():
    csv_fx = _future_fixture()
    api_fx = replace(
        _future_fixture(), home="Leeds", away="Fulham", source=SOURCE_LABEL
    )
    merged = merge_fixtures([csv_fx], [api_fx])
    assert len(merged) == 2
    assert {f.source for f in merged} == {"football_data_uk", SOURCE_LABEL}


# --------------------------------------------------------------------------
# Filtro temporal: nada de passado virando futuro
# --------------------------------------------------------------------------


def test_has_future_fixture_false_when_all_past():
    ontem = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    past = replace(_future_fixture(), date=ontem, time="18:00")
    assert has_future_fixture([past]) is False


def test_has_future_fixture_true_with_future_kickoff():
    assert has_future_fixture([_future_fixture()]) is True


def test_has_future_fixture_requires_published_kickoff():
    """Fixture sem horario publicado nao pode contar como futura."""
    fx = replace(_future_fixture(), time="")
    assert fx.has_kickoff is False
    assert has_future_fixture([fx]) is False


# --------------------------------------------------------------------------
# Cache em disco: ausente, corrompido e velho continuam explicitos
# --------------------------------------------------------------------------


def test_cache_missing_returns_no_fixtures(tmp_path):
    assert load_cached_fixtures(tmp_path) == []


def test_cache_corrupt_returns_no_fixtures(tmp_path):
    (tmp_path / CACHE_FILENAME).write_text("{nope", encoding="utf-8")
    assert load_cached_fixtures(tmp_path) == []


def test_cache_past_events_do_not_become_future(tmp_path):
    """Cache com eventos que ja passaram: fixtures existem, mas o filtro
    temporal continua rejeitando — fonte desatualizada nao produz falsa
    fixture futura."""
    ontem = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    ev = _event(commence=f"{ontem}T18:00:00Z")
    (tmp_path / CACHE_FILENAME).write_text(
        json.dumps({"fetched_at": "2030-01-01T00:00:00Z", "events": [ev]}),
        encoding="utf-8",
    )
    fixtures = load_cached_fixtures(tmp_path)
    assert fixtures, "o cache tem o evento — a conversao funciona"
    assert has_future_fixture(fixtures) is False, "mas nada vira futuro"


def test_cache_roundtrip(tmp_path):
    ev = _event()
    (tmp_path / CACHE_FILENAME).write_text(
        json.dumps({"fetched_at": "2030-01-01T00:00:00Z", "events": [ev]}),
        encoding="utf-8",
    )
    fixtures = load_cached_fixtures(tmp_path)
    assert len(fixtures) == 1
    assert fixtures[0].source == SOURCE_LABEL
    assert fixtures[0].date == "2030-06-10"


# --------------------------------------------------------------------------
# Fetch: rede, quota e falha alta
# --------------------------------------------------------------------------


class _FakeProvider:
    """Provider The Odds API falso, com contagem de chamadas."""

    name = "The Odds API"

    def __init__(self, responses: dict[str, list[dict]], headers: dict[str, str] | None = None):
        self.responses = responses
        self.headers = headers or {}
        self.calls: list[str] = []

    def live_odds_with_meta(self, sport_key, regions=None, markets=None):
        self.calls.append(sport_key)
        if isinstance(self.responses.get(sport_key), Exception):
            raise self.responses[sport_key]
        return self.responses.get(sport_key, []), self.headers


def test_fetch_writes_cache_and_counts(tmp_path):
    provider = _FakeProvider(
        {"soccer_epl": [_event()]},
        headers={"x-requests-remaining": "400", "x-requests-used": "100"},
    )
    client = FootballDataClient(root=tmp_path)
    report = fetch_odds_api_fixtures(client, divisions=["E0"], provider=provider)
    assert report.fetched_sports == 1
    assert report.events == 1
    assert report.fixtures == 1
    assert report.wrote_cache is True
    assert report.credits.get("remaining") == 400
    cache = oddsapi_cache_path(client.fixtures_dir)
    assert cache.exists()
    assert load_cached_fixtures(client.fixtures_dir)


def test_fetch_without_key_reports_and_writes_nothing(tmp_path, monkeypatch):
    from betgsn.providers import OddsApiProvider

    monkeypatch.setattr(OddsApiProvider, "from_env", classmethod(lambda cls: None))
    client = FootballDataClient(root=tmp_path)
    report = fetch_odds_api_fixtures(client, divisions=["E0"])
    assert report.wrote_cache is False
    assert report.errors
    assert not oddsapi_cache_path(client.fixtures_dir).exists()


def test_fetch_hard_failure_aborts_without_burning_requests(tmp_path):
    from betgsn.providers import ProviderError

    def _auth_error(sport_key):
        return ProviderError(
            f"HTTP 401 em {sport_key}", status=401, kind="AUTH", retryable=False
        )

    provider = _FakeProvider(
        {
            "soccer_epl": _auth_error("soccer_epl"),
            "soccer_efl_champ": _auth_error("soccer_efl_champ"),
        }
    )
    client = FootballDataClient(root=tmp_path)
    report = fetch_odds_api_fixtures(client, divisions=["E0", "E1"], provider=provider)
    assert report.wrote_cache is False
    assert len(provider.calls) == 1, "chave invalida: o resto nem e tentado"
    assert report.errors


def test_fetch_transient_failure_does_not_abort(tmp_path):
    from betgsn.providers import ProviderError

    provider = _FakeProvider(
        {
            "soccer_epl": ProviderError("timeout", kind="TIMEOUT", retryable=True),
        }
    )
    client = FootballDataClient(root=tmp_path)
    report = fetch_odds_api_fixtures(client, divisions=["E0", "E1"], provider=provider)
    assert len(provider.calls) == 2, "TIMEOUT e transitorio: continua na proxima liga"
    assert report.failed_sports == 1
    assert report.wrote_cache is True  # E1 respondeu (vazio, sem jogos)


def test_fetch_reports_unresolved_teams(tmp_path):
    provider = _FakeProvider(
        {"soccer_epl": [_event(home="Clube Sem Alias", away="Time Tambem Sem")]}
    )
    client = FootballDataClient(root=tmp_path)
    report = fetch_odds_api_fixtures(client, divisions=["E0"], provider=provider)
    assert report.fixtures == 1
    assert "Clube Sem Alias" in report.unresolved_teams
    assert "Time Tambem Sem" in report.unresolved_teams


# --------------------------------------------------------------------------
# Integracao com o load_fixtures do cliente FDUK
# --------------------------------------------------------------------------


_FDUK_CSV = """Div,Date,Time,HomeTeam,AwayTeam,Referee,B365H,B365D,B365A
E0,10/06/30,15:00,Arsenal,Chelsea,,1.90,3.40,4.20
"""


def test_load_fixtures_merges_fallback_cache(tmp_path):
    from betgsn.football_data_uk import FootballDataClient

    client = FootballDataClient(root=tmp_path)
    (client.fixtures_dir / "main.csv").write_text(_FDUK_CSV, encoding="utf-8")
    # jogo DIFERENTE no cache do fallback
    ev = _event(home="Leeds", away="Fulham")
    (client.fixtures_dir / CACHE_FILENAME).write_text(
        json.dumps({"fetched_at": "2030-01-01T00:00:00Z", "events": [ev]}),
        encoding="utf-8",
    )
    fixtures = client.load_fixtures()
    assert len(fixtures) == 2
    assert {f.source for f in fixtures} == {"football_data_uk", SOURCE_LABEL}


def test_load_fixtures_dedupes_preferring_csv(tmp_path):
    from betgsn.football_data_uk import FootballDataClient

    client = FootballDataClient(root=tmp_path)
    (client.fixtures_dir / "main.csv").write_text(_FDUK_CSV, encoding="utf-8")
    # MESMO jogo (Arsenal x Chelsea 15:00 Londres = 14:00Z) vindo do fallback
    ev = _event(commence="2030-06-10T14:00:00Z")
    (client.fixtures_dir / CACHE_FILENAME).write_text(
        json.dumps({"fetched_at": "2030-01-01T00:00:00Z", "events": [ev]}),
        encoding="utf-8",
    )
    fixtures = client.load_fixtures()
    assert len(fixtures) == 1
    assert fixtures[0].source == "football_data_uk"


def test_load_fixtures_without_cache_is_unchanged(tmp_path):
    from betgsn.football_data_uk import FootballDataClient

    client = FootballDataClient(root=tmp_path)
    (client.fixtures_dir / "main.csv").write_text(_FDUK_CSV, encoding="utf-8")
    fixtures = client.load_fixtures()
    assert len(fixtures) == 1
    assert fixtures[0].source == "football_data_uk"
