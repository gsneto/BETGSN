"""Testes de `betgsn.real_signals` — sinais a partir de dados reais.

O que precisa ficar provado:
  - o servico usa JOGOS FUTUROS, nao o dataset sintetico;
  - reusa o MESMO motor (`analyze_fixture` + `build_report`);
  - falha explicita quando nao ha dados, em vez de cair no sintetico;
  - a calibracao medida do modelo acompanha o snapshot;
  - jogos sem rating sao contados, nao inventados.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from betgsn.football_data_uk import UpcomingFixture
from betgsn.model import TeamRating
from betgsn.real_signals import (
    MODEL_CALIBRATION,
    MIN_HISTORY,
    RealDataError,
    RealSignalsService,
    calibrate_ev,
)


def _rating(name: str, attack: float = 1.1, defense: float = 0.9) -> TeamRating:
    return TeamRating(
        name=name, attack=attack, defense=defense,
        goals_for=1.5, goals_against=1.0, xg_for=1.4, xg_against=1.1,
        corners_for=5.0, corners_against=4.5, cards_for=2.2, cards_against=2.4,
        shots_for=12.0, shots_on_target_for=4.2, form_points=1.5,
        matches_played=20,
    )


def _fixture(home: str, away: str, **kw) -> UpcomingFixture:
    odds = kw.pop("odds", {
        "Resultado Final (1X2)": {
            "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
            "Bet365": {"1": 1.85, "X": 3.35, "2": 4.10},
            "Betfair Exchange": {"1": 1.95, "X": 3.45, "2": 4.30},
        }
    })
    best = {
        m: {oc: max(b[oc] for b in books.values() if oc in b) for oc in books["Pinnacle"]}
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
        division="E0", league="Premier League (England)",
        date="2026-09-20", time="14:00", timezone="Europe/London",
        home=home, away=away, odds=odds, best_odds=best, best_books=who,
        **kw,
    )


# --------------------------------------------------------------------------
# Calibracao
# --------------------------------------------------------------------------


def test_model_calibration_documents_the_measured_bias():
    """O vies do modelo e MEDIDO, nao estimado."""
    assert MODEL_CALIBRATION["gap_pp"] > 0, "o modelo e superconfiante"
    assert MODEL_CALIBRATION["ev_predicted"] > 0
    assert MODEL_CALIBRATION["return_realized"] < 0
    assert MODEL_CALIBRATION["simulated_roi"] < 0
    assert "superconfiante" in MODEL_CALIBRATION["verdict"]


def test_calibrate_ev_discounts_the_measured_gap():
    assert calibrate_ev(0.80) == pytest.approx(0.80 - MODEL_CALIBRATION["gap_pp"])
    # um EV alto vira muito menor
    assert calibrate_ev(0.25) < 0.02
    # um EV ja negativo continua negativo
    assert calibrate_ev(-0.05) < 0


def test_calibrate_ev_does_not_make_the_model_good():
    """A correcao reduz o vies, nao cria vantagem."""
    # EV de +10% (acima do gap de 24pp) ainda fica negativo
    assert calibrate_ev(0.10) < 0


# --------------------------------------------------------------------------
# Snapshot: exige dados reais
# --------------------------------------------------------------------------


def test_snapshot_raises_without_fixtures(tmp_path, monkeypatch):
    """Sem jogos futuros em cache, falha EXPLICITA — nunca cai no sintetico."""
    from betgsn import real_signals as mod
    from betgsn.football_data_uk import FootballDataClient

    svc = RealSignalsService()
    monkeypatch.setattr(
        FootballDataClient, "load_fixtures", lambda self: [],
        raising=True,
    )
    with pytest.raises(RealDataError, match="nenhum jogo futuro"):
        svc.snapshot()


def test_snapshot_raises_when_fixtures_have_no_odds(monkeypatch):
    from betgsn.football_data_uk import FootballDataClient

    svc = RealSignalsService()
    sem_odds = UpcomingFixture(
        division="E0", league="Premier League (England)",
        date="2026-09-20", time="14:00", timezone="Europe/London",
        home="A", away="B",
    )
    monkeypatch.setattr(
        FootballDataClient, "load_fixtures", lambda self: [sem_odds],
        raising=True,
    )
    with pytest.raises(RealDataError, match="nenhum com odds"):
        svc.snapshot()


def test_snapshot_rejects_fixture_in_the_past(monkeypatch):
    """Fixture com kickoff ANTERIOR ao agora nunca vira jogo futuro.

    E a protecao que impede um cache desatualizado (rodada que ja acabou)
    de produzir sinais falsos. O fallback da The Odds API nao muda isso.
    """
    from betgsn.football_data_uk import FootballDataClient

    ontem = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    passado = UpcomingFixture(
        division="E0", league="Premier League (England)",
        date=ontem, time="15:00", timezone="UTC",
        home="Arsenal", away="Chelsea",
        odds={"Resultado Final (1X2)": {
            "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
            "Bet365": {"1": 1.85, "X": 3.35, "2": 4.10},
            "Betfair Exchange": {"1": 1.95, "X": 3.45, "2": 4.30},
        }},
        best_odds={"Resultado Final (1X2)": {"1": 1.95, "X": 3.45, "2": 4.30}},
        best_books={"Resultado Final (1X2)": {
            "1": "Betfair Exchange", "X": "Betfair Exchange", "2": "Betfair Exchange"}},
    )
    assert passado.has_odds and passado.has_kickoff
    monkeypatch.setattr(
        FootballDataClient, "load_fixtures", lambda self: [passado],
        raising=True,
    )
    svc = RealSignalsService()
    with pytest.raises(RealDataError, match="após o instante atual"):
        svc.snapshot()


def test_snapshot_accepts_future_fixture_and_labels_fallback_source(monkeypatch):
    """Fixture FUTURA com odds entra no snapshot; quando vem do fallback
    (The Odds API), a fonte aparece em `sources` — proveniencia explicita."""
    from betgsn import real_signals as mod
    from betgsn.football_data_uk import FootballDataClient

    amanha = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    futuro = UpcomingFixture(
        division="E0", league="Premier League (England)",
        date=amanha, time="18:00", timezone="UTC",
        home="Arsenal", away="Chelsea", source="the_odds_api",
        odds={"Resultado Final (1X2)": {
            "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
            "Bet365": {"1": 1.85, "X": 3.35, "2": 4.10},
            "Betfair Exchange": {"1": 1.95, "X": 3.45, "2": 4.30},
        }},
        best_odds={"Resultado Final (1X2)": {"1": 1.95, "X": 3.45, "2": 4.30}},
        best_books={"Resultado Final (1X2)": {
            "1": "Betfair Exchange", "X": "Betfair Exchange", "2": "Betfair Exchange"}},
    )
    monkeypatch.setattr(
        FootballDataClient, "load_fixtures", lambda self: [futuro], raising=True,
    )
    # uma partida historica qualquer: o corpus vazio e rejeitado por
    # contrato (HistoricalCorpus), e o fit real esta mockado acima
    from betgsn.football_data_uk import CsvMatch

    partida = CsvMatch(
        division="E0", league="Premier League (England)", season="2025/26",
        date="2026-01-17", time="15:00", timezone="Europe/London",
        home="Arsenal", away="Chelsea", home_goals=2, away_goals=1,
    )
    monkeypatch.setattr(
        FootballDataClient, "load_matches", lambda self, **kw: [partida],
        raising=True,
    )
    monkeypatch.setattr(
        mod,
        "_fit_on_real_history",
        lambda client, cutoff=None, window_years=3, matches=None: (
            {"Arsenal": _rating("Arsenal"), "Chelsea": _rating("Chelsea")},
            2.7, ["Arsenal", "Chelsea"], 400, ("2025-01-01", "2026-01-01"),
        ),
    )
    svc = RealSignalsService()
    snap = svc.snapshot()
    assert snap.fixtures == [futuro]
    assert any("The Odds API" in s for s in snap.sources)


def test_error_messages_say_how_to_fix():
    """Mensagem de erro precisa dizer o comando, nao so que falhou."""
    from betgsn import real_signals as mod
    import inspect

    src = inspect.getsource(mod)
    assert "--import-fixtures-live" in src


# --------------------------------------------------------------------------
# Reuso do motor
# --------------------------------------------------------------------------


def test_service_reuses_the_same_engine():
    """O servico nao pode ter logica estatistica propria."""
    from betgsn import real_signals as mod
    import inspect

    src = inspect.getsource(mod)
    assert "analyze_fixture" in src, "deve usar o mesmo caminho do Scanner"
    assert "build_report" in src, "deve usar o mesmo gerador de sinais"
    # nao pode reimplementar Poisson nem Kelly
    assert "poisson" not in src.lower()
    assert "kelly_fraction" not in src


def test_min_history_is_meaningful():
    assert MIN_HISTORY >= 100


# --------------------------------------------------------------------------
# Fixture helper (sanidade dos dados de teste)
# --------------------------------------------------------------------------


def test_fixture_helper_is_consistent():
    fx = _fixture("Arsenal", "Chelsea")
    assert fx.has_odds
    assert fx.n_books == 3
    assert fx.match == "Arsenal vs Chelsea"
    assert fx.kickoff == "2026-09-20 14:00"
    for market, books in fx.odds.items():
        for oc, odd in fx.best_odds[market].items():
            assert odd == max(b[oc] for b in books.values() if oc in b)


def test_report_uses_supplied_odds_and_can_return_no_signals(monkeypatch):
    """Contrato determinístico: cotações fornecidas e ausência legítima de edge."""
    from betgsn.real_signals import RealSnapshot

    fx = _fixture("Arsenal", "Chelsea")
    for book in fx.odds["Resultado Final (1X2)"].values():
        book["1"] = 10.0
    snap = RealSnapshot(
        fixtures=[fx], ratings={"Arsenal": _rating("Arsenal"), "Chelsea": _rating("Chelsea")},
        league_goals=2.7, teams=["Arsenal", "Chelsea"], n_history=100,
        history_window=("2025-01-01", "2026-01-01"), generated_at="2026-09-19",
        computed_in_ms=0.0, sources=["controlled-test"],
    )
    svc = RealSignalsService()
    monkeypatch.setattr(svc, "snapshot", lambda **kwargs: snap)
    report, _ = svc.report(market_keys=("1x2",))
    assert report.signals
    for signal in report.signals:
        assert signal.best_odd == fx.odds[signal.market][signal.best_book][signal.outcome]
    empty, _ = svc.report(market_keys=("1x2",), min_ev=100.0)
    assert empty.signals == []


def test_report_kickoff_is_canonical_utc_instant(monkeypatch):
    """Signal.kickoff e o INSTANTE em UTC canonico.

    O jogo e 14:00 em Londres no verao (BST = UTC+1) = 13:00Z. Essa e a
    mesma chave que /api/fixtures usa — o mesmo jogo representa o mesmo
    instante em todos os endpoints. O horario local nunca ganha um "Z".
    """
    from dataclasses import replace

    from betgsn.real_signals import RealSnapshot
    from betgsn.timeutil import utc_key

    summer = _fixture("Arsenal", "Chelsea")                     # 2026-09-20, BST
    winter = replace(_fixture("Arsenal", "Chelsea"), date="2026-01-17")  # GMT

    def _snap(fx):
        return RealSnapshot(
            fixtures=[fx],
            ratings={"Arsenal": _rating("Arsenal"), "Chelsea": _rating("Chelsea")},
            league_goals=2.7, teams=["Arsenal", "Chelsea"], n_history=100,
            history_window=("2025-01-01", "2026-01-01"),
            generated_at="2026-09-19T00:00:00Z",
            computed_in_ms=0.0, sources=["controlled-test"],
        )

    svc = RealSignalsService()
    monkeypatch.setattr(svc, "snapshot", lambda **kwargs: _snap(summer))
    report, _ = svc.report(market_keys=("1x2",))
    assert report.signals
    for signal in report.signals:
        assert signal.kickoff == "2026-09-20T13:00:00Z", signal.kickoff
        # e a chave canonica: reparsear nao muda o instante
        assert utc_key(signal.kickoff) == signal.kickoff

    monkeypatch.setattr(svc, "snapshot", lambda **kwargs: _snap(winter))
    report, _ = svc.report(market_keys=("1x2",))
    assert report.signals
    for signal in report.signals:
        assert signal.kickoff == "2026-01-17T14:00:00Z", signal.kickoff


# --------------------------------------------------------------------------
# I-08: calibrate_ev e diagnostico — o EV exibido permanece o BRUTO
# --------------------------------------------------------------------------


def test_signal_ev_is_raw_not_calibrated(monkeypatch):
    """O EV dos sinais NAO passa por calibrate_ev por baixo.

    Aplicar a correcao de populacao silenciosamente trocaria um numero
    inflado por um numero que finge ser calibrado — a dispersao do gap
    por sinal nunca foi medida. O aviso viaja SEPARADO
    (ModelCalibrationInfo -> API -> UI). Ver docstring de calibrate_ev.
    """
    from betgsn.real_signals import RealSnapshot

    fx = _fixture("Arsenal", "Chelsea")
    for book in fx.odds["Resultado Final (1X2)"].values():
        book["1"] = 10.0  # edge enorme: o sinal aparece com EV alto
    snap = RealSnapshot(
        fixtures=[fx],
        ratings={"Arsenal": _rating("Arsenal"), "Chelsea": _rating("Chelsea")},
        league_goals=2.7, teams=["Arsenal", "Chelsea"], n_history=100,
        history_window=("2025-01-01", "2026-01-01"),
        generated_at="2026-09-19T00:00:00Z",
        computed_in_ms=0.0, sources=["controlled-test"],
    )
    svc = RealSignalsService()
    monkeypatch.setattr(svc, "snapshot", lambda **kwargs: snap)
    report, _ = svc.report(market_keys=("1x2",))
    assert report.signals
    for signal in report.signals:
        # o EV transportado e o calculo bruto do motor: mp*odd - 1
        assert signal.ev == pytest.approx(
            signal.model_prob * signal.best_odd - 1.0)
        # e a correcao diagnostica mudaria materialmente o numero
        # (o gap medido e grande) — por isso ela nao pode ser aplicada
        # sem ter sido validada por segmento
        assert calibrate_ev(signal.ev) < signal.ev - 0.10


def test_decision_path_does_not_consume_calibrate_ev():
    """O caminho de decisao usa a vantagem VALIDADA, nao o EV do modelo.

    O hardening de calibracao (quando existir) tera que entrar pelo
    value_strategy/staking com evidencia propria — nunca pelo EV
    inflado do modelo. Este teste protege o contrato: enquanto
    calibrate_ev for diagnostico, decide_bet nao o consome.
    """
    import inspect

    from betgsn import staking
    from betgsn.api import service as api_service

    src_staking = inspect.getsource(staking)
    assert "calibrate_ev" not in src_staking
    assert "MODEL_CALIBRATION" not in src_staking

    src_api = inspect.getsource(api_service)
    assert "decide_bet" in src_api
    assert "calibrate_ev" not in src_api
