"""Endpoints /api/quant — observabilidade sem recompute, sem decisão.

O que esta suíte prova:

  - cada endpoint responde 200 com status explícito;
  - artefato/cache ausente é MISSING/NO_VALID_CACHE — nunca servido
    como atual, nunca 500;
  - nada recalcula: collect_bets/load_matches não podem rodar num hit;
  - nenhum endpoint promove ou contorna o NO BET (produção segue
    vindo do promotion gate);
  - CLV status expõe o ciclo de vida PENDING/NO_CLOSE/CLOSED/... e a
    nota do gate (n < 30 -> bloqueado).
"""

from __future__ import annotations

import json

import pytest

from fastapi.testclient import TestClient

from betgsn.api.server import app

client = TestClient(app)


def _forbid_recompute(monkeypatch):
    from betgsn import model_walkforward as mw
    from betgsn import value_walkforward as vwf

    def _boom(*a, **k):  # pragma: no cover - falha se chamado
        raise AssertionError("endpoint de observabilidade não recalcula")

    monkeypatch.setattr(vwf, "collect_bets", _boom)
    monkeypatch.setattr(mw, "compute_model_validation", _boom)


# ==========================================================================
# benchmarks
# ==========================================================================


def test_quant_benchmarks_declares_protocol(monkeypatch):
    _forbid_recompute(monkeypatch)
    r = client.get("/api/quant/benchmarks")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "benchmark_manifest"
    assert body["benchmark_reference"] == "ETAPA_19"
    assert "corpus" in body and "protocol" in body
    # estado de cada cache é explícito
    for entry in body["caches"].values():
        assert entry["status"] in ("VALID", "STALE", "MISSING")


def test_quant_benchmarks_never_promotes(monkeypatch):
    _forbid_recompute(monkeypatch)
    body = client.get("/api/quant/benchmarks").json()
    # nada de production_eligible aqui: decisão vive no promotion gate
    assert "production_eligible" not in body


# ==========================================================================
# model vs market
# ==========================================================================


def test_quant_model_vs_market_missing_cache(monkeypatch):
    from betgsn import model_walkforward as mw

    _forbid_recompute(monkeypatch)
    monkeypatch.setattr(mw, "cached_model_evidence", lambda *a, **k: None)
    r = client.get("/api/quant/model-vs-market")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "NO_VALID_CACHE"
    assert "detail" in body


def test_quant_model_vs_market_valid_cache(monkeypatch):
    from betgsn import model_walkforward as mw

    _forbid_recompute(monkeypatch)
    payload = {
        "model": "BASELINE_V1 (teste)",
        "generated_at": "2026-09-23 00:00:00",
        "cache_fingerprint": "abc123",
        "comparison": {
            "n_bets_oos": 490736, "n_windows_valid": 24, "n_windows": 24,
            "market_raw": {"brier": 0.2005, "logloss": 0.5869},
            "market_fair": {"brier": 0.2005, "logloss": 0.5870},
            "model_raw": {"brier": 0.2091, "logloss": 0.6070},
            "model_calibrated": {"brier": 0.2095},
            "paired_model_vs_raw": {"verdict": "model_worse"},
            "strategy_model": {"roi": -0.0489, "n_bets": 181172},
        },
    }
    monkeypatch.setattr(mw, "cached_model_evidence", lambda *a, **k: payload)
    body = client.get("/api/quant/model-vs-market").json()
    assert body["status"] == "OK"
    assert body["market_raw"]["brier"] == 0.2005
    assert body["model_raw"]["brier"] == 0.2091
    assert body["n_bets_oos"] == 490736
    # fontes separadas: market e model são campos distintos
    assert body["market_raw"] != body["model_raw"]
    assert "line-shopping" in body["note"]


# ==========================================================================
# line-shopping / ml (artefatos)
# ==========================================================================


def test_quant_line_shopping_missing(monkeypatch):
    from betgsn.api import quant_service

    _forbid_recompute(monkeypatch)
    monkeypatch.setattr(
        quant_service, "_read_artifact", lambda name: None)
    body = client.get("/api/quant/line-shopping").json()
    assert body["status"] == "MISSING"


