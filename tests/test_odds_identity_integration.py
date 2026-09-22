"""Testes de integracao C1/C2 — identidade de partida e store canonico de odds.

Prova, ponta a ponta, o que a auditoria apontou como quebrado:

  A) uma observacao gravada pelo PRODUTOR e encontrada pelo CONSUMIDOR com a
     MESMA match_key (`event_key`);
  B) `movement` encontra historico persistido real quando ele existe;
  C) `clv` encontra a observacao de fechamento correspondente;
  D) a captura ao vivo (caminho operacional) alimenta o MESMO store que a
     API consome em movement/CLV/coverage;
  E) os testes nao dependem do SQLite default vazio: o store e injetado.

Nada aqui fabrica odds: as observacoes sao as que o produtor geraria a partir
de um evento de provider.
"""
from __future__ import annotations

from datetime import datetime, timezone

from betgsn.football_data_uk import UpcomingFixture
from betgsn.odds_normalize import event_key
from betgsn.odds_snapshots import OddsObservation, OddsSnapshotStore
from betgsn.timeutil import utc_key

MARKET = "Resultado Final (1X2)"
KICKOFF = "2030-01-01T12:00:00Z"
KEY = event_key("Arsenal", "Chelsea", utc_key(KICKOFF))


def _fixture() -> UpcomingFixture:
    """Jogo futuro no mesmo formato do Data Layer ({mercado:{casa:{resultado:odd}}})."""
    odds = {
        MARKET: {
            "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
            "Bet365": {"1": 1.95, "X": 3.35, "2": 4.10},
        },
    }
    best = {MARKET: {"1": 1.95, "X": 3.40, "2": 4.20}}
    who = {MARKET: {"1": "Bet365", "X": "Pinnacle", "2": "Pinnacle"}}
    return UpcomingFixture(
        division="E0", league="Premier League (England)",
        date="2030-01-01", time="12:00", timezone="UTC",
        home="Arsenal", away="Chelsea",
        odds=odds, best_odds=best, best_books=who,
    )


def _event(commence_time: str = KICKOFF) -> dict:
    """Evento cru no formato da The Odds API (com `commence_time`)."""
    return {
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "commence_time": commence_time,
        "bookmakers": [
            {
                "key": "pinnacle", "title": "Pinnacle",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Arsenal", "price": 1.90},
                    {"name": "Draw", "price": 3.40},
                    {"name": "Chelsea", "price": 4.20},
                ]}],
            },
        ],
    }


class _LiveProvider:
    def __init__(self, events: list[dict]) -> None:
        self.events = events

    def live_odds_with_meta(self, sport_key, regions=None, markets=None):
        return list(self.events), {"x-requests-last": "2"}


def _patch_fixtures(monkeypatch, fixtures: list[UpcomingFixture]) -> None:
    from betgsn.football_data_uk import FootballDataClient

    monkeypatch.setattr(FootballDataClient, "load_fixtures", lambda self: list(fixtures))
    monkeypatch.setattr(
        FootballDataClient, "corpus_signature", lambda self: "test-signature"
    )


def _patch_store(monkeypatch, db_path) -> None:
    """Aponta o store da API para um SQLite de teste (E: nunca o default)."""
    import betgsn.odds_snapshots as snap_mod

    real = snap_mod.OddsSnapshotStore
    monkeypatch.setattr(snap_mod, "OddsSnapshotStore", lambda *a, **k: real(db_path))


# ==========================================================================
# A) identidade unica entre produtor e consumidor
# ==========================================================================


def test_event_key_is_canonical_not_display_name():
    fixture = _fixture()
    assert fixture.event_key == KEY
    assert fixture.event_key != fixture.match
    assert fixture.match == "Arsenal vs Chelsea"


def test_odds_service_has_no_persistence_path(tmp_path):
    """I-04: o produtor operacional e a captura ao vivo, nao o OddsService.

    A prova de que o produtor grava na chave que o consumidor usa esta em
    `test_live_capture_feeds_the_canonical_store` (secao D). Este teste
    trava o contrato do writer unico: o OddsService nao possui caminho de
    persistencia — nem parametro, nem metodo, nem escrita.
    """
    import inspect

    from betgsn.odds_service import OddsService

    init_params = inspect.signature(OddsService.__init__).parameters
    assert "store" not in init_params

    fetch_params = inspect.signature(OddsService.fetch).parameters
    assert "persist" not in fetch_params
    assert not hasattr(OddsService, "_persist")

    # uma coleta real nao grava observacao em store algum
    from betgsn.backtest_sources import LiveOddsCapture, OddsHistoryCache

    store = OddsSnapshotStore(tmp_path / "odds.db")
    service = OddsService(
        providers=[("The Odds API", _LiveProvider([_event()]))],
        now=lambda: "2030-01-01T10:00:00Z",
    )
    result = service.fetch("soccer_epl")
    assert result.ok is True
    assert store.stats()["observations"] == 0

    # o caminho operacional (captura) grava sob a MESMA chave do fixture
    fixture = _fixture()
    capture = LiveOddsCapture(
        _LiveProvider([_event()]), OddsHistoryCache(tmp_path / "cache"),
        regions="eu", markets="h2h", store=store,
    )
    capture.capture(
        ["soccer_epl"], now=datetime(2030, 1, 1, 10, 0, tzinfo=timezone.utc)
    )
    found = store.all_observations(fixture.event_key)
    assert {o.outcome for o in found} == {"1", "X", "2"}


# ==========================================================================
# B) movement encontra historico persistido
# ==========================================================================


