"""Testes de `betgsn.backtest_sources` — importacao de dados historicos.

Nenhum teste toca a rede: os provedores sao simulados. Os payloads abaixo
sao FIXTURES DE TESTE, construidas para exercitar o parser — nao sao
capturas de mercado nem dados do produto.

O bloco mais importante e o de seguranca temporal: um snapshot de odds
posterior ao kickoff nunca pode ser usado, e um snapshot velho demais
tambem nao.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from betgsn.backtest_data import (
    HistoricalCorpus,
    NaiveSyntheticOddsSource,
    RealHistoricalOddsSource,
)
from betgsn.backtest_engine import BacktestConfig, run_backtest, simulate_bankroll
from betgsn.backtest_sources import (
    HistoricalFixtureImporter,
    HistoricalOddsImporter,
    OddsHistoryCache,
    OddsSnapshot,
    apply_statistics,
    corpus_fingerprint,
    fixture_to_match,
    load_imported_matches,
    normalize_team,
    odds_from_snapshot,
)
from betgsn.model import HistoricalMatch
from betgsn.providers import ProviderError

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _match(day: str, home: str, away: str, hg: int, ag: int, **kw) -> HistoricalMatch:
    return HistoricalMatch(
        home=home, away=away, home_goals=hg, away_goals=ag,
        home_xg=float(hg), away_xg=float(ag),
        home_corners=5, away_corners=4, home_cards=2, away_cards=2,
        home_shots=12, away_shots=10,
        kickoff=kw.get("kickoff", f"{day} 16:00"),
        timezone=kw.get("timezone", ""),
        league=kw.get("league", "Liga Teste"),
        season=kw.get("season", "2025"),
    )


def _small_corpus() -> list[HistoricalMatch]:
    """4 times, 24 partidas semanais — historico suficiente para o fit."""
    teams = ["Alfa", "Bravo", "Charlie", "Delta"]
    out: list[HistoricalMatch] = []
    day = 1
    for rnd in range(6):
        for i, home in enumerate(teams):
            away = teams[(i + rnd + 1) % 4]
            if home == away:
                continue
            out.append(
                _match(f"2025-03-{day:02d}", home, away,
                       2 + (rnd % 3), rnd % 2, weight=1.0 + 0.02 * rnd)
            )
            day += 1
    return out


def _odds_event(home: str, away: str, odd_home: float = 3.40,
                odd_draw: float = 3.40, odd_away: float = 3.40) -> dict:
    """Evento no formato da The Odds API (fixture de teste, nao mercado real)."""
    return {
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {
                "key": "pinnacle", "title": "Pinnacle",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": home, "price": odd_home},
                    {"name": away, "price": odd_away},
                    {"name": "Draw", "price": odd_draw},
                ]}],
            },
            {
                "key": "bet365", "title": "Bet365",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": home, "price": odd_home - 0.10},
                    {"name": away, "price": odd_away - 0.10},
                    {"name": "Draw", "price": odd_draw - 0.10},
                ]}],
            },
            {
                "key": "betano", "title": "Betano",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": home, "price": odd_home - 0.15},
                    {"name": away, "price": odd_away - 0.15},
                    {"name": "Draw", "price": odd_draw - 0.15},
                ]}],
            },
        ],
    }


class FakeOddsProvider:
    """Simula `OddsApiProvider.historical_odds` sem rede."""

    def __init__(self, events_by_date: dict[str, list[dict]],
                 lag_minutes: int = 0) -> None:
        self.events_by_date = events_by_date
        self.lag_minutes = lag_minutes
        self.calls: list[tuple[str, str]] = []

    def historical_odds(self, sport_key: str, date_iso: str) -> dict:
        self.calls.append((sport_key, date_iso))
        events = self.events_by_date.get(date_iso)
        if events is None:
            return {"timestamp": date_iso, "data": []}
        # simula latencia: o snapshot e um pouco anterior ao horario pedido
        from datetime import timedelta

        from betgsn.timeutil import UTC_FORMAT, parse_kickoff

        ts = parse_kickoff(date_iso) - timedelta(minutes=self.lag_minutes)
        return {"timestamp": ts.strftime(UTC_FORMAT), "data": events}


class FakeFootballProvider:
    """Simula `ApiFootballProvider` sem rede."""

    def __init__(self, fixtures: list[dict], stats: dict[int, list[dict]] | None = None):
        self._fixtures = fixtures
        self._stats = stats or {}

    def fixtures_by_season(self, league: int, season: int) -> list[dict]:
        return self._fixtures

    def fixture_statistics(self, fixture_id: int) -> list[dict]:
        return self._stats.get(fixture_id, [])


# --------------------------------------------------------------------------
# Normalizacao de nomes
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("São Paulo", "Sao Paulo"),
        ("Sao Paulo FC", "São Paulo"),
        ("CR Flamengo", "Flamengo"),
        ("Botafogo FR", "Botafogo"),
        ("Atletico-MG", "Atletico MG"),
        ("  Grêmio  ", "Gremio"),
    ],
)
def test_normalize_team_matches_variants(a, b):
    assert normalize_team(a) == normalize_team(b)


def test_normalize_team_keeps_distinct_teams_distinct():
    assert normalize_team("Flamengo") != normalize_team("Fluminense")
    assert normalize_team("Sao Paulo") != normalize_team("Santos")


# --------------------------------------------------------------------------
# Snapshot e cache
# --------------------------------------------------------------------------


def test_snapshot_json_round_trip():
    snap = OddsSnapshot(
        sport_key="soccer_epl",
        requested_date="2025-05-05T16:00:00Z",
        timestamp="2025-05-05T15:00:00Z",
        events=({"home_team": "A", "away_team": "B"},),
    )
    restored = OddsSnapshot.from_json(snap.to_json())
    assert restored == snap
    assert restored.utc_key == "2025-05-05T15:00:00Z"


def test_cache_save_load_and_find_before(tmp_path):
    cache = OddsHistoryCache(tmp_path)
    for ts in ("2025-05-05T10:00:00Z", "2025-05-05T14:00:00Z", "2025-05-05T18:00:00Z"):
        cache.save(OddsSnapshot("soccer_epl", ts, ts, ({"home_team": "A"},)))

    assert len(cache.load("soccer_epl")) == 3
    # antes das 18h -> o de 14h
    found = cache.find_before("soccer_epl", "2025-05-05T17:00:00Z", max_age_hours=24)
    assert found is not None and found.utc_key == "2025-05-05T14:00:00Z"
    # antes das 15h -> o de 14h
    found = cache.find_before("soccer_epl", "2025-05-05T15:00:00Z", max_age_hours=24)
    assert found is not None and found.utc_key == "2025-05-05T14:00:00Z"


def test_cache_never_returns_snapshot_after_cutoff(tmp_path):
    """GARANTIA CENTRAL: snapshot posterior ao kickoff e inutil."""
    cache = OddsHistoryCache(tmp_path)
    cache.save(OddsSnapshot("soccer_epl", "t", "2025-05-05T20:00:00Z", ()))

    assert cache.find_before("soccer_epl", "2025-05-05T19:00:00Z") is None
    # no instante exato tambem nao: o snapshot e simultaneo, nao anterior
    assert cache.find_before("soccer_epl", "2025-05-05T20:00:00Z") is None
    # um minuto depois ja vale
    assert cache.find_before("soccer_epl", "2025-05-05T20:01:00Z") is not None


def test_cache_rejects_stale_snapshot(tmp_path):
    cache = OddsHistoryCache(tmp_path)
    cache.save(OddsSnapshot("soccer_epl", "t", "2025-05-01T10:00:00Z", ()))
    # kickoff 5 dias depois: odd velha demais para representar o mercado
    assert cache.find_before("soccer_epl", "2025-05-06T10:00:00Z",
                             max_age_hours=24) is None
    assert cache.find_before("soccer_epl", "2025-05-06T10:00:00Z",
                             max_age_hours=24 * 7) is not None


def test_cache_is_per_sport(tmp_path):
    cache = OddsHistoryCache(tmp_path)
    cache.save(OddsSnapshot("soccer_epl", "t", "2025-05-05T10:00:00Z", ()))
    assert cache.find_before("soccer_brazil_campeonato", "2025-05-06T10:00:00Z") is None


def test_cache_stats(tmp_path):
    cache = OddsHistoryCache(tmp_path)
    cache.save(OddsSnapshot("soccer_epl", "t", "2025-05-05T10:00:00Z", ()))
    cache.save(OddsSnapshot("soccer_epl", "t", "2025-05-06T10:00:00Z", ()))
    stats = cache.stats()
    assert stats["soccer_epl"]["snapshots"] == 2
    assert stats["soccer_epl"]["first"] == "2025-05-05T10:00:00Z"
    assert stats["soccer_epl"]["last"] == "2025-05-06T10:00:00Z"


# --------------------------------------------------------------------------
# Extracao de odds do snapshot
# --------------------------------------------------------------------------


def test_odds_from_snapshot_matches_normalized_names():
    snap = OddsSnapshot(
        "soccer_brazil_campeonato", "t", "2025-05-05T10:00:00Z",
        (_odds_event("São Paulo", "Flamengo"),),
    )
    match = _match("2025-05-05", "Sao Paulo", "Flamengo", 1, 0)
    odds = odds_from_snapshot(snap, match)
    assert "Resultado Final (1X2)" in odds
    assert odds["Resultado Final (1X2)"]["Pinnacle"]["1"] == pytest.approx(3.40)
    assert set(odds["Resultado Final (1X2)"]) == {"Pinnacle", "Bet365", "Betano"}


def test_odds_from_snapshot_returns_empty_for_unknown_match():
    snap = OddsSnapshot("soccer_epl", "t", "2025-05-05T10:00:00Z",
                        (_odds_event("A", "B"),))
    match = _match("2025-05-05", "C", "D", 1, 0)
    assert odds_from_snapshot(snap, match) == {}


# --------------------------------------------------------------------------
# Importador de odds
# --------------------------------------------------------------------------


def test_importer_stores_snapshots(tmp_path):
    events = {"2025-05-05T12:00:00Z": [_odds_event("Alfa", "Bravo")]}
    provider = FakeOddsProvider(events, lag_minutes=30)
    cache = OddsHistoryCache(tmp_path)
    importer = HistoricalOddsImporter(provider, cache)

    report = importer.import_window(
        "soccer_brazil_campeonato",
        "2025-05-05T12:00:00Z",
        "2025-05-05T12:00:00Z",
        step_hours=6,
    )
    assert report.requested == 1
    assert report.imported == 1
    assert report.failed == 0
    snapshots = cache.load("soccer_brazil_campeonato")
    assert len(snapshots) == 1
    # o timestamp gravado e o da API, nao o horario pedido
    assert snapshots[0].timestamp == "2025-05-05T11:30:00Z"


def test_importer_records_provider_errors(tmp_path):
    class FailingProvider:
        def historical_odds(self, sport_key, date_iso):
            raise ProviderError("quota excedida")

    importer = HistoricalOddsImporter(FailingProvider(), OddsHistoryCache(tmp_path))
    report = importer.import_window(
        "soccer_epl", "2025-05-05T00:00:00Z", "2025-05-05T06:00:00Z", step_hours=6
    )
    assert report.failed > 0
    assert any("quota" in e for e in report.errors)


def test_importer_skips_payload_without_timestamp(tmp_path):
    """Sem timestamp nao existe point-in-time: o snapshot e recusado."""
    class NoTimestampProvider:
        def historical_odds(self, sport_key, date_iso):
            return {"data": [_odds_event("A", "B")]}

    cache = OddsHistoryCache(tmp_path)
    importer = HistoricalOddsImporter(NoTimestampProvider(), cache)
    report = importer.import_window(
        "soccer_epl", "2025-05-05T00:00:00Z", "2025-05-05T00:00:00Z", step_hours=6
    )
    assert report.skipped == 1
    assert report.imported == 0
    assert cache.load("soccer_epl") == []


def test_importer_respects_max_requests(tmp_path):
    provider = FakeOddsProvider({})
    importer = HistoricalOddsImporter(provider, OddsHistoryCache(tmp_path))
    importer.import_window(
        "soccer_epl", "2025-01-01T00:00:00Z", "2025-12-31T00:00:00Z",
        step_hours=24, max_requests=5,
    )
    assert len(provider.calls) == 5


# --------------------------------------------------------------------------
# Fonte de odds reais end-to-end
# --------------------------------------------------------------------------


def test_real_source_uses_snapshot_before_kickoff(tmp_path):
    corpus = _small_corpus()
    target = corpus[-1]
    day = target.kickoff[:10]
    events = {f"{day}T04:00:00Z": [_odds_event(target.home, target.away, 4.0, 3.5, 2.0)]}
    provider = FakeOddsProvider(events)
    cache = OddsHistoryCache(tmp_path)
    HistoricalOddsImporter(provider, cache).import_window(
        "soccer_brazil_campeonato",
        f"{day}T04:00:00Z", f"{day}T04:00:00Z", step_hours=6,
    )

    src = RealHistoricalOddsSource(cache=cache, sport_key="soccer_brazil_campeonato",
                                   max_age_hours=24)
    odds = src.odds_for(target, {})
    assert odds
    # o instante registrado e o do snapshot, nunca o do kickoff
    assert src.last_as_of == f"{day}T04:00:00Z"
    assert src.last_as_of < HistoricalCorpus(corpus).utc_key_of(target)


def test_backtest_with_real_odds_source_runs_end_to_end(tmp_path):
    """Prova o item 1: backtest com odds historicas reais importadas."""
    corpus_matches = _small_corpus()
    cache = OddsHistoryCache(tmp_path)
    # um snapshot por dia as 00:00Z, sempre antes do kickoff (16:00 local)
    events: dict[str, list[dict]] = {}
    for m in corpus_matches:
        events[f"{m.kickoff[:10]}T00:00:00Z"] = [
            _odds_event(m.home, m.away, 4.2, 3.6, 1.9)
        ]
    provider = FakeOddsProvider(events)
    importer = HistoricalOddsImporter(provider, cache)
    importer.import_window(
        "soccer_brazil_campeonato",
        f"{corpus_matches[0].kickoff[:10]}T00:00:00Z",
        f"{corpus_matches[-1].kickoff[:10]}T00:00:00Z",
        step_hours=24,
    )

    config = BacktestConfig(
        min_history=4, min_ev=0.02, market_keys=("1x2",),
        odds_source="real_historical", odds_sport_key="soccer_brazil_campeonato",
        odds_max_age_hours=24, odds_cache_root=str(tmp_path),
    )
    run = run_backtest(HistoricalCorpus(corpus_matches), config)
    assert run.n_matches_evaluated > 0
    assert run.signals, "o backtest com odds reais nao gerou sinais"

    for s in run.signals:
        assert s.signal.odds_source == "real_historical"
        # invariante temporal: odds nunca posteriores ao kickoff
        assert s.signal.odds_as_of < s.signal.kickoff_utc
        # as odds usadas vieram do snapshot, nao do gerador sintetico
        assert s.signal.best_odd > 1.0


def test_backtest_with_real_odds_fails_without_cache(tmp_path):
    """Sem cache, o backtest falha em vez de inventar odds."""
    corpus_matches = _small_corpus()
    config = BacktestConfig(
        min_history=4, market_keys=("1x2",), odds_source="real_historical",
        odds_sport_key="soccer_brazil_campeonato",
    )
    src = RealHistoricalOddsSource(cache=OddsHistoryCache(tmp_path))
    with pytest.raises(ProviderError):
        src.odds_for(corpus_matches[-1], {})


# --------------------------------------------------------------------------
# Importador de partidas (API-Football)
# --------------------------------------------------------------------------


def _fixture_payload(fixture_id: int, date: str, home: str, away: str,
                     hg: int | None, ag: int | None, status: str = "FT") -> dict:
    return {
        "fixture": {"id": fixture_id, "date": date, "status": {"short": status}},
        "league": {"id": 71, "name": "Serie A", "season": 2024},
        "teams": {"home": {"id": 1, "name": home}, "away": {"id": 2, "name": away}},
        "goals": {"home": hg, "away": ag},
    }


def test_fixture_to_match_parses_offset_and_fields():
    payload = _fixture_payload(
        10, "2024-04-14T15:00:00-03:00", "Palmeiras", "Flamengo", 2, 1
    )
    match = fixture_to_match(payload)
    assert match is not None
    assert match.home == "Palmeiras" and match.away == "Flamengo"
    assert match.home_goals == 2 and match.away_goals == 1
    assert match.league == "Serie A" and match.season == "2024"
    # o offset do provedor fica na string e e normalizado na leitura
    from betgsn.timeutil import utc_key

    assert utc_key(match.kickoff, match.timezone) == "2024-04-14T18:00:00Z"


@pytest.mark.parametrize("status", ["NS", "1H", "HT", "2H", "PST", "CANC", ""])
def test_fixture_to_match_skips_unfinished(status):
    payload = _fixture_payload(10, "2024-04-14T15:00:00-03:00", "A", "B", None, None, status)
    assert fixture_to_match(payload) is None


def test_fixture_to_match_skips_missing_score():
    payload = _fixture_payload(10, "2024-04-14T15:00:00-03:00", "A", "B", None, 1, "FT")
    assert fixture_to_match(payload) is None


def test_fixture_to_match_skips_invalid_date():
    payload = _fixture_payload(10, "data ruim", "A", "B", 1, 0, "FT")
    assert fixture_to_match(payload) is None


def test_apply_statistics_reads_corners_cards_and_xg():
    match = _match("2024-04-14", "Palmeiras", "Flamengo", 2, 1)
    payload = [
        {"team": {"id": 99, "name": "Palmeiras"}, "statistics": [
            {"type": "Corner Kicks", "value": 7},
            {"type": "Yellow Cards", "value": 3},
            {"type": "Red Cards", "value": 1},
            {"type": "Total Shots", "value": 15},
            {"type": "expected_goals", "value": "1.85"},
        ]},
        {"team": {"id": 2, "name": "Flamengo"}, "statistics": [
            {"type": "Corner Kicks", "value": 4},
            {"type": "Yellow Cards", "value": 2},
            {"type": "Red Cards", "value": None},
            {"type": "Total Shots", "value": 9},
            {"type": "expected_goals", "value": "0.94"},
        ]},
    ]
    enriched = apply_statistics(match, payload)
    assert enriched.home_corners == 7 and enriched.away_corners == 4
    assert enriched.home_cards == 4          # 3 amarelos + 1 vermelho
    assert enriched.away_cards == 2
    assert enriched.home_xg == pytest.approx(1.85)
    assert enriched.away_xg == pytest.approx(0.94)
    assert enriched.home_shots == 15
    # o placar e o kickoff sao preservados
    assert enriched.home_goals == 2 and enriched.kickoff == match.kickoff


def test_apply_statistics_handles_missing_values():
    """Dado ausente fica None — nunca vira zero silencioso."""
    match = _match("2024-04-14", "A", "B", 1, 0)
    payload = [
        {"team": {"id": 1, "name": "A"}, "statistics": [{"type": "Corner Kicks", "value": None}]},
        {"team": {"id": 2, "name": "B"}, "statistics": []},
    ]
    enriched = apply_statistics(match, payload)
    assert enriched.home_corners is None
    assert enriched.away_corners is None
    assert enriched.home_cards is None


def test_apply_statistics_swaps_when_order_reversed():
    """Identidade, não ordenação numérica do ID, determina o mando."""
    match = _match("2024-04-14", "A", "B", 1, 0)
    payload = [
        {"team": {"id": 2, "name": "B"}, "statistics": [{"type": "Corner Kicks", "value": 9}]},
        {"team": {"id": 99, "name": "A"}, "statistics": [{"type": "Corner Kicks", "value": 2}]},
    ]
    enriched = apply_statistics(match, payload)
    assert enriched.home_corners == 2
    assert enriched.away_corners == 9


def test_fixture_importer_writes_and_reloads(tmp_path):
    fixtures = [
        _fixture_payload(1, "2024-04-14T15:00:00-03:00", "Palmeiras", "Flamengo", 2, 1),
        _fixture_payload(2, "2024-04-21T16:00:00-03:00", "Flamengo", "Palmeiras", 0, 0),
        _fixture_payload(3, "2024-05-01T16:00:00-03:00", "Palmeiras", "Corinthians", None, None, "NS"),
    ]
    stats = {1: [
        {"team": {"id": 1, "name": "Palmeiras"}, "statistics": [{"type": "Corner Kicks", "value": 6}]},
        {"team": {"id": 2, "name": "Flamengo"}, "statistics": [{"type": "Corner Kicks", "value": 3}]},
    ]}
    importer = HistoricalFixtureImporter(
        FakeFootballProvider(fixtures, stats), root=tmp_path
    )
    report = importer.import_season(71, 2024, with_statistics=True)
    assert report.imported == 2       # o jogo nao finalizado fica de fora
    assert report.skipped == 1

    loaded = importer.load_season(71, 2024)
    assert len(loaded) == 2
    assert loaded[0].home_corners == 6
    assert loaded[1].home_corners is None   # sem estatisticas para o fixture 2

    assert importer.available_seasons() == [
        {"league": 71, "season": 2024, "matches": 2, "file": "league71_season2024.jsonl"}
    ]
    assert len(load_imported_matches(tmp_path)) == 2


def test_imported_corpus_runs_backtest(tmp_path):
    """Prova o item 2: corpus de temporada real alimenta o backtest."""
    fixtures = []
    teams = ["Alfa", "Bravo", "Charlie", "Delta"]
    fixture_id = 1
    day = 1
    for rnd in range(6):
        for i, home in enumerate(teams):
            away = teams[(i + rnd + 1) % 4]
            if home == away:
                continue
            fixtures.append(_fixture_payload(
                fixture_id, f"2024-04-{day:02d}T15:00:00-03:00", home, away,
                2 + (rnd % 3), rnd % 2,
            ))
            fixture_id += 1
            day += 1

    importer = HistoricalFixtureImporter(
        FakeFootballProvider(fixtures), root=tmp_path
    )
    report = importer.import_season(71, 2024)
    assert report.imported == len(fixtures)

    corpus = HistoricalCorpus(load_imported_matches(tmp_path))
    stats = corpus.stats()
    assert stats.n_matches == len(fixtures)
    assert stats.competitions == ("Serie A",)
    assert stats.seasons == ("2024",)

    config = BacktestConfig(min_history=4, min_ev=0.02, market_keys=("1x2",))
    run = run_backtest(corpus, config)
    assert run.n_matches_evaluated > 0
    assert run.signals
    sim = simulate_bankroll(run.signals, config)
    assert sim.n_bets > 0
    # kickoffs normalizados para UTC (15:00-03:00 -> 18:00Z)
    assert all(s.signal.kickoff_utc.endswith("Z") for s in run.signals)


def test_imported_corpus_mixes_timezones_safely(tmp_path):
    """Fontes com fusos diferentes convivem sem quebrar a ordem."""
    fixtures = [
        _fixture_payload(1, "2024-04-14T15:00:00-03:00", "A", "B", 1, 0),
        _fixture_payload(2, "2024-04-14T20:00:00+01:00", "B", "C", 0, 0),   # 19:00Z
        _fixture_payload(3, "2024-04-15T09:00:00+09:00", "C", "A", 2, 2),   # 00:00Z
    ]
    importer = HistoricalFixtureImporter(FakeFootballProvider(fixtures), root=tmp_path)
    importer.import_season(71, 2024)
    corpus = HistoricalCorpus(load_imported_matches(tmp_path))
    keys = list(corpus.all_kickoffs())
    assert keys == sorted(keys)
    assert keys[0] == "2024-04-14T18:00:00Z"
    assert keys[-1] == "2024-04-15T00:00:00Z"


# --------------------------------------------------------------------------
# Fingerprint do corpus
# --------------------------------------------------------------------------


def test_corpus_fingerprint_is_stable_and_sensitive():
    a = _small_corpus()
    b = _small_corpus()
    assert corpus_fingerprint(a) == corpus_fingerprint(b)
    c = list(a)
    c[0] = _match("2025-03-01", c[0].home, c[0].away, 9, 9)
    assert corpus_fingerprint(c) != corpus_fingerprint(a)


def test_corpus_fingerprint_ignores_order():
    a = _small_corpus()
    assert corpus_fingerprint(a) == corpus_fingerprint(list(reversed(a)))


# --------------------------------------------------------------------------
# Comparacao: mercado sintetico vs mercado real
# --------------------------------------------------------------------------


def test_naive_and_real_sources_produce_different_odds(tmp_path):
    """Os dois mercados nao sao a mesma coisa — a distincao e real."""
    corpus_matches = _small_corpus()
    target = corpus_matches[-1]
    cache = OddsHistoryCache(tmp_path)
    events = {f"{target.kickoff[:10]}T04:00:00Z": [
        _odds_event(target.home, target.away, 4.2, 3.6, 1.9)
    ]}
    HistoricalOddsImporter(FakeOddsProvider(events), cache).import_window(
        "soccer_brazil_campeonato",
        f"{target.kickoff[:10]}T04:00:00Z", f"{target.kickoff[:10]}T04:00:00Z",
        step_hours=6,
    )

    naive = NaiveSyntheticOddsSource()
    real = RealHistoricalOddsSource(cache=cache, max_age_hours=24 * 3)
    odds_naive = naive.odds_for(target, {"Resultado Final (1X2)": {"1": 0.4, "X": 0.3, "2": 0.3}})
    odds_real = real.odds_for(target, {"Resultado Final (1X2)": {"1": 0.4, "X": 0.3, "2": 0.3}})

    assert odds_naive and odds_real
    # o mercado real tem exatamente as casas do snapshot; o sintetico tem as 10 do dataset
    assert set(odds_real["Resultado Final (1X2)"]) == {"Pinnacle", "Bet365", "Betano"}
    assert len(odds_naive["Resultado Final (1X2)"]) > len(odds_real["Resultado Final (1X2)"])


# ==========================================================================
# 9. SEGURANCA — a chave nunca pode sair do processo
# ==========================================================================


def test_redact_url_removes_api_key():
    """Erro de rede nao pode imprimir a chave no terminal/log/HTTP."""
    from betgsn.providers import redact_url

    url = "https://api.the-odds-api.com/v4/sports/x/odds?apiKey=SEGREDO123&regions=eu"
    safe = redact_url(url)
    assert "SEGREDO123" not in safe
    assert "apiKey=REDACTED" in safe
    assert "regions=eu" in safe  # parametros nao sensiveis permanecem


def test_redact_url_handles_multiple_credential_names():
    from betgsn.providers import redact_url

    for param in ("apiKey", "apikey", "key", "token", "api_key"):
        safe = redact_url(f"https://exemplo.com/x?{param}=VALORSECRETO&a=1")
        assert "VALORSECRETO" not in safe, f"{param} vazou"


def test_redact_url_without_query_is_unchanged():
    from betgsn.providers import redact_url

    url = "https://exemplo.com/sem/query"
    assert redact_url(url) == url


def test_redact_text_scrubs_free_form_messages():
    """Defesa em profundidade para mensagens de terceiros gravadas em disco."""
    from betgsn.backtest_sources import redact_text

    raw = 'erro: HTTP 401 em https://x.com/odds?apiKey=SEGREDO123&regions=eu'
    assert "SEGREDO123" not in redact_text(raw)
    assert "***" in redact_text(raw)


def test_provider_error_never_contains_key(monkeypatch):
    """O ProviderError levantado pelo cliente HTTP nao carrega a chave."""
    import urllib.error

    from betgsn import providers

    def fake_urlopen(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://api.the-odds-api.com/v4/x?apiKey=SEGREDO123",
            401, "Unauthorized", {}, None,
        )

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(providers.ProviderError) as exc:
        providers._get("https://api.the-odds-api.com/v4/x?apiKey=SEGREDO123")
    assert "SEGREDO123" not in str(exc.value)
    assert "REDACTED" in str(exc.value)


def test_capture_report_errors_are_sanitized_on_disk(tmp_path):
    """O manifesto em disco nao pode conter credencial."""
    from betgsn.backtest_sources import LiveOddsCapture, OddsHistoryCache

    class LeakyProvider:
        def live_odds_with_meta(self, sport_key, regions=None, markets=None):
            raise ProviderError(
                f"HTTP 401 em https://x.com/odds?apiKey=SEGREDO123&regions={regions}"
            )

    cache = OddsHistoryCache(tmp_path)
    capture = LiveOddsCapture(LeakyProvider(), cache)  # type: ignore[arg-type]
    report = capture.capture(["soccer_epl"])

    assert report.snapshots_saved == 0
    assert report.errors

    manifest = tmp_path.parent / "manifest.json"
    assert manifest.exists()
    content = manifest.read_text(encoding="utf-8")
    assert "SEGREDO123" not in content, "chave vazou para o manifesto"
    assert "***" in content


def test_env_file_loading(tmp_path):
    """O .env e lido sem sobrescrever o ambiente real."""
    from betgsn.providers import load_env_file

    env = tmp_path / ".env"
    env.write_text(
        "# comentario\n"
        "\n"
        "TEST_KEY_A=valor1\n"
        "export TEST_KEY_B=\"valor com espaco\"\n"
        "TEST_KEY_C='aspas simples'\n"
        "linha invalida sem igual\n",
        encoding="utf-8",
    )
    import os

    for key in ("TEST_KEY_A", "TEST_KEY_B", "TEST_KEY_C"):
        os.environ.pop(key, None)

    applied = load_env_file(env)
    assert applied == 3
    assert os.environ["TEST_KEY_A"] == "valor1"
    assert os.environ["TEST_KEY_B"] == "valor com espaco"
    assert os.environ["TEST_KEY_C"] == "aspas simples"

    # ambiente real tem prioridade sobre o arquivo
    os.environ["TEST_KEY_A"] = "do_ambiente"
    load_env_file(env)
    assert os.environ["TEST_KEY_A"] == "do_ambiente"

    for key in ("TEST_KEY_A", "TEST_KEY_B", "TEST_KEY_C"):
        os.environ.pop(key, None)


# ==========================================================================
# 10. CAPTURA DE ODDS AO VIVO -> historico real
# ==========================================================================


class FakeLiveProvider:
    """Simula o endpoint de odds ao vivo (lista de eventos + headers)."""

    def __init__(self, events: list[dict], credits: int = 2) -> None:
        self.events = events
        self.credits = credits
        self.calls: list[str] = []

    def live_odds_with_meta(self, sport_key, regions=None, markets=None):
        self.calls.append(markets or "")
        return self.events, {
            "x-requests-last": str(self.credits),
            "x-requests-used": "5",
            "x-requests-remaining": "495",
        }


def test_capture_saves_snapshot_with_current_timestamp(tmp_path):
    from betgsn.backtest_sources import LiveOddsCapture, OddsHistoryCache

    events = [_odds_event("Alfa", "Bravo")]
    provider = FakeLiveProvider(events)
    cache = OddsHistoryCache(tmp_path)

    moment = datetime(2025, 5, 1, 12, 0, tzinfo=timezone.utc)
    report = LiveOddsCapture(provider, cache, regions="eu",
                             markets="h2h").capture(["soccer_epl"], now=moment)

    assert report.snapshots_saved == 1
    assert report.events_with_odds == 1
    assert report.credits_last == 2
    assert report.credits_remaining == 495
    assert report.captured_at == "2025-05-01T12:00:00Z"

    snapshots = cache.load("soccer_epl")
    assert len(snapshots) == 1
    assert snapshots[0].timestamp == "2025-05-01T12:00:00Z"
    assert snapshots[0].provider == "the-odds-api-live"


def test_captured_snapshot_becomes_historical_odds(tmp_path):
    """O ponto central: capturar agora cria historico real utilizavel depois."""
    from betgsn.backtest_sources import LiveOddsCapture, OddsHistoryCache

    provider = FakeLiveProvider([_odds_event("Alfa", "Bravo", 4.0, 3.5, 2.0)])
    cache = OddsHistoryCache(tmp_path)
    # captura em 01/05 para um jogo em 10/05
    LiveOddsCapture(provider, cache, regions="eu", markets="h2h").capture(
        ["soccer_brazil_campeonato"],
        now=datetime(2025, 5, 1, 12, 0, tzinfo=timezone.utc),
    )

    # em 11/05 o jogo ja aconteceu: o snapshot virou historico
    src = RealHistoricalOddsSource(
        cache=cache, sport_key="soccer_brazil_campeonato", max_age_hours=24 * 30
    )
    match = _match("2025-05-10", "Alfa", "Bravo", 1, 0)
    odds = src.odds_for(match, {})
    assert odds
    assert src.last_as_of == "2025-05-01T12:00:00Z"
    assert src.last_as_of < "2025-05-10T16:00:00Z"


def test_capture_removes_unsupported_market_and_retries(tmp_path):
    """Mercado nao suportado nao pode derrubar a captura inteira."""
    from betgsn.backtest_sources import LiveOddsCapture, OddsHistoryCache

    class PickyProvider:
        def __init__(self):
            self.calls: list[str] = []

        def live_odds_with_meta(self, sport_key, regions=None, markets=None):
            self.calls.append(markets or "")
            if "btts" in (markets or ""):
                raise ProviderError(
                    "HTTP 422 em https://x/odds?apiKey=***: "
                    '{"message":"Markets not supported by this endpoint: btts"}'
                )
            return [_odds_event("A", "B")], {"x-requests-last": "2"}

    provider = PickyProvider()
    cache = OddsHistoryCache(tmp_path)
    report = LiveOddsCapture(
        provider, cache, markets="h2h,btts,totals"
    ).capture(["soccer_epl"])

    assert report.snapshots_saved == 1
    assert len(provider.calls) == 2          # tentou, removeu btts, tentou de novo
    assert "btts" in provider.calls[0]
    assert "btts" not in provider.calls[1]
    assert report.markets_used["soccer_epl"] == ["h2h", "totals"]
    assert any("btts" in e for e in report.errors)


def test_capture_pending_matches_skips_when_none_future(tmp_path):
    from betgsn.backtest_sources import LiveOddsCapture, OddsHistoryCache

    past = _match("2020-01-01", "A", "B", 1, 0)
    provider = FakeLiveProvider([_odds_event("A", "B")])
    report = LiveOddsCapture(provider, OddsHistoryCache(tmp_path)).capture_pending_matches(
        [past], "soccer_epl", now=datetime(2025, 5, 1, tzinfo=timezone.utc)
    )
    assert report.snapshots_saved == 0
    assert provider.calls == []              # nem gastou cota
    assert report.errors


def test_capture_pending_matches_captures_when_future_exists(tmp_path):
    from betgsn.backtest_sources import LiveOddsCapture, OddsHistoryCache

    future = _match("2025-12-01", "A", "B", 1, 0)
    provider = FakeLiveProvider([_odds_event("A", "B")])
    report = LiveOddsCapture(provider, OddsHistoryCache(tmp_path)).capture_pending_matches(
        [future], "soccer_epl", now=datetime(2025, 5, 1, tzinfo=timezone.utc)
    )
    assert report.snapshots_saved == 1
    assert len(provider.calls) == 1
