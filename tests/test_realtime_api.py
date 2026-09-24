"""API do terminal em tempo real: board, match detail, sinais, SSE.

O engine real NAO sobe nos testes (BETGSN_REALTIME=0 no conftest);
estes testes injetam um engine construido com providers fake.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import betgsn.api.realtime as realtime_module
from betgsn.backtest_sources import LiveOddsCapture, OddsHistoryCache
from betgsn.odds_provider import OddsFetchRequest, OddsProviderFetch
from betgsn.odds_snapshots import OddsSnapshotStore
from betgsn.realtime.config import RealtimeConfig
from betgsn.realtime.engine import RealtimeOddsEngine
from betgsn.realtime.events import EventBus

KICKOFF_UTC = "2026-09-28T19:00:00Z"
SPORT = "soccer_france_ligue_one"
NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


class StaticProvider:
    name = "FakeOdds"

    def __init__(self, price: float = 2.10):
        self.price = price

    def available(self) -> bool:
        return True

    def divisions_for(self, scope: str) -> tuple[str, ...]:
        return ("F1",)

    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch:
        from betgsn.odds_normalize import normalize_events

        stamp = request.fetched_at
        event = {
            "home_team": "Lens",
            "away_team": "Lyon",
            "commence_time": KICKOFF_UTC,
            "bookmakers": [
                {
                    "key": "pinnacle",
                    "title": "Pinnacle",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Lens", "price": self.price, "timestamp": stamp},
                                {"name": "Draw", "price": 3.40, "timestamp": stamp},
                                {"name": "Lyon", "price": 3.10, "timestamp": stamp},
                            ],
                        }
                    ],
                }
            ],
        }
        quotes = normalize_events(
            [event], self.name, stamp, sport_key=SPORT
        )
        return OddsProviderFetch(
            quotes=tuple(quotes),
            raw_events=(event,),
            snapshot_provider="fake-live",
        )


@pytest.fixture()
def engine(tmp_path: Path) -> RealtimeOddsEngine:
    store = OddsSnapshotStore(tmp_path / "odds.db")
    capture = LiveOddsCapture(
        providers=[StaticProvider()],
        regions="eu",
        markets="h2h",
        store=store,
        fixtures=None,
    )
    eng = RealtimeOddsEngine(
        config=RealtimeConfig(interval_seconds=300.0),
        captures=[("FakeOdds", capture, 300.0)],
        store=store,
        bus=EventBus(),
        sport_keys=[SPORT],
        clock=lambda: NOW,
    )
    capture.observer = eng._observer
    eng._capture_tick(eng.loops[0])
    return eng


@pytest.fixture()
def client(monkeypatch, engine: RealtimeOddsEngine):
    monkeypatch.setattr(realtime_module, "_engine", engine)
    from betgsn.api.server import app

    with TestClient(app) as test_client:
        yield test_client


def test_status_reports_engine_alive(client):
    body = client.get("/api/realtime/status").json()
    assert body["engine"]["state"]["events"] >= 1
    assert body["engine"]["providers"]["FakeOdds"]["ticks"] == 1
    assert body["boot"]["engine_ready"] is True


def test_board_lists_event_with_market_and_books(client):
    body = client.get("/api/realtime/board").json()
    assert body["events"], "board vazio"
    event = body["events"][0]
    assert event["home"] == "Lens"
    assert event["markets"], "sem mercados"
    market = event["markets"][0]
    assert market["n_books"] >= 1
    assert market["selections"]
    selection = market["selections"][0]
    assert selection["books"][0]["bookmaker"] == "Pinnacle"
    assert selection["books"][0]["freshness"] in (
        "FRESH", "RECENT", "STALE", "UNKNOWN"
    )


def test_match_detail_has_grid_signals_timeline_and_model_state(client):
    body = client.get("/api/realtime/board").json()
    event_key = body["events"][0]["event_key"]
    detail = client.get(
        "/api/realtime/match", params={"event_key": event_key}
    ).json()
    assert detail["event"]["markets"]
    assert detail["movement_timeline"], "timeline do store vazia"
    assert "status" in detail["model_comparison"]
    assert detail["clv"]["n"] == 0  #: n=0 explicito, nunca CLV=0
    assert isinstance(detail["signals"], list)


def test_match_detail_404_for_unknown_event(client):
    response = client.get(
        "/api/realtime/match", params={"event_key": "nope|nope|2026-01-01T00:00:00Z"}
    )
    assert response.status_code == 404


def test_signals_endpoint_lists_alphas(client):
    body = client.get("/api/realtime/signals").json()
    assert "alphas" in body
    assert "bookmaker_microstructure" in body["alphas"]
    for signal in body["signals"]:
        assert signal["reason"]
        assert signal["production"] == "NO_BET"


def test_providers_endpoint_shows_loop_and_store_health(client):
    body = client.get("/api/realtime/providers").json()
    assert body["providers"]["FakeOdds"]["ticks"] == 1


def test_sse_stream_emits_hello_then_events(client):
    with client.stream(
        "GET", "/api/realtime/stream", params={"max_seconds": 1}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = list(response.iter_lines())
    text = "\n".join(lines)
    assert "event: HELLO" in text
    assert "event: CLOSE" in text  #: encerramento explicito, nao EOF mudo


def test_start_starts_injected_engine_and_is_idempotent(client):
    """POST start sobe o engine injetado; segundo POST nao duplica nada."""
    first = client.post("/api/realtime/start").json()
    assert first["started"] is True
    assert first["boot"]["engine_running"] is True
    second = client.post("/api/realtime/start").json()
    assert second["started"] is True
    engine_ref = realtime_module.engine()
    assert engine_ref is not None
    engine_ref.stop()