def test_movement_finds_persisted_observations(monkeypatch, tmp_path):
    from betgsn.api.service import BetgsnService

    fixture = _fixture()
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    store.add([
        OddsObservation(
            match_key=fixture.event_key, market=MARKET, outcome="1",
            bookmaker="Pinnacle", odd=1.90, timestamp="2020-01-01T10:00:00Z",
            kickoff=KICKOFF, provider="The Odds API",
        ),
        OddsObservation(
            match_key=fixture.event_key, market=MARKET, outcome="1",
            bookmaker="Pinnacle", odd=2.10, timestamp="2020-01-02T10:00:00Z",
            kickoff=KICKOFF, provider="The Odds API",
        ),
    ])
    _patch_fixtures(monkeypatch, [fixture])
    _patch_store(monkeypatch, db)

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
    assert entry.price_delta == 0.20


def test_movement_is_no_data_without_history(monkeypatch, tmp_path):
    """Ausencia de historico e NO_DATA explicito — nunca STABLE inventado."""
    from betgsn.api.service import BetgsnService

    fixture = _fixture()
    _patch_fixtures(monkeypatch, [fixture])
    _patch_store(monkeypatch, tmp_path / "vazio.db")

    overview = BetgsnService().movement()
    entry = next(
        m for m in overview.movements
        if m.market == MARKET and m.outcome == "1"
    )
    assert entry.status == "NO_DATA"
    assert entry.n_observations == 0
    # a odd exibida e a melhor disponivel no fixture, nao um historico falso
    assert entry.current_odd == fixture.best_odds[MARKET]["1"]


# ==========================================================================
# C) CLV encontra a observacao correspondente
# ==========================================================================


def test_clv_finds_persisted_closing_observation(monkeypatch, tmp_path):
    from betgsn.api.service import BetgsnService

    fixture = _fixture()
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    store.add([
        OddsObservation(
            match_key=fixture.event_key, market=MARKET, outcome="1",
            bookmaker="Pinnacle", odd=1.80, timestamp="2030-01-01T11:30:00Z",
            kickoff=KICKOFF, provider="The Odds API",
        ),
    ])
    _patch_fixtures(monkeypatch, [fixture])
    _patch_store(monkeypatch, db)

    report = BetgsnService().clv()
    entry = next(
        e for e in report.entries
        if e.market == MARKET and e.outcome == "1"
    )
    assert entry.status == "OK"
    assert entry.closing_odd == 1.80
    assert entry.entry_odd == fixture.best_odds[MARKET]["1"]
    assert entry.clv_percentage > 0


def test_clv_no_closing_odds_when_none_persisted(monkeypatch, tmp_path):
    from betgsn.api.service import BetgsnService

    fixture = _fixture()
    _patch_fixtures(monkeypatch, [fixture])
    _patch_store(monkeypatch, tmp_path / "vazio.db")

    report = BetgsnService().clv()
    entry = next(
        e for e in report.entries
        if e.market == MARKET and e.outcome == "1"
    )
    assert entry.status == "NO_CLOSING_ODDS"
    assert entry.closing_odd is None
    assert entry.clv_percentage is None


# ==========================================================================
# D) producao alimenta o mesmo store consumido pela API
# ==========================================================================


def test_live_capture_feeds_the_canonical_store(tmp_path):
    from betgsn.backtest_sources import LiveOddsCapture, OddsHistoryCache

    cache = OddsHistoryCache(tmp_path / "cache")
    store = OddsSnapshotStore(tmp_path / "odds.db")
    capture = LiveOddsCapture(
        _LiveProvider([_event()]), cache, regions="eu", markets="h2h", store=store,
    )

    moment = datetime(2030, 1, 1, 10, 0, tzinfo=timezone.utc)
    report = capture.capture(["soccer_epl"], now=moment)

    assert report.snapshots_saved == 1
    assert report.observations_saved == 3
    assert store.stats()["observations"] == 3

    # mesma chave que a API consulta
    found = store.all_observations(KEY)
    assert {o.outcome for o in found} == {"1", "X", "2"}
    assert all(o.provider == "The Odds API" for o in found)


def test_live_capture_without_store_does_not_persist(tmp_path):
    """Sem store injetado nada e gravado — nao existe persistencia fantasma."""
    from betgsn.backtest_sources import LiveOddsCapture, OddsHistoryCache

    cache = OddsHistoryCache(tmp_path / "cache")
    capture = LiveOddsCapture(
        _LiveProvider([_event()]), cache, regions="eu", markets="h2h",
    )
    report = capture.capture(
        ["soccer_epl"], now=datetime(2030, 1, 1, 10, 0, tzinfo=timezone.utc)
    )
    assert report.snapshots_saved == 1
    assert report.observations_saved == 0


# ==========================================================================
# E) o store usado e o injetado (nunca o default vazio)
# ==========================================================================


def test_api_reads_the_injected_store_path(monkeypatch, tmp_path):
    import betgsn.odds_snapshots as snap_mod
    from betgsn.api.service import BetgsnService

    fixture = _fixture()
    db = tmp_path / "custom" / "odds.db"
    store = OddsSnapshotStore(db)
    store.add([
        OddsObservation(
            match_key=fixture.event_key, market=MARKET, outcome="1",
            bookmaker="Pinnacle", odd=1.90, timestamp="2020-01-01T10:00:00Z",
            kickoff=KICKOFF, provider="The Odds API",
        ),
    ])
    _patch_fixtures(monkeypatch, [fixture])

    seen: list = []
    real = snap_mod.OddsSnapshotStore

    def factory(*a, **k):
        instance = real(db)
        seen.append(instance._path)
        return instance

    monkeypatch.setattr(snap_mod, "OddsSnapshotStore", factory)

    overview = BetgsnService().movement()
    entry = next(
        m for m in overview.movements
        if m.market == MARKET and m.outcome == "1"
    )
    assert entry.status != "NO_DATA"
    assert seen and all(p == db for p in seen)
