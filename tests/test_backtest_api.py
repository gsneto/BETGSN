"""Testes das rotas HTTP do modulo de backtest.

Cobrem o fluxo assincrono (disparo + polling), os contratos, a paginacao,
a comparacao e os casos de erro.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from betgsn.api.server import app

#: Periodo curto para o teste nao ficar lento.
FAST_REQUEST = {
    "start_date": "2025-09-01",
    "end_date": "2025-10-31",
    "competitions": ["Serie A"],
    "market_keys": ["1x2", "ou"],
    "min_ev": 0.05,
    "min_history": 200,
}


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def _wait_for_completion(client: TestClient, timeout_s: float = 90.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = client.get("/api/backtest/status").json()
        if status["phase"] in ("done", "error"):
            return status
        time.sleep(0.2)
    raise AssertionError("backtest nao terminou no tempo esperado")


@pytest.fixture(scope="module")
def finished_run(client: TestClient) -> dict:
    """Dispara um backtest pequeno e devolve o status final."""
    resp = client.post("/api/backtest/run", json=FAST_REQUEST)
    assert resp.status_code == 200
    status = _wait_for_completion(client)
    assert status["phase"] == "done", status.get("error")
    return status


# --------------------------------------------------------------------------
# opcoes e status
# --------------------------------------------------------------------------


def test_options_endpoint(client):
    r = client.get("/api/backtest/options")
    assert r.status_code == 200
    body = r.json()
    assert body["min_date"] <= body["max_date"]
    assert body["competitions"]
    assert len(body["markets"]) == 9
    assert {m["key"] for m in body["markets"]} >= {"1x2", "ou", "btts", "ah"}
    assert body["min_sample"] >= 1
    assert body["point_in_time_schema"]
    assert any(s["key"] == "naive_synthetic" for s in body["odds_sources"])
    # o BETGSN nao possui odds historicas reais: precisa declarar isso
    assert body["missing_data_notes"], "faltam as notas de dados ausentes"


def test_status_endpoint_returns_phase(client):
    r = client.get("/api/backtest/status")
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] in {
        "idle", "preparing", "analyzing", "metrics", "saving", "done", "error",
    }
    assert 0.0 <= body["progress"] <= 1.0


# --------------------------------------------------------------------------
# execucao
# --------------------------------------------------------------------------


def test_run_completes_and_returns_run_id(finished_run):
    assert finished_run["run_id"]
    assert finished_run["progress"] == 1.0
    assert finished_run["message"]


def test_run_detail_contract(client, finished_run):
    r = client.get(f"/api/backtest/runs/{finished_run['run_id']}")
    assert r.status_code == 200
    d = r.json()

    assert d["point_in_time_schema"] == "pit-1"
    assert d["config_hash"]
    assert d["model_version"]

    agg = d["aggregate"]
    for field in (
        "n_signals", "n_wins", "n_losses", "n_pushes", "hit_rate",
        "hit_rate_ci", "avg_odd", "avg_ev", "brier", "brier_ci",
        "logloss", "ev_gap", "sample_sufficient",
    ):
        assert field in agg
    assert agg["n_wins"] + agg["n_losses"] + agg["n_pushes"] == agg["n_signals"]
    assert len(agg["hit_rate_ci"]) == 2

    assert d["calibration"], "sem calibracao"
    for b in d["calibration"]:
        assert b["lower"] <= b["avg_predicted"] <= b["upper"]
        assert b["ci_low"] <= b["observed_rate"] <= b["ci_high"]
        assert "sufficient" in b

    assert d["ev_buckets"]
    assert set(d["temporal"]) == {"day", "week", "month"}
    assert d["segments"]
    assert {s["dimension"] for s in d["segments"]} >= {"mercado", "confianca"}

    sim = d["simulation"]
    assert sim["initial_bankroll"] == FAST_REQUEST.get("bankroll", 1000.0)
    assert sim["equity"]
    assert sim["max_drawdown"] >= 0.0
    assert sim["n_bets"] == sim["n_wins"] + sim["n_losses"] + sim["n_pushes"]

    assert d["missing_data_notes"]


def test_run_signals_pagination(client, finished_run):
    run_id = finished_run["run_id"]
    first = client.get(
        f"/api/backtest/runs/{run_id}/signals", params={"limit": 5, "offset": 0}
    )
    assert first.status_code == 200
    page1 = first.json()
    assert len(page1["items"]) <= 5
    assert page1["total"] > 0

    second = client.get(
        f"/api/backtest/runs/{run_id}/signals", params={"limit": 5, "offset": 5}
    )
    page2 = second.json()
    if page1["total"] > 5:
        ids1 = {i["signal_id"] for i in page1["items"]}
        ids2 = {i["signal_id"] for i in page2["items"]}
        assert not (ids1 & ids2), "paginacao repetiu itens"


def test_signal_detail_exposes_point_in_time_context(client, finished_run):
    r = client.get(
        f"/api/backtest/runs/{finished_run['run_id']}/signals", params={"limit": 1}
    )
    item = r.json()["items"][0]
    # rastreabilidade completa: o que o modelo usou naquele instante
    for field in (
        "kickoff", "kickoff_utc", "home", "away", "market", "outcome", "best_odd",
        "model_prob", "market_prob", "edge", "ev", "confidence",
        "n_prior_matches", "league_goals", "lambda_home", "lambda_away",
        "home_attack", "away_defense", "odds_source", "odds_as_of",
        "model_version", "config_hash", "result_home_goals", "outcome_result",
        "settled",
    ):
        assert field in item, f"campo ausente no detalhe: {field}"
    # o instante das odds nunca pode ser posterior ao kickoff
    assert item["odds_as_of"] <= item["kickoff_utc"]
    assert item["n_prior_matches"] >= FAST_REQUEST["min_history"]


def test_signals_filter_by_market_and_confidence(client, finished_run):
    run_id = finished_run["run_id"]
    r = client.get(
        f"/api/backtest/runs/{run_id}/signals",
        params={"market": "Resultado Final (1X2)", "limit": 20},
    )
    assert r.status_code == 200
    assert all(i["market"] == "Resultado Final (1X2)" for i in r.json()["items"])


def test_signals_search(client, finished_run):
    run_id = finished_run["run_id"]
    r = client.get(
        f"/api/backtest/runs/{run_id}/signals",
        params={"search": "zzzz-inexistente", "limit": 5},
    )
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_runs_list_includes_finished_run(client, finished_run):
    r = client.get("/api/backtest/runs")
    assert r.status_code == 200
    runs = r.json()
    assert any(x["run_id"] == finished_run["run_id"] for x in runs)
    alvo = next(x for x in runs if x["run_id"] == finished_run["run_id"])
    assert alvo["hit_rate"] is not None
    assert alvo["brier"] is not None


# --------------------------------------------------------------------------
# comparacao e exclusao
# --------------------------------------------------------------------------


def test_compare_requires_two_runs(client, finished_run):
    run_id = finished_run["run_id"]
    # mesma execucao comparada com ela mesma: delta zero
    r = client.get("/api/backtest/compare", params={"a": run_id, "b": run_id})
    assert r.status_code == 200
    body = r.json()
    assert body["same_config"] is True
    assert body["metrics"]["brier"]["delta"] == pytest.approx(0.0)
    assert body["temporal_month"]


def test_compare_unknown_run_returns_404(client, finished_run):
    r = client.get(
        "/api/backtest/compare",
        params={"a": finished_run["run_id"], "b": "bt_inexistente"},
    )
    assert r.status_code == 404


def test_detail_unknown_run_returns_404(client):
    assert client.get("/api/backtest/runs/bt_nao_existe").status_code == 404


def test_signals_unknown_run_returns_404(client):
    assert client.get("/api/backtest/runs/bt_nao_existe/signals").status_code == 404


def test_delete_run(client):
    resp = client.post("/api/backtest/run", json={**FAST_REQUEST, "min_ev": 0.12})
    assert resp.status_code == 200
    status = _wait_for_completion(client)
    assert status["phase"] == "done", status.get("error")
    run_id = status["run_id"]

    assert client.delete(f"/api/backtest/runs/{run_id}").status_code == 200
    assert client.get(f"/api/backtest/runs/{run_id}").status_code == 404
    assert client.delete(f"/api/backtest/runs/{run_id}").status_code == 404


# --------------------------------------------------------------------------
# validacao de entrada
# --------------------------------------------------------------------------


def test_invalid_market_key_is_rejected(client):
    r = client.post(
        "/api/backtest/run",
        json={**FAST_REQUEST, "market_keys": ["mercado_inexistente"]},
    )
    # o motor levanta ValueError, o servico marca erro e o status expoe
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        status = _wait_for_completion(client)
        assert status["phase"] == "error"
        assert "mercado" in (status["error"] or "")


def test_invalid_config_is_rejected(client):
    assert client.post("/api/backtest/run", json={"bankroll": -10}).status_code == 422
    assert client.post(
        "/api/backtest/run", json={"kelly_fraction": 5}
    ).status_code == 422
    assert client.post(
        "/api/backtest/run", json={"min_confidence": "FORTEZA"}
    ).status_code == 422


def test_real_historical_odds_source_reports_missing_data(client):
    """Sem odds reais, o backtest falha explicitamente em vez de inventar."""
    resp = client.post(
        "/api/backtest/run",
        json={**FAST_REQUEST, "odds_source": "real_historical"},
    )
    assert resp.status_code == 200
    status = _wait_for_completion(client)
    assert status["phase"] == "error"
    assert "odds" in (status["error"] or "").lower()


# --------------------------------------------------------------------------
# corpora e cache de odds (dados historicos reais)
# --------------------------------------------------------------------------


def test_options_exposes_corpora(client):
    body = client.get("/api/backtest/options").json()
    assert body["corpora"], "sem lista de corpora"
    keys = {c["key"] for c in body["corpora"]}
    # local (sintetico), imported (API-Football) e football_data_uk (CSV publico)
    assert keys == {"local", "imported", "football_data_uk"}

    local = next(c for c in body["corpora"] if c["key"] == "local")
    assert local["available"] is True
    assert local["n_matches"] > 0
    assert local["fingerprint"], "corpus local precisa de fingerprint"
    assert local["first_kickoff"] <= local["last_kickoff"]


def test_options_exposes_football_data_uk_odds_source(client):
    body = client.get("/api/backtest/options").json()
    sources = {s["key"] for s in body["odds_sources"]}
    assert "football_data_uk" in sources
    assert "naive_synthetic" in sources
    # bookmakers reais ficam disponiveis para filtro
    assert isinstance(body["books"], list)


def test_options_exposes_odds_cache_status(client):
    body = client.get("/api/backtest/options").json()
    assert "odds_cache" in body
    assert isinstance(body["odds_cache"], list)


def test_options_default_config_has_corpus_and_sport(client):
    config = client.get("/api/backtest/options").json()["default_config"]
    assert config["corpus_source"] == "local"
    assert config["odds_sport_key"]
    assert config["odds_source"] == "naive_synthetic"


def test_imported_corpus_unavailable_fails_clearly(client):
    """Sem temporadas importadas, o corpus 'imported' falha com instrucao."""
    options = client.get("/api/backtest/options").json()
    imported = next(c for c in options["corpora"] if c["key"] == "imported")
    if imported["available"]:
        pytest.skip("ha temporadas importadas neste ambiente")

    resp = client.post(
        "/api/backtest/run", json={**FAST_REQUEST, "corpus_source": "imported"}
    )
    assert resp.status_code == 200
    status = _wait_for_completion(client)
    assert status["phase"] == "error"
    error = (status["error"] or "").lower()
    assert "import" in error
    # a mensagem precisa dizer COMO resolver
    assert "--import-fixtures" in (status["error"] or "")


def test_local_corpus_fingerprint_is_stable(client):
    a = client.get("/api/backtest/options").json()
    b = client.get("/api/backtest/options").json()
    fa = next(c for c in a["corpora"] if c["key"] == "local")["fingerprint"]
    fb = next(c for c in b["corpora"] if c["key"] == "local")["fingerprint"]
    assert fa == fb
