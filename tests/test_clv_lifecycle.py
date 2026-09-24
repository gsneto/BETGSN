"""Ciclo de vida do CLV prospectivo — PENDING/NO_CLOSE/CLOSED/INVALID/MISMATCH.

O estado operacional de uma entrada de CLV tem que distinguir:

  - PENDING: kickoff no futuro — o fechamento AINDA pode chegar;
  - NO_CLOSE: kickoff passou sem fechamento valido — NUNCA chegou;
  - CLOSED: fechamento valido apos a entrada — CLV calculado;
  - INVALID: dado inconsistente (fechamento antes da entrada, odd
    invalida, timestamps fora de ordem);
  - MISMATCH: a entrada nao resolve a nenhuma observacao do store.

Ausencia de fechamento NUNCA vira CLV=0. O preco de execucao comeca
UNKNOWN e nao pode ser presumido igual ao preco observado.
"""

from __future__ import annotations

import sqlite3

import pytest

from betgsn.odds_normalize import event_key
from betgsn.odds_snapshots import (
    CLV_ENTRY_SOURCE,
    CLV_LIFECYCLE_STATES,
    OddsObservation,
    OddsSnapshotStore,
)
from betgsn.timeutil import utc_key

MARKET = "Resultado Final (1X2)"
KICKOFF = "2030-01-01T12:00:00Z"
KEY = event_key("Arsenal", "Chelsea", utc_key(KICKOFF))


def _obs(outcome: str, book: str, odd: float, ts: str,
         kickoff: str = KICKOFF, market: str = MARKET) -> OddsObservation:
    return OddsObservation(
        match_key=KEY, market=market, outcome=outcome, bookmaker=book,
        odd=odd, timestamp=ts, kickoff=kickoff, provider="The Odds API",
    )


def _register(store: OddsSnapshotStore, *, entry_odd: float = 2.00,
              entry_ts: str = "2030-01-01T09:55:00Z", kickoff: str = KICKOFF,
              prediction_ts: str = "2030-01-01T10:00:00Z",
              home: str = "Arsenal", away: str = "Chelsea",
              league: str = "Premier League (England)",
              bookmaker: str = "Pinnacle") -> None:
    assert store.register_entry(
        match_key=KEY, market=MARKET, outcome="1",
        entry_odd=entry_odd, entry_timestamp=entry_ts, entry_n_books=3,
        kickoff=kickoff, prediction_timestamp=prediction_ts,
        source=CLV_ENTRY_SOURCE,
        home=home, away=away, league=league, entry_bookmaker=bookmaker,
    )


# ==========================================================================
# estados do ciclo de vida
# ==========================================================================


def test_lifecycle_pending_before_kickoff(tmp_path):
    """Kickoff no futuro: sem fechamento AINDA — PENDING, nunca CLV=0."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    store.add([_obs("1", "Pinnacle", 2.00, "2030-01-01T09:55:00Z")])
    _register(store)
    rec = store.clv_entries()[0]

    lc = store.clv_lifecycle(rec, now="2030-01-01T10:30:00Z")
    assert lc.state == "PENDING"
    assert lc.result is None or lc.result.clv_percentage is None
    assert "futuro" in lc.detail


def test_lifecycle_no_close_after_kickoff(tmp_path):
    """Kickoff passou, observacao fora da janela de fechamento: NO_CLOSE."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    # unica observacao: 09:55 (fora da janela de 120min do kickoff 12:00)
    store.add([_obs("1", "Pinnacle", 2.00, "2030-01-01T09:55:00Z")])
    _register(store)
    rec = store.clv_entries()[0]

    lc = store.clv_lifecycle(rec, now="2030-01-01T13:00:00Z")
    assert lc.state == "NO_CLOSE"
    assert lc.result is None or lc.result.clv_percentage is None


def test_lifecycle_closed_with_valid_close(tmp_path):
    store = OddsSnapshotStore(tmp_path / "odds.db")
    store.add([
        _obs("1", "Pinnacle", 2.20, "2030-01-01T09:55:00Z"),
        _obs("1", "Pinnacle", 2.00, "2030-01-01T11:30:00Z"),
    ])
    _register(store, entry_odd=2.20)
    rec = store.clv_entries()[0]

    lc = store.clv_lifecycle(rec, now="2030-01-01T13:00:00Z")
    assert lc.state == "CLOSED"
    assert lc.result is not None
    assert lc.result.clv_percentage == pytest.approx(0.10, abs=1e-6)


