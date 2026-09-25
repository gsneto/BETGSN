"""Smoke contratual do realtime — mesmos schemas de tools/realtime_smoke.py.

O harness bate no TestClient (nao sobe processo). Confirma que o
board expoe `priced_signals` + `policy_fingerprint`, que o endpoint
dedicado `/api/realtime/priced-signals` responde e que o match detail
carrega `execution_diagnostics`/`execution_erosion`. A ausencia de
dado (n=0) e permitida — o que NAO pode acontecer e schema faltando
ou chave inventada.
"""
from __future__ import annotations

import pytest

from tests.test_realtime_api import client, engine  # noqa: F401 - fixtures


REQUIRED_BOARD_KEYS = {
    "generated_at", "events", "signals", "priced_signals",
    "policy_fingerprint", "last_moves", "problems", "boot",
}


def test_board_expoe_priced_signals_e_policy_fingerprint(client):
    body = client.get("/api/realtime/board").json()
    missing = REQUIRED_BOARD_KEYS - set(body)
    assert not missing, f"schema do board perdeu chaves: {sorted(missing)}"
    assert isinstance(body["priced_signals"], list)
    assert isinstance(body["policy_fingerprint"], str) and body["policy_fingerprint"]


def test_priced_signals_endpoint_responde(client):
    body = client.get("/api/realtime/priced-signals").json()
    for key in ("generated_at", "policy_fingerprint", "priced_signals",
                "execution_erosion"):
        assert key in body, f"/priced-signals sem '{key}'"
    assert isinstance(body["priced_signals"], list)
    ero = body["execution_erosion"]
    for key in ("n", "ratio", "status", "clv_before", "clv_after"):
        assert key in ero
    # A ausencia (n=0) e permitida — nunca inventar valor.
    if ero["n"] == 0:
        assert ero["status"] == "UNKNOWN"
        assert ero["ratio"] is None


def test_match_expoe_execution_diagnostics(client):
    board = client.get("/api/realtime/board").json()
    if not board["events"]:
        pytest.skip("sem eventos no board de teste")
    event_key = board["events"][0]["event_key"]
    body = client.get(
        "/api/realtime/match", params={"event_key": event_key}
    ).json()
    for key in ("event", "signals", "priced_signals",
                "execution_diagnostics", "execution_erosion",
                "movement_timeline", "clv", "problems"):
        assert key in body, f"/match sem '{key}'"
    assert isinstance(body["priced_signals"], list)
    assert isinstance(body["execution_diagnostics"], list)


def test_priced_signals_filtro_por_event_key(client):
    board = client.get("/api/realtime/board").json()
    if not board["events"]:
        pytest.skip("sem eventos")
    event_key = board["events"][0]["event_key"]
    body = client.get(
        "/api/realtime/priced-signals", params={"event_key": event_key}
    ).json()
    assert body["priced_signals"] == body.get("priced_signals", [])


def test_fair_override_endpoint_valida_selo(client):
    board = client.get("/api/realtime/board").json()
    if not board["events"]:
        pytest.skip("sem eventos")
    event = board["events"][0]
    payload = {
        "event_key": event["event_key"],
        "market": event["markets"][0]["market"],
        "selection": event["markets"][0]["selections"][0]["selection"],
        "prob": 0.42,
        "window_id": "w23",
        "method": "isotonic",
        "n": 512,
        "calibration_fingerprint": "cal-fp-x",
    }
    # Sem calibration_fingerprint deve rejeitar 400
    bad = {k: v for k, v in payload.items() if k != "calibration_fingerprint"}
    r = client.post("/api/realtime/fair-override", json=bad)
    assert r.status_code == 400
    # Payload completo persiste
    ok = client.post("/api/realtime/fair-override", json=payload)
    assert ok.status_code == 200
    assert "key" in ok.json()
    # DELETE remove
    d = client.delete("/api/realtime/fair-override", params={
        "event_key": payload["event_key"],
        "market": payload["market"],
        "selection": payload["selection"],
    })
    assert d.status_code == 200 and d.json()["cleared"] is True


def test_executions_endpoint_registra_e_remove(client):
    board = client.get("/api/realtime/board").json()
    if not board["events"]:
        pytest.skip("sem eventos")
    event = board["events"][0]
    body = {
        "event_key": event["event_key"],
        "market": event["markets"][0]["market"],
        "selection": event["markets"][0]["selections"][0]["selection"],
        "executed_price": 2.05,
        "executed_at": "2026-09-28T18:59:30Z",
    }
    # missing executed_at → 400
    r = client.post("/api/realtime/executions", json={k: v for k, v in body.items() if k != "executed_at"})
    assert r.status_code == 400
    # ok
    r = client.post("/api/realtime/executions", json=body)
    assert r.status_code == 200
    # delete
    d = client.delete("/api/realtime/executions", params={
        "event_key": body["event_key"],
        "market": body["market"],
        "selection": body["selection"],
    })
    assert d.status_code == 200 and d.json()["cleared"] is True
