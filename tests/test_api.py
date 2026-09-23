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
    # 503 = cache de fixtures real sem jogos futuros (cache vencido entre
    # rodadas do site): o caminho real falha EXPLICITO, nunca cai no
    # sintetico. Ver test_signals_defaults_to_real.
    assert r.status_code in (200, 503)
    if r.status_code != 200:
        return
    body = r.json()
    assert body["n_games"] > 0
    assert body["kpis"]["total"] >= 0
    assert body["kpis"]["total"] == sum(body["kpis"][key] for key in ("strong", "medium", "weak"))


def test_signals_route(client):
    r = client.get("/api/signals")
    assert r.status_code in (200, 503)
    if r.status_code != 200:
        return
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
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        assert len(r.json()) > 0


def test_odds_routes(client):
    r = client.get("/api/odds")
    assert r.status_code in (200, 503)
    if r.status_code != 200:
        return
    ov = r.json()
    match = ov["matches"][0]
    r2 = client.get("/api/odds/comparison", params={"match": match})
    assert r2.status_code == 200
    assert r2.json()["rows"]


def test_odds_comparison_unknown_match_returns_404(client):
    r = client.get("/api/odds/comparison", params={"match": "Nao Existe vs Nada"})
    # 404 com dados presentes; 503 quando o cache real esta sem jogos
    # futuros (a verificacao de match so roda com snapshot disponivel).
    assert r.status_code in (404, 503)


def test_stats_and_model_routes(client):
    assert client.get("/api/stats").status_code in (200, 503)
    assert client.get("/api/model").status_code in (200, 503)


def test_recalculate_route(client):
    """/api/recalculate e assincrono: devolve o job imediatamente (200)."""
    r = client.post("/api/recalculate", json=S.ModelConfiguration(
        bankroll=1500.0, min_ev=0.045).model_dump())
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"]
    assert body["phase"] in ("starting", "fixtures", "history", "ratings",
                             "analyzing", "signals", "done", "error")
    assert 0.0 <= body["progress"] <= 1.0

    # job em execucao: um segundo POST e recusado (409), nao enfileirado
    dup = client.post("/api/recalculate", json=CONFIG.model_dump())
    assert dup.status_code in (200, 409)

    # aguarda a conclusao do job (timeout generoso: pipeline real)
    import time as _time
    deadline = _time.monotonic() + 300.0
    final = None
    while _time.monotonic() < deadline:
        final = client.get("/api/recalculate/status").json()
        if final["phase"] in ("done", "error", "cancelled"):
            break
        _time.sleep(0.25)
    assert final is not None and final["phase"] in ("done", "error", "cancelled")

    if final["phase"] == "done":
        assert final["progress"] == 1.0
        assert final["has_snapshot"] is True
        assert final["snapshot_generated_at"]
        r2 = client.get("/api/signals")
        assert r2.status_code in (200, 503)
        if r2.status_code == 200:
            assert all(s["ev"] >= 0.045 for s in r2.json()["signals"])
    # restaura o default para os demais testes de rota
    client.post("/api/recalculate", json=CONFIG.model_dump())
    deadline = _time.monotonic() + 300.0
    while _time.monotonic() < deadline:
        if client.get("/api/recalculate/status").json()["phase"] in (
                "done", "error", "cancelled", "idle"):
            break
        _time.sleep(0.25)


