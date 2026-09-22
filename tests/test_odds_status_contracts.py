"""H4 — contratos de status de Movement/CLV: dominio -> API -> frontend.

O dominio (`OddsSnapshotStore`) produz estados que a API precisa
REPRESENTAR, nao descartar:

  movement: NO_DATA (0 obs), INSUFFICIENT_DATA (1 obs), OK (>= 2 obs);
  CLV: OK, NO_CLOSING_ODDS, CLOSING_BEFORE_ENTRY (clv_prospective).

O schema da API aceitava `BEFORE_OPENING` — estado que o dominio NUNCA
produz — e nao tinha representacao para INSUFFICIENT_DATA nem
CLOSING_BEFORE_ENTRY. Aqui se prova que:

  - todos os estados produzidos pelo dominio têm representacao valida;
  - estados invalidos sao rejeitados pelo schema (Literal fechado);
  - o frontend (types/api.ts) recebe exatamente os mesmos enums;
  - INSUFFICIENT_DATA e CLOSING_BEFORE_ENTRY atravessam a API.
"""
from __future__ import annotations

import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

from betgsn.api import schemas as S
from betgsn.api.service import clv_status_to_api, movement_status_to_api
from betgsn.football_data_uk import UpcomingFixture
from betgsn.odds_normalize import event_key
from betgsn.odds_snapshots import (
    OddsObservation,
    OddsSnapshotStore,
)
from betgsn.timeutil import utc_key

MARKET = "Resultado Final (1X2)"
KICKOFF = "2030-01-01T12:00:00Z"


def _fixture() -> UpcomingFixture:
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


def _obs(key: str, odd: float, timestamp: str) -> OddsObservation:
    return OddsObservation(
        match_key=key, market=MARKET, outcome="1",
        bookmaker="Pinnacle", odd=odd, timestamp=timestamp,
        kickoff=KICKOFF, provider="The Odds API",
    )


def _patch_fixtures(monkeypatch, fixtures: list[UpcomingFixture]) -> None:
    from betgsn.football_data_uk import FootballDataClient

    monkeypatch.setattr(
        FootballDataClient, "load_fixtures", lambda self: list(fixtures)
    )
    monkeypatch.setattr(
        FootballDataClient, "corpus_signature", lambda self: "test-signature"
    )


def _patch_store(monkeypatch, db_path) -> None:
    import betgsn.odds_snapshots as snap_mod

    real = snap_mod.OddsSnapshotStore
    monkeypatch.setattr(
        snap_mod, "OddsSnapshotStore", lambda *a, **k: real(db_path)
    )


# ==========================================================================
# todos os estados do dominio têm representacao valida na API
# ==========================================================================


def test_every_domain_clv_status_reaches_the_api(tmp_path):
    """OK, NO_CLOSING_ODDS e CLOSING_BEFORE_ENTRY sao reais e mapeados."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    key = event_key("Arsenal", "Chelsea", utc_key(KICKOFF))
    store.add([_obs(key, 1.80, "2030-01-01T11:30:00Z")])

    ok = store.clv(key, MARKET, "1", entry_odd=1.95)
    assert ok.status == "OK"

    no_closing = store.clv(
        event_key("Sem", "Historico", utc_key(KICKOFF)), MARKET, "1",
        entry_odd=1.95,
    )
    assert no_closing.status == "NO_CLOSING_ODDS"

    before_entry = store.clv_prospective(
        key, MARKET, "1", entry_odd=1.95,
        entry_timestamp="2030-01-01T11:45:00Z",
    )
    assert before_entry.status == "CLOSING_BEFORE_ENTRY"
    assert before_entry.clv_percentage is None

    for result in (ok, no_closing, before_entry):
        mapped = clv_status_to_api(result.status)
        entry = S.ClvEntry(
            match="Arsenal vs Chelsea", market=MARKET, outcome="1",
            entry_odd=1.95, status=mapped,
        )
        assert entry.status == mapped


def test_every_domain_movement_state_reaches_the_api(tmp_path):
    """NO_DATA, INSUFFICIENT_DATA e OK (-> MOVING/STABLE) sao mapeados."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    key = event_key("Arsenal", "Chelsea", utc_key(KICKOFF))

    empty = store.movement(key, MARKET, "1")
    assert empty.status == "NO_DATA"

    store.add([_obs(key, 1.90, "2020-01-01T10:00:00Z")])
    single = store.movement(key, MARKET, "1")
    assert single.status == "INSUFFICIENT_DATA"

    store.add([_obs(key, 2.10, "2020-01-02T10:00:00Z")])
    pair = store.movement(key, MARKET, "1")
    assert pair.status == "OK"

    assert movement_status_to_api(
        empty.n_observations, empty.price_delta) == "NO_DATA"
    assert movement_status_to_api(
        single.n_observations, single.price_delta) == "INSUFFICIENT_DATA"
    assert movement_status_to_api(
        pair.n_observations, pair.price_delta) == "MOVING"
    assert movement_status_to_api(2, 0.0) == "STABLE"

    for status in ("NO_DATA", "INSUFFICIENT_DATA", "MOVING", "STABLE"):
        m = S.OddsMovement(match="x", market=MARKET, outcome="1", status=status)
        assert m.status == status


