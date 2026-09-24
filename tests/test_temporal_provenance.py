"""Auditoria temporal ponta a ponta — CAPTURE → … → RESULTADO.

A regra central: uma decisão em T só pode usar informação disponível
em <= T, e precisa ser RASTREÁVEL até as quotes que a sustentaram.

Este arquivo ataca a cadeia no ponto onde ela é mais fácil de quebrar
sem ninguém notar:

    CAPTURE (odds observadas) -> TIMESTAMP (da observação, não de now)
    -> PERSISTÊNCIA (store append-only) -> SELEÇÃO (line_at PIT)
    -> DECISÃO (register_entry FIRST-WINS) -> CLV (fechamento posterior)

Cada teste re-deriva a decisão a partir das observações cruas do store
e compara: se a entrada não puder ser reconstruída das quotes com
timestamp <= decisão, o teste falha — look-ahead ou perda de
rastreabilidade, os dois são bug.
"""

from __future__ import annotations

import statistics

import pytest

from betgsn.api.service import BetgsnService
from betgsn.football_data_uk import UpcomingFixture
from betgsn.model import TeamRating
from betgsn.odds_normalize import event_key
from betgsn.odds_snapshots import OddsObservation, OddsSnapshotStore
from betgsn.real_signals import RealSignalsService, RealSnapshot
from betgsn.timeutil import utc_key

MARKET = "Resultado Final (1X2)"
KICKOFF = "2030-01-01T12:00:00Z"
KEY = event_key("Arsenal", "Chelsea", utc_key(KICKOFF))
DECISION_TS = "2030-01-01T10:00:00Z"


def _rating(name: str) -> TeamRating:
    return TeamRating(
        name=name, attack=1.1, defense=0.9,
        goals_for=1.5, goals_against=1.0, xg_for=1.4, xg_against=1.1,
        corners_for=5.0, corners_against=4.5, cards_for=2.2, cards_against=2.4,
        shots_for=12.0, shots_on_target_for=4.2, form_points=1.5,
        matches_played=20,
    )


def _fixture() -> UpcomingFixture:
    books = {
        "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
        "Bet365": {"1": 1.95, "X": 3.35, "2": 4.10},
        "William Hill": {"1": 2.05, "X": 3.30, "2": 4.00},
    }
    best = {
        oc: max(b[oc] for b in books.values())
        for oc in ("1", "X", "2")
    }
    who = {
        oc: next(b for b, prices in books.items() if prices[oc] == best[oc])
        for oc in ("1", "X", "2")
    }
    return UpcomingFixture(
        division="E0", league="Premier League (England)",
        date="2030-01-01", time="12:00", timezone="UTC",
        home="Arsenal", away="Chelsea",
        odds={MARKET: books}, best_odds={MARKET: best},
        best_books={MARKET: who},
    )


def _obs(outcome: str, book: str, odd: float, ts: str) -> OddsObservation:
    return OddsObservation(
        match_key=KEY, market=MARKET, outcome=outcome, bookmaker=book,
        odd=odd, timestamp=ts, kickoff=KICKOFF, provider="The Odds API",
    )


def _isolate(monkeypatch, tmp_path, fixtures, generated_at, db):
    from betgsn import odds_snapshots as snap_mod
    from betgsn import real_signals as rs
    from betgsn.football_data_uk import FootballDataClient

    real_cls = snap_mod.OddsSnapshotStore
    monkeypatch.setattr(
        snap_mod, "OddsSnapshotStore", lambda *a, **k: real_cls(db))
    monkeypatch.setattr(
        FootballDataClient, "load_fixtures", lambda self: list(fixtures))
    monkeypatch.setattr(
        FootballDataClient, "corpus_signature", lambda self: "prov-test")

    snap = RealSnapshot(
        fixtures=fixtures,
        ratings={"Arsenal": _rating("Arsenal"), "Chelsea": _rating("Chelsea")},
        league_goals=2.7, teams=["Arsenal", "Chelsea"],
        n_history=100, history_window=("2029-01-01", "2030-01-01"),
        generated_at=generated_at, computed_in_ms=0.0,
        sources=["controlled-test"],
    )
    svc = RealSignalsService()
    monkeypatch.setattr(svc, "snapshot", lambda **kwargs: snap)
    monkeypatch.setattr(
        rs.real_signals_service, "report", lambda **kw: svc.report(**kw))


# ==========================================================================
# rastreabilidade: decisão -> quotes que a sustentaram
# ==========================================================================


