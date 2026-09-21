"""Contrato de health de providers na API (correcoes H1 + H5).

O que esta em jogo:

  H1 — a API nao pode FABRICAR health (latency 12.0 fixa, quota 999999,
       status derivado so de env_status). O estado apresentado tem que ser
       o REAL, produzido pelo Odds Layer (HealthTracker/CreditController).

  H5 — um so vocabulario: HEALTHY / DEGRADED / UNAVAILABLE / STALE /
       NO_COVERAGE (odds_health.ProviderState). "CURRENT" nao existe.
       Ausencia de observacao e UNKNOWN, com campos None.

Estrategia: injetar no BetgsnService um OddsService com providers falsos
(mesmos fakes do test_odds_layer), produzir cada estado de verdade via
fetch() e conferir que o DTO da API espelha o registro do tracker.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from betgsn.api import schemas as S
from betgsn.api.server import app
from betgsn.api.service import BetgsnService, api_provider_status
from betgsn.odds_health import ProviderState
from betgsn.odds_service import OddsService
from betgsn.providers import FAILURE_AUTH, FAILURE_RATE_LIMIT, ProviderError

KICKOFF = "2030-01-01T12:00:00Z"


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# ------------------------------------------------------------------ fakes


def _book(title, markets):
    return {"key": title.lower(), "title": title, "markets": markets}


def _h2h(home, away, p_home, p_draw, p_away):
    return {"key": "h2h", "outcomes": [
        {"name": home, "price": p_home},
        {"name": away, "price": p_away},
        {"name": "Draw", "price": p_draw},
    ]}


def _event(home="Alfa", away="Bravo"):
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": KICKOFF,
        "bookmakers": [_book("Pinnacle", [_h2h(home, away, 2.0, 3.4, 3.6)])],
    }


class FakeProvider:
    def __init__(self, events=None, headers=None, exc=None):
        self.events = events if events is not None else []
        self.headers = headers or {}
        self.exc = exc

    def live_odds_with_meta(self, sport_key, regions=None, markets=None):
        if self.exc is not None:
            raise self.exc
        return self.events, self.headers


class Clock:
    def __init__(self, value: str):
        self.value = value

    def __call__(self) -> str:
        return self.value


class PerfCounter:
    """Relogio de performance injetavel: cada chamada avanca `step`."""

    def __init__(self, step: float = 0.0):
        self.step = step
        self.value = 100.0

    def __call__(self) -> float:
        self.value += self.step
        return self.value


# ------------------------------------------------- mapeamento explicito H5


def test_api_status_maps_every_domain_state():
    """Cada ProviderState do dominio tem equivalente identico na API."""
    expected = {
        ProviderState.HEALTHY: "HEALTHY",
        ProviderState.DEGRADED: "DEGRADED",
        ProviderState.UNAVAILABLE: "UNAVAILABLE",
        ProviderState.STALE: "STALE",
        ProviderState.NO_COVERAGE: "NO_COVERAGE",
    }
    for state, api in expected.items():
        assert api_provider_status(state) == api


def test_api_status_absent_observation_is_unknown():
    """Sem observacao: UNKNOWN. Nunca HEALTHY (nem 'CURRENT')."""
    assert api_provider_status(None) == "UNKNOWN"


def test_schema_rejects_current_vocabulary():
    """'CURRENT' nao faz mais parte do contrato da API."""
    with pytest.raises(ValidationError):
        S.ProviderHealth(name="x", status="CURRENT")


def test_overview_flags_use_domain_vocabulary():
    ph = S.ProviderHealth(name="x", status="HEALTHY")
    ov = S.ProviderOverview(providers=[ph], generated_at="t",
                            any_healthy=True, any_stale=False,
                            any_unavailable=False)
    assert ov.any_healthy is True


# --------------------------------------------- estado real chega na API (H1)


def _svc_with(*providers, **kwargs) -> tuple[BetgsnService, OddsService]:
    odds = OddsService(providers=list(providers), **kwargs)
    return BetgsnService(source="synthetic", odds_service=odds), odds


def _by_name(overview: S.ProviderOverview) -> dict[str, S.ProviderHealth]:
    return {p.name: p for p in overview.providers}


def test_api_reflects_healthy_state_from_odds_layer():
    svc, odds = _svc_with(("The Odds API", FakeProvider([_event()])),
                          now=Clock("2029-12-31T12:00:00Z"))
    odds.fetch("soccer_epl", persist=False)
    dto = _by_name(svc.providers())["The Odds API"]
    assert odds.health_snapshot()["The Odds API"]["state"] == "HEALTHY"
    assert dto.status == "HEALTHY"
    assert dto.last_update == "2029-12-31T12:00:00Z"


def test_api_reflects_degraded_state_from_odds_layer():
    svc, odds = _svc_with(
        ("The Odds API", FakeProvider(exc=ProviderError(
            "429", status=429, kind=FAILURE_RATE_LIMIT, retryable=True))),
        ("ParlayAPI", FakeProvider([_event()])),
        now=Clock("2029-12-31T12:00:00Z"),
    )
    odds.fetch("soccer_epl", persist=False)
    dto = _by_name(svc.providers())["The Odds API"]
    assert odds.health_snapshot()["The Odds API"]["state"] == "DEGRADED"
    assert dto.status == "DEGRADED"
    assert dto.error  # o erro real do tracker aparece


def test_api_reflects_unavailable_state_from_odds_layer():
    svc, odds = _svc_with(
        ("The Odds API", FakeProvider(exc=ProviderError(
            "401", status=401, kind=FAILURE_AUTH))),
        now=Clock("2029-12-31T12:00:00Z"),
    )
    odds.fetch("soccer_epl", persist=False)
    dto = _by_name(svc.providers())["The Odds API"]
    assert odds.health_snapshot()["The Odds API"]["state"] == "UNAVAILABLE"
    assert dto.status == "UNAVAILABLE"


def test_api_reflects_no_coverage_state_from_odds_layer():
    svc, odds = _svc_with(("The Odds API", FakeProvider([])),
                          now=Clock("2029-12-31T12:00:00Z"))
    odds.fetch("soccer_epl", persist=False)
    dto = _by_name(svc.providers())["The Odds API"]
    assert odds.health_snapshot()["The Odds API"]["state"] == "NO_COVERAGE"
    assert dto.status == "NO_COVERAGE"


def test_api_reflects_stale_state_from_odds_layer():
    clock = Clock("2029-12-31T12:00:00Z")
    provider = FakeProvider([_event()])
    svc, odds = _svc_with(("The Odds API", provider), now=clock)
    odds.fetch("soccer_epl", persist=False)

    # provider passa a falhar e o tempo avanca alem do stale_after: a
    # coleta degradada entrega cache velho e marca STALE no tracker.
    provider.exc = ProviderError("500", status=500, kind="SERVER", retryable=True)
    clock.value = "2029-12-31T13:00:00Z"
    odds.fetch("soccer_epl", persist=False)

    dto = _by_name(svc.providers())["The Odds API"]
    assert odds.health_snapshot()["The Odds API"]["state"] == "STALE"
    assert dto.status == "STALE"


def test_unobserved_provider_is_unknown_not_healthy():
    """Configurado mas nunca coletado: UNKNOWN com campos ausentes."""
    svc, _ = _svc_with(("ParlayAPI", FakeProvider([_event()])),
                       now=Clock("2029-12-31T12:00:00Z"))
    overview = svc.providers()
    dto = _by_name(overview)["The Odds API"]
    assert dto.status == "UNKNOWN"
    assert dto.last_update is None
    assert dto.last_execution is None
    assert dto.latency_ms is None
    assert dto.error is None
    assert overview.any_healthy is False


# ---------------------------------------------------- nada sintetico (H1)


def test_no_fictional_quota_when_credit_information_absent():
    """Sem informacao de credito: quota e None, nunca 999999."""
    svc, odds = _svc_with(("The Odds API", FakeProvider([_event()])),
                          now=Clock("2029-12-31T12:00:00Z"))
    odds.fetch("soccer_epl", persist=False)  # sem headers de quota, sem limite
    dto = _by_name(svc.providers())["The Odds API"]
    assert dto.status == "HEALTHY"
    assert dto.quota_remaining is None
    assert dto.quota_remaining != 999999


def test_real_quota_from_provider_headers_flows_to_api():
    """Saldo real do header (x-requests-remaining) chega intacto na API."""
    svc, odds = _svc_with(
        ("The Odds API", FakeProvider([_event()],
                                      headers={"x-requests-remaining": "7"})),
        now=Clock("2029-12-31T12:00:00Z"),
    )
    odds.fetch("soccer_epl", persist=False)
    dto = _by_name(svc.providers())["The Odds API"]
    assert dto.quota_remaining == 7


def test_latency_is_measured_not_fixed():
    """Latencia vem do cronometro do Odds Layer; 12.0 fixo nao existe mais."""
    perf = PerfCounter(step=0.25)  # cada chamada "dura" 250 ms
    svc, odds = _svc_with(("The Odds API", FakeProvider([_event()])),
                          now=Clock("2029-12-31T12:00:00Z"), perf=perf)
    odds.fetch("soccer_epl", persist=False)
    dto = _by_name(svc.providers())["The Odds API"]
    assert dto.latency_ms == pytest.approx(250.0)
    assert dto.latency_ms != 12.0


def test_latency_absent_when_never_measured():
    """Provider sem chamada bem-sucedida: latencia None, nao inventada."""
    svc, odds = _svc_with(
        ("The Odds API", FakeProvider(exc=ProviderError(
            "429", status=429, kind=FAILURE_RATE_LIMIT, retryable=True))),
        now=Clock("2029-12-31T12:00:00Z"),
    )
    odds.fetch("soccer_epl", persist=False)
    dto = _by_name(svc.providers())["The Odds API"]
    assert dto.status == "DEGRADED"
    assert dto.latency_ms is None


def test_last_update_is_tracker_timestamp_not_now():
    """last_update e o last_success_at do tracker, nao o instante do request."""
    svc, odds = _svc_with(("The Odds API", FakeProvider([_event()])),
                          now=Clock("2029-12-31T12:00:00Z"))
    odds.fetch("soccer_epl", persist=False)
    overview = svc.providers()
    dto = _by_name(overview)["The Odds API"]
    assert dto.last_update == "2029-12-31T12:00:00Z"
    assert dto.last_update != overview.generated_at


# ------------------------------------------------------- rota HTTP de ponta a ponta


def test_providers_endpoint_has_no_synthetic_values(client):
    """Na rota real: nenhum 12.0 de latencia, nenhuma quota 999999."""
    r = client.get("/api/providers")
    assert r.status_code == 200
    body = r.json()
    for p in body["providers"]:
        assert p["latency_ms"] != 12.0
        assert p["quota_remaining"] != 999999
        if p["status"] == "UNKNOWN":
            # ausencia de observacao => campos observaveis ausentes
            assert p["latency_ms"] is None
            assert p["last_update"] is None
            assert p["quota_remaining"] is None