def test_recalculate_status_shape(client):
    body = client.get("/api/recalculate/status").json()
    for field in ("job_id", "phase", "progress", "message", "error",
                  "snapshot_generated_at", "has_snapshot"):
        assert field in body
    assert 0.0 <= body["progress"] <= 1.0


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
        # o servidor envia tres snapshots ao conectar: status do pipeline,
        # progresso do backtest e progresso do recalculo
        eventos = set()
        for _ in range(3):
            msg = ws.receive_json()
            eventos.add(msg["event"])
        assert "status" in eventos
        assert "backtest:progress" in eventos
        assert "recalculate:progress" in eventos
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
    if not books:
        pytest.skip("jogos atuais sem oportunidades acima do filtro de EV")
    # o dataset sintetico usa casas brasileiras que NUNCA aparecem nas
    # fontes reais (football-data.co.uk / The Odds API). As fontes reais
    # trazem dezenas de casas (Betfair Exchange, Pinnacle, Codere,
    # Unibet...) — a lista muda com o tempo, entao o teste verifica o
    # que seria ERRO: uma casa exclusiva do gerador sintetico vazando
    # para o modo real.
    sinteticas = {"Betano", "KTO", "Novibet", "EstrelaBet",
                  "Betnacional", "Superbet"}
    assert books.isdisjoint(sinteticas), (
        f"casas exclusivas do dataset sintetico no modo real: "
        f"{books & sinteticas}"
    )


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
        # vocabulario canonico do dominio (odds_health.ProviderState) +
        # UNKNOWN para provider sem observacao. "CURRENT" nao existe.
        assert p["status"] in {
            "HEALTHY", "DEGRADED", "UNAVAILABLE", "STALE", "NO_COVERAGE",
            "UNKNOWN",
        }


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
    # ProviderHealth — vocabulario do dominio; "CURRENT" e invalido
    ph = S.ProviderHealth(name="test", status="HEALTHY")
    assert ph.name == "test"
    assert ph.status == "HEALTHY"
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        S.ProviderHealth(name="test", status="CURRENT")
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
    """CLV expoe odd float da linha correta — a mediana REAL do store.

    Sem entrada registrada a linha fica NO_ENTRY_ODDS com entry_odd None
    (nunca um dict, nunca o best_odds atual do fixture). Com entrada,
    entry_odd e a mediana PIT das casas observadas.
    """
    from betgsn.odds_snapshots import OddsObservation, OddsSnapshotStore

    fixture = _upcoming_fixture()
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    store.add([
        OddsObservation(
            match_key=fixture.event_key,
            market="Resultado Final (1X2)", outcome="1",
            bookmaker="Pinnacle", odd=1.90, timestamp="2026-09-20T10:00:00Z",
            kickoff="2026-09-20T13:00:00Z", provider="The Odds API",
        ),
        OddsObservation(
            match_key=fixture.event_key,
            market="Resultado Final (1X2)", outcome="1",
            bookmaker="Bet365", odd=2.10, timestamp="2026-09-20T10:00:00Z",
            kickoff="2026-09-20T13:00:00Z", provider="The Odds API",
        ),
    ])
    _patch_fixture_source(monkeypatch, tmp_path, [fixture])

    # sem registro: todas as linhas NO_ENTRY_ODDS, entry_odd None
    report = BetgsnService().clv()
    assert report.entries
    assert report.total_bets == len(report.entries)
    for entry in report.entries:
        assert entry.status == "NO_ENTRY_ODDS"
        assert entry.entry_odd is None
        assert entry.entry_timestamp is None

    # registro real (line_at + register_entry — o caminho do fluxo live):
    # mediana das duas casas (1.90, 2.10) = 2.00, nao o best_odds (1.95)
    line = store.line_at(
        fixture.event_key, "Resultado Final (1X2)", "1",
        "2026-09-20T10:30:00Z",
    )
    assert line is not None and line.odd == 2.00
    assert store.register_entry(
        match_key=fixture.event_key, market="Resultado Final (1X2)",
        outcome="1", entry_odd=line.odd, entry_timestamp=line.timestamp,
        entry_n_books=line.n_books, kickoff="2026-09-20T13:00:00Z",
        prediction_timestamp="2026-09-20T10:30:00Z",
    )

    report = BetgsnService().clv()
    entry = next(
        e for e in report.entries
        if e.market == "Resultado Final (1X2)" and e.outcome == "1"
    )
    assert isinstance(entry.entry_odd, float)
    assert entry.entry_odd == 2.00
    assert entry.entry_odd != fixture.best_odds["Resultado Final (1X2)"]["1"]
    assert entry.outcome in fixture.best_odds[entry.market]