def test_lifecycle_invalid_when_closing_before_entry(tmp_path):
    """Fechamento ANTERIOR a entrada: aposta pos-fechamento — INVALID."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    store.add([_obs("1", "Pinnacle", 1.80, "2030-01-01T11:30:00Z")])
    _register(store, entry_odd=1.80, entry_ts="2030-01-01T11:30:00Z",
              prediction_ts="2030-01-01T11:45:00Z")
    rec = store.clv_entries()[0]

    lc = store.clv_lifecycle(rec, now="2030-01-01T13:00:00Z")
    assert lc.state == "INVALID"
    assert "fechamento" in lc.detail.lower()


def test_lifecycle_invalid_malformed_odd(tmp_path):
    """Dado inconsistente (odd <= 1) e classificado, nao explode."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    rec = store.clv_entries  # apenas para lembrar que o store esta vazio
    from betgsn.odds_snapshots import ClvEntryRecord

    bad = ClvEntryRecord(
        id=1, match_key=KEY, market=MARKET, outcome="1",
        entry_odd=0.5, entry_timestamp="2030-01-01T09:55:00Z",
        entry_n_books=1, kickoff=KICKOFF,
        prediction_timestamp="2030-01-01T10:00:00Z",
        source=CLV_ENTRY_SOURCE, created_at="2030-01-01T10:00:01Z",
    )
    lc = store.clv_lifecycle(bad, now="2030-01-01T13:00:00Z")
    assert lc.state == "INVALID"
    assert lc.result is None


def test_lifecycle_invalid_entry_at_or_after_kickoff(tmp_path):
    from betgsn.odds_snapshots import ClvEntryRecord

    store = OddsSnapshotStore(tmp_path / "odds.db")
    bad = ClvEntryRecord(
        id=1, match_key=KEY, market=MARKET, outcome="1",
        entry_odd=2.00, entry_timestamp=KICKOFF,
        entry_n_books=1, kickoff=KICKOFF,
        prediction_timestamp="2030-01-01T10:00:00Z",
        source=CLV_ENTRY_SOURCE, created_at="2030-01-01T10:00:01Z",
    )
    lc = store.clv_lifecycle(bad, now="2030-01-01T13:00:00Z")
    assert lc.state == "INVALID"


def test_lifecycle_mismatch_without_observations(tmp_path):
    """Entrada que nao resolve a nenhuma observacao da linha: MISMATCH."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    # registro SEM nenhuma observacao no store para aquela linha
    _register(store)
    rec = store.clv_entries()[0]

    lc = store.clv_lifecycle(rec, now="2030-01-01T13:00:00Z")
    assert lc.state == "MISMATCH"
    assert "identidade" in lc.detail


def test_all_lifecycle_states_declared():
    assert CLV_LIFECYCLE_STATES == (
        "PENDING", "NO_CLOSE", "CLOSED", "INVALID", "MISMATCH",
    )


# ==========================================================================
# sweep operacional: idempotente, classifica tudo
# ==========================================================================


def test_sweep_classifies_and_is_idempotent(tmp_path):
    store = OddsSnapshotStore(tmp_path / "odds.db")
    # entrada 1: fecha (CLOSED)
    store.add([
        _obs("1", "Pinnacle", 2.20, "2030-01-01T09:55:00Z"),
        _obs("1", "Pinnacle", 2.00, "2030-01-01T11:30:00Z"),
    ])
    _register(store, entry_odd=2.20)

    first = store.clv_lifecycle_sweep(now="2030-01-01T13:00:00Z")
    second = store.clv_lifecycle_sweep(now="2030-01-01T13:00:00Z")

    assert first.n_entries == 1
    assert first.by_state["CLOSED"] == 1
    # idempotente: leitura pura, mesmo resultado, nenhuma duplicata
    assert second.by_state == first.by_state
    assert second.n_entries == first.n_entries == len(store.clv_entries())
    assert first.evaluated_at == "2030-01-01T13:00:00Z"


def test_sweep_pending_then_closed_as_observations_arrive(tmp_path):
    """Ciclo operacional: PENDING -> CLOSED quando o fechamento chega."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    store.add([_obs("1", "Pinnacle", 2.20, "2030-01-01T09:55:00Z")])
    _register(store, entry_odd=2.20)

    before = store.clv_lifecycle_sweep(now="2030-01-01T10:30:00Z")
    assert before.by_state["PENDING"] == 1

    # captura posterior: fechamento chega ao store
    store.add([_obs("1", "Pinnacle", 2.00, "2030-01-01T11:30:00Z")])
    after = store.clv_lifecycle_sweep(now="2030-01-01T13:00:00Z")
    assert after.by_state["CLOSED"] == 1
    assert after.by_state["PENDING"] == 0


def test_sweep_summary_to_dict(tmp_path):
    store = OddsSnapshotStore(tmp_path / "odds.db")
    store.add([_obs("1", "Pinnacle", 2.00, "2030-01-01T09:55:00Z")])
    _register(store)
    payload = store.clv_lifecycle_sweep(now="2030-01-01T10:30:00Z").to_dict()
    assert payload["by_state"]["PENDING"] == 1
    assert payload["entries"][0]["execution_status"] == "UNKNOWN"


