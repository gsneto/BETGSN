"""Testes de quota-aware capture, CLV progress/dataset e integridade temporal."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# --------------------------------------------------------------------------
# QuotaScheduler
# --------------------------------------------------------------------------


def test_quota_classifica_estados():
    from betgsn.quota_scheduler import (
        STATE_AUTH_ERROR, STATE_AVAILABLE, STATE_EXHAUSTED, STATE_RATE_LIMITED,
        classify_provider,
    )

    assert classify_provider({"state": "HEALTHY"}) == STATE_AVAILABLE
    assert classify_provider({
        "state": "UNAVAILABLE", "last_status": 401,
        "last_error": "OUT_OF_USAGE_CREDITS quota has been reached",
    }) == STATE_EXHAUSTED
    assert classify_provider({
        "state": "UNAVAILABLE", "last_status": 401,
        "last_error": "Invalid or inactive API key",
    }) == STATE_AUTH_ERROR
    assert classify_provider({
        "state": "UNAVAILABLE", "last_status": 429, "last_error": "slow down",
    }) == STATE_RATE_LIMITED
    # NO_COVERAGE respondeu: não é falha de quota/auth
    assert classify_provider({"state": "NO_COVERAGE"}) == STATE_AVAILABLE


def test_quota_scheduler_bloqueia_exhausted_ate_cooldown():
    from betgsn.quota_scheduler import STATE_EXHAUSTED, QuotaScheduler

    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)
    health = {
        "The Odds API": {
            "state": "UNAVAILABLE", "last_status": 401,
            "last_error": "OUT_OF_USAGE_CREDITS",
            "last_failure_at": (now - timedelta(minutes=10)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
        },
    }
    sched = QuotaScheduler(health, now=now)
    assert sched.status("The Odds API").state == STATE_EXHAUSTED
    assert sched.should_attempt("The Odds API") is False
    # após o cooldown (6h), tenta de novo
    later = now + timedelta(hours=7)
    sched2 = QuotaScheduler(health, now=later)
    assert sched2.should_attempt("The Odds API") is True


def test_quota_scheduler_auth_nao_se_resolve_rapido():
    from betgsn.quota_scheduler import STATE_AUTH_ERROR, QuotaScheduler

    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)
    health = {"P": {
        "state": "UNAVAILABLE", "last_status": 401,
        "last_error": "Invalid or inactive API key",
        "last_failure_at": (now - timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
    }}
    sched = QuotaScheduler(health, now=now)
    assert sched.status("P").state == STATE_AUTH_ERROR
    assert sched.should_attempt("P") is False  # cooldown de 24h


def test_quota_scheduler_overall_waiting_quando_todos_bloqueados():
    from betgsn.quota_scheduler import (
        OVERALL_WAITING, QuotaScheduler,
    )

    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    health = {
        "The Odds API": {"state": "UNAVAILABLE", "last_status": 401,
                         "last_error": "OUT_OF_USAGE_CREDITS",
                         "last_failure_at": stamp},
        "ParlayAPI": {"state": "UNAVAILABLE", "last_status": 403,
                      "last_error": "credit limit reached",
                      "last_failure_at": stamp},
        "OddsPapi": {"state": "UNAVAILABLE", "last_status": 429,
                     "last_error": "request limit exceeded",
                     "last_failure_at": stamp},
    }
    sched = QuotaScheduler(health, now=now)
    overall = sched.overall(list(health))
    assert overall["status"] == OVERALL_WAITING
    assert overall["attemptable"] == []


def test_quota_scheduler_ready_com_um_attemptable():
    from betgsn.quota_scheduler import OVERALL_READY, QuotaScheduler

    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)
    health = {
        "A": {"state": "UNAVAILABLE", "last_status": 401,
              "last_error": "OUT_OF_USAGE_CREDITS",
              "last_failure_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
        "B": {"state": "HEALTHY"},
    }
    sched = QuotaScheduler(health, now=now)
    assert sched.overall(["A", "B"])["status"] == OVERALL_READY


def test_quota_scheduler_provider_sem_observacao_e_tentavel():
    from betgsn.quota_scheduler import QuotaScheduler

    sched = QuotaScheduler({})
    assert sched.should_attempt("novo") is True


# --------------------------------------------------------------------------
# CLV progress + dataset
# --------------------------------------------------------------------------


def test_clv_progress_n_zero_e_insufficient(tmp_path):
    from betgsn.clv_dataset import clv_progress
    from betgsn.odds_snapshots import OddsSnapshotStore

    store = OddsSnapshotStore(tmp_path / "odds.db")
    prog = clv_progress(store)
    assert prog["closed"] == 0
    assert prog["target"] == 200
    assert prog["remaining"] == 200
    assert prog["status"] == "CLV_INSUFFICIENT_DATA"


def test_clv_dataset_separa_referencias(tmp_path):
    from betgsn.clv_dataset import clv_dataset
    from betgsn.odds_snapshots import (
        CLV_ENTRY_SOURCE, OddsObservation, OddsSnapshotStore,
    )

    store = OddsSnapshotStore(tmp_path / "odds.db")
    # kickoff no PASSADO: o sweep usa o relógio real e só fecha o que já
    # passou do kickoff.
    kickoff = "2026-09-20T19:00:00Z"
    match = "Lens|Lyon|2026-09-20T19:00:00Z"
    # observações: entrada (1h antes) + fechamento (30min antes)
    obs = [
        OddsObservation(match_key=match, market="Resultado Final (1X2)",
                        outcome="1", bookmaker="Pinnacle", odd=2.00,
                        timestamp="2026-09-20T18:00:00Z", kickoff=kickoff),
        OddsObservation(match_key=match, market="Resultado Final (1X2)",
                        outcome="1", bookmaker="Betfair", odd=2.02,
                        timestamp="2026-09-20T18:00:00Z", kickoff=kickoff),
        OddsObservation(match_key=match, market="Resultado Final (1X2)",
                        outcome="1", bookmaker="Pinnacle", odd=1.90,
                        timestamp="2026-09-20T18:30:00Z", kickoff=kickoff),
        OddsObservation(match_key=match, market="Resultado Final (1X2)",
                        outcome="1", bookmaker="Betfair", odd=1.92,
                        timestamp="2026-09-20T18:30:00Z", kickoff=kickoff),
    ]
    store.add(obs)
    store.register_entry(
        match_key=match, market="Resultado Final (1X2)", outcome="1",
        entry_odd=2.00, entry_timestamp="2026-09-20T18:00:00Z",
        entry_n_books=2, kickoff=kickoff,
        prediction_timestamp="2026-09-20T18:00:00Z",
        source=CLV_ENTRY_SOURCE,
    )
    ds = clv_dataset(store)
    assert ds["fingerprint"]
    rows = {r["event_key"]: r for r in ds["rows"]}
    assert match in rows
    refs = rows[match]["reference_closes"]
    assert "bookmaker_close" in refs
    assert "exchange_close" in refs
    assert "consensus_close" in refs
    # fechamento real presente → estado CLOSED
    assert rows[match]["state"] == "CLOSED"
    assert rows[match]["clv"] is not None


def test_clv_dataset_sem_close_nao_inventa(tmp_path):
    from betgsn.clv_dataset import clv_dataset
    from betgsn.odds_snapshots import (
        CLV_ENTRY_SOURCE, OddsObservation, OddsSnapshotStore,
    )

    store = OddsSnapshotStore(tmp_path / "odds.db")
    kickoff = "2026-09-28T19:00:00Z"
    match = "Lens|Lyon|2026-09-28T19:00:00Z"
    store.add([OddsObservation(
        match_key=match, market="Resultado Final (1X2)", outcome="1",
        bookmaker="Pinnacle", odd=2.00,
        timestamp="2026-09-28T18:00:00Z", kickoff=kickoff)])
    store.register_entry(
        match_key=match, market="Resultado Final (1X2)", outcome="1",
        entry_odd=2.00, entry_timestamp="2026-09-28T18:00:00Z",
        entry_n_books=1, kickoff=kickoff,
        prediction_timestamp="2026-09-28T18:00:00Z",
        source=CLV_ENTRY_SOURCE,
    )
    ds = clv_dataset(store)
    row = ds["rows"][0]
    # sem fechamento válido, CLV é None (nunca 0)
    assert row["clv"] is None
    assert row["closing_price"] is None
    assert row["state"] in ("PENDING", "NO_CLOSE", "MISMATCH")


# --------------------------------------------------------------------------
# integridade temporal no replay (future quote)
# --------------------------------------------------------------------------


def _quote(book, price, ts):
    from betgsn.odds_normalize import NormalizedQuote

    return NormalizedQuote(
        event_id="Lens|Lyon|2026-09-28T19:00:00Z", provider="P",
        sport_key="s", league="L", home_team="Lens", away_team="Lyon",
        kickoff="2026-09-28T19:00:00Z", bookmaker=book,
        market="Resultado Final (1X2)", selection="1", price=price,
        timestamp=ts, line=None,
    )


@pytest.mark.parametrize("stamp", [
    "2026-09-24T12:00:01Z",   # +1s
    "2026-09-24T12:00:30Z",   # +30s
    "2026-09-24T12:02:00Z",   # +120s
    "2026-09-25T12:00:00Z",   # +1d
])
def test_replay_nunca_usa_quote_futura(stamp):
    from betgsn.realtime.state import MarketState

    now = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
    state = MarketState()
    events, lines, problems = state.apply([_quote("Pinnacle", 2.1, stamp)],
                                          now=now)
    assert not events and not lines
    assert problems and problems[0].reason == "FUTURE_TIMESTAMP"


def test_state_ordena_por_timestamp_e_nao_aceita_regressao():
    from betgsn.realtime.state import MarketState

    now = datetime(2026, 9, 24, 12, 5, 0, tzinfo=timezone.utc)
    state = MarketState()
    state.apply([_quote("A", 2.10, "2026-09-24T12:00:00Z")], now=now)
    # quote mais antiga não substitui a mais nova
    state.apply([_quote("A", 2.20, "2026-09-24T11:00:00Z")], now=now)
    latest = state.event_quotes("Lens|Lyon|2026-09-28T19:00:00Z")
    assert latest[0].price == 2.10
    assert latest[0].timestamp == "2026-09-24T12:00:00Z"


# --------------------------------------------------------------------------
# alpha ablation (line shopping separado de edge)
# --------------------------------------------------------------------------


def test_line_shopping_ablation_mede_advantage_e_persistence():
    from betgsn.alpha_lab import ForwardObservation, line_shopping_ablation

    def obs(best, median, ref_move, med_move):
        return ForwardObservation(
            signal_type="BEST_PRICE_GAP", alpha_id="best_price_gap",
            event_key="e", market="m", selection="s", bookmaker="A",
            signal_timestamp="t", observed_at="t", decision_price=best,
            median_price=median, best_price=best, book_count=3,
            dispersion_ratio=0.05, deviation=0.0, direction="FLAT",
            seconds_to_kickoff=3600.0, market_move={300: med_move},
            reference_move={300: ref_move}, closing_price=None,
            closing_move=None, market_followed=None, converged=True)

    rows = [obs(2.20, 2.00, 0.01, 0.0), obs(2.20, 2.00, -0.01, 0.0)]
    out = line_shopping_ablation(rows)["BEST_PRICE_GAP"]
    assert out["n"] == 2
    assert out["entry_advantage_pct"] == pytest.approx(0.10, abs=1e-6)
    assert out["best_persistence_5m"] == pytest.approx(0.5)


def test_quota_scheduler_persiste_no_store(tmp_path):
    """O health persistido alimenta o scheduler entre processos."""
    from betgsn.odds_snapshots import OddsSnapshotStore
    from betgsn.quota_scheduler import QuotaScheduler, STATE_EXHAUSTED

    store = OddsSnapshotStore(tmp_path / "odds.db")
    store.save_provider_health(
        {"The Odds API": {
            "state": "UNAVAILABLE", "consecutive_failures": 21,
            "total_failures": 21, "total_successes": 0,
            "last_success_at": "", "last_failure_at": "2026-09-25T12:00:00Z",
            "last_error": "HTTP 401 ... OUT_OF_USAGE_CREDITS",
            "last_status": 401, "last_kind": "AUTH",
            "credits_remaining": None, "observations": 0,
            "updated_at": "2026-09-25T12:00:00Z", "latency_ms": None,
        }},
        {"The Odds API": {"provider": "The Odds API", "exhausted": True}},
    )
    sched = QuotaScheduler.from_store(
        store, now=datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc))
    assert sched.status("The Odds API").state == STATE_EXHAUSTED
    assert sched.should_attempt("The Odds API") is False


def test_clv_progress_nao_converte_pending_em_closed(tmp_path):
    """PENDING nunca vira CLOSED nem CLV=0."""
    from betgsn.clv_dataset import clv_dataset, clv_progress
    from betgsn.odds_snapshots import (
        CLV_ENTRY_SOURCE, OddsObservation, OddsSnapshotStore,
    )

    store = OddsSnapshotStore(tmp_path / "odds.db")
    kickoff = "2099-01-01T19:00:00Z"  # futuro
    match = "A|B|2099-01-01T19:00:00Z"
    store.add([OddsObservation(
        match_key=match, market="Resultado Final (1X2)", outcome="1",
        bookmaker="Pinnacle", odd=2.0, timestamp="2098-12-31T18:00:00Z",
        kickoff=kickoff)])
    store.register_entry(
        match_key=match, market="Resultado Final (1X2)", outcome="1",
        entry_odd=2.0, entry_timestamp="2098-12-31T18:00:00Z",
        entry_n_books=1, kickoff=kickoff,
        prediction_timestamp="2098-12-31T18:00:00Z", source=CLV_ENTRY_SOURCE)
    prog = clv_progress(store)
    assert prog["closed"] == 0
    assert prog["pending"] == 1
    assert prog["status"] == "CLV_INSUFFICIENT_DATA"
    ds = clv_dataset(store)
    assert ds["rows"][0]["clv"] is None