# --------------------------------------------------------------------------
# contrato temporal: kickoff/prediction_timestamp em UTC canonico
# --------------------------------------------------------------------------

import re  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

UTC_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_fixtures_kickoff_is_utc_instant_with_local_preserved(monkeypatch, tmp_path):
    """kickoff e o INSTANTE em UTC canonico; local e fuso nao se perdem.

    14:00 em Londres no verao (BST, UTC+1) = 13:00Z. Nao se poe "Z" na
    hora local: a conversao usa o fuso IANA da liga.
    """
    fixture = _upcoming_fixture()  # 2026-09-20 14:00 Europe/London (BST)
    _patch_fixture_source(monkeypatch, tmp_path, [fixture])

    item = BetgsnService().fixtures().fixtures[0]
    assert item.kickoff == "2026-09-20T13:00:00Z"
    assert UTC_STAMP_RE.match(item.kickoff)
    assert item.kickoff_local == "2026-09-20 14:00"
    assert item.timezone == "Europe/London"


def test_fixtures_kickoff_respects_dst(monkeypatch, tmp_path):
    """O fuso da liga muda com o horario de verao — e o instante muda junto.

    Mesmo horario local (14:00 em Londres): 13:00Z no verao (BST) e
    14:00Z no inverno (GMT). Offset fixo quebraria um dos dois.
    """
    from dataclasses import replace

    summer = _upcoming_fixture()                            # 2026-09-20 (BST)
    winter = replace(_upcoming_fixture(), date="2026-01-17")  # (GMT)
    _patch_fixture_source(monkeypatch, tmp_path, [summer, winter])

    items = BetgsnService().fixtures().fixtures
    by_date = {i.kickoff_local[:10]: i for i in items}
    assert by_date["2026-09-20"].kickoff == "2026-09-20T13:00:00Z"
    assert by_date["2026-01-17"].kickoff == "2026-01-17T14:00:00Z"


def test_fixtures_kickoff_is_round_trip_stable(monkeypatch, tmp_path):
    """A chave UTC canonica e estavel: reparsear nao muda o instante."""
    from betgsn.timeutil import utc_key

    fixture = _upcoming_fixture()
    _patch_fixture_source(monkeypatch, tmp_path, [fixture])

    item = BetgsnService().fixtures().fixtures[0]
    assert utc_key(item.kickoff) == item.kickoff


def test_signals_and_games_kickoff_are_canonical_utc(svc_snapshot):
    """kickoff representa o mesmo tipo de instante em todos os endpoints."""
    svc, snap = svc_snapshot
    rep = svc.signal_report(snap)
    games = svc.games(snap)
    assert rep.signals and games
    for s in rep.signals:
        assert UTC_STAMP_RE.match(s.kickoff), s.kickoff
    for g in games:
        assert UTC_STAMP_RE.match(g.kickoff), g.kickoff


def test_signal_report_prediction_timestamp_is_real_utc_instant(svc_snapshot):
    """prediction_timestamp/generated_at sao instantes reais em UTC.

    Uma hora local sem offset, lida como UTC, deslocaria o instante da
    previsao — e com ele o corte point-in-time — pelo fuso da maquina.
    """
    svc, snap = svc_snapshot
    rep = svc.signal_report(snap)
    assert UTC_STAMP_RE.match(rep.generated_at)
    assert rep.provenance.prediction_timestamp == rep.generated_at
    ts = datetime.strptime(rep.generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - ts).total_seconds()) < 3600.0