def test_every_entry_is_reconstructible_from_pit_quotes(monkeypatch, tmp_path):
    """Cada entrada congelada deve ser RECONSTRUÍDA das observações com
    timestamp <= decisão: a mediana das últimas por casa naquele
    instante. Se a entrada divergir, ou houve look-ahead, ou a decisão
    perdeu rastreabilidade."""
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    # três casas, timestamps distintos, todos <= decisão (10:00)
    store.add([
        _obs("1", "Pinnacle", 1.50, "2030-01-01T09:30:00Z"),
        _obs("1", "Bet365", 2.00, "2030-01-01T09:45:00Z"),
        _obs("1", "William Hill", 6.00, "2030-01-01T09:15:00Z"),
        # quote POSTERIOR a decisão: nao pode sustentar a entrada
        _obs("1", "Betfair", 9.00, "2030-01-01T10:30:00Z"),
    ])
    _isolate(monkeypatch, tmp_path, [_fixture()], DECISION_TS, db)
    BetgsnService(source="real").real_signal_report(market_keys=["1x2"])

    entries = OddsSnapshotStore(db).clv_entries()
    rec = next(r for r in entries
               if (r.market, r.outcome) == (MARKET, "1"))

    # re-derivação INDEPENDENTE: ultimas observações por casa <= decisão
    obs = [
        o for o in OddsSnapshotStore(db).all_observations(KEY)
        if o.market == MARKET and o.outcome == "1"
        and o.timestamp <= DECISION_TS
    ]
    assert obs, "sem quotes PIT a entrada nem existiria"
    latest: dict[str, OddsObservation] = {}
    for o in obs:
        latest[o.bookmaker] = o
    expected_median = statistics.median(o.odd for o in latest.values())
    assert rec.entry_odd == pytest.approx(expected_median, abs=1e-9)
    assert rec.entry_n_books == len(latest)
    # a quote post-decisão (Betfair 9.00) nao sustentou nada
    assert rec.entry_odd != 9.00


def test_no_entry_timestamp_after_decision(monkeypatch, tmp_path):
    """Varredura global: NENHUMA entrada tem timestamp > decisão."""
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    store.add([
        _obs("1", "Pinnacle", 2.00, "2030-01-01T09:30:00Z"),
        _obs("X", "Pinnacle", 3.40, "2030-01-01T09:30:00Z"),
        _obs("2", "Pinnacle", 4.20, "2030-01-01T09:30:00Z"),
    ])
    _isolate(monkeypatch, tmp_path, [_fixture()], DECISION_TS, db)
    BetgsnService(source="real").real_signal_report(market_keys=["1x2"])

    for rec in OddsSnapshotStore(db).clv_entries():
        assert utc_key(rec.entry_timestamp) <= utc_key(rec.prediction_timestamp)
        assert utc_key(rec.entry_timestamp) < utc_key(rec.kickoff)


def test_decision_at_exact_quote_timestamp_is_inclusive(monkeypatch, tmp_path):
    """Quote no MESMO instante da decisão é visível (<=, não <):
    line_at usa timestamp <= at."""
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    store.add([_obs("1", "Pinnacle", 2.10, DECISION_TS)])
    line = store.line_at(KEY, MARKET, "1", DECISION_TS)
    assert line is not None
    assert line.odd == 2.10
    assert line.timestamp == DECISION_TS


# ==========================================================================
# persistência: append-only, nada é reescrito
# ==========================================================================


def test_observations_are_never_rewritten(monkeypatch, tmp_path):
    """Re-adicionar a MESMA observação é idempotente; o histórico não
    muda (append-only de verdade, verificado pela leitura crua)."""
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    obs = [_obs("1", "Pinnacle", 2.00, "2030-01-01T09:30:00Z")]
    assert store.add(obs) == 1
    assert store.add(obs) == 0  # duplicata exata ignorada
    raw = store.all_observations(KEY)
    assert len(raw) == 1
    assert raw[0].odd == 2.00
    assert raw[0].timestamp == "2030-01-01T09:30:00Z"


def test_post_kickoff_observation_is_rejected_at_construction():
    """CAPTURE: odds depois do kickoff nem nascem como observação."""
    with pytest.raises(ValueError, match="depois do kickoff"):
        _obs("1", "Pinnacle", 2.00, "2030-01-01T13:00:00Z")


# ==========================================================================
# fechamento: sempre posterior à entrada, anterior ao kickoff
# ==========================================================================


