"""Testes de VALOR da semantica do CoverageReport (auditoria H2).

O contrato promete — e estes testes conferem NUMEROS, nao so chaves:

  - bookmaker ativo = bookmaker OBSERVADO nas odds dos fixtures. Chave
    de provider configurada NAO conta como bookmaker;
  - xG coverage so existe com evidencia real; sem evidencia e None;
  - CLV coverage vem do store canonico (C2), medido sobre as linhas
    apostaveis dos fixtures — nao sobre contagens de observacoes;
  - None = "nao medido" e 0.0 = "medido e zero": um nunca substitui o
    outro;
  - multiplos bookmakers e multiplos fixtures entram na conta.

O store e injetado em SQLite temporario (nunca o default) e os fixtures
sao monkeypatched — nada aqui depende de rede nem de cache real.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from betgsn.football_data_uk import UpcomingFixture
from betgsn.odds_normalize import event_key
from betgsn.odds_snapshots import OddsObservation, OddsSnapshotStore
from betgsn.timeutil import utc_key

MARKET = "Resultado Final (1X2)"
OU_MARKET = "Total de Gols"
KICKOFF = "2030-01-01T12:00:00Z"
PROVIDER_KEYS = (
    "BETGSN_ODDS_API_KEY",
    "BETGSN_PARLAY_API_KEY",
    "BETGSN_APIFOOTBALL_KEY",
    "BETGSN_FOOTBALLDATA_KEY",
)


def _fixture(
    home: str,
    away: str,
    books: dict[str, dict[str, float]],
    *,
    ou_books: dict[str, dict[str, float]] | None = None,
    date: str = "2030-01-01",
) -> UpcomingFixture:
    """Fixture com odds no formato do Data Layer ({mercado:{casa:{resultado:odd}}})."""
    odds: dict[str, dict[str, dict[str, float]]] = {MARKET: books}
    if ou_books:
        odds[OU_MARKET] = ou_books

    best_odds: dict[str, dict[str, float]] = {}
    best_books: dict[str, dict[str, str]] = {}
    for market, market_books in odds.items():
        best: dict[str, float] = {}
        who: dict[str, str] = {}
        for book, outcomes in market_books.items():
            for oc, odd in outcomes.items():
                if odd and (oc not in best or odd > best[oc]):
                    best[oc] = odd
                    who[oc] = book
        best_odds[market] = best
        best_books[market] = who

    return UpcomingFixture(
        division="E0",
        league="Premier League (England)",
        date=date,
        time="12:00",
        timezone="UTC",
        home=home,
        away=away,
        odds=odds,
        best_odds=best_odds,
        best_books=best_books,
    )


def _patch_env(monkeypatch, tmp_path, fixtures: list[UpcomingFixture]):
    """Fixtures injetados + store em SQLite temporario + ambiente limpo.

    Devolve o caminho do banco para o teste gravar observacoes.
    """
    from betgsn.football_data_uk import FootballDataClient
    import betgsn.odds_snapshots as snap_mod

    monkeypatch.setattr(FootballDataClient, "load_fixtures", lambda self: list(fixtures))
    db = tmp_path / "odds.db"
    real = snap_mod.OddsSnapshotStore
    monkeypatch.setattr(snap_mod, "OddsSnapshotStore", lambda *a, **k: real(db))
    for key in PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)
    return db


def _closing_obs(
    fixture: UpcomingFixture,
    outcome: str,
    odd: float,
    market: str = MARKET,
    bookmaker: str = "Pinnacle",
    minutes_before: float = 30.0,
) -> OddsObservation:
    """Observacao de fechamento valida (antes do kickoff, dentro da janela)."""
    from datetime import datetime, timedelta, timezone

    kickoff_dt = datetime.strptime(
        f"{fixture.date} {fixture.time or '00:00'}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=timezone.utc)
    ts = kickoff_dt - timedelta(minutes=minutes_before)
    return OddsObservation(
        match_key=fixture.event_key,
        market=market,
        outcome=outcome,
        bookmaker=bookmaker,
        odd=odd,
        timestamp=ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        kickoff=utc_key(fixture.kickoff, fixture.timezone),
        provider="The Odds API",
    )


# --------------------------------------------------------------------------
# bookmakers: observados, nao configurados
# --------------------------------------------------------------------------


def test_bookmakers_active_are_the_observed_ones(monkeypatch, tmp_path):
    """Multiplos bookmakers contados pelo que aparece nas odds dos fixtures."""
    from betgsn.api.service import BetgsnService

    fx1 = _fixture("Arsenal", "Chelsea", {
        "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
        "Bet365": {"1": 1.95, "X": 3.35, "2": 4.10},
    })
    fx2 = _fixture("Liverpool", "Everton", {
        "Betfair Exchange": {"1": 2.20, "X": 3.30, "2": 3.10},
    })
    _patch_env(monkeypatch, tmp_path, [fx1, fx2])

    rep = BetgsnService().coverage()
    assert rep.n_bookmakers_active == 3
    assert rep.bookmakers_observed == ["Bet365", "Betfair Exchange", "Pinnacle"]


def test_configured_provider_key_is_not_a_bookmaker(monkeypatch, tmp_path):
    """Chave de API configurada sem odds observadas nao vira bookmaker."""
    from betgsn.api.service import BetgsnService

    fx = _fixture("Arsenal", "Chelsea", {
        "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
    })
    _patch_env(monkeypatch, tmp_path, [fx])
    # TRES providers com chave configurada: nenhum deles aparece nas odds
    for key in PROVIDER_KEYS[:3]:
        monkeypatch.setenv(key, "chave-de-teste")

    rep = BetgsnService().coverage()
    assert rep.n_bookmakers_active == 1
    assert rep.bookmakers_observed == ["Pinnacle"]


def test_no_bookmaker_without_any_evidence(monkeypatch, tmp_path):
    """Sem fixture observado, zero bookmakers — mesmo com chaves configuradas."""
    from betgsn.api.service import BetgsnService

    _patch_env(monkeypatch, tmp_path, [])
    for key in PROVIDER_KEYS:
        monkeypatch.setenv(key, "chave-de-teste")

    rep = BetgsnService().coverage()
    assert rep.n_bookmakers_active == 0
    assert rep.bookmakers_observed == []


# --------------------------------------------------------------------------
# xG: sem evidencia real, a medicao e ausente (None), nunca 0.0
# --------------------------------------------------------------------------


def test_xg_coverage_is_none_without_real_evidence(monkeypatch, tmp_path):
    from betgsn.api.service import BetgsnService

    fx = _fixture("Arsenal", "Chelsea", {
        "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
    })
    _patch_env(monkeypatch, tmp_path, [fx])
    # ate mesmo uma chave de API-Football (fonte de xG) configurada nao e
    # evidencia de xG observado
    monkeypatch.setenv("BETGSN_APIFOOTBALL_KEY", "chave-de-teste")

    rep = BetgsnService().coverage()
    assert rep.xg_coverage is None
    assert any(g["gap"] == "no_real_xg_source" for g in rep.gaps)


# --------------------------------------------------------------------------
# CLV: fonte canonica (store), populacao = linhas apostaveis
# --------------------------------------------------------------------------


def test_clv_coverage_from_canonical_store_partial(monkeypatch, tmp_path):
    """1 de 3 linhas apostaveis com fechamento valido -> 1/3."""
    from betgsn.api.service import BetgsnService

    fx = _fixture("Arsenal", "Chelsea", {
        "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
    })
    db = _patch_env(monkeypatch, tmp_path, [fx])
    OddsSnapshotStore(db).add([
        _closing_obs(fx, "1", 1.80),
    ])

    rep = BetgsnService().coverage()
    assert rep.clv_coverage == pytest.approx(1 / 3)


def test_clv_coverage_measured_zero_when_no_closing(monkeypatch, tmp_path):
    """Ha linhas apostaveis e nenhuma com fechamento: ZERO MEDIDO, nao None."""
    from betgsn.api.service import BetgsnService

    fx = _fixture("Arsenal", "Chelsea", {
        "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
    })
    _patch_env(monkeypatch, tmp_path, [fx])  # store vazio

    rep = BetgsnService().coverage()
    assert rep.clv_coverage == 0.0


def test_clv_coverage_multiple_fixtures_and_markets(monkeypatch, tmp_path):
    """2/8 linhas com fechamento valido entre 2 fixtures e 2 mercados."""
    from betgsn.api.service import BetgsnService

    fx1 = _fixture("Arsenal", "Chelsea", {
        "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
    }, ou_books={
        "Bet365": {"Over 2.5": 1.80, "Under 2.5": 2.05},
    })  # 5 linhas apostaveis
    fx2 = _fixture("Liverpool", "Everton", {
        "Pinnacle": {"1": 2.20, "X": 3.30, "2": 3.10},
    }, date="2030-01-02")  # 3 linhas apostaveis
    db = _patch_env(monkeypatch, tmp_path, [fx1, fx2])
    OddsSnapshotStore(db).add([
        _closing_obs(fx1, "1", 1.80),
        _closing_obs(fx2, "X", 3.25),
    ])

    rep = BetgsnService().coverage()
    assert rep.clv_coverage == pytest.approx(2 / 8)


def test_clv_coverage_ignores_observation_outside_window(monkeypatch, tmp_path):
    """Observacao 3 dias antes do kickoff nao e fechamento: nao conta."""
    from betgsn.api.service import BetgsnService

    fx = _fixture("Arsenal", "Chelsea", {
        "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
    })
    db = _patch_env(monkeypatch, tmp_path, [fx])
    OddsSnapshotStore(db).add([
        _closing_obs(fx, "1", 1.80, minutes_before=3 * 24 * 60),
    ])

    rep = BetgsnService().coverage()
    assert rep.clv_coverage == 0.0


# --------------------------------------------------------------------------
# ausencia de dados: None, nunca 0.0
# --------------------------------------------------------------------------


def test_absence_of_data_is_null_not_zero(monkeypatch, tmp_path):
    from betgsn.api.service import BetgsnService

    _patch_env(monkeypatch, tmp_path, [])

    rep = BetgsnService().coverage()
    assert rep.n_fixtures_total == 0
    assert rep.n_fixtures_with_odds == 0
    assert rep.odds_coverage is None
    assert rep.clv_coverage is None
    assert rep.xg_coverage is None
    assert rep.n_bookmakers_active == 0
    assert any(g["gap"] == "no_bettable_lines" for g in rep.gaps)


# --------------------------------------------------------------------------
# fixtures e odds: contagem e fracao medidas sobre o que existe
# --------------------------------------------------------------------------


def test_odds_coverage_fraction_over_observed_fixtures(monkeypatch, tmp_path):
    from betgsn.api.service import BetgsnService

    fx1 = _fixture("Arsenal", "Chelsea", {
        "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
    })
    fx2 = _fixture("Liverpool", "Everton", {
        "Bet365": {"1": 2.20, "X": 3.30, "2": 3.10},
    }, date="2030-01-02")
    fx3 = UpcomingFixture(
        division="E0", league="Premier League (England)",
        date="2030-01-03", time="12:00", timezone="UTC",
        home="Leeds", away="Fulham",
    )  # sem odds
    _patch_env(monkeypatch, tmp_path, [fx1, fx2, fx3])

    rep = BetgsnService().coverage()
    assert rep.n_fixtures_total == 3
    assert rep.n_fixtures_with_odds == 2
    assert rep.odds_coverage == pytest.approx(2 / 3)


def test_all_fixtures_with_odds_is_full_coverage(monkeypatch, tmp_path):
    from betgsn.api.service import BetgsnService

    fx1 = _fixture("Arsenal", "Chelsea", {
        "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
    })
    fx2 = _fixture("Liverpool", "Everton", {
        "Bet365": {"1": 2.20, "X": 3.30, "2": 3.10},
    }, date="2030-01-02")
    _patch_env(monkeypatch, tmp_path, [fx1, fx2])

    rep = BetgsnService().coverage()
    assert rep.odds_coverage == 1.0
    assert rep.n_fixtures_with_odds == rep.n_fixtures_total == 2


# --------------------------------------------------------------------------
# contrato serializado pela rota
# --------------------------------------------------------------------------


def test_coverage_route_serializes_absence_as_null(monkeypatch, tmp_path):
    from betgsn.api.server import app

    _patch_env(monkeypatch, tmp_path, [])
    with TestClient(app) as client:
        body = client.get("/api/coverage").json()

    assert body["xg_coverage"] is None
    assert body["odds_coverage"] is None
    assert body["clv_coverage"] is None
    assert body["n_bookmakers_active"] == 0
    assert body["bookmakers_observed"] == []
    assert "fixtures_coverage" not in body


def test_coverage_route_reports_observed_bookmakers(monkeypatch, tmp_path):
    from betgsn.api.server import app

    fx = _fixture("Arsenal", "Chelsea", {
        "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
        "Bet365": {"1": 1.95, "X": 3.35, "2": 4.10},
    })
    db = _patch_env(monkeypatch, tmp_path, [fx])
    OddsSnapshotStore(db).add([_closing_obs(fx, "1", 1.80)])
    with TestClient(app) as client:
        body = client.get("/api/coverage").json()

    assert body["n_bookmakers_active"] == 2
    assert body["bookmakers_observed"] == ["Bet365", "Pinnacle"]
    assert body["clv_coverage"] == pytest.approx(1 / 3)
    assert body["odds_coverage"] == 1.0


# --------------------------------------------------------------------------
# identidade canônica: a bet usa a MESMA chave que o store grava
# --------------------------------------------------------------------------


def test_clv_bets_use_canonical_event_key(monkeypatch, tmp_path):
    """A leitura do CLV encontra o que foi gravado sob `event_key` (C2)."""
    from betgsn.api.service import BetgsnService

    fx = _fixture("Arsenal", "Chelsea", {
        "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
    })
    db = _patch_env(monkeypatch, tmp_path, [fx])
    # grava pela chave canonica, como o produtor faz
    assert fx.event_key == event_key("Arsenal", "Chelsea", utc_key(KICKOFF))
    OddsSnapshotStore(db).add([
        _closing_obs(fx, "1", 1.80),
        _closing_obs(fx, "X", 3.30),
        _closing_obs(fx, "2", 4.00),
    ])

    rep = BetgsnService().coverage()
    assert rep.clv_coverage == 1.0
