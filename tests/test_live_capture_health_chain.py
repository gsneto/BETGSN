"""Cadeia de coleta real: captura -> store -> health -> API -> provenance.

O QUE ESTA EM JOGO
------------------
`/api/providers` reporta UNKNOWN ("Configurado; nenhuma coleta registrada
pelo Odds Layer") mesmo com todos os providers configurados. A causa e
operacional: o writer do health e a CAPTURA (CLI `--capture-odds`,
processo separado), e nenhuma coleta havia passado pelo Odds Layer.

Estes testes fixam o contrato ponta a ponta, sem rede externa:

    provider (shape real da ParlayAPI, fake deterministico)
      -> LiveOddsCapture (caminho de contrato `fetch_odds`)
      -> OddsObservation com timestamp REAL (last_update, nunca fetched_at)
      -> OddsSnapshotStore (observacoes + provider_health + quota)
      -> BetgsnService.providers() (UNKNOWN antes, HEALTHY depois)
      -> Provenance.odds_timestamp / data_source (dashboard)
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import betgsn.odds_snapshots as snap_mod
from betgsn.api.service import BetgsnService
from betgsn.backtest_sources import LiveOddsCapture, OddsHistoryCache
from betgsn.odds_provider import OddsFetchRequest, OddsProviderFetch
from betgsn.odds_snapshots import OddsSnapshotStore
from betgsn.odds_normalize import event_key
from betgsn.timeutil import utc_key

_REPO = Path(__file__).resolve().parents[1]

#: Instante da captura (falso, deterministico) — NUNCA igual ao last_update
#: real do bookmaker: o teste prova que um nao substitui o outro.
CAPTURE_AT = datetime(2026, 9, 23, 11, 0, 0, tzinfo=timezone.utc)
CAPTURE_STAMP = "2026-09-23T11:00:00Z"

#: last_update REAL publicado pelo bookmaker (anterior a captura).
LAST_UPDATE = "2026-09-23T10:45:12Z"

KICKOFF = "2026-09-28T19:00:00Z"
BRA = "soccer_brazil_campeonato"


def _parlay_event(home: str = "Flamengo", away: str = "Palmeiras") -> dict:
    """Evento no shape REAL da ParlayAPI (OpenAPI v3.2.0).

    `last_update` POR BOOKMAKER — o instante real da observacao de preco.
    """
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": KICKOFF,
        "bookmakers": [
            {
                "key": "pinnacle", "title": "Pinnacle",
                "last_update": LAST_UPDATE,
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": home, "price": 2.10},
                    {"name": "Draw", "price": 3.30},
                    {"name": away, "price": 3.40},
                ]}],
            },
        ],
    }


class FakeParlayProvider:
    """Provider de contrato (FASE B) no formato ParlayAPI.

    Devolve quotes JA normalizadas pelo parser canonico — igual ao
    adapter real (`_fetch_odds_api_format` + `_parlay_inject_last_update`).
    """

    name = "ParlayAPI"

    def __init__(self, events: list[dict] | None = None,
                 no_coverage: bool = False, exc: Exception | None = None):
        self.events = events if events is not None else [_parlay_event()]
        self.no_coverage = no_coverage
        self.exc = exc
        self.calls: list[OddsFetchRequest] = []

    def available(self) -> bool:
        return True

    def divisions_for(self, scope: str) -> tuple[str, ...]:
        return ("BRA",) if scope == BRA else ()

    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch:
        from betgsn.providers import _parlay_inject_last_update
        from betgsn.odds_normalize import normalize_events

        self.calls.append(request)
        if self.exc is not None:
            raise self.exc
        if self.no_coverage:
            return OddsProviderFetch(no_coverage=True)
        events = _parlay_inject_last_update(list(self.events))
        quotes = normalize_events(
            events, self.name, request.fetched_at, sport_key=BRA)
        return OddsProviderFetch(
            quotes=tuple(quotes),
            raw_events=tuple(events),
            snapshot_provider="parlayapi-live",
        )


def _capture(tmp_path: Path, provider, now: datetime = CAPTURE_AT):
    store = OddsSnapshotStore(tmp_path / "odds.db")
    capture = LiveOddsCapture(
        provider, OddsHistoryCache(tmp_path / "cache"),
        regions="eu", markets="h2h",
        store=store,
    )
    report = capture.capture([BRA], now=now)
    return report, store


def _service_with_store(monkeypatch, db_path: Path) -> BetgsnService:
    """BetgsnService real lendo o store do banco temporario."""
    real = snap_mod.OddsSnapshotStore

    def _factory(*a, **k):
        return real(db_path)

    monkeypatch.setattr(snap_mod, "OddsSnapshotStore", _factory)
    return BetgsnService(source="real")


def _dto(service: BetgsnService):
    return {p.name: p for p in service.providers().providers}


# ==========================================================================
# 1. UNKNOWN antes da primeira coleta; HEALTHY depois
# ==========================================================================


def test_provider_is_unknown_before_any_capture(tmp_path, monkeypatch):
    """Configurado, store vazio: UNKNOWN com campos ausentes — nunca
    HEALTHY por configuracao.

    O provider `ParlayAPI` so aparece na lista quando o ambiente tem a
    chave; com chave e sem coleta, o contrato e UNKNOWN explicito.
    """
    monkeypatch.setenv("BETGSN_PARLAY_API_KEY", "test-key")
    monkeypatch.setenv("BETGSN_PARLAY_API_BASE", "https://parlay-api.com")
    monkeypatch.setenv("BETGSN_ODDS_API_KEY", "")
    monkeypatch.setenv("BETGSN_ODDSPAPI_API_KEY", "")
    monkeypatch.setenv("BETGSN_ODDS_API_IO_KEY", "")
    monkeypatch.setenv("BETGSN_OPTICODDS_API_KEY", "")
    OddsSnapshotStore(tmp_path / "odds.db")  # store vazio (sem health)

    service = _service_with_store(monkeypatch, tmp_path / "odds.db")
    dto = _dto(service)["ParlayAPI"]
    assert dto.status == "UNKNOWN"
    assert dto.last_update is None
    assert dto.last_execution is None
    assert dto.latency_ms is None
    assert service.providers().any_healthy is False


# ------------------------------------------------------------------ HEALTHY


def test_capture_flows_to_provider_health_and_api(tmp_path, monkeypatch):
    """A cadeia inteira: captura real-shape -> health persistido -> DTO.

    Depois de uma coleta bem-sucedida o provider NAO permanece UNKNOWN:
    status HEALTHY, last_update/last_execution preenchidos, latencia
    medida (nao fixa), quota None (ParlayAPI nao informa creditos — None
    e desconhecido, nunca zero).
    """
    report, store = _capture(tmp_path, FakeParlayProvider())
    assert report.errors == []
    assert report.observations_saved == 3

    health = store.load_provider_health()["ParlayAPI"]
    assert health["state"] == "HEALTHY"
    assert health["total_successes"] == 1
    assert health["last_success_at"] == CAPTURE_STAMP
    assert health["latency_ms"] is not None

    service = _service_with_store(monkeypatch, tmp_path / "odds.db")
    dto = _dto(service)["ParlayAPI"]
    assert dto.status == "HEALTHY"
    assert dto.last_update == CAPTURE_STAMP
    assert dto.last_execution == CAPTURE_STAMP
    assert dto.latency_ms is not None
    assert dto.latency_ms != 12.0
    assert dto.quota_used is None      # desconhecido, nunca fabricado
    assert dto.quota_remaining is None
    assert dto.error is None
    assert service.providers().any_healthy is True


def test_observation_keeps_real_timestamp_not_capture_stamp(tmp_path):
    """O last_update REAL do bookmaker chega inteiro a observacao.

    A chave fisica (match_key, mercado, resultado, casa, timestamp) usa
    o horario DA OBSERVACAO — o stamp da captura NUNCA o substitui.
    """
    _, store = _capture(tmp_path, FakeParlayProvider())
    key = event_key("Flamengo", "Palmeiras", utc_key(KICKOFF))
    rows = store.all_observations(key)
    assert len(rows) == 3
    assert all(r.timestamp == LAST_UPDATE for r in rows)
    assert all(r.timestamp != CAPTURE_STAMP for r in rows)
    assert all(r.provider == "ParlayAPI" for r in rows)
    assert all(r.bookmaker == "Pinnacle" for r in rows)


def test_capture_without_coverage_is_no_coverage_not_failure(tmp_path, monkeypatch):
    """Provider responde sem cobertura: NO_COVERAGE, sem erro fabricado."""
    report, store = _capture(tmp_path, FakeParlayProvider(no_coverage=True))
    assert report.observations_saved == 0

    health = store.load_provider_health()["ParlayAPI"]
    assert health["state"] == "NO_COVERAGE"

    service = _service_with_store(monkeypatch, tmp_path / "odds.db")
    assert _dto(service)["ParlayAPI"].status == "NO_COVERAGE"


def test_capture_failure_registers_real_error(tmp_path, monkeypatch):
    """Falha real vira registro de erro no health — nunca silencio."""
    from betgsn.providers import FAILURE_AUTH, ProviderError

    report, store = _capture(tmp_path, FakeParlayProvider(
        exc=ProviderError("401", status=401, kind=FAILURE_AUTH)))
    assert report.observations_saved == 0

    health = store.load_provider_health()["ParlayAPI"]
    assert health["state"] == "UNAVAILABLE"
    assert "401" in (health["last_error"] or "")

    service = _service_with_store(monkeypatch, tmp_path / "odds.db")
    dto = _dto(service)["ParlayAPI"]
    assert dto.status == "UNAVAILABLE"
    assert dto.error and "401" in dto.error


# ==========================================================================
# 2. Provenance / data_source: odds temporais chegam ao dashboard
# ==========================================================================


def _real_snapshot(analyses: list | None) -> object:
    from types import SimpleNamespace

    fixture = SimpleNamespace(home="Flamengo", away="Palmeiras",
                              kickoff=utc_key(KICKOFF), league="", round_label="")
    analysis = SimpleNamespace(fixture=fixture, markets={}, odds={})
    result = SimpleNamespace(analyses=analyses or [analysis])
    return SimpleNamespace(
        source="real",
        generated_at="2026-09-23T11:05:00Z",
        result=result,
    )


def test_provenance_odds_timestamp_from_real_observations(tmp_path, monkeypatch):
    """Com observacoes reais no store, provenance carrega o timestamp.

    PIT: só observacoes com timestamp <= generated_at do snapshot valem
    (observacao futura nao pode justificar decisao passada).
    """
    _capture(tmp_path, FakeParlayProvider())
    service = _service_with_store(monkeypatch, tmp_path / "odds.db")
    snap = _real_snapshot(None)

    prov = service.provenance(snap)
    assert prov.odds_timestamp == LAST_UPDATE
    assert prov.odds_timestamp is not None
    assert "indispon" not in service.data_source()
    assert "ParlayAPI" in service.data_source()


def test_provenance_odds_timestamp_none_without_observations(tmp_path, monkeypatch):
    """Store vazio: odds_timestamp None e mensagem honesta de ausencia."""
    OddsSnapshotStore(tmp_path / "odds.db")
    service = _service_with_store(monkeypatch, tmp_path / "odds.db")
    snap = _real_snapshot(None)

    prov = service.provenance(snap)
    assert prov.odds_timestamp is None
    assert "indispon" in service.data_source()


def test_provenance_ignores_observations_after_prediction(tmp_path, monkeypatch):
    """Observacao POSTERIOR ao snapshot nao entra (point-in-time)."""
    from betgsn.odds_snapshots import OddsObservation

    store = OddsSnapshotStore(tmp_path / "odds.db")
    store.add([
        OddsObservation(
            match_key=event_key("Flamengo", "Palmeiras", utc_key(KICKOFF)),
            market="Resultado Final (1X2)", outcome="1",
            bookmaker="Pinnacle", odd=2.10,
            timestamp="2026-09-23T10:45:12Z", kickoff=KICKOFF,
            provider="ParlayAPI",
        ),
        OddsObservation(
            match_key=event_key("Flamengo", "Palmeiras", utc_key(KICKOFF)),
            market="Resultado Final (1X2)", outcome="1",
            bookmaker="Pinnacle", odd=2.30,
            timestamp="2026-09-23T11:30:00Z", kickoff=KICKOFF,
            provider="ParlayAPI",
        ),
    ])
    service = _service_with_store(monkeypatch, tmp_path / "odds.db")
    snap = _real_snapshot(None)  # gerado as 11:05

    prov = service.provenance(snap)
    assert prov.odds_timestamp == "2026-09-23T10:45:12Z"


def test_provenance_synthetic_never_touches_store(tmp_path, monkeypatch):
    """Fonte sintetica: odds_timestamp None, sem consultar o store."""
    _capture(tmp_path, FakeParlayProvider())
    service = _service_with_store(monkeypatch, tmp_path / "odds.db")
    snap = _real_snapshot(None)
    snap.source = "synthetic"

    prov = service.provenance(snap)
    assert prov.odds_timestamp is None


# ==========================================================================
# 3. CLI --capture-odds --providers: captura controlada por provider
# ==========================================================================


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("betgsn_cli_health", _REPO / "betgsn.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _NamedProvider(FakeParlayProvider):
    def __init__(self, name: str):
        super().__init__()
        self.name = name


def _registry_with(*names):
    import betgsn.odds_registry as registry_mod
    from betgsn.odds_registry import OddsProviderRegistry, ProviderSpec

    registry = OddsProviderRegistry()
    for i, name in enumerate(names, start=1):
        registry.register(
            ProviderSpec(name=name, factory=lambda n=name: _NamedProvider(n),
                         priority=i)
        )
    return registry_mod, registry


def test_cli_capture_provider_filter_selects_only_named(
        monkeypatch, capsys, tmp_path):
    """""--providers=ParlayAPI" captura SO o provider nomeado."""
    from betgsn.config import output_root

    monkeypatch.setenv("BETGSN_OUTPUT_DIR", str(tmp_path))
    registry_mod, registry = _registry_with("The Odds API", "ParlayAPI")
    monkeypatch.setattr(registry_mod, "default_odds_registry", lambda: registry)

    cli = _load_cli_module()
    rc = cli.capture_odds_cli(["--sports=" + BRA, "--providers=ParlayAPI"])

    assert rc == 0
    store = OddsSnapshotStore(output_root() / "odds_snapshots.db")
    providers_used = set(store.stats()["providers"])
    assert providers_used == {"ParlayAPI"}
    health = store.load_provider_health()
    assert set(health) == {"ParlayAPI"}