# ==========================================================================
# estados invalidos sao rejeitados
# ==========================================================================


def test_clv_status_mapping_rejects_unknown_statuses():
    for bad in ("BEFORE_OPENING", "MOVING", "ok", ""):
        with pytest.raises(ValueError, match="sem representacao"):
            clv_status_to_api(bad)


def test_api_schemas_reject_invalid_statuses():
    base_movement = dict(match="x", market=MARKET, outcome="1")
    with pytest.raises(ValidationError):
        S.OddsMovement(**base_movement, status="BEFORE_OPENING")
    with pytest.raises(ValidationError):
        S.OddsMovement(**base_movement, status="OK")
    with pytest.raises(ValidationError):
        S.OddsMovement(**base_movement, status="insufficient_data")

    base_clv = dict(match="x", market=MARKET, outcome="1", entry_odd=1.95)
    with pytest.raises(ValidationError):
        S.ClvEntry(**base_clv, status="BEFORE_OPENING")
    with pytest.raises(ValidationError):
        S.ClvEntry(**base_clv, status="NO_DATA")
    # NO_ENTRY_ODDS e valido e carrega entry_odd/entry_timestamp None:
    # ausencia de observacao PIT e ausencia explicita, nunca odd sintetica
    no_entry = S.ClvEntry(
        match="x", market=MARKET, outcome="1",
        entry_odd=None, entry_timestamp=None, status="NO_ENTRY_ODDS",
    )
    assert no_entry.status == "NO_ENTRY_ODDS"
    assert no_entry.entry_odd is None


# ==========================================================================
# INSUFFICIENT_DATA atravessa a API (nunca colapsa em STABLE)
# ==========================================================================


def test_movement_single_observation_is_insufficient_data(
    monkeypatch, tmp_path
):
    """Uma observacao so nao prova que o preco esta estavel."""
    from betgsn.api.service import BetgsnService

    fixture = _fixture()
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    store.add([_obs(fixture.event_key, 1.90, "2020-01-01T10:00:00Z")])
    _patch_fixtures(monkeypatch, [fixture])
    _patch_store(monkeypatch, db)

    overview = BetgsnService().movement()
    entry = next(
        m for m in overview.movements
        if m.market == MARKET and m.outcome == "1"
    )
    assert entry.status == "INSUFFICIENT_DATA"
    assert entry.n_observations == 1
    # as demais linhas, sem nenhuma observacao, seguem NO_DATA
    others = [m for m in overview.movements if m.outcome != "1"]
    assert others and all(m.status == "NO_DATA" for m in others)


# ==========================================================================
# CLOSING_BEFORE_ENTRY atravessa a API (fechamento anterior a entrada)
# ==========================================================================


def test_clv_endpoint_carries_closing_before_entry(monkeypatch, tmp_path):
    """O estado prospectivo do dominio chega inteiro ao consumidor da API.

    Pelo caminho REAL (I-02): a entrada e registrada pelo proprio store
    (`register_entry`, FIRST-WINS) a partir da observacao de 11:30 —
    dentro da janela de 120min do kickoff 12:00. O fechamento encontrado
    (11:30) NAO e posterior a entrada (11:30): o cenario exato que
    `clv_prospective` protege — CLV nao pode ser atribuido. Nenhum
    metodo do dominio e mockado.
    """
    from betgsn.api.service import BetgsnService

    fixture = _fixture()
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    store.add([_obs(fixture.event_key, 1.80, "2030-01-01T11:30:00Z")])
    _patch_fixtures(monkeypatch, [fixture])
    _patch_store(monkeypatch, db)

    # registro real: line_at no instante da decisao (11:45) + congelamento
    line = store.line_at(
        fixture.event_key, MARKET, "1", "2030-01-01T11:45:00Z")
    assert line is not None
    assert store.register_entry(
        match_key=fixture.event_key, market=MARKET, outcome="1",
        entry_odd=line.odd, entry_timestamp=line.timestamp,
        entry_n_books=line.n_books, kickoff=KICKOFF,
        prediction_timestamp="2030-01-01T11:45:00Z",
    )

    report = BetgsnService().clv()
    entry = next(
        e for e in report.entries
        if e.market == MARKET and e.outcome == "1"
    )
    assert entry.status == "CLOSING_BEFORE_ENTRY"
    assert entry.closing_odd == 1.80
    assert entry.clv_percentage is None


# ==========================================================================
# frontend: os enums de types/api.ts sao os mesmos do schema
# ==========================================================================


def _ts_union(model, field: str) -> str:
    literals = typing.get_args(model.model_fields[field].annotation)
    return " | ".join(f'"{lit}"' for lit in literals)


def test_frontend_types_mirror_api_status_enums():
    ts_path = (
        Path(__file__).resolve().parents[1]
        / "web" / "src" / "types" / "api.ts"
    )
    ts = ts_path.read_text(encoding="utf-8")

    assert f"status: {_ts_union(S.OddsMovement, 'status')};" in ts
    assert f"status: {_ts_union(S.ClvEntry, 'status')};" in ts
    # o estado fantasma (nunca produzido pelo dominio) nao pode voltar
    assert "BEFORE_OPENING" not in ts