def test_movement_passes_utc_instants_as_cutoff(monkeypatch, tmp_path):
    """O cutoff do movimento recebe kickoff UTC, nunca a hora local.

    `fx.kickoff` e hora LOCAL da competicao; sem a conversao com o fuso
    da liga, o corte temporal (e o minutes_to_kickoff) seria deslocado.
    """
    from betgsn.features import movement as movement_mod

    fixture = _upcoming_fixture()  # 14:00 Londres (BST) -> 13:00Z
    _patch_fixture_source(monkeypatch, tmp_path, [fixture])

    calls: list[tuple[str, str]] = []

    def fake(points, market, outcome, prediction_timestamp, kickoff):
        calls.append((prediction_timestamp, kickoff))
        return {}

    monkeypatch.setattr(movement_mod, "movement_features", fake)
    overview = BetgsnService().movement()
    assert overview.movements
    assert calls
    for prediction_timestamp, kickoff in calls:
        assert UTC_STAMP_RE.match(prediction_timestamp), prediction_timestamp
        assert kickoff == "2026-09-20T13:00:00Z", kickoff
    assert UTC_STAMP_RE.match(overview.generated_at)


# --------------------------------------------------------------------------
# contrato NO BET: a decisao do Quant chega inteira a API e ao frontend
# --------------------------------------------------------------------------


def test_signals_route_carries_quant_decision(client):
    """A decisao (BET | NO_BET) e um campo de primeira classe do relatorio."""
    r = client.get("/api/signals", params={"source": "synthetic"})
    assert r.status_code == 200
    decision = r.json()["decision"]
    assert decision is not None
    assert decision["action"] == "NO_BET", (
        "odds sinteticas de demonstracao nao sao evidencia confiavel"
    )
    assert decision["reason"]
    assert decision["fraction"] == 0.0, "NO_BET nao cria stake"
    assert decision["checks"], "as verificacoes do Quant precisam chegar"


def test_real_signals_preserve_no_bet(client):
    """Odds reais sem timestamp de publicacao -> o Quant decide NO_BET.

    A decisao vem do Quant (staking.decide_bet), nao de uma regra da API:
    o motivo e as verificacoes falhas sao preservados.
    """
    r = client.get("/api/signals", params={"source": "real"})
    if r.status_code != 200:
        pytest.skip("sem jogos futuros em cache neste ambiente")
    decision = r.json()["decision"]
    assert decision is not None
    assert decision["action"] == "NO_BET"
    assert "evidencia_confiavel" in decision["reason"]
    assert decision["fraction"] == 0.0
    failed = [c for c in decision["checks"] if not c["passed"]]
    assert failed, "NO_BET precisa expor qual verificacao falhou"
    assert any(c["name"] == "evidencia_confiavel" for c in failed)


def test_quant_decision_is_not_hardcoded(svc_snapshot):
    """A API nao fabrica NO_BET: com evidencia confiavel o Quant diz BET."""
    svc, _ = svc_snapshot
    decision = svc._quant_decision("timestamped")
    assert decision.action == "BET"
    assert decision.should_bet is True
    assert 0 < decision.fraction <= 0.05
    assert all(c.passed for c in decision.checks)
    assert {c.name for c in decision.checks} == {
        "evidencia_confiavel", "limite_inferior_positivo",
        "amostra_suficiente", "ruina_toleravel",
    }


def test_quant_decision_no_bet_when_evidence_is_exploratory(svc_snapshot):
    svc, _ = svc_snapshot
    decision = svc._quant_decision("exploratory")
    assert decision.action == "NO_BET"
    assert decision.fraction == 0.0
    assert "evidencia_confiavel" in decision.reason
    assert not decision.should_bet


