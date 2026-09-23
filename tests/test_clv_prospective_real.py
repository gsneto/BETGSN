"""I-02 / I-03 / I-07 — CLV prospectivo REAL: entrada do store, FIRST-WINS,
mediana-vs-mediana, NO_ENTRY_ODDS e agregacoes corretas.

O que esta sufixa prova, de ponta a ponta, SEM monkeypatch de metodos do
dominio (apenas o isolamento padrao de fixtures/store em tmp):

  - a entrada vem de observacao REAL do OddsSnapshotStore
    (`line_at` no instante da decisao), congelada FIRST-WINS;
  - entry_timestamp e timestamp real da observacao — nunca o
    prediction_timestamp, nunca now();
  - entry_odd e a MEDIANA das casas no instante T — nunca o MAX de
    fx.best_odds (I-07: mediana vs mediana, mesma fonte);
  - store contendo SOMENTE observacoes posteriores ao instante do report
    NAO produz entrada: /api/clv expoe NO_ENTRY_ODDS (T-18, teste critico
    de leakage);
  - CLOSING_BEFORE_ENTRY ocorre pelo caminho real (T-7);
  - avg_clv_percentage e media de verdade, median_clv_percentage e
    avg_clv_probability sao calculados, coverage e None sem entradas;
  - entry_timestamp < closing_timestamp < kickoff no caminho OK.
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
TOTALS = "Total de Gols"
KICKOFF = "2030-01-01T12:00:00Z"
KEY = event_key("Arsenal", "Chelsea", utc_key(KICKOFF))


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _rating(name: str) -> TeamRating:
    return TeamRating(
        name=name, attack=1.1, defense=0.9,
        goals_for=1.5, goals_against=1.0, xg_for=1.4, xg_against=1.1,
        corners_for=5.0, corners_against=4.5, cards_for=2.2, cards_against=2.4,
        shots_for=12.0, shots_on_target_for=4.2, form_points=1.5,
        matches_played=20,
    )


def _fixture(
    home: str = "Arsenal",
    away: str = "Chelsea",
    *,
    date: str = "2030-01-01",
    time: str = "12:00",
    books_1x2: tuple[str, ...] = ("Pinnacle", "Bet365", "William Hill"),
) -> UpcomingFixture:
    """Jogo futuro com 1X2 (3 casas) e Total de Gols (2 casas)."""
    base_1x2 = {
        "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
        "Bet365": {"1": 1.95, "X": 3.35, "2": 4.10},
        "William Hill": {"1": 2.05, "X": 3.30, "2": 4.00},
    }
    totals = {
        "Pinnacle": {"Over 2.5": 1.85, "Under 2.5": 2.00},
        "Bet365": {"Over 2.5": 1.80, "Under 2.5": 2.05},
    }
    odds = {
        MARKET: {b: base_1x2[b] for b in books_1x2},
        TOTALS: totals,
    }
    best: dict[str, dict[str, float]] = {}
    who: dict[str, dict[str, str]] = {}
    for market, books in odds.items():
        outcomes = {oc for book in books.values() for oc in book}
        best[market] = {
            oc: max(book[oc] for book in books.values() if oc in book)
            for oc in outcomes
        }
        who[market] = {
            oc: next(book for book, prices in books.items()
                     if prices.get(oc) == best[market][oc])
            for oc in outcomes
        }
    return UpcomingFixture(
        division="E0", league="Premier League (England)",
        date=date, time=time, timezone="UTC",
        home=home, away=away,
        odds=odds, best_odds=best, best_books=who,
    )


def _obs(
    key: str, market: str, outcome: str, book: str, odd: float, ts: str,
    kickoff: str = KICKOFF,
) -> OddsObservation:
    return OddsObservation(
        match_key=key, market=market, outcome=outcome, bookmaker=book,
        odd=odd, timestamp=ts, kickoff=kickoff, provider="The Odds API",
    )


def _isolate(
    monkeypatch,
    tmp_path,
    fixtures: list[UpcomingFixture],
    generated_at: str,
    db=None,
):
    """Isola fixtures + store em tmp e controla o snapshot real.

    Mesmo padrao do test_real_signal_contract: o motor e de verdade, so
    a fonte de dados e controlada. Nenhum metodo do dominio (store) e
    mockado — o fluxo roda integro.
    """
    from betgsn import odds_snapshots as snap_mod
    from betgsn import real_signals as rs
    from betgsn.football_data_uk import FootballDataClient

    db = db or (tmp_path / "odds.db")
    real_cls = snap_mod.OddsSnapshotStore
    monkeypatch.setattr(
        snap_mod, "OddsSnapshotStore", lambda *a, **k: real_cls(db))
    monkeypatch.setattr(
        FootballDataClient, "load_fixtures", lambda self: list(fixtures))
    monkeypatch.setattr(
        FootballDataClient, "corpus_signature", lambda self: "test-signature")

    teams = sorted({t for fx in fixtures for t in (fx.home, fx.away)})
    snap = RealSnapshot(
        fixtures=fixtures,
        ratings={t: _rating(t) for t in teams},
        league_goals=2.7,
        teams=teams,
        n_history=100,
        history_window=("2029-01-01", "2030-01-01"),
        generated_at=generated_at,
        computed_in_ms=0.0,
        sources=["controlled-test"],
    )
    svc = RealSignalsService()
    monkeypatch.setattr(svc, "snapshot", lambda **kwargs: snap)
    monkeypatch.setattr(
        rs.real_signals_service, "report", lambda **kw: svc.report(**kw))
    return db


def _real_report(monkeypatch, tmp_path, fixtures, generated_at, db=None):
    """Executa o fluxo real (real_signal_report) e devolve o caminho do db."""
    db = _isolate(monkeypatch, tmp_path, fixtures, generated_at, db=db)
    api = BetgsnService(source="real").real_signal_report(
        market_keys=["1x2"])
    assert api.source == "real"
    return db, api


def _clv_report(db) -> list:
    """Recupera os registros congelados direto do store."""
    return OddsSnapshotStore(db).clv_entries()


# ==========================================================================
# line_at — mediana point-in-time (I-07)
# ==========================================================================


def test_line_at_none_without_observations(tmp_path):
    store = OddsSnapshotStore(tmp_path / "odds.db")
    assert store.line_at(KEY, MARKET, "1", "2030-01-01T10:00:00Z") is None


def test_line_at_single_book_median_is_the_book_odd(tmp_path):
    """T-9: uma casa so -> mediana = odd da casa."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    store.add([_obs(KEY, MARKET, "1", "Pinnacle", 1.91,
                    "2030-01-01T09:00:00Z")])
    line = store.line_at(KEY, MARKET, "1", "2030-01-01T10:00:00Z")
    assert line is not None
    assert line.odd == 1.91
    assert line.n_books == 1
    assert line.bookmaker == "Pinnacle"
    assert line.timestamp == "2030-01-01T09:00:00Z"