def test_cli_capture_provider_filter_rejects_unknown_name(
        monkeypatch, capsys, tmp_path):
    """Nome desconhecido: erro explicito com a lista dos disponiveis."""
    monkeypatch.setenv("BETGSN_OUTPUT_DIR", str(tmp_path))
    registry_mod, registry = _registry_with("ParlayAPI")
    monkeypatch.setattr(registry_mod, "default_odds_registry", lambda: registry)

    cli = _load_cli_module()
    rc = cli.capture_odds_cli(["--sports=" + BRA, "--providers=NaoExiste"])

    assert rc == 1
    out = capsys.readouterr().out
    assert "NaoExiste" in out
    assert "ParlayAPI" in out


# ==========================================================================
# 4. Coverage real por provider (manifesto de captura -> DTO)
# ==========================================================================


def test_providers_dto_carries_real_coverage_after_capture(
        monkeypatch, tmp_path):
    """Apos a captura, o DTO traz a cobertura REAL observada.

    {sport_key: True} apenas para esportes em que o provider gravou
    quotes — sem captura, coverage permanece vazio (ausencia, nao
    cobertura inventada).
    """
    from betgsn.config import output_root

    monkeypatch.setenv("BETGSN_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("BETGSN_PARLAY_API_KEY", "test-key")
    monkeypatch.setenv("BETGSN_PARLAY_API_BASE", "https://parlay-api.com")
    for key in ("BETGSN_ODDS_API_KEY", "BETGSN_ODDSPAPI_API_KEY",
                "BETGSN_ODDS_API_IO_KEY", "BETGSN_OPTICODDS_API_KEY"):
        monkeypatch.setenv(key, "")
    registry_mod, registry = _registry_with("ParlayAPI")
    monkeypatch.setattr(registry_mod, "default_odds_registry", lambda: registry)

    cli = _load_cli_module()
    assert cli.capture_odds_cli(["--sports=" + BRA, "--providers=ParlayAPI"]) == 0

    service = BetgsnService(source="real")
    dto = _dto(service)["ParlayAPI"]
    assert dto.status == "HEALTHY"
    assert dto.coverage == {BRA: True}

    # provider sem captura: cobertura vazia, nunca inventada
    assert service.providers().any_healthy is True
    others = {p.name: p for p in service.providers().providers}
    others.pop("ParlayAPI")
    assert all(p.coverage == {} for p in others.values())
    assert output_root().name == tmp_path.name