def test_quant_line_shopping_artifact(monkeypatch):
    from betgsn.api import quant_service

    _forbid_recompute(monkeypatch)
    artifact = {
        "kind": "line_shopping_audit",
        "audit": {
            "n_lines_total": 587015, "n_lines_rule": 6751,
            "rule_population_by_price": {"best": {"roi": 0.0161}},
            "ablation_semantics": {}, "price_uplift": {},
            "by_window": [], "by_band": [], "by_n_books": [],
            "answers": {}, "limitations": ["…"],
        },
        "strategy_model_decomposition": {"delta_price_effect": 0.0624},
    }
    monkeypatch.setattr(
        quant_service, "_read_artifact",
        lambda name: artifact if name == "line_shopping_audit.json" else None)
    body = client.get("/api/quant/line-shopping").json()
    assert body["status"] == "OK"
    assert body["n_lines_rule"] == 6751
    assert body["strategy_model_decomposition"]["delta_price_effect"] == 0.0624
    assert body["limitations"]


def test_quant_ml_missing(monkeypatch):
    from betgsn.api import quant_service

    _forbid_recompute(monkeypatch)
    monkeypatch.setattr(
        quant_service, "_read_artifact", lambda name: None)
    body = client.get("/api/quant/ml").json()
    assert body["status"] == "MISSING"


def test_quant_ml_artifact(monkeypatch):
    from betgsn.api import quant_service

    _forbid_recompute(monkeypatch)
    artifact = {
        "protocol": {"gap_days_embargo": 2},
        "models": {"elo": {"model_raw": {"logloss": 0.6}}},
        "ensemble": {"status": "PENDENTE", "reason": "…"},
        "declarations": ["sem ranking de modelos"],
    }
    monkeypatch.setattr(
        quant_service, "_read_artifact",
        lambda name: artifact if name == "ml_oos_validation.json" else None)
    body = client.get("/api/quant/ml").json()
    assert body["status"] == "OK"
    assert body["ensemble"]["status"] == "PENDENTE"
    assert body["declarations"]


# ==========================================================================
# CLV status
# ==========================================================================


def test_quant_clv_status_lifecycle(monkeypatch, tmp_path):
    from betgsn import odds_snapshots as snap_mod
    from betgsn.api import quant_service
    from betgsn.odds_normalize import event_key
    from betgsn.odds_snapshots import OddsObservation, OddsSnapshotStore
    from betgsn.timeutil import utc_key

    _forbid_recompute(monkeypatch)
    kickoff = "2030-01-01T12:00:00Z"
    key = event_key("Arsenal", "Chelsea", utc_key(kickoff))
    db = tmp_path / "odds.db"
    real_cls = snap_mod.OddsSnapshotStore

    def _store(*a, **k):
        return real_cls(db)

    monkeypatch.setattr(snap_mod, "OddsSnapshotStore", _store)

    store = real_cls(db)
    store.add([OddsObservation(
        match_key=key, market="Resultado Final (1X2)", outcome="1",
        bookmaker="Pinnacle", odd=2.0,
        timestamp="2030-01-01T09:55:00Z", kickoff=kickoff,
        provider="The Odds API",
    )])
    store.register_entry(
        match_key=key, market="Resultado Final (1X2)", outcome="1",
        entry_odd=2.0, entry_timestamp="2030-01-01T09:55:00Z",
        entry_n_books=1, kickoff=kickoff,
        prediction_timestamp="2030-01-01T10:00:00Z",
    )

    body = client.get("/api/quant/clv/status").json()
    assert body["status"] == "OK"
    # kickoff no futuro (2030) -> PENDING
    assert body["lifecycle"]["PENDING"] == 1
    assert body["n_entries"] == 1
    # evidência prospectiva: n=0 sem fechamento — nunca CLV=0
    assert body["clv_prospective"]["n"] == 0
    assert "bloqueada" in body["promotion_gate_note"]
    assert "PENDING" in body["lifecycle_note"]


def test_quant_clv_status_empty_store(monkeypatch, tmp_path):
    from betgsn import odds_snapshots as snap_mod
    from betgsn.api import quant_service

    _forbid_recompute(monkeypatch)
    real_cls = snap_mod.OddsSnapshotStore
    db = tmp_path / "odds.db"

    def _store(*a, **k):
        return real_cls(db)

    monkeypatch.setattr(snap_mod, "OddsSnapshotStore", _store)

    body = client.get("/api/quant/clv/status").json()
    assert body["status"] == "OK"
    assert body["n_entries"] == 0
    assert body["lifecycle"]["CLOSED"] == 0
    # observabilidade nula-segura: nada medido => None, nunca 0
    assert body["clv_statistics"]["n"] == 0
    assert body["clv_statistics"]["mean"] is None
    assert body["clv_statistics"]["last_closing_timestamp"] is None
    assert body["close_rate"] is None
    assert body["resolve_rate"] is None
    assert body["capture"]["last_observation_timestamp"] is None
    assert body["capture"]["n_observations"] == 0
    assert body["provider_issues"] == []


