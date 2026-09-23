"""H3 — sinais REAIS nao podem perder league/round_label no caminho da API.

`real_signal_report` monta um `RunResult` adapter para reusar o tradutor
`_signal`. Se as analises reais do snapshot nao forem preservadas no
adapter, `_fixture_of` nao encontra o jogo e o sinal chega ao frontend com
league=""/round_label="" mesmo quando o snapshot real sabe a resposta.

O que precisa ficar provado:
  - sinal real mantem a liga do snapshot;
  - sinal real mantem a rodada (round_label) do snapshot;
  - ausencia LEGITIMA (fixture sem liga/rodada) continua vazia — nada e
    inventado no caminho.
"""
from __future__ import annotations

from betgsn.api.service import BetgsnService
from betgsn.football_data_uk import UpcomingFixture
from betgsn.model import TeamRating
from betgsn.real_signals import RealSignalsService, RealSnapshot

LEAGUE = "Premier League (England)"
DIVISION = "E0"


def _rating(name: str) -> TeamRating:
    return TeamRating(
        name=name, attack=1.1, defense=0.9,
        goals_for=1.5, goals_against=1.0, xg_for=1.4, xg_against=1.1,
        corners_for=5.0, corners_against=4.5, cards_for=2.2, cards_against=2.4,
        shots_for=12.0, shots_on_target_for=4.2, form_points=1.5,
        matches_played=20,
    )


def _fixture(home: str, away: str, *, league: str = LEAGUE,
             division: str = DIVISION) -> UpcomingFixture:
    odds = {
        "Resultado Final (1X2)": {
            "Pinnacle": {"1": 10.0, "X": 3.40, "2": 4.20},
            "Bet365": {"1": 10.0, "X": 3.35, "2": 4.10},
            "Betfair Exchange": {"1": 10.0, "X": 3.45, "2": 4.30},
        }
    }
    best = {
        m: {oc: max(b[oc] for b in books.values() if oc in b)
            for oc in books["Pinnacle"]}
        for m, books in odds.items()
    }
    who = {
        m: {
            oc: next(b for b, o in books.items() if o.get(oc) == best[m][oc])
            for oc in best[m]
        }
        for m, books in odds.items()
    }
    return UpcomingFixture(
        division=division, league=league,
        date="2026-09-20", time="14:00", timezone="Europe/London",
        home=home, away=away, odds=odds, best_odds=best, best_books=who,
    )


def _real_report(monkeypatch, fx: UpcomingFixture):
    """(report, snap) reais: motor de verdade, snapshot controlado."""
    snap = RealSnapshot(
        fixtures=[fx],
        ratings={fx.home: _rating(fx.home), fx.away: _rating(fx.away)},
        league_goals=2.7, teams=[fx.home, fx.away], n_history=100,
        history_window=("2025-01-01", "2026-01-01"), generated_at="2026-09-19",
        computed_in_ms=0.0, sources=["controlled-test"],
    )
    svc = RealSignalsService()
    monkeypatch.setattr(svc, "snapshot", lambda **kwargs: snap)
    report, snap_out = svc.report(market_keys=("1x2",))
    assert report.signals, "cenario de teste precisa produzir sinal"
    return report, snap_out


def _api_report(monkeypatch, fx: UpcomingFixture):
    from betgsn import real_signals as rs

    report, snap = _real_report(monkeypatch, fx)
    monkeypatch.setattr(
        rs.real_signals_service, "report",
        lambda **kw: (report, snap),
    )
    return BetgsnService(source="real").real_signal_report(market_keys=["1x2"])


def test_real_signal_keeps_league(monkeypatch):
    fx = _fixture("Arsenal", "Chelsea")
    api = _api_report(monkeypatch, fx)
    assert api.source == "real"
    assert api.signals
    for s in api.signals:
        assert s.league == LEAGUE, (
            "a liga existia no snapshot real e chegou vazia na API"
        )


def test_real_signal_keeps_round_label(monkeypatch):
    fx = _fixture("Arsenal", "Chelsea")
    api = _api_report(monkeypatch, fx)
    assert api.signals
    for s in api.signals:
        assert s.round_label == DIVISION, (
            "a rodada existia no snapshot real e chegou vazia na API"
        )


def test_real_signal_without_league_stays_empty(monkeypatch):
    """Ausencia legitima nao e preenchida com valor inventado."""
    fx = _fixture("Estreante FC", "Debutante FC", league="", division="")
    api = _api_report(monkeypatch, fx)
    assert api.signals
    for s in api.signals:
        assert s.league == ""
        assert s.round_label == ""