# ==========================================================================
# proveniência (schema v4) e executabilidade
# ==========================================================================


def test_register_entry_persists_provenance(tmp_path):
    store = OddsSnapshotStore(tmp_path / "odds.db")
    _register(store, bookmaker="Bet365")
    rec = store.clv_entries()[0]
    assert rec.home == "Arsenal"
    assert rec.away == "Chelsea"
    assert rec.league == "Premier League (England)"
    assert rec.entry_bookmaker == "Bet365"
    assert rec.execution_status == "UNKNOWN"


def test_register_entry_first_wins_keeps_provenance(tmp_path):
    store = OddsSnapshotStore(tmp_path / "odds.db")
    _register(store, home="Arsenal", away="Chelsea", bookmaker="Pinnacle")
    # segunda chamada com proveniencia DIFERENTE: FIRST-WINS congela tudo
    assert store.register_entry(
        match_key=KEY, market=MARKET, outcome="1",
        entry_odd=2.50, entry_timestamp="2030-01-01T09:58:00Z",
        entry_n_books=1, kickoff=KICKOFF,
        prediction_timestamp="2030-01-01T10:00:00Z",
        source=CLV_ENTRY_SOURCE,
        home="Outro", away="Time", entry_bookmaker="Outra casa",
    ) is False
    rec = store.clv_entries()[0]
    assert rec.home == "Arsenal"
    assert rec.entry_bookmaker == "Pinnacle"
    assert rec.entry_odd == 2.00


def test_register_entry_rejects_fake_execution_status(tmp_path):
    """Execucao nao pode ser atestada no registro: so UNKNOWN e aceito."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    with pytest.raises(ValueError, match="UNKNOWN"):
        store.register_entry(
            match_key=KEY, market=MARKET, outcome="1",
            entry_odd=2.00, entry_timestamp="2030-01-01T09:55:00Z",
            entry_n_books=1, kickoff=KICKOFF,
            prediction_timestamp="2030-01-01T10:00:00Z",
            execution_status="EXECUTED",
        )


def test_backwards_register_entry_without_provenance(tmp_path):
    """Chamadores antigos (sem kwargs novos) continuam funcionando."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    assert store.register_entry(
        match_key=KEY, market=MARKET, outcome="1",
        entry_odd=2.00, entry_timestamp="2030-01-01T09:55:00Z",
        entry_n_books=1, kickoff=KICKOFF,
        prediction_timestamp="2030-01-01T10:00:00Z",
    )
    rec = store.clv_entries()[0]
    assert rec.home == ""
    assert rec.entry_bookmaker == ""
    assert rec.execution_status == "UNKNOWN"


# ==========================================================================
# migração v3 -> v4 (ALTER TABLE, bases existentes)
# ==========================================================================


def test_v3_database_migrates_to_v4(tmp_path):
    """Base v3 (sem colunas de proveniencia) e migrada, nao descartada."""
    db = tmp_path / "v3.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE clv_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_key TEXT NOT NULL,
            market TEXT NOT NULL,
            outcome TEXT NOT NULL,
            entry_odd REAL NOT NULL,
            entry_timestamp TEXT NOT NULL,
            entry_n_books INTEGER NOT NULL,
            kickoff TEXT NOT NULL,
            prediction_timestamp TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(match_key, market, outcome, source)
        );
        """
    )
    conn.execute(
        """INSERT INTO clv_entries
            (match_key, market, outcome, entry_odd, entry_timestamp,
             entry_n_books, kickoff, prediction_timestamp, source, created_at)
         VALUES (?, ?, ?, 2.0, '2030-01-01T09:55:00Z', 1, ?,
                 '2030-01-01T10:00:00Z', 'real_signal_report',
                 '2030-01-01T10:00:01Z')""",
        (KEY, MARKET, "1", KICKOFF),
    )
    conn.commit()
    conn.close()

    store = OddsSnapshotStore(db)
    # colunas novas existem
    cols = {
        r["name"] for r in store._conn().execute(
            "PRAGMA table_info(clv_entries)")
    }
    store._conn().close()
    assert {"home", "away", "league", "entry_bookmaker",
            "execution_status"} <= cols
    # linha antiga legivel, com defaults honestos (vazio/UNKNOWN)
    rec = store.clv_entries()[0]
    assert rec.entry_odd == 2.0
    assert rec.home == ""
    assert rec.execution_status == "UNKNOWN"
    # schema version atualizada
    version = store._conn().execute(
        "SELECT value FROM schema_meta WHERE key='version'").fetchone()
    store._conn().close()
    assert version["value"] == "4"
