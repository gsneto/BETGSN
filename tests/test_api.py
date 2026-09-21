"""Testes da camada API do BETGSN.

Objetivo: garantir que a API NAO altera os numeros do pipeline e que os
contratos serializam. Toda comparacao e feita contra o pipeline chamado
diretamente — a API e apenas apresentacao.

    .venv\\Scripts\\python -m pytest tests -q
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from betgsn.api import schemas as S
from betgsn.api.server import app
from betgsn.api.service import BetgsnService
from betgsn.pipeline import run

CONFIG = S.ModelConfiguration()


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def svc_snapshot():
    svc = BetgsnService()
    return svc, svc.recalculate(CONFIG)


@pytest.fixture(scope="module")
def core():
    """Pipeline chamado diretamente, com os mesmos parametros do default."""
    return run(
        bankroll=CONFIG.bankroll,
        kelly_frac=CONFIG.kelly_fraction,
        stake_cap=CONFIG.stake_cap,
        min_ev=CONFIG.min_ev,
        use_xg=CONFIG.use_xg,
        rounds=CONFIG.rounds,
        max_exposure_frac=CONFIG.max_exposure,
    )


# --------------------------------------------------------------- paridade


def test_signal_count_matches_pipeline(svc_snapshot, core):
    svc, snap = svc_snapshot
    rep = svc.signal_report(snap)
    assert len(rep.signals) == len(core.report.signals)


def test_signal_numbers_are_untouched(svc_snapshot, core):
    """A API nao arredonda nem recalcula EV, edge, prob ou stake."""
    svc, snap = svc_snapshot
    rep = svc.signal_report(snap)
    for api_sig, core_sig in zip(rep.signals, core.report.signals):
        assert api_sig.match == core_sig.match
        assert api_sig.market == core_sig.market
        assert api_sig.outcome == core_sig.outcome
        assert api_sig.best_odd == core_sig.best_odd
        assert api_sig.best_book == core_sig.best_book
        assert api_sig.model_prob == core_sig.model_prob
        assert api_sig.market_prob == core_sig.market_prob
        assert api_sig.edge == core_sig.edge
        assert api_sig.ev == core_sig.ev
        assert api_sig.kelly == core_sig.kelly
        assert api_sig.stake == core_sig.stake
        assert api_sig.confidence == core_sig.confidence.value


def test_games_match_pipeline_analyses(svc_snapshot, core):
    svc, snap = svc_snapshot
    games = svc.games(snap)
    assert len(games) == len(core.analyses)
    for g, a in zip(games, core.analyses):
        assert g.lambda_home == a.lambdas[0]
        assert g.lambda_away == a.lambdas[1]
        m1 = a.markets["Resultado Final (1X2)"]
        assert g.prob_home == m1["1"]
        assert g.prob_draw == m1["X"]
        assert g.prob_away == m1["2"]


def test_1x2_probabilities_sum_to_one(svc_snapshot):
    svc, snap = svc_snapshot
    for g in svc.games(snap):
        assert g.prob_home + g.prob_draw + g.prob_away == pytest.approx(1.0, abs=1e-6)


def test_kpis_are_consistent_with_signals(svc_snapshot):
    svc, snap = svc_snapshot
    rep = svc.signal_report(snap)
    k = rep.kpis
    assert k.total == len(rep.signals)
    assert k.strong == sum(1 for s in rep.signals if s.confidence == "FORTE")
    assert k.medium == sum(1 for s in rep.signals if s.confidence == "MEDIA")
    assert k.weak == sum(1 for s in rep.signals if s.confidence == "FRACA")
    assert k.strong + k.medium + k.weak == k.total
    assert k.expected_profit == pytest.approx(
        sum(s.expected_profit for s in rep.signals), abs=0.02)
    assert k.total_exposure == pytest.approx(sum(s.stake for s in rep.signals), abs=0.02)


def test_exposure_respects_configured_cap(svc_snapshot):
    svc, snap = svc_snapshot
    k = svc.kpis(snap)
    assert k.total_exposure_pct <= CONFIG.max_exposure + 1e-6


def test_stake_cap_respected_per_signal(svc_snapshot):
    svc, snap = svc_snapshot
    rep = svc.signal_report(snap)
    assert all(s.stake_pct <= CONFIG.stake_cap + 1e-6 for s in rep.signals)


def test_min_ev_filter_respected(svc_snapshot):
    svc, snap = svc_snapshot
    rep = svc.signal_report(snap)
    assert all(s.ev >= CONFIG.min_ev for s in rep.signals)


def test_recalculate_is_deterministic(svc_snapshot):
    """Mesmo seed, mesma configuracao -> exatamente os mesmos numeros."""
    svc, snap = svc_snapshot
    first = [s.ev for s in svc.signal_report(snap).signals]
    snap2 = svc.recalculate(CONFIG)
    second = [s.ev for s in svc.signal_report(snap2).signals]
    assert first == second


def test_different_bankroll_scales_stakes_not_probabilities(svc_snapshot):
    svc, _ = svc_snapshot
    a = svc.signal_report(svc.recalculate(S.ModelConfiguration(bankroll=1000.0)))
    b = svc.signal_report(svc.recalculate(S.ModelConfiguration(bankroll=2000.0)))
    assert [s.model_prob for s in a.signals] == [s.model_prob for s in b.signals]
    assert [s.ev for s in a.signals] == [s.ev for s in b.signals]
    assert b.kpis.total_exposure > a.kpis.total_exposure


# ------------------------------------------------------------- serializacao


def test_odds_comparison_probabilities(svc_snapshot):
    svc, snap = svc_snapshot
    ov = svc.odds_overview(snap)
    mc = svc.market_comparison(snap, ov.matches[0], "Resultado Final (1X2)")
    assert mc.outcomes == ["1", "X", "2"]
    assert sum(mc.market_probs.values()) == pytest.approx(1.0, abs=1e-6)
    assert sum(mc.model_probs.values()) == pytest.approx(1.0, abs=1e-6)
    for oc, odd in mc.best_odds.items():
        assert all(odd >= r.odds[oc] for r in mc.rows)


def test_bookmaker_margins_are_plausible(svc_snapshot):
    svc, snap = svc_snapshot
    books = svc.odds_overview(snap).bookmakers
    assert books, "sem casas no snapshot"
    assert all(-0.01 < b.avg_margin < 0.15 for b in books)
    # Pinnacle e a casa de margem mais fina no gerador de odds
    assert min(books, key=lambda b: b.avg_margin).book == "Pinnacle"


def test_stats_breakdowns(svc_snapshot):
    svc, snap = svc_snapshot
    st = svc.stats(snap)
    rep = svc.signal_report(snap)
    assert sum(m.signals for m in st.by_market) == len(rep.signals)
    assert sum(c.signals for c in st.by_confidence) == len(rep.signals)
    assert sum(b.signals for b in st.by_book) == len(rep.signals)
    assert sum(e.count for e in st.ev_distribution) == len(rep.signals)
    assert len(st.teams) == 20


def test_model_contract(svc_snapshot):
    svc, snap = svc_snapshot
    md = svc.model(snap)
    assert md.constants.rho_dixon_coles == -0.05
    assert md.constants.ev_forte == 0.08
    assert md.n_markets == 9
    assert len(md.documentation) > 500


# ------------------------------------------------------------------ rotas


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"ok", "computing"}
    assert body["version"]


def test_dashboard_route(client):
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert body["n_games"] > 0
    assert body["kpis"]["total"] >= 0
    assert body["kpis"]["total"] == sum(body["kpis"][key] for key in ("strong", "medium", "weak"))


def test_signals_route(client):
    r = client.get("/api/signals")
    assert r.status_code == 200
    body = r.json()
    assert body["kpis"]["total"] == len(body["signals"])
    for signal in body["signals"]:
        for field in ("match", "market", "outcome", "best_odd", "model_prob",
                      "market_prob", "edge", "ev", "stake", "confidence"):
            assert field in signal
    evs = [s["ev"] for s in body["signals"]]
    assert evs == sorted(evs, reverse=True), "sinais devem vir ranqueados por EV"


def test_games_route(client):
    r = client.get("/api/games")
    assert r.status_code == 200
    assert len(r.json()) > 0


def test_odds_routes(client):
    r = client.get("/api/odds")
    assert r.status_code == 200
    ov = r.json()
    match = ov["matches"][0]
    r2 = client.get("/api/odds/comparison", params={"match": match})
    assert r2.status_code == 200
    assert r2.json()["rows"]


def test_odds_comparison_unknown_match_returns_404(client):
    r = client.get("/api/odds/comparison", params={"match": "Nao Existe vs Nada"})
    assert r.status_code == 404


def test_stats_and_model_routes(client):
    assert client.get("/api/stats").status_code == 200
    assert client.get("/api/model").status_code == 200


def test_recalculate_route(client):
    r = client.post("/api/recalculate", json=S.ModelConfiguration(
        bankroll=1500.0, min_ev=0.045).model_dump())
    assert r.status_code == 200
    body = r.json()
    assert body["configuration"]["bankroll"] == 1500.0
    r2 = client.get("/api/signals")
    assert all(s["ev"] >= 0.045 for s in r2.json()["signals"])
    # restaura o default para os demais testes de rota
    client.post("/api/recalculate", json=CONFIG.model_dump())


def test_recalculate_rejects_invalid_config(client):
    r = client.post("/api/recalculate", json={"bankroll": -5})
    assert r.status_code == 422


def test_model_performance_route(client):
    r = client.get("/api/model/performance", params={"split": 0.7, "min_ev": 0.03})
    assert r.status_code == 200
    body = r.json()
    assert body["n_bets"] >= 0
    assert body["logloss"] > 0
    assert body["calibration_bins"]


def test_websocket_status(client):
    with client.websocket_connect("/api/ws") as ws:
        # o servidor envia dois snapshots ao conectar: status do pipeline e
        # progresso do backtest
        primeiro = ws.receive_json()
        segundo = ws.receive_json()
        eventos = {primeiro["event"], segundo["event"]}
        assert "status" in eventos
        assert "backtest:progress" in eventos
        ws.send_text("ping")
        assert ws.receive_json()["event"] == "pong"
        ws.send_text("status")
        assert ws.receive_json()["event"] == "status"


# --------------------------------------------------------------------------
# fonte dos sinais: real vs sintetico
# --------------------------------------------------------------------------


def test_signals_defaults_to_real(client):
    """O padrao e dado real. Se nao houver, falha com instrucao — nao cai
    silenciosamente no dataset sintetico."""
    r = client.get("/api/signals")
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        body = r.json()
        assert body["source"] == "real"
        assert body["source_detail"]
        assert "football-data.co.uk" in body["source_detail"] or "real" in body["source_detail"]
    else:
        # sem dados: a mensagem precisa dizer como resolver
        assert "--import-fixtures-live" in r.json()["detail"]


def test_signals_synthetic_still_available(client):
    r = client.get("/api/signals", params={"source": "synthetic"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "synthetic"
    assert body["signals"]
    # o modo sintetico se identifica como tal
    assert "sintetiza" in body["source_detail"].lower()
    assert body["calibration"] is None


def test_signals_rejects_unknown_source(client):
    r = client.get("/api/signals", params={"source": "qualquer"})
    assert r.status_code == 422


def test_signals_status_endpoint(client):
    r = client.get("/api/signals/status")
    assert r.status_code == 200
    body = r.json()
    assert "real" in body and "synthetic" in body
    assert body["default_source"] in ("real", "synthetic")
    assert body["synthetic"]["available"] is True


def test_real_signals_carry_calibration_warning(client):
    """Sinal real precisa vir com o aviso do vies medido do modelo."""
    r = client.get("/api/signals", params={"source": "real"})
    if r.status_code != 200:
        pytest.skip("sem jogos futuros em cache neste ambiente")
    body = r.json()
    cal = body["calibration"]
    assert cal is not None, "sinal real sem aviso de calibracao"
    assert cal["gap_pp"] > 0
    assert cal["simulated_roi"] < 0
    assert "superconfiante" in cal["verdict"]
    assert body["source"] == "real"
    assert body["kpis"]["total"] == len(body["signals"])
    assert body["skipped_no_rating"] >= 0


def test_real_signals_use_real_odds(client):
    """As odds precisam vir de casas reais, nao do gerador sintetico."""
    r = client.get("/api/signals", params={"source": "real"})
    if r.status_code != 200:
        pytest.skip("sem jogos futuros em cache neste ambiente")
    books = {s["best_book"] for s in r.json()["signals"]}
    # o dataset sintetico usa nomes de casa brasileiras; o arquivo real usa
    # Betfair Exchange / Pinnacle / Bet365 / Paddy Power / SkyBet...
    reais = {"Betfair Exchange", "Pinnacle", "Bet365", "Paddy Power", "SkyBet",
             "Betfred", "BetVictor", "Bet&Win", "William Hill", "Ladbrokes",
             "Melhor do mercado", "Media do mercado"}
    if not books:
        pytest.skip("jogos atuais sem oportunidades acima do filtro de EV")
    assert books <= reais, f"casas inesperadas: {books - reais}"


# --------------------------------------------------------------------------
# novos endpoints (provider health, fixtures, movement, coverage, CLV)
# --------------------------------------------------------------------------


def test_health_with_providers(client):
    """GET /api/health deve incluir providers."""
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert "providers" in body
    assert isinstance(body["providers"], dict)


def test_providers_route(client):
    """GET /api/providers deve retornar observabilidade."""
    r = client.get("/api/providers")
    assert r.status_code == 200
    body = r.json()
    assert "providers" in body
    assert isinstance(body["providers"], list)
    for p in body["providers"]:
        assert "name" in p
        assert "status" in p
        assert p["status"] in {"CURRENT", "STALE", "UNAVAILABLE", "NO_COVERAGE", "DEGRADED"}


def test_fixtures_route(client):
    """GET /api/fixtures deve retornar lista de jogos."""
    r = client.get("/api/fixtures")
    assert r.status_code == 200
    body = r.json()
    assert "n_fixtures" in body
    assert "fixtures" in body
    assert isinstance(body["fixtures"], list)


def test_movement_route(client):
    """GET /api/movement deve retornar movimentos."""
    r = client.get("/api/movement")
    assert r.status_code == 200
    body = r.json()
    assert "movements" in body
    assert isinstance(body["movements"], list)


def test_coverage_route(client):
    """GET /api/coverage deve retornar cobertura."""
    r = client.get("/api/coverage")
    assert r.status_code == 200
    body = r.json()
    assert "clv_coverage" in body
    assert "odds_coverage" in body
    assert "providers" in body


def test_clv_route(client):
    """GET /api/clv deve retornar CLV."""
    r = client.get("/api/clv")
    assert r.status_code == 200
    body = r.json()
    assert "total_bets" in body
    assert "entries" in body
    assert isinstance(body["entries"], list)


def test_schema_types_are_typed():
    """Verifica que os novos schemas Pydantic existem e serializam."""
    from betgsn.api import schemas as S
    from betgsn.api import server as srv
    # ProviderHealth
    ph = S.ProviderHealth(name="test", status="CURRENT")
    assert ph.name == "test"
    assert ph.status == "CURRENT"
    # FixtureOverview
    fo = S.FixtureOverview(generated_at="2026-01-01", n_fixtures=0,
                            n_with_odds=0, fixtures=[], source="test")
    assert fo.n_fixtures == 0
    # CoverageReport — None = "não medido"; nunca 0.0 no lugar de None
    cr = S.CoverageReport(generated_at="2026-01-01", providers=[],
                           clv_coverage=0.5, odds_coverage=0.5,
                           xg_coverage=None,
                           n_fixtures_with_odds=1, n_fixtures_total=2,
                           n_bookmakers_active=1,
                           bookmakers_observed=["Pinnacle"],
                           gaps=[], source="test")
    assert cr.clv_coverage == 0.5
    assert cr.xg_coverage is None
    # ClvReport
    clr = S.ClvReport(generated_at="2026-01-01", total_bets=1, bets_with_clv=1,
                       coverage=1.0, entries=[], source="test")
    assert clr.total_bets == 1


# --------------------------------------------------------------------------
# contratos de odds na API: bookmaker != mercado != resultado != odd
# --------------------------------------------------------------------------


def _upcoming_fixture():
    """Jogo futuro real no MESMO formato do Data Layer.

    `odds` = {mercado: {casa: {resultado: odd}}}, com varios mercados e
    resultados. E o formato que a API precisa adaptar sem confundir os
    niveis (casa / mercado / resultado / odd).
    """
    from betgsn.football_data_uk import UpcomingFixture

    odds = {
        "Resultado Final (1X2)": {
            "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
            "Bet365": {"1": 1.95, "X": 3.35, "2": 4.10},
        },
        "Total de Gols": {
            "Bet365": {"Over 2.5": 1.80, "Under 2.5": 2.05},
            "Pinnacle": {"Over 2.5": 1.85, "Under 2.5": 2.00},
        },
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
        date="2026-09-20", time="14:00", timezone="Europe/London",
        home="Arsenal", away="Chelsea",
        odds=odds, best_odds=best, best_books=who,
    )


def _patch_fixture_source(monkeypatch, tmp_path, fixtures):
    """Serve fixtures controlados e isola o store de snapshots em tmp."""
    from betgsn import odds_snapshots as snap_mod
    from betgsn.football_data_uk import FootballDataClient

    real_store = snap_mod.OddsSnapshotStore
    monkeypatch.setattr(FootballDataClient, "load_fixtures",
                        lambda self: list(fixtures))
    monkeypatch.setattr(
        FootballDataClient, "fixtures_inventory",
        lambda self: {"available": True, "n_fixtures": len(fixtures)},
    )
    monkeypatch.setattr(FootballDataClient, "corpus_signature",
                        lambda self: "test-signature")
    monkeypatch.setattr(
        snap_mod, "OddsSnapshotStore",
        lambda *args, **kwargs: real_store(tmp_path / "odds.db"),
    )


def test_fixtures_best_odds_is_flat_numeric_map(monkeypatch, tmp_path):
    """best_odds precisa ser dict[str, float] com a odd REAL por resultado.

    Nada de {casa: {resultado: odd}} (o que o schema rejeita) nem de
    valores fabricados: cada odd e o maximo entre as casas da linha.
    """
    fixture = _upcoming_fixture()
    _patch_fixture_source(monkeypatch, tmp_path, [fixture])

    item = BetgsnService().fixtures().fixtures[0]
    assert item.best_odds is not None
    assert all(isinstance(value, float) for value in item.best_odds.values())
    assert item.best_odds == {
        "1": 1.95, "X": 3.40, "2": 4.20,
        "Over 2.5": 1.85, "Under 2.5": 2.05,
    }
    for market, books in fixture.odds.items():
        for oc, best in fixture.best_odds[market].items():
            assert item.best_odds[oc] == best


def test_fixtures_bookmakers_are_books_not_markets(monkeypatch, tmp_path):
    fixture = _upcoming_fixture()
    _patch_fixture_source(monkeypatch, tmp_path, [fixture])

    item = BetgsnService().fixtures().fixtures[0]
    assert set(item.bookmakers) == {"Bet365", "Pinnacle"}
    assert item.n_bookmakers == 2
    assert set(item.markets) == {"Resultado Final (1X2)", "Total de Gols"}
    assert set(item.bookmakers).isdisjoint(item.markets)


def test_movement_never_treats_market_or_book_as_kickoff(monkeypatch, tmp_path):
    """O resultado e o resultado; mercado e casa jamais viram kickoff."""
    fixture = _upcoming_fixture()
    _patch_fixture_source(monkeypatch, tmp_path, [fixture])

    overview = BetgsnService().movement()
    assert overview.movements
    outcomes = {m.outcome for m in overview.movements}
    assert outcomes <= {"1", "X", "2", "Over 2.5", "Under 2.5"}
    assert outcomes.isdisjoint({"Bet365", "Pinnacle"})
    # sem snapshots persistidos o status e explicito, nunca inventado
    assert all(m.status == "NO_DATA" for m in overview.movements)
    for m in overview.movements:
        assert m.current_odd == fixture.best_odds[m.market][m.outcome]


def test_clv_entry_odd_is_numeric_and_matches_outcome(monkeypatch, tmp_path):
    """CLV recebe odd float da linha correta; nunca um dict."""
    fixture = _upcoming_fixture()
    _patch_fixture_source(monkeypatch, tmp_path, [fixture])

    report = BetgsnService().clv()
    assert report.entries
    assert report.total_bets == len(report.entries)
    for entry in report.entries:
        assert isinstance(entry.entry_odd, float)
        assert entry.outcome in fixture.best_odds[entry.market]
        assert entry.entry_odd == fixture.best_odds[entry.market][entry.outcome]