def test_line_at_median_multiple_books_and_representative(tmp_path):
    """T-10: multiplas casas -> mediana correta + casa representativa."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    store.add([
        _obs(KEY, MARKET, "1", "Pinnacle", 1.50, "2030-01-01T09:00:00Z"),
        _obs(KEY, MARKET, "1", "Bet365", 2.00, "2030-01-01T09:15:00Z"),
        _obs(KEY, MARKET, "1", "William Hill", 6.00, "2030-01-01T09:30:00Z"),
    ])
    line = store.line_at(KEY, MARKET, "1", "2030-01-01T10:00:00Z")
    assert line.odd == 2.00
    assert line.n_books == 3
    # casa representativa: a odd mais proxima da mediana — nao a maior
    assert line.bookmaker == "Bet365"
    # timestamp = maior timestamp entre as observacoes usadas
    assert line.timestamp == "2030-01-01T09:30:00Z"


def test_line_at_excludes_observations_after_the_instant(tmp_path):
    """PIT: observacao pos-decisao nao participa da linha em T."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    store.add([
        _obs(KEY, MARKET, "1", "Pinnacle", 2.00, "2030-01-01T09:00:00Z"),
        _obs(KEY, MARKET, "1", "Pinnacle", 3.00, "2030-01-01T11:00:00Z"),
    ])
    line = store.line_at(KEY, MARKET, "1", "2030-01-01T10:00:00Z")
    assert line.odd == 2.00
    # depois da decisao a linha muda — mas a entrada em T nao (leakage)
    later = store.line_at(KEY, MARKET, "1", "2030-01-01T11:30:00Z")
    assert later.odd == 3.00