def _patch_isolated_store(monkeypatch, tmp_path):
    """Store SQLite isolado no tmp_path para o /api/quant/clv/status."""
    from betgsn import odds_snapshots as snap_mod

    real_cls = snap_mod.OddsSnapshotStore
    db = tmp_path / "odds.db"

    def _store(*a, **k):
        return real_cls(db)

    monkeypatch.setattr(snap_mod, "OddsSnapshotStore", _store)
    return real_cls(db)


def test_quant_clv_status_observability_closed(monkeypatch, tmp_path):
    """Entrada CLOSED: estatísticas reais + capture + rates no corpo."""
    from datetime import datetime, timedelta, timezone

    from betgsn.odds_normalize import event_key
    from betgsn.odds_snapshots import OddsObservation
    from betgsn.timeutil import utc_key

    _forbid_recompute(monkeypatch)
    store = _patch_isolated_store(monkeypatch, tmp_path)

    now = datetime.now(timezone.utc)
    kickoff = (now - timedelta(days=1)).replace(
        hour=12, minute=0, second=0, microsecond=0)
    kickoff_s = kickoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    key = event_key("Arsenal", "Chelsea", utc_key(kickoff_s))
    market = "Resultado Final (1X2)"

    def _o(odd: float, ts: datetime) -> OddsObservation:
        return OddsObservation(
            match_key=key, market=market, outcome="1",
            bookmaker="Pinnacle", odd=odd,
            timestamp=ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            kickoff=kickoff_s,
            provider="The Odds API",
        )

    entry_ts = kickoff - timedelta(hours=4)
    close_ts = kickoff - timedelta(hours=1)
    store.add([_o(2.00, entry_ts)])
    store.register_entry(
        match_key=key, market=market, outcome="1",
        entry_odd=2.00,
        entry_timestamp=entry_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        entry_n_books=1,
        kickoff=kickoff_s,
        prediction_timestamp=(
            entry_ts + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    store.add([_o(2.20, close_ts)])

    body = client.get("/api/quant/clv/status").json()
    assert body["status"] == "OK"
    assert body["lifecycle"]["CLOSED"] == 1
    stats = body["clv_statistics"]
    assert stats["n"] == 1
    assert stats["mean"] is not None
    assert stats["mean"] == pytest.approx(2.0 / 2.2 - 1.0, abs=1e-6)
    assert stats["median"] is not None
    assert stats["last_closing_timestamp"] == (
        close_ts.strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert body["close_rate"] == 1.0
    assert body["resolve_rate"] == 1.0
    capture = body["capture"]
    assert capture["last_observation_timestamp"] == (
        close_ts.strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert capture["n_observations"] == 2
    assert capture["n_matches"] == 1
    assert capture["providers"] == {"The Odds API": 2}


def test_quant_clv_status_provider_issues(monkeypatch, tmp_path):
    """Providers com problema (estado != HEALTHY) aparecem nominalmente."""
    _forbid_recompute(monkeypatch)
    store = _patch_isolated_store(monkeypatch, tmp_path)
    store.save_provider_health(
        {
            "The Odds API": {"state": "HEALTHY", "observations": 1},
            "ParlayAPI": {
                "state": "DEGRADED", "consecutive_failures": 2,
                "last_error": "timeout",
            },
        },
        {},
    )

    body = client.get("/api/quant/clv/status").json()
    assert body["status"] == "OK"
    assert body["provider_issues"] == ["ParlayAPI"]
    health = body["provider_health"]
    assert health["ParlayAPI"]["state"] == "DEGRADED"
    assert health["ParlayAPI"]["consecutive_failures"] == 2
    assert health["The Odds API"]["state"] == "HEALTHY"


# ==========================================================================
# decisão intocada: /api/signals continua NO_BET/exploratory
# ==========================================================================


def test_quant_endpoints_do_not_expose_decision(monkeypatch):
    """Observabilidade não é decisão: nenhum endpoint /api/quant expõe
    action/stake — o caminho de decisão segue sendo o promotion gate +
    /api/signals."""
    _forbid_recompute(monkeypatch)
    for path in (
        "/api/quant/benchmarks", "/api/quant/model-vs-market",
        "/api/quant/line-shopping", "/api/quant/ml", "/api/quant/clv/status",
    ):
        body = client.get(path).json()
        assert "action" not in body
        assert "fraction" not in body
