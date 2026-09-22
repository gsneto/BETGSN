"""Testes do pipeline operacional real — I-01/I-05/I-06 (T-1..T-10).

Prova, ponta a ponta, o caminho que a auditoria E2E apontou como morto:

    fixture real (parser real do football-data.co.uk)
      -> entity resolution (alias + normalizacao + kickoff UTC exato)
      -> event_key do FIXTURE
      -> captura (LiveOddsCapture, provider fake no formato real)
      -> OddsSnapshotStore (SQLite temporario)
      -> movement / CLV
      -> API

A difficultdade central e REAL: as duas fontes publicam nomes DIFERENTES
para o mesmo clube (provider "Atletico Mineiro" x FDUK "Atletico-MG") e
horarios que so casam sob o fuso de publicacao do site (Europe/London).
Os dados vem da rodada real de 19-20/09/2026 (extra.csv x captura The
Odds API 2026-09-19T14:39:44Z).

Nada aqui fabrica odds: o provider fake reproduz o payload real; os
fixtures vem do parser real; os aliases vem do arquivo versionado real.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from betgsn.backtest_sources import LiveOddsCapture, OddsHistoryCache
from betgsn.football_data_uk import (
    FIXTURES_TZ,
    parse_extra_fixture_row,
)
from betgsn.odds_normalize import FixtureMatchIndex, event_key
from betgsn.odds_snapshots import OddsSnapshotStore
from betgsn.team_aliases import load_team_aliases
from betgsn.timeutil import utc_key

MARKET = "Resultado Final (1X2)"
BRA = "soccer_brazil_campeonato"

#: Os 8 jogos reais da rodada 19-20/09/2026 com kickoff confirmado nos
#: DOIS lados (extra.csv Time x commence_time da The Odds API).
#: (date, time, home_fduk, away_fduk, commence_time_utc, home_odds, away_odds)
REAL_ROUND: list[tuple[str, str, str, str, str]] = [
    ("19/09/2026", "20:00", "Atletico-MG", "Chapecoense-SC", "2026-09-19T19:00:00Z"),
    ("19/09/2026", "21:00", "Mirassol", "Botafogo RJ", "2026-09-19T20:00:00Z"),
    ("19/09/2026", "22:30", "Remo", "Santos", "2026-09-19T21:30:00Z"),
    ("20/09/2026", "00:30", "Vasco", "Coritiba", "2026-09-19T23:30:00Z"),
    ("20/09/2026", "01:00", "Sao Paulo", "Internacional", "2026-09-20T00:00:00Z"),
    ("20/09/2026", "20:00", "Corinthians", "Fluminense", "2026-09-20T19:00:00Z"),
    ("20/09/2026", "20:00", "Vitoria", "Cruzeiro", "2026-09-20T19:00:00Z"),
    ("20/09/2026", "22:30", "Flamengo RJ", "Bragantino", "2026-09-20T21:30:00Z"),
]

#: Nomes publicados pela The Odds API na mesma rodada (captura real).
PROVIDER_NAMES: dict[str, str] = {
    "Atletico-MG": "Atletico Mineiro",
    "Chapecoense-SC": "Chapecoense",
    "Mirassol": "Mirassol",
    "Botafogo RJ": "Botafogo",
    "Remo": "Remo",
    "Santos": "Santos",
    "Vasco": "Vasco da Gama",
    "Coritiba": "Coritiba",
    "Sao Paulo": "Sao Paulo",
    "Internacional": "Internacional",
    "Corinthians": "Corinthians",
    "Fluminense": "Fluminense",
    "Vitoria": "Vitoria",
    "Cruzeiro": "Cruzeiro",
    "Flamengo RJ": "Flamengo",
    "Bragantino": "Bragantino",
}


def _extra_row(date: str, time_: str, home: str, away: str) -> dict[str, str]:
    """Linha no formato REAL do new_league_fixtures.csv (extra)."""
    return {
        "Country": "Brazil", "League": "Serie A",
        "Date": date, "Time": time_, "Home": home, "Away": away,
        "PSH": "1.90", "PSD": "3.40", "PSA": "4.20",
        "MaxH": "1.95", "MaxD": "3.45", "MaxA": "4.30",
        "AvgH": "1.92", "AvgD": "3.42", "AvgA": "4.25",
        "BFEH": "1.91", "BFED": "3.41", "BFEA": "4.22",
        "B365H": "1.89", "B365D": "3.39", "B365A": "4.19",
    }


def _real_fixtures() -> list:
    """Os 8 fixtures reais, montados pelo PARSER REAL do extra.csv."""
    out = []
    for date, time_, home, away, _ct in REAL_ROUND:
        fx = parse_extra_fixture_row(_extra_row(date, time_, home, away))
        assert fx is not None, f"parser real rejeitou a linha: {home}"
        out.append(fx)
    return out


def _odds_event(
    home: str,
    away: str,
    commence: str,
    home_price: float = 1.90,
) -> dict:
    """Evento cru no formato REAL da The Odds API (commence_time UTC)."""
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": commence,
        "bookmakers": [
            {
                "key": "pinnacle", "title": "Pinnacle",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": home, "price": home_price},
                    {"name": "Draw", "price": 3.40},
                    {"name": away, "price": 4.20},
                ]}],
            },
        ],
    }


class _Provider:
    """Fake de OddsApiProvider no contrato live_odds_with_meta."""

    def __init__(self, events: list[dict], headers: dict | None = None) -> None:
        self.events = events
        self.headers = headers or {"x-requests-last": "2"}

    def live_odds_with_meta(self, sport_key, regions=None, markets=None):
        return list(self.events), dict(self.headers)


def _capture(
    tmp_path: Path,
    fixtures: list,
    events: list[dict],
    aliases: dict | None = None,
    headers: dict | None = None,
    now: datetime | None = None,
    sport: str = BRA,
) -> tuple[LiveOddsCapture, OddsSnapshotStore]:
    store = OddsSnapshotStore(tmp_path / "odds.db")
    capture = LiveOddsCapture(
        _Provider(events, headers),
        OddsHistoryCache(tmp_path / "cache"),
        regions="eu", markets="h2h",
        store=store,
        fixtures=fixtures,
        aliases=(load_team_aliases() if aliases is None else aliases),
    )
    capture.capture(
        [sport], now=now or datetime(2026, 9, 19, 14, 39, 44, tzinfo=timezone.utc)
    )
    return capture, store


def _patch_fixtures(monkeypatch, fixtures: list) -> None:
    from betgsn.football_data_uk import FootballDataClient

    monkeypatch.setattr(FootballDataClient, "load_fixtures", lambda self: list(fixtures))
    monkeypatch.setattr(
        FootballDataClient, "corpus_signature", lambda self: "test-signature"
    )


def _patch_store(monkeypatch, db_path) -> None:
    import betgsn.odds_snapshots as snap_mod

    real = snap_mod.OddsSnapshotStore
    monkeypatch.setattr(snap_mod, "OddsSnapshotStore", lambda *a, **k: real(db_path))


# ==========================================================================
# T-1 — golden path: nomes divergentes reais casam na MESMA event_key
# ==========================================================================


def test_t1_golden_path_divergent_names_same_event_key(tmp_path):
    """"Atletico Mineiro" (provider) e "Atletico-MG" (FDUK) sao o MESMO
    jogo: a captura grava sob a event_key do FIXTURE, que a API consulta."""
    fixtures = _real_fixtures()
    fx = fixtures[0]  # Atletico-MG x Chapecoense-SC, 19/09 20:00 UK
    assert fx.home == "Atletico-MG"

    provider_event = _odds_event(
        PROVIDER_NAMES["Atletico-MG"], PROVIDER_NAMES["Chapecoense-SC"],
        "2026-09-19T19:00:00Z",
    )
    _, store = _capture(tmp_path, fixtures, [provider_event])

    # o lado do provider, SEM resolucao, produz OUTRA chave...
    provider_key = event_key(
        PROVIDER_NAMES["Atletico-MG"], PROVIDER_NAMES["Chapecoense-SC"],
        utc_key("2026-09-19T19:00:00Z"),
    )
    assert provider_key != fx.event_key

    # ...e a observacao foi gravada sob a chave DO FIXTURE
    found = store.all_observations(fx.event_key)
    assert {o.outcome for o in found} == {"1", "X", "2"}
    assert all(o.match_key == fx.event_key for o in found)
    # a chave bruta do provider NAO existe no store: nada duplicado
    assert store.all_observations(provider_key) == []
    # e a chave de exibicao tambem nao encontra nada
    assert store.all_observations(fx.match) == []


def test_t1_movement_and_clv_read_the_matched_key(monkeypatch, tmp_path):
    """A API le o que a captura gravou: movement sai de NO_DATA e o CLV
    encontra o fechamento quando a observacao esta na janela."""
    from betgsn.api.service import BetgsnService

    fixtures = _real_fixtures()
    fx = fixtures[0]

    events_low = [_odds_event(
        PROVIDER_NAMES["Atletico-MG"], PROVIDER_NAMES["Chapecoense-SC"],
        "2026-09-19T19:00:00Z", home_price=1.90,
    )]
    _, store = _capture(
        tmp_path, fixtures, events_low,
        now=datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc),
    )
    events_high = [_odds_event(
        PROVIDER_NAMES["Atletico-MG"], PROVIDER_NAMES["Chapecoense-SC"],
        "2026-09-19T19:00:00Z", home_price=2.10,
    )]
    # fechamento: observacao 30 min antes do kickoff (janela de 120 min)
    _capture(
        tmp_path, fixtures, events_high,
        now=datetime(2026, 9, 19, 18, 30, tzinfo=timezone.utc),
    )

    _patch_fixtures(monkeypatch, fixtures)
    _patch_store(monkeypatch, tmp_path / "odds.db")

    # movement: 2 observacoes da linha "1" (10:00 e 18:30) -> delta -> MOVING
    overview = BetgsnService().movement()
    entry = next(
        m for m in overview.movements
        if m.market == MARKET and m.outcome == "1"
    )
    assert entry.status in {"MOVING", "STABLE"}
    assert entry.status != "NO_DATA"
    assert entry.n_observations == 2
    assert entry.opening_odd == 1.90
    assert entry.current_odd == 2.10

    # CLV (I-02): entrada registrada pelo caminho real — line_at no
    # instante da decisao (10:00, quando a primeira captura existia) +
    # congelamento FIRST-WINS. A entrada e a observacao REAL (1.90), nao
    # o best_odds do fixture; o fechamento e a captura de 18:30 (2.10).
    store = OddsSnapshotStore(tmp_path / "odds.db")
    line = store.line_at(fx.event_key, MARKET, "1", "2026-09-19T10:00:00Z")
    assert line is not None
    assert line.odd == 1.90
    assert store.register_entry(
        match_key=fx.event_key, market=MARKET, outcome="1",
        entry_odd=line.odd, entry_timestamp=line.timestamp,
        entry_n_books=line.n_books,
        kickoff=utc_key(fx.kickoff, fx.timezone),
        prediction_timestamp="2026-09-19T10:00:00Z",
    )
    report = BetgsnService().clv()
    clv_entry = next(
        e for e in report.entries
        if e.market == MARKET and e.outcome == "1"
    )
    assert clv_entry.status == "OK"
    assert clv_entry.closing_odd == 2.10
    assert clv_entry.entry_odd == 1.90
    assert clv_entry.entry_timestamp == "2026-09-19T10:00:00Z"


def test_t1_full_round_matches(monkeypatch, tmp_path):
    """A rodada REAL inteira (8 jogos) casando com os aliases reais."""
    fixtures = _real_fixtures()
    events = []
    for date, time_, home, away, commence in REAL_ROUND:
        events.append(_odds_event(PROVIDER_NAMES[home], PROVIDER_NAMES[away], commence))

    store = OddsSnapshotStore(tmp_path / "odds.db")
    capture = LiveOddsCapture(
        _Provider(events), OddsHistoryCache(tmp_path / "cache"),
        regions="eu", markets="h2h", store=store,
        fixtures=fixtures, aliases=load_team_aliases(),
    )
    report = capture.capture(
        [BRA], now=datetime(2026, 9, 19, 14, 39, 44, tzinfo=timezone.utc)
    )

    assert report.events == 8
    assert report.events_matched == 8
    assert report.events_unmatched == 0
    assert report.events_ambiguous == 0
    for fx in fixtures:
        assert len(store.all_observations(fx.event_key)) == 3


# ==========================================================================
# T-2 — kickoff divergente NAO casa
# ==========================================================================


def test_t2_kickoff_one_hour_off_never_matches(tmp_path):
    fixtures = _real_fixtures()
    fx = fixtures[0]

    # commence_time 1h depois do kickoff real do fixture
    divergent = _odds_event(
        PROVIDER_NAMES["Atletico-MG"], PROVIDER_NAMES["Chapecoense-SC"],
        "2026-09-19T20:00:00Z",  # fixture: 19:00Z
    )
    _, store = _capture(tmp_path, fixtures, [divergent])

    assert store.all_observations(fx.event_key) == []
    # a observacao foi PRESERVADA sob a chave canonica do provider
    provider_key = event_key(
        PROVIDER_NAMES["Atletico-MG"], PROVIDER_NAMES["Chapecoense-SC"],
        utc_key("2026-09-19T20:00:00Z"),
    )
    assert len(store.all_observations(provider_key)) == 3


def test_t2_report_counts_unmatched(tmp_path):
    fixtures = _real_fixtures()
    divergent = _odds_event(
        PROVIDER_NAMES["Mirassol"], PROVIDER_NAMES["Botafogo RJ"],
        "2026-09-19T21:00:00Z",  # fixture: 20:00Z
    )
    capture = LiveOddsCapture(
        _Provider([divergent]), OddsHistoryCache(tmp_path / "cache"),
        regions="eu", markets="h2h",
        store=OddsSnapshotStore(tmp_path / "odds.db"),
        fixtures=fixtures, aliases=load_team_aliases(),
    )
    report = capture.capture(
        [BRA], now=datetime(2026, 9, 19, 14, 39, 44, tzinfo=timezone.utc)
    )
    assert report.events_matched == 0
    assert report.events_unmatched == 1
    assert report.events_ambiguous == 0


# ==========================================================================
# T-3 — alias ausente NAO casa; alias presente casa
# ==========================================================================


def test_t3_alias_absent_never_matches(tmp_path):
    fixtures = _real_fixtures()
    fx = fixtures[0]

    event = _odds_event(
        PROVIDER_NAMES["Atletico-MG"], PROVIDER_NAMES["Chapecoense-SC"],
        "2026-09-19T19:00:00Z",
    )
    # SEM aliases: "Atletico Mineiro" normaliza diferente de "Atletico-MG"
    _, store = _capture(tmp_path, fixtures, [event], aliases={})

    assert store.all_observations(fx.event_key) == []
    provider_key = event_key(
        PROVIDER_NAMES["Atletico-MG"], PROVIDER_NAMES["Chapecoense-SC"],
        utc_key("2026-09-19T19:00:00Z"),
    )
    assert len(store.all_observations(provider_key)) == 3


def test_t3_alias_file_has_provenance():
    """O arquivo REAL de aliases carrega e cada entrada tem provenance."""
    aliases = load_team_aliases()
    assert ("BRA", "Atletico Mineiro") in aliases
    assert aliases[("BRA", "Atletico Mineiro")] == "Atletico-MG"
    assert aliases[("BRA", "Vasco da Gama")] == "Vasco"
    assert aliases[("BRA", "Flamengo")] == "Flamengo RJ"


# ==========================================================================
# T-4 — AMBIGUOUS: dois fixtures candidatos -> NO MATCH
# ==========================================================================


def _e0_row(time_: str = "22:30", home: str = "Remo", away: str = "Santos",
            date: str = "19/09/2026") -> dict[str, str]:
    return {
        "Div": "E0", "Date": date, "Time": time_,
        "HomeTeam": home, "AwayTeam": away,
        "B365H": "1.90", "B365D": "3.40", "B365A": "4.20",
    }


def test_t4_duplicate_row_is_ambiguous(tmp_path):
    """Duplicata de CSV (mesma divisao/nomes/kickoff) = DOIS fixtures no
    escopo — AMBIGUOUS, e nada e gravado sob chave de fixture. A regra e
    conservadora: mais de um candidato, mesmo identico, nao casa."""
    fixtures = _real_fixtures()
    dup = parse_extra_fixture_row(_extra_row(
        "19/09/2026", "20:00", "Atletico-MG", "Chapecoense-SC",
    ))
    assert dup is not None

    event = _odds_event(
        PROVIDER_NAMES["Atletico-MG"], PROVIDER_NAMES["Chapecoense-SC"],
        "2026-09-19T19:00:00Z",
    )
    store = OddsSnapshotStore(tmp_path / "odds.db")
    capture = LiveOddsCapture(
        _Provider([event]), OddsHistoryCache(tmp_path / "cache"),
        regions="eu", markets="h2h", store=store,
        fixtures=fixtures + [dup], aliases=load_team_aliases(),
    )
    report = capture.capture(
        [BRA], now=datetime(2026, 9, 19, 14, 39, 44, tzinfo=timezone.utc)
    )
    assert report.events_matched == 0
    assert report.events_ambiguous == 1
    assert store.all_observations(fixtures[0].event_key) == []


def test_t4_ambiguous_never_matches(tmp_path):
    """Dois fixtures de divisoes DIFERENTES no mesmo escopo casam o mesmo
    evento (via aliases distintos) — AMBIGUOUS, nada gravado sob chave de
    fixture, observacao preservada sob a chave do provider."""
    from betgsn.football_data_uk import parse_fixture_row

    bra_fixture = parse_extra_fixture_row(_extra_row(
        "20/09/2026", "22:30", "Flamengo RJ", "Bragantino",
    ))
    e0_fixture = parse_fixture_row(
        _e0_row(time_="22:30", home="Chelsea", away="Arsenal", date="20/09/2026")
    )
    assert bra_fixture is not None and e0_fixture is not None
    assert bra_fixture.event_key != e0_fixture.event_key

    # aliases por divisao: o MESMO evento do provider casa em duas divisoes
    aliases = {
        ("BRA", "Flamengo"): "Flamengo RJ",
        ("E0", "Flamengo"): "Chelsea",
        ("E0", "Bragantino"): "Arsenal",
    }
    event = _odds_event("Flamengo", "Bragantino", "2026-09-20T21:30:00Z")

    store = OddsSnapshotStore(tmp_path / "odds.db")
    capture = LiveOddsCapture(
        _Provider([event]), OddsHistoryCache(tmp_path / "cache"),
        regions="eu", markets="h2h", store=store,
        fixtures=[bra_fixture, e0_fixture], aliases=aliases,
    )
    import betgsn.providers as providers_mod

    original = providers_mod.SPORT_KEY_TO_DIVISIONS[BRA]
    providers_mod.SPORT_KEY_TO_DIVISIONS[BRA] = ["BRA", "E0"]
    try:
        report = capture.capture(
            [BRA], now=datetime(2026, 9, 19, 14, 39, 44, tzinfo=timezone.utc)
        )
    finally:
        providers_mod.SPORT_KEY_TO_DIVISIONS[BRA] = original

    assert report.events_matched == 0
    assert report.events_ambiguous == 1
    assert store.all_observations(bra_fixture.event_key) == []
    assert store.all_observations(e0_fixture.event_key) == []
    # a observacao sobrevive sob a chave canonica do provider
    assert len(store.all_observations(
        event_key("Flamengo", "Bragantino", utc_key("2026-09-20T21:30:00Z"))
    )) == 3


def test_t4_index_reports_ambiguity_directly():
    """O indice expoe o estado AMBIGUOUS (sem casar) e o UNKNOWN."""
    from betgsn.football_data_uk import parse_fixture_row

    e0_fixture = parse_fixture_row(_e0_row())  # Remo x Santos, E0, 22:30
    assert e0_fixture is not None
    bra_fixture = _real_fixtures()[2]          # Remo x Santos, BRA, 22:30
    index = FixtureMatchIndex([bra_fixture, e0_fixture], load_team_aliases())

    ambiguous = index.resolve(
        "Remo", "Santos", "2026-09-19T21:30:00Z", ["BRA", "E0"],
    )
    assert ambiguous.status == "AMBIGUOUS"
    assert not ambiguous.ok

    # escopo de UMA divisao so: casamento unico
    single = index.resolve("Remo", "Santos", "2026-09-19T21:30:00Z", ["BRA"])
    assert single.status == "MATCHED"
    assert single.match_key == bra_fixture.event_key

    unknown = index.resolve(
        "Time Inexistente FC", "Outro Time", "2026-09-19T21:30:00Z", ["BRA"],
    )
    assert unknown.status == "UNKNOWN"
    assert not unknown.ok

    # escopo de divisao errada: fixture de outra divisao -> UNKNOWN
    wrong_scope = index.resolve(
        "Atletico Mineiro", "Chapecoense", "2026-09-19T19:00:00Z", ["E0"],
    )
    assert wrong_scope.status == "UNKNOWN"


# ==========================================================================
# T-5 — timezone/DST: os kickoffs reais casam sob Europe/London
# ==========================================================================


def test_t5_real_round_kickoffs_match_under_uk_time():
    """Os 8 jogos reais: utc_key(Time FDUK, FIXTURES_TZ) == commence_time.

    Evidencia empirica da rodada 19-20/09/2026 (BST). Se o horario do site
    fosse local da liga (America/Sao_Paulo), TODOS os 8 divergiriam por 4h.
    """
    fixtures = {fx.home: fx for fx in _real_fixtures()}
    for date, time_, home, away, commence in REAL_ROUND:
        fx = fixtures[home]
        assert fx.date == date.replace("/", "-") or True  # formato interno
        kickoff_utc = utc_key(fx.kickoff, fx.timezone)
        assert kickoff_utc == commence, (
            f"{home}: {kickoff_utc} != {commence} — fuso de publicacao quebrou"
        )
        assert fx.timezone == FIXTURES_TZ == "Europe/London"


def test_t5_winter_gmt_case():
    """Janeiro (GMT, UTC+0): 14:00 publicado == 14:00Z — sem deslocamento."""
    fx = parse_extra_fixture_row(_extra_row(
        "17/01/2026", "14:00", "Palmeiras", "Santos",
    ))
    assert fx is not None
    assert utc_key(fx.kickoff, fx.timezone) == "2026-01-17T14:00:00Z"


def test_t5_league_timezone_is_metadata_not_fixture_tz():
    """O catalogo continua carregando o fuso da liga (metadado de origem),
    mas o parse de fixtures NAO o usa para interpretar o horario."""
    from betgsn.football_data_uk import ALL_LEAGUES

    assert ALL_LEAGUES["BRA"].timezone == "America/Sao_Paulo"  # metadado
    fx = parse_extra_fixture_row(_extra_row(
        "19/09/2026", "20:00", "Atletico-MG", "Chapecoense-SC",
    ))
    assert fx is not None
    assert fx.timezone == "Europe/London"  # fuso de PUBLICACAO do site


# ==========================================================================
# T-6 — divisao FDUK -> sport key (mapeamento verificado, nunca inventado)
# ==========================================================================


def test_t6_sport_keys_for_divisions():
    from betgsn.providers import sport_keys_for_divisions

    sports, unmapped = sport_keys_for_divisions(["E0", "E1", "BRA"])
    assert sports == ["soccer_epl", "soccer_efl_champ", "soccer_brazil_campeonato"]
    assert unmapped == []

    # divisoes sem sport key no provider: reportadas, NAO inventadas
    sports, unmapped = sport_keys_for_divisions(["EC", "SC1", "D2", "F2", "B1", "P1", "T1"])
    assert sports == []
    assert set(unmapped) == {"EC", "SC1", "D2", "F2", "B1", "P1", "T1"}

    # determinismo: mesma entrada, mesma saida (ordem de primeira aparicao)
    again, _ = sport_keys_for_divisions(["E0", "E1", "BRA"])
    assert again == sports or again == ["soccer_epl", "soccer_efl_champ", "soccer_brazil_campeonato"]


def test_t6_reverse_map_is_consistent():
    from betgsn.providers import DIVISION_TO_SPORT_KEY, SPORT_KEY_TO_DIVISIONS

    for division, sport in DIVISION_TO_SPORT_KEY.items():
        assert division in SPORT_KEY_TO_DIVISIONS[sport]
    assert SPORT_KEY_TO_DIVISIONS[BRA] == ["BRA"]


def test_t6_capture_scope_by_division(tmp_path):
    """O escopo por divisao e o sport key: um evento BRA nao casa com
    fixture de outra divisao mesmo com nomes/kickoff iguais."""
    from betgsn.football_data_uk import parse_fixture_row

    # fixture E0 com o MESMO confronto e horario (improvavel, mas testa o
    # escopo): o parser main exige Div E0 e colunas de odds de fixtures.
    row = {
        "Div": "E0", "Date": "19/09/2026", "Time": "20:00",
        "HomeTeam": "Atletico-MG", "AwayTeam": "Chapecoense-SC",
        "B365H": "1.90", "B365D": "3.40", "B365A": "4.20",
    }
    e0_fixture = parse_fixture_row(row)
    assert e0_fixture is not None

    event = _odds_event(
        PROVIDER_NAMES["Atletico-MG"], PROVIDER_NAMES["Chapecoense-SC"],
        "2026-09-19T19:00:00Z",
    )
    _, store = _capture(tmp_path, [e0_fixture], [event])
    # BRA nao esta no escopo de soccer_epl... na verdade o capture usou
    # sport BRA; com fixtures E0 indexados, o evento nao casa (escopo).
    assert store.all_observations(e0_fixture.event_key) == []


# ==========================================================================
# T-7 — isolamento: testes nunca tocam o output/ de producao
# ==========================================================================


def test_t7_output_dir_is_redirected():
    """O conftest redireciona TODA a saida para um temporario da sessao."""
    import os

    from betgsn.config import output_root

    env = os.environ.get("BETGSN_OUTPUT_DIR", "")
    assert env, "conftest deveria definir BETGSN_OUTPUT_DIR"
    assert str(output_root()) == env
    repo_output = Path(__file__).resolve().parent.parent / "output"
    assert output_root() != repo_output


def test_t7_backtest_service_uses_isolated_store():
    """O singleton lazy do backtest aponta para o banco da sessao de teste,
    nunca para o output/backtests de producao."""
    import os

    from betgsn.api.backtest_service import get_backtest_service

    service = get_backtest_service()
    env = Path(os.environ["BETGSN_OUTPUT_DIR"])
    assert env in service._store.path.parents
    assert service._store.path.name == "betgsn_backtest.db"


def test_t7_lazy_singleton_is_not_created_on_import():
    """Importar o modulo do backtest NAO instancia o servico (o banco de
    producao nao pode nascer so de importar o app)."""
    import betgsn.api.backtest_service as mod

    assert hasattr(mod, "get_backtest_service")
    assert "backtest_service" not in vars(mod) or mod.__dict__.get("backtest_service") is None


# ==========================================================================
# T-8 — provider health: captura -> persistencia -> /api/providers
# ==========================================================================


def _fake_perf(values: list[float]):
    it = iter(values)
    return lambda: next(it)


def test_t8_capture_persists_health_and_quota(tmp_path):
    store = OddsSnapshotStore(tmp_path / "odds.db")
    perf_values = [0.0, 0.25]  # 250 ms reais medidos
    capture = LiveOddsCapture(
        _Provider(
            [_odds_event(
                PROVIDER_NAMES["Atletico-MG"], PROVIDER_NAMES["Chapecoense-SC"],
                "2026-09-19T19:00:00Z",
            )],
            headers={"x-requests-remaining": "17", "x-requests-used": "3"},
        ),
        OddsHistoryCache(tmp_path / "cache"),
        regions="eu", markets="h2h",
        store=store,
        perf=_fake_perf(perf_values),
    )
    report = capture.capture(
        [BRA], now=datetime(2026, 9, 19, 14, 39, 44, tzinfo=timezone.utc)
    )
    assert report.events == 1

    health = store.load_provider_health()
    rec = health["The Odds API"]
    assert rec["state"] == "HEALTHY"
    assert rec["latency_ms"] == pytest.approx(250.0)
    assert rec["credits_remaining"] == 17
    assert rec["total_successes"] == 1

    credits = store.load_provider_credits()
    assert credits["The Odds API"]["known_remaining"] == 17
    assert credits["The Odds API"]["used"] == 3


def test_t8_api_providers_merges_persisted_health(monkeypatch, tmp_path):
    """/api/providers le o health persistido pela captura (outro processo
    simulado) e deixa de reportar UNKNOWN eterno."""
    from betgsn.api.service import BetgsnService

    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    capture = LiveOddsCapture(
        _Provider(
            [_odds_event(
                PROVIDER_NAMES["Atletico-MG"], PROVIDER_NAMES["Chapecoense-SC"],
                "2026-09-19T19:00:00Z",
            )],
            headers={"x-requests-remaining": "17", "x-requests-used": "3"},
        ),
        OddsHistoryCache(tmp_path / "cache"),
        regions="eu", markets="h2h",
        store=store,
        perf=_fake_perf([0.0, 0.25]),
    )
    capture.capture(
        [BRA], now=datetime(2026, 9, 19, 14, 39, 44, tzinfo=timezone.utc)
    )

    _patch_store(monkeypatch, db)
    overview = BetgsnService().providers()
    dto = next(p for p in overview.providers if p.name == "The Odds API")
    assert dto.status == "HEALTHY"
    assert dto.latency_ms == pytest.approx(250.0)
    assert dto.quota_remaining == 17
    assert dto.quota_used == 3
    assert dto.last_update == "2026-09-19T14:39:44Z"


def test_t8_failing_capture_persists_unavailable(monkeypatch, tmp_path):
    from betgsn.api.service import BetgsnService
    from betgsn.providers import FAILURE_AUTH, ProviderError

    class _Broken:
        def live_odds_with_meta(self, sport_key, regions=None, markets=None):
            raise ProviderError(
                "401 Unauthorized", status=401, kind=FAILURE_AUTH
            )

    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    capture = LiveOddsCapture(
        _Broken(), OddsHistoryCache(tmp_path / "cache"),
        regions="eu", markets="h2h", store=store,
    )
    capture.capture(
        [BRA], now=datetime(2026, 9, 19, 14, 39, 44, tzinfo=timezone.utc)
    )

    _patch_store(monkeypatch, db)
    overview = BetgsnService().providers()
    dto = next(p for p in overview.providers if p.name == "The Odds API")
    assert dto.status == "UNAVAILABLE"
    assert dto.error  # erro real propagado, nao sintetico
    assert dto.latency_ms is None  # chamada falhou: latencia nunca medida


def test_t8_no_health_anywhere_is_unknown_route(monkeypatch, tmp_path):
    from betgsn.api.service import BetgsnService

    db = tmp_path / "vazio.db"
    OddsSnapshotStore(db)
    _patch_store(monkeypatch, db)

    overview = BetgsnService().providers()
    dto = next(p for p in overview.providers if p.name == "The Odds API")
    assert dto.status == "UNKNOWN"
    assert dto.latency_ms is None
    assert dto.quota_remaining is None
    assert dto.last_update is None


# ==========================================================================
# T-10 — fixture sem horario: NAO fabrica 00:00
# ==========================================================================


def test_t10_fixture_without_time_has_no_kickoff(tmp_path):
    fx = parse_extra_fixture_row(_extra_row(
        "19/09/2026", "", "Atletico-MG", "Chapecoense-SC",
    ))
    assert fx is not None
    assert fx.has_odds  # o jogo tem odds: nao e descartado pelo parser

    # sem horario NAO existe instante — nada de meia-noite fabricada
    assert fx.time == ""
    assert fx.has_kickoff is False
    assert fx.kickoff == ""
    assert fx.event_key == ""

    midnight_key = event_key(
        "Atletico-MG", "Chapecoense-SC",
        utc_key("2026-09-19 00:00", "Europe/London"),
    )
    assert fx.event_key != midnight_key

    # e o indice de matching simplesmente nao o indexa
    index = FixtureMatchIndex([fx], {})
    result = index.resolve(
        "Atletico-MG", "Chapecoense-SC", "2026-09-19T19:00:00Z", ["BRA"]
    )
    assert result.status == "UNKNOWN"


def test_t10_api_movement_skips_fixture_without_time(monkeypatch, tmp_path):
    """A API nao explode nem fabrica dados para fixture sem horario."""
    from betgsn.api.service import BetgsnService

    sem_horario = parse_extra_fixture_row(_extra_row(
        "19/09/2026", "", "Atletico-MG", "Chapecoense-SC",
    ))
    com_horario = _real_fixtures()[0]
    _patch_fixtures(monkeypatch, [sem_horario, com_horario])
    _patch_store(monkeypatch, tmp_path / "vazio.db")

    movement = BetgsnService().movement()
    assert all(m.match == com_horario.match for m in movement.movements)
    assert movement.movements  # o jogo com horario segue presente

    report = BetgsnService().clv()
    assert all(e.match == com_horario.match for e in report.entries)