def test_line_at_uses_last_observation_per_book_within_cutoff(tmp_path):
    store = OddsSnapshotStore(tmp_path / "odds.db")
    store.add([
        _obs(KEY, MARKET, "1", "Pinnacle", 1.80, "2030-01-01T09:00:00Z"),
        _obs(KEY, MARKET, "1", "Pinnacle", 1.90, "2030-01-01T09:30:00Z"),
    ])
    line = store.line_at(KEY, MARKET, "1", "2030-01-01T10:00:00Z")
    assert line.odd == 1.90
    assert line.timestamp == "2030-01-01T09:30:00Z"


def test_line_at_n_books_reflects_books_actually_used(tmp_path):
    """T-15/T-16: n_books = casas realmente utilizadas no instante."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    store.add([
        _obs(KEY, MARKET, "1", "Pinnacle", 1.90, "2030-01-01T09:00:00Z"),
        _obs(KEY, MARKET, "1", "Bet365", 2.00, "2030-01-01T09:00:00Z"),
        _obs(KEY, MARKET, "1", "William Hill", 2.10, "2030-01-01T09:00:00Z"),
        # Betfair so observou DEPOIS da decisao: nao conta no instante T
        _obs(KEY, MARKET, "1", "Betfair", 2.20, "2030-01-01T11:00:00Z"),
    ])
    line = store.line_at(KEY, MARKET, "1", "2030-01-01T10:00:00Z")
    assert line.n_books == 3
    assert line.odd == 2.00


# ==========================================================================
# register_entry — FIRST-WINS e guardas PIT (T-19)
# ==========================================================================


def test_register_entry_first_wins_freezes_the_entry(tmp_path):
    """T-19: primeira entrada congela; re-registro nao altera."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    assert store.register_entry(
        match_key=KEY, market=MARKET, outcome="1",
        entry_odd=2.00, entry_timestamp="2030-01-01T09:45:00Z",
        entry_n_books=3, kickoff=KICKOFF,
        prediction_timestamp="2030-01-01T10:00:00Z",
    ) is True
    # novas observacoes mudariam a linha, mas a entrada ja esta congelada
    assert store.register_entry(
        match_key=KEY, market=MARKET, outcome="1",
        entry_odd=2.50, entry_timestamp="2030-01-01T11:00:00Z",
        entry_n_books=3, kickoff=KICKOFF,
        prediction_timestamp="2030-01-01T11:30:00Z",
    ) is False
    rec = store.clv_entries()
    assert len(rec) == 1
    assert rec[0].entry_odd == 2.00
    assert rec[0].entry_timestamp == "2030-01-01T09:45:00Z"


