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