def test_decision_schema_rejects_unknown_action():
    """action so aceita BET | NO_BET — nada de terceiro estado ambiguo."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        S.BetDecision(action="TALVEZ", reason="x")

# --------------------------------------------------------------------------
# I-12: evidence_status atravessa data/source -> decisao -> API intacto
# --------------------------------------------------------------------------


def test_decision_carries_evidence_status_synthetic(client):
    """O status da evidencia viaja como campo, nao como texto de check."""
    r = client.get("/api/signals", params={"source": "synthetic"})
    assert r.status_code == 200
    decision = r.json()["decision"]
    assert decision is not None
    assert decision["evidence_status"] == "synthetic"
    # e o mesmo status que fundamentou o NO_BET
    assert decision["action"] == "NO_BET"


def test_decision_carries_evidence_status_real_exploratory(client):
    """Odds reais sem timestamp de publicacao: exploratory chega exploratory.

    A existencia de uma previsao NAO promove "exploratory" a
    "validated": sao estados de evidencia, nao de output.
    """
    r = client.get("/api/signals", params={"source": "real"})
    if r.status_code != 200:
        pytest.skip("sem jogos futuros em cache neste ambiente")
    decision = r.json()["decision"]
    assert decision is not None
    assert decision["evidence_status"] == "exploratory"
    assert decision["action"] == "NO_BET"


def test_decision_evidence_status_is_the_one_given(svc_snapshot):
    """O tradutor nao inventa status: o que entra e o que sai."""
    svc, _ = svc_snapshot
    for status in ("exploratory", "validated", "timestamped", "real",
                   "synthetic"):
        assert svc._quant_decision(status).evidence_status == status


def test_decision_schema_rejects_unknown_evidence_status():
    """Literal fechado: status fora do vocabulario canonico e erro."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        S.BetDecision(action="BET", reason="x",
                      evidence_status="quase_validated")


def test_frontend_types_mirror_evidence_status_union():
    """O enum de evidence_status e o mesmo no schema e no types/api.ts."""
    from pathlib import Path

    ts_path = (
        Path(__file__).resolve().parents[1]
        / "web" / "src" / "types" / "api.ts"
    )
    ts = ts_path.read_text(encoding="utf-8")
    assert "evidence_status:" in ts
    for status in ("exploratory", "validated", "timestamped", "real",
                   "synthetic"):
        assert f'"{status}"' in ts


# --------------------------------------------------------------------------
# I-15: provenance transporta origem real ate a API
# --------------------------------------------------------------------------


def test_provenance_carries_real_source_and_data_version(monkeypatch):
    """source/prediction_timestamp/data_version sao os do dominio.

    `data_version` para fonte real e a assinatura do corpus — a MESMA
    que invalida caches. `odds_timestamp` fica None: odds do
    football-data.co.uk nao tem timestamp de publicacao, e ausencia
    explícita é mais honesta que um carimbo fabricado.
    """
    from types import SimpleNamespace

    from betgsn.football_data_uk import FootballDataClient

    monkeypatch.setattr(
        FootballDataClient, "corpus_signature", lambda self: "sig-abc")
    svc = BetgsnService()
    snap = SimpleNamespace(source="real",
                           generated_at="2026-09-22T10:00:00Z")
    prov = svc.provenance(snap)
    assert prov.source == "real"
    assert prov.prediction_timestamp == "2026-09-22T10:00:00Z"
    assert prov.data_version == "sig-abc"
    assert prov.xg_status == "UNAVAILABLE"
    assert prov.odds_timestamp is None


def test_provenance_synthetic_marks_demo_dataset(svc_snapshot):
    svc, snap = svc_snapshot
    prov = svc.provenance(snap)
    assert prov.source == "synthetic"
    assert prov.xg_status == "ESTIMATED"
    assert prov.data_version


def test_signal_carries_kickoff_provider_and_odd(svc_snapshot, core):
    """Timestamp do jogo e casa da melhor odd chegam ao consumidor."""
    from betgsn.timeutil import utc_key

    svc, snap = svc_snapshot
    rep = svc.signal_report(snap)
    for api_sig, core_sig in zip(rep.signals, core.report.signals):
        assert api_sig.kickoff == utc_key(core_sig.kickoff)
        assert api_sig.best_book == core_sig.best_book
        assert api_sig.best_odd == core_sig.best_odd


# --------------------------------------------------------------------------
# I-13: endpoints de portfolio respeitam a decisao do Quant
# --------------------------------------------------------------------------