def test_register_entry_rejects_entry_after_the_decision(tmp_path):
    """Guarda PIT: observacao pos-decisao nao pode definir a entrada."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    with pytest.raises(ValueError, match="leakage"):
        store.register_entry(
            match_key=KEY, market=MARKET, outcome="1",
            entry_odd=2.00, entry_timestamp="2030-01-01T10:30:00Z",
            entry_n_books=1, kickoff=KICKOFF,
            prediction_timestamp="2030-01-01T10:00:00Z",
        )
    assert store.clv_entries() == []


def test_register_entry_rejects_entry_at_or_after_kickoff(tmp_path):
    store = OddsSnapshotStore(tmp_path / "odds.db")
    with pytest.raises(ValueError, match="kickoff"):
        store.register_entry(
            match_key=KEY, market=MARKET, outcome="1",
            entry_odd=2.00, entry_timestamp=KICKOFF,
            entry_n_books=1, kickoff=KICKOFF,
            prediction_timestamp=KICKOFF,
        )
    assert store.clv_entries() == []


# ==========================================================================
# fluxo real: real_signal_report -> register_entry (T-1, T-2, T-11, T-18)
# ==========================================================================


def test_real_flow_registers_entry_from_real_observations(
    monkeypatch, tmp_path
):
    """T-1/T-2/T-11: entrada = mediana PIT real; nunca best_odds (MAX)."""
    fixture = _fixture()
    db = tmp_path / "odds.db"
    # odds assimetricas: mediana 2.00, MAX (best_odds do fixture) 6.00
    store = OddsSnapshotStore(db)
    store.add([
        _obs(KEY, MARKET, "1", "Pinnacle", 1.50, "2030-01-01T09:30:00Z"),
        _obs(KEY, MARKET, "1", "Bet365", 2.00, "2030-01-01T09:45:00Z"),
        _obs(KEY, MARKET, "1", "William Hill", 6.00, "2030-01-01T09:15:00Z"),
    ])
    db, _ = _real_report(
        monkeypatch, tmp_path, [fixture], "2030-01-01T10:00:00Z", db=db)

    recs = {
        (r.market, r.outcome): r
        for r in OddsSnapshotStore(db).clv_entries()
    }
    rec = recs[(MARKET, "1")]
    # T-1: timestamp real de observacao — nunca prediction_timestamp
    obs_ts = {
        "2030-01-01T09:30:00Z", "2030-01-01T09:45:00Z",
        "2030-01-01T09:15:00Z",
    }
    assert rec.entry_timestamp in obs_ts
    assert rec.entry_timestamp != "2030-01-01T10:00:00Z"
    # T-2/T-11: mediana das casas (2.00), nao o MAX (6.00)
    assert rec.entry_odd == 2.00
    assert rec.entry_odd != fixture.best_odds[MARKET]["1"]
    assert rec.entry_n_books == 3
    # kickoff em UTC canonico
    assert rec.kickoff == KICKOFF
    assert rec.prediction_timestamp == "2030-01-01T10:00:00Z"


def test_real_flow_median_not_max_regression(monkeypatch, tmp_path):
    """T-11 (regressao best-vs-median): assimetria extrema nao vira MAX."""
    fixture = _fixture(books_1x2=("Pinnacle", "Bet365", "William Hill"))
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    store.add([
        _obs(KEY, MARKET, "1", "Pinnacle", 1.50, "2030-01-01T09:30:00Z"),
        _obs(KEY, MARKET, "1", "Bet365", 2.00, "2030-01-01T09:45:00Z"),
        _obs(KEY, MARKET, "1", "William Hill", 6.00, "2030-01-01T09:15:00Z"),
    ])
    db, _ = _real_report(
        monkeypatch, tmp_path, [fixture], "2030-01-01T10:00:00Z", db=db)

    report = BetgsnService().clv()
    entry = next(
        e for e in report.entries
        if e.market == MARKET and e.outcome == "1"
    )
    # entry_odd = MEDIANA (2.00) — o best_odds do fixture e 6.00 (MAX)
    assert entry.entry_odd == 2.00
    assert entry.entry_odd != fixture.best_odds[MARKET]["1"]
    # as demais linhas, sem observacao, sao NO_ENTRY_ODDS explicitas
    others = [
        e for e in report.entries if not (e.market == MARKET and e.outcome == "1")
    ]
    assert others and all(e.status == "NO_ENTRY_ODDS" for e in others)
    assert all(e.entry_odd is None for e in others)
    assert all(e.entry_timestamp is None for e in others)


def test_real_flow_only_observations_after_decision_no_entry(
    monkeypatch, tmp_path
):
    """T-18 (TESTE CRITICO): store SO com observacoes posteriores ao
    instante do report -> NENHUMA entrada. Se alguem fizer
    entry_timestamp = prediction_timestamp (ou usar best_odds atual),
    este teste falha: apareceria uma entrada que nao existia."""
    fixture = _fixture()
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    # unica observacao: DEPOIS do instante do report (10:00)
    store.add([
        _obs(KEY, MARKET, "1", "Pinnacle", 1.80, "2030-01-01T11:00:00Z"),
    ])

    db, _ = _real_report(
        monkeypatch, tmp_path, [fixture], "2030-01-01T10:00:00Z", db=db)

    # nada foi registrado — ausencia nao virou odd
    assert OddsSnapshotStore(db).clv_entries() == []

    report = BetgsnService().clv()
    assert report.entries
    for e in report.entries:
        assert e.status == "NO_ENTRY_ODDS"
        assert e.entry_odd is None
        assert e.entry_timestamp is None


def test_real_flow_later_observations_do_not_rewrite_entry(
    monkeypatch, tmp_path
):
    """T-18 (leakage): observacoes posteriores mudariam a mediana, mas a
    entrada continua baseada no instante T — e o re-report nao a altera
    (FIRST-WINS)."""
    fixture = _fixture()
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    store.add([
        _obs(KEY, MARKET, "1", "Pinnacle", 2.00, "2030-01-01T09:30:00Z"),
        _obs(KEY, MARKET, "1", "Bet365", 2.00, "2030-01-01T09:45:00Z"),
    ])
    db, _ = _real_report(
        monkeypatch, tmp_path, [fixture], "2030-01-01T10:00:00Z", db=db)
    first = next(
        r for r in OddsSnapshotStore(db).clv_entries()
        if (r.market, r.outcome) == (MARKET, "1")
    )
    assert first.entry_odd == 2.00
    assert first.entry_timestamp == "2030-01-01T09:45:00Z"

    # novas observacoes MOVEM a mediana para 3.00...
    store2 = OddsSnapshotStore(db)
    store2.add([
        _obs(KEY, MARKET, "1", "Pinnacle", 3.00, "2030-01-01T11:00:00Z"),
        _obs(KEY, MARKET, "1", "Bet365", 3.00, "2030-01-01T11:10:00Z"),
    ])
    moved = store2.line_at(KEY, MARKET, "1", "2030-01-01T11:30:00Z")
    assert moved.odd == 3.00

    # ...mas um novo report nao reescreve a entrada congelada
    db, _ = _real_report(
        monkeypatch, tmp_path, [fixture], "2030-01-01T11:30:00Z", db=db)
    rec = next(
        r for r in OddsSnapshotStore(db).clv_entries()
        if (r.market, r.outcome) == (MARKET, "1")
    )
    assert rec.entry_odd == 2.00
    assert rec.entry_timestamp == "2030-01-01T09:45:00Z"

    report = BetgsnService().clv()
    entry = next(
        e for e in report.entries
        if e.market == MARKET and e.outcome == "1"
    )
    assert entry.entry_odd == 2.00
    assert entry.entry_timestamp == "2030-01-01T09:45:00Z"


def test_real_flow_multiple_markets_and_books(monkeypatch, tmp_path):
    """T-15/T-16: multiplos mercados/casas; n_books correto por linha."""
    fixture = _fixture()
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    store.add([
        _obs(KEY, MARKET, "1", "Pinnacle", 2.00, "2030-01-01T09:30:00Z"),
        _obs(KEY, MARKET, "1", "Bet365", 2.10, "2030-01-01T09:30:00Z"),
        _obs(KEY, MARKET, "1", "William Hill", 2.20, "2030-01-01T09:30:00Z"),
        _obs(KEY, TOTALS, "Over 2.5", "Pinnacle", 1.90, "2030-01-01T09:30:00Z"),
        _obs(KEY, TOTALS, "Over 2.5", "Bet365", 2.00, "2030-01-01T09:30:00Z"),
    ])
    db, _ = _real_report(
        monkeypatch, tmp_path, [fixture], "2030-01-01T10:00:00Z", db=db)

    recs = {
        (r.market, r.outcome): r
        for r in OddsSnapshotStore(db).clv_entries()
    }
    assert recs[(MARKET, "1")].entry_n_books == 3
    assert recs[(MARKET, "1")].entry_odd == 2.10
    assert recs[(TOTALS, "Over 2.5")].entry_n_books == 2
    assert recs[(TOTALS, "Over 2.5")].entry_odd == 1.95


# ==========================================================================
# /api/clv — status reais e contrato PIT (T-3..T-8)
# ==========================================================================


def _seed_ok_clv(db, outcome: str, entry_odd: float, closing_odd: float):
    """Tres casas: entrada em 09:55, fechamento em 11:30 (janela de 120min)."""
    store = OddsSnapshotStore(db)
    store.add([
        _obs(KEY, MARKET, outcome, "Pinnacle", entry_odd,
             "2030-01-01T09:55:00Z"),
        _obs(KEY, MARKET, outcome, "Bet365", entry_odd,
             "2030-01-01T09:55:00Z"),
        _obs(KEY, MARKET, outcome, "William Hill", entry_odd,
             "2030-01-01T09:55:00Z"),
        _obs(KEY, MARKET, outcome, "Pinnacle", closing_odd,
             "2030-01-01T11:30:00Z"),
        _obs(KEY, MARKET, outcome, "Bet365", closing_odd,
             "2030-01-01T11:30:00Z"),
        _obs(KEY, MARKET, outcome, "William Hill", closing_odd,
             "2030-01-01T11:30:00Z"),
    ])


def test_clv_report_ok_entry_lt_closing_lt_kickoff(monkeypatch, tmp_path):
    """T-3/T-4/T-5/T-6: fechamento vem do store; entry < closing < kickoff."""
    fixture = _fixture()
    db = tmp_path / "odds.db"
    _seed_ok_clv(db, "1", entry_odd=2.20, closing_odd=2.00)
    db, _ = _real_report(
        monkeypatch, tmp_path, [fixture], "2030-01-01T10:00:00Z", db=db)

    report = BetgsnService().clv()
    entry = next(
        e for e in report.entries
        if e.market == MARKET and e.outcome == "1"
    )
    assert entry.status == "OK"
    # T-3: closing odd = mediana das ultimas observacoes por casa
    assert entry.closing_odd == 2.00
    # T-4: closing timestamp = timestamp real da observacao de fechamento
    assert entry.closing_timestamp == "2030-01-01T11:30:00Z"
    # T-5: entry < closing
    assert entry.entry_timestamp is not None
    assert utc_key(entry.entry_timestamp) < utc_key(entry.closing_timestamp)
    # T-6: closing < kickoff
    assert utc_key(entry.closing_timestamp) < utc_key(KICKOFF)
    # CLV = +10% (2.20 -> 2.00)
    assert entry.clv_percentage == pytest.approx(0.10, abs=1e-6)
    assert entry.entry_odd == 2.20
    # cobertura medida sobre a populacao do relatorio (5 linhas apostaveis)
    assert report.total_bets == len(report.entries) == 5
    assert report.coverage == pytest.approx(1 / 5)


def test_clv_report_closing_before_entry_real_path(monkeypatch, tmp_path):
    """T-7: CLOSING_BEFORE_ENTRY pelo caminho real — sem monkeypatch do
    dominio. Unica observacao: 11:30 (janela de fechamento). Report as
    11:45 registra entrada com timestamp 11:30; o fechamento encontrado
    (11:30) NAO e posterior a entrada."""
    fixture = _fixture(books_1x2=("Pinnacle",))
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    store.add([
        _obs(KEY, MARKET, "1", "Pinnacle", 1.80, "2030-01-01T11:30:00Z"),
    ])
    db, _ = _real_report(
        monkeypatch, tmp_path, [fixture], "2030-01-01T11:45:00Z", db=db)

    recs = OddsSnapshotStore(db).clv_entries()
    assert any((r.market, r.outcome) == (MARKET, "1") for r in recs)

    report = BetgsnService().clv()
    entry = next(
        e for e in report.entries
        if e.market == MARKET and e.outcome == "1"
    )
    assert entry.status == "CLOSING_BEFORE_ENTRY"
    assert entry.closing_odd == 1.80
    assert entry.clv_percentage is None
    assert entry.clv_probability is None
    assert entry.entry_timestamp == "2030-01-01T11:30:00Z"


def test_clv_report_no_closing_odds(monkeypatch, tmp_path):
    """T-8: entrada registrada, mas sem fechamento dentro da janela."""
    fixture = _fixture()
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    # observacao as 08:00: fora da janela de 120min do kickoff das 12:00
    store.add([
        _obs(KEY, MARKET, "1", "Pinnacle", 2.00, "2030-01-01T08:00:00Z"),
        _obs(KEY, MARKET, "1", "Bet365", 2.20, "2030-01-01T08:00:00Z"),
    ])
    db, _ = _real_report(
        monkeypatch, tmp_path, [fixture], "2030-01-01T09:00:00Z", db=db)

    report = BetgsnService().clv()
    entry = next(
        e for e in report.entries
        if e.market == MARKET and e.outcome == "1"
    )
    assert entry.status == "NO_CLOSING_ODDS"
    assert entry.entry_odd == 2.10
    assert entry.closing_odd is None
    # T-20: "" -> None na serializacao da API
    assert entry.closing_bookmaker is None
    assert entry.closing_timestamp is None
    assert entry.clv_percentage is None


# ==========================================================================
# agregacoes (I-03): media de verdade, mediana, probability, coverage
# ==========================================================================


def test_avg_clv_percentage_is_mean_not_sum(monkeypatch, tmp_path):
    """T-12: 3 entradas OK de +10% -> avg 0.10, nunca 0.30 (soma)."""
    fixture = _fixture()
    db = tmp_path / "odds.db"
    for oc, entry, closing in (("1", 2.20, 2.00), ("X", 3.60, 3.2727),
                               ("2", 4.40, 4.00)):
        store = OddsSnapshotStore(db)
        store.add([
            _obs(KEY, MARKET, oc, "Pinnacle", entry,
                 "2030-01-01T09:55:00Z"),
            _obs(KEY, MARKET, oc, "Pinnacle", closing,
                 "2030-01-01T11:30:00Z"),
        ])
    db, _ = _real_report(
        monkeypatch, tmp_path, [fixture], "2030-01-01T10:00:00Z", db=db)

    report = BetgsnService().clv()
    ok = [e for e in report.entries if e.status == "OK"]
    assert len(ok) == 3
    for e in ok:
        assert e.clv_percentage == pytest.approx(0.10, abs=1e-3)
    assert report.avg_clv_percentage == pytest.approx(0.10, abs=1e-3)
    assert report.avg_clv_percentage != pytest.approx(0.30, abs=1e-3)
    assert report.bets_with_clv == 3
    assert report.total_bets == len(report.entries)
    assert report.coverage == pytest.approx(3 / 5)  # 3 OK de 5 linhas


def test_median_clv_percentage_and_avg_probability_calculated(
    monkeypatch, tmp_path
):
    """T-13: mediana/avg_clv_probability populados; mediana e a mediana."""
    fixture = _fixture()
    db = tmp_path / "odds.db"
    cases = (("1", 2.20, 2.00), ("X", 2.40, 2.00), ("2", 2.60, 2.00))
    for oc, entry, closing in cases:
        store = OddsSnapshotStore(db)
        store.add([
            _obs(KEY, MARKET, oc, "Pinnacle", entry,
                 "2030-01-01T09:55:00Z"),
            _obs(KEY, MARKET, oc, "Pinnacle", closing,
                 "2030-01-01T11:30:00Z"),
        ])
    db, _ = _real_report(
        monkeypatch, tmp_path, [fixture], "2030-01-01T10:00:00Z", db=db)

    report = BetgsnService().clv()
    pcts = sorted(
        e.clv_percentage for e in report.entries if e.status == "OK")
    assert len(pcts) == 3
    assert report.median_clv_percentage == pytest.approx(
        statistics.median(pcts), abs=1e-6)
    # mediana das clvs: 2.40/2.00 -> 0.20
    assert report.median_clv_percentage == pytest.approx(0.20, abs=1e-3)
    assert report.avg_clv_probability is not None
    probs = [
        e.clv_probability for e in report.entries if e.status == "OK"]
    assert report.avg_clv_probability == pytest.approx(
        statistics.fmean(probs), abs=1e-6)


def test_zero_registered_entries_all_none(monkeypatch, tmp_path):
    """T-14: zero entradas registradas -> NO_ENTRY_ODDS + agregados None +
    coverage None (nunca 0.0 fabricado)."""
    fixture = _fixture()
    db, _ = _real_report(
        monkeypatch, tmp_path, [fixture], "2030-01-01T10:00:00Z")

    report = BetgsnService().clv()
    assert report.entries
    assert all(e.status == "NO_ENTRY_ODDS" for e in report.entries)
    assert report.bets_with_clv == 0
    assert report.avg_clv_percentage is None
    assert report.median_clv_percentage is None
    assert report.avg_clv_probability is None
    assert report.positive_clv_rate is None
    assert report.coverage is None


def test_by_market_semantics_matches_the_store(monkeypatch, tmp_path):
    """I-03: by_market com media por mercado (semantica do store), nao soma."""
    fixture = _fixture()
    db = tmp_path / "odds.db"
    _seed_ok_clv(db, "1", entry_odd=2.20, closing_odd=2.00)
    db, _ = _real_report(
        monkeypatch, tmp_path, [fixture], "2030-01-01T10:00:00Z", db=db)

    report = BetgsnService().clv()
    market_bucket = report.by_market[MARKET]
    assert market_bucket["n"] == 3  # 1, X, 2
    assert market_bucket["with_clv"] == 1
    assert market_bucket["avg_clv_percentage"] == pytest.approx(
        0.10, abs=1e-3)


def test_clv_prospective_ok_still_mediana_vs_mediana(tmp_path):
    """I-07 no dominio: entrada mediana vs fechamento mediano, com a
    MESMA fonte — comparavel metodologicamente."""
    store = OddsSnapshotStore(tmp_path / "odds.db")
    store.add([
        _obs(KEY, MARKET, "1", "Pinnacle", 1.50, "2030-01-01T09:30:00Z"),
        _obs(KEY, MARKET, "1", "Bet365", 2.00, "2030-01-01T09:30:00Z"),
        _obs(KEY, MARKET, "1", "William Hill", 6.00, "2030-01-01T09:30:00Z"),
        _obs(KEY, MARKET, "1", "Pinnacle", 2.20, "2030-01-01T11:30:00Z"),
        _obs(KEY, MARKET, "1", "Bet365", 2.20, "2030-01-01T11:30:00Z"),
        _obs(KEY, MARKET, "1", "William Hill", 2.20, "2030-01-01T11:30:00Z"),
    ])
    result = store.clv_prospective(
        KEY, MARKET, "1",
        entry_odd=2.00,  # mediana na entrada
        entry_timestamp="2030-01-01T09:45:00Z",
    )
    assert result.status == "OK"
    assert result.closing_odd == 2.20  # mediana no fechamento
    assert result.clv_percentage == pytest.approx(
        2.00 / 2.20 - 1.0, abs=1e-6)