def test_closed_clv_has_entry_lt_closing_lt_kickoff(monkeypatch, tmp_path):
    """No estado CLOSED, a ordem temporal é verificável nas quotes cruas."""
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    store.add([
        _obs("1", "Pinnacle", 2.20, "2030-01-01T09:55:00Z"),
        _obs("1", "Pinnacle", 2.00, "2030-01-01T11:30:00Z"),
    ])
    _isolate(monkeypatch, tmp_path, [_fixture()], DECISION_TS, db)
    BetgsnService(source="real").real_signal_report(market_keys=["1x2"])

    sweep = OddsSnapshotStore(db).clv_lifecycle_sweep(
        now="2030-01-01T13:00:00Z")
    closed = [lc for lc in sweep.lifecycles if lc.state == "CLOSED"]
    assert closed, "fechamento válido deveria produzir CLOSED"
    lc = closed[0]
    assert utc_key(lc.entry.entry_timestamp) < utc_key(
        lc.result.closing_timestamp)
    assert utc_key(lc.result.closing_timestamp) < utc_key(lc.entry.kickoff)
    # e a odd de fechamento existe nas quotes cruas do store
    raw = [
        o for o in OddsSnapshotStore(db).all_observations(KEY)
        if o.market == MARKET and o.outcome == "1"
    ]
    assert lc.result.closing_odd in {o.odd for o in raw}


# ==========================================================================
# matching: quote de OUTRO evento nunca fecha a entrada
# ==========================================================================


def test_other_event_quote_never_closes_the_entry(tmp_path):
    """Entrada do evento A; quote de fechamento plausível gravada sob a
    chave do evento B (mesmo mercado/resultado, dentro da janela). A
    entrada de A NÃO fecha com a odd de B: matching por match_key, não
    por semelhança de mercado."""
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    store.add([_obs("1", "Pinnacle", 2.20, "2030-01-01T09:55:00Z")])
    assert store.register_entry(
        match_key=KEY, market=MARKET, outcome="1",
        entry_odd=2.20, entry_timestamp="2030-01-01T09:55:00Z",
        entry_n_books=1, kickoff=KICKOFF,
        prediction_timestamp=DECISION_TS,
    )
    other_key = event_key("City", "United", utc_key(KICKOFF))
    # fechamento VÁLIDO para a linha — mas do OUTRO evento
    store.add([OddsObservation(
        match_key=other_key, market=MARKET, outcome="1",
        bookmaker="Pinnacle", odd=1.50,
        timestamp="2030-01-01T11:30:00Z", kickoff=KICKOFF,
        provider="The Odds API",
    )])

    sweep = store.clv_lifecycle_sweep(now="2030-01-01T13:00:00Z")
    lc = next(l for l in sweep.lifecycles
              if l.entry.match_key == KEY)
    # kickoff passou sem fechamento DA LINHA: NO_CLOSE — nunca CLOSED
    # com o preço de outro evento
    assert lc.state == "NO_CLOSE"
    assert lc.result is None or lc.result.closing_odd != 1.50


def test_closing_bookmaker_and_timestamp_are_observed(tmp_path):
    """CLOSED: bookmaker e timestamp do fechamento existem nas quotes
    cruas DA LINHA (mesmo match_key/mercado/resultado) — atribuição
    fabricada não passa na auditoria."""
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    store.add([
        _obs("1", "Pinnacle", 2.20, "2030-01-01T09:55:00Z"),
        _obs("1", "Bet365", 2.10, "2030-01-01T09:50:00Z"),
        _obs("1", "Pinnacle", 2.00, "2030-01-01T11:30:00Z"),
        _obs("1", "Bet365", 1.95, "2030-01-01T11:25:00Z"),
    ])
    assert store.register_entry(
        match_key=KEY, market=MARKET, outcome="1",
        entry_odd=2.15, entry_timestamp="2030-01-01T09:55:00Z",
        entry_n_books=2, kickoff=KICKOFF,
        prediction_timestamp=DECISION_TS,
    )

    sweep = store.clv_lifecycle_sweep(now="2030-01-01T13:00:00Z")
    lc = next(l for l in sweep.lifecycles if l.state == "CLOSED")
    line_obs = [
        o for o in store.all_observations(KEY)
        if o.market == MARKET and o.outcome == "1"
    ]
    assert lc.result.closing_bookmaker in {
        o.bookmaker for o in line_obs}
    assert lc.result.closing_timestamp in {o.timestamp for o in line_obs}
    # o bookmaker de fechamento é o REPRESENTATIVO da mediana — sempre
    # uma casa que realmente observou a linha
    assert lc.result.n_books_closing >= 1