def _patch_server_service(monkeypatch):
    """Troca o service global do servidor por um sintetico isolado.

    O snapshot e criado EXPLICITAMENTE por um recalculate — mesmo ciclo
    de producao do recalculate assincrono (cdd91cb): nenhum endpoint GET
    constroi snapshot implicitamente, e cold start responde 503 com
    instrucao (ver `_snapshot` no server). Os endpoints de portfolio
    derivam tudo do snapshot, entao o teste precisa de um snapshot real
    calculado pelo pipeline, igual a producao.
    """
    from betgsn.api import server

    synthetic = BetgsnService(source="synthetic")
    synthetic.recalculate(CONFIG)
    monkeypatch.setattr(server, "service", synthetic)
    return synthetic


def test_portfolio_exposure_no_bet_creates_no_exposure(monkeypatch):
    """NO_BET chegando ao portfolio: nenhuma exposicao e criada.

    O contrato tambem carrega o MOTIVO e o status de evidencia da
    decisao — a UI precisa explicar por que as stakes sao zero.
    """
    _patch_server_service(monkeypatch)
    with TestClient(app) as c:
        r = c.get("/api/portfolio/exposure")
    assert r.status_code == 200
    body = r.json()
    assert body["decision_action"] == "NO_BET"
    assert body["decision_reason"]
    assert body["decision_evidence_status"]
    assert body["total_exposure"] == 0.0
    assert body["total_exposure_pct"] == 0.0
    assert body["within_limits"] is True
    assert all(v == 0.0 for v in body["by_match"].values())


def test_portfolio_parlays_empty_under_no_bet(monkeypatch):
    """Uma multipla e uma aposta: NO_BET nao construi nenhuma."""
    _patch_server_service(monkeypatch)
    with TestClient(app) as c:
        r = c.get("/api/portfolio/best-parlays")
    assert r.status_code == 200
    assert r.json() == []


def test_portfolio_exposure_uses_stakes_only_when_quant_approves(monkeypatch):
    """Com BET do Quant, a exposicao volta a ser a soma das stakes."""
    _patch_server_service(monkeypatch)
    approved = S.BetDecision(action="BET", reason="evidencia confiavel",
                             fraction=0.01, evidence_status="timestamped")
    monkeypatch.setattr(
        BetgsnService, "_quant_decision", lambda self, ev: approved)
    with TestClient(app) as c:
        r = c.get("/api/portfolio/exposure")
        signals = c.get("/api/signals", params={"source": "synthetic"})
    assert r.status_code == 200
    body = r.json()
    assert body["decision_action"] == "BET"
    expected = sum(s["stake"] for s in signals.json()["signals"])
    assert body["total_exposure"] == pytest.approx(expected)


def test_portfolio_parlays_available_when_quant_approves(monkeypatch):
    """O caminho BET nao foi destruido pelo hardening."""
    from betgsn.portfolio.parlay import best_parlays

    _patch_server_service(monkeypatch)
    approved = S.BetDecision(action="BET", reason="evidencia confiavel",
                             fraction=0.01, evidence_status="timestamped")
    monkeypatch.setattr(
        BetgsnService, "_quant_decision", lambda self, ev: approved)
    with TestClient(app) as c:
        r = c.get("/api/portfolio/best-parlays")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    # mesmo com BET, as multiplas vem do mesmo gerador do dominio
    for parlay in body:
        assert parlay["n_legs"] >= 2


# --------------------------------------------------------------------------
# Fixtures futuras reais via fallback (The Odds API): recalculate/games 200
# --------------------------------------------------------------------------


def _controlled_real_snapshot():
    """Snapshot REAL controlado: um jogo futuro com odds multi-casa.

    Simula o estado apos o fallback: fixtures futuras da The Odds API no
    cache, ratings vindos do historico real. O motor que roda em cima
    (`report`) e o verdadeiro — so a origem dos dados e controlada.
    """
    from datetime import datetime, timedelta, timezone

    from betgsn.football_data_uk import UpcomingFixture
    from betgsn.model import TeamRating
    from betgsn.real_signals import RealSnapshot

    amanha = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    odds = {"Resultado Final (1X2)": {
        "Pinnacle": {"1": 1.90, "X": 3.40, "2": 4.20},
        "Bet365": {"1": 1.85, "X": 3.35, "2": 4.10},
        "Betfair Exchange": {"1": 1.95, "X": 3.45, "2": 4.30},
    }}

    def rating(name: str) -> TeamRating:
        return TeamRating(
            name=name, attack=1.1, defense=0.9, goals_for=1.5,
            goals_against=1.0, xg_for=1.4, xg_against=1.1,
            corners_for=5.0, corners_against=4.5, cards_for=2.2,
            cards_against=2.4, shots_for=12.0, shots_on_target_for=4.2,
            form_points=1.5, matches_played=20,
        )

    fx = UpcomingFixture(
        division="E0", league="Premier League (England)",
        date=amanha, time="18:00", timezone="UTC",
        home="Arsenal", away="Chelsea", source="the_odds_api",
        odds=odds,
        best_odds={"Resultado Final (1X2)": {"1": 1.95, "X": 3.45, "2": 4.30}},
        best_books={"Resultado Final (1X2)": {
            "1": "Betfair Exchange", "X": "Betfair Exchange",
            "2": "Betfair Exchange"}},
    )
    return RealSnapshot(
        fixtures=[fx],
        ratings={"Arsenal": rating("Arsenal"), "Chelsea": rating("Chelsea")},
        league_goals=2.7, teams=["Arsenal", "Chelsea"], n_history=400,
        history_window=("2025-01-01", "2026-01-01"),
        generated_at="2026-09-22T00:00:00Z", computed_in_ms=0.0,
        sources=["The Odds API (fallback de fixtures)"],
        cutoff="2026-09-22T00:00:00Z",
        history=[],
    )


def test_recalculate_and_games_200_with_real_future_fixtures(monkeypatch):
    """/api/recalculate (assincrono) e /api/games respondem 200 quando
    existem fixtures futuras reais — aqui injetadas como se tivessem
    vindo do fallback."""
    import time as _time

    import betgsn.real_signals as rs

    snap = _controlled_real_snapshot()
    monkeypatch.setattr(
        rs.real_signals_service, "snapshot",
        lambda force=False, **kwargs: snap,
    )

    with TestClient(app) as c:
        r = c.post("/api/recalculate", json=CONFIG.model_dump())
        assert r.status_code == 200, r.text
        # job assincrono: aguarda a conclusao antes de consultar os jogos
        deadline = _time.monotonic() + 60.0
        while _time.monotonic() < deadline:
            st = c.get("/api/recalculate/status").json()
            if st["phase"] in ("done", "error", "cancelled"):
                break
            _time.sleep(0.05)
        assert st["phase"] == "done", st
        g = c.get("/api/games")
        assert g.status_code == 200, g.text
        games = g.json()
        assert len(games) == 1
        assert games[0]["home"] == "Arsenal"
        assert games[0]["away"] == "Chelsea"


def test_signals_503_explicit_when_no_future_fixture(monkeypatch):
    """Ausencia de fixture futura continua sendo ERRO explicito (503 com
    instrucao) — o fallback nao transforma falta de cobertura em sucesso."""
    from datetime import datetime, timedelta, timezone

    from betgsn.football_data_uk import FootballDataClient, UpcomingFixture

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
            "1": "Betfair Exchange", "X": "Betfair Exchange",
            "2": "Betfair Exchange"}},
    )
    monkeypatch.setattr(
        FootballDataClient, "load_fixtures", lambda self: [passado], raising=True,
    )
    # o servico cacheia o snapshot pela assinatura dos ARQUIVOS; o
    # monkeypatch nao muda arquivos, entao o cache precisa ser descartado
    # para o teste exercitar o caminho de dados de verdade
    import betgsn.real_signals as rs

    rs.real_signals_service.invalidate()
    with TestClient(app) as c:
        r = c.get("/api/signals")
    assert r.status_code == 503
    assert "--import-fixtures-live" in r.json()["detail"]
    # restaura o cache para os demais testes do modulo
    rs.real_signals_service.invalidate()
