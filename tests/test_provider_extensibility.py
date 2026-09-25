"""FASE B.5 — integração operacional: captura multi-provider + colisões.

O que esta suíte prova (Task 5 do spec da FASE B):

  1. providers de contrato (com `fetch_odds`) alimentam a captura pelo
     caminho novo: escopo canônico por divisões do ADAPTER, health e
     creditos POR NOME do provider, snapshot rotulado pelo provider;
  2. a falha de um provider NAO contamina os demais nem apaga o que ja
     estava gravado no store;
  3. ausencia de cobertura vira health NO_COVERAGE, nunca `errors`;
  4. colisoes de chave física entre providers sao contabilizadas
     (`collision`, dono = primeira ocorrencia), repeticoes do mesmo
     provider sao `deduped`, e o que ja existia no store e `ignored` —
     o store fica com UMA linha por chave física;
  5. nenhum dado sintetico: provider sem mercado nao produz quotes
     dele, timestamp e o instante injetado/observado, kickoff e o
     publicado, provider name preservado nas observacoes e no health;
  6. compatibilidade total: provider LEGADO (`live_odds_with_meta`)
     continua no caminho de hoje — headers x-requests-*, label de
     snapshot "the-odds-api-live" e health sob "The Odds API";
  7. verificacoes estruturais (estilo FASE A): o core nao importa os
     modulos de provider; o caminho de contrato nao le headers
     x-requests-* nem o mapa legado SPORT_KEY_TO_DIVISIONS.

Os eventos usados sao INVENTADOS e declarados como tal: demonstracao de
contrato, sem dados reais externos.
"""
from __future__ import annotations

import ast
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from betgsn.backtest_sources import (
    CaptureAccounting,
    CaptureReport,
    LiveOddsCapture,
    OddsHistoryCache,
)
from betgsn.odds_normalize import event_key, normalize_events
from betgsn.odds_provider import (
    CreditUpdate,
    OddsFetchRequest,
    OddsProvider,
    OddsProviderFetch,
    divisions_for,
)
from betgsn.odds_registry import OddsProviderRegistry, ProviderSpec
from betgsn.odds_snapshots import OddsSnapshotStore
from betgsn.providers import FAILURE_AUTH, ProviderError
from betgsn.timeutil import utc_key
from test_odds_provider_contract import run_contract_conformance

_REPO = Path(__file__).resolve().parents[1]

#: Instante INVENTADO injetado na captura (o contrato nao consulta relogio).
NOW = datetime(2026, 9, 22, 18, 0, tzinfo=timezone.utc)
STAMP = "2026-09-22T18:00:00Z"
#: Kickoff INVENTADO, futuro em relacao a NOW.
KICKOFF = "2026-09-25T16:00:00Z"
#: Timestamp proprio de evento, INVENTADO, anterior ao instante da captura.
EVENT_TIMESTAMP = "2026-09-22T17:59:00+00:00"

MARKET = "Resultado Final (1X2)"
SPORT = "soccer_epl"


# ==========================================================================
# Eventos crus (shape The Odds API) — numeros INVENTADOS p/ contrato
# ==========================================================================


def _h2h_event(
    home: str,
    away: str,
    kickoff: str = KICKOFF,
    price: float = 2.10,
    bookmaker: str = "Pinnacle",
    timestamp: str | None = None,
) -> dict:
    """Evento h2h completo: 3 quotes (1, X, 2) de uma casa."""
    event = {
        "home_team": home,
        "away_team": away,
        "commence_time": kickoff,
        "bookmakers": [
            {
                "key": "bk",
                "title": bookmaker,
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": home, "price": price},
                            {"name": "Draw", "price": 3.40},
                            {"name": away, "price": 3.80},
                        ],
                    }
                ],
            }
        ],
    }
    if timestamp is not None:
        event["timestamp"] = timestamp
    return event


def _single_outcome_event(
    home: str,
    away: str,
    kickoff: str = KICKOFF,
    price: float = 2.10,
    bookmaker: str = "Pinnacle",
) -> dict:
    """Evento h2h com UM unico resultado: 1 quote (para colisao exata)."""
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": kickoff,
        "bookmakers": [
            {
                "key": "bk",
                "title": bookmaker,
                "markets": [
                    {"key": "h2h", "outcomes": [{"name": home, "price": price}]}
                ],
            }
        ],
    }


def _event_key(home: str, away: str, kickoff: str = KICKOFF) -> str:
    return event_key(home, away, utc_key(kickoff))


def _fake_perf(values: list[float]):
    it = iter(values)
    return lambda: next(it)


# ==========================================================================
# Fakes de contrato (FASE B): name / available / fetch_odds
# ==========================================================================


class _BaseFakeContractProvider:
    """Provider de contrato FALSO — quotes nascem do parser canonico.

    Registra cada `OddsFetchRequest` recebido para o teste inspecionar o
    escopo canônico pedido pela captura. Sem fabricacao: sem evento nao
    existe quote; `no_coverage` e explicito; creditos ausentes sao None.
    """

    def __init__(
        self,
        events,
        *,
        snapshot_provider: str,
        credits: CreditUpdate | None = None,
        no_coverage: bool = False,
        error: Exception | None = None,
        divisions: tuple[str, ...] = (),
    ) -> None:
        self._events = list(events)
        self._snapshot_provider = snapshot_provider
        self._credits = credits
        self._no_coverage = no_coverage
        self._error = error
        self._divisions = tuple(divisions)
        self.requests: list[OddsFetchRequest] = []

    def available(self) -> bool:
        return True

    def divisions_for(self, scope: str) -> tuple[str, ...]:
        return self._divisions

    def fetch_odds(self, request: OddsFetchRequest) -> OddsProviderFetch:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if self._no_coverage:
            return OddsProviderFetch(no_coverage=True)
        quotes = tuple(
            normalize_events(self._events, self.name, request.fetched_at)
        )
        return OddsProviderFetch(
            quotes=quotes,
            raw_events=tuple(self._events),
            snapshot_provider=self._snapshot_provider,
            credits=self._credits,
        )


class FakeProviderA(_BaseFakeContractProvider):
    """Provider A: casa Pinnacle, jogo Alfa x Bravo."""

    name = "fake-a"

    def __init__(self, events=None, **kwargs) -> None:
        if events is None:
            events = [_h2h_event("Alfa FC", "Bravo FC")]
        super().__init__(events, snapshot_provider="fake-a-live", **kwargs)


class FakeProviderB(_BaseFakeContractProvider):
    """Provider B: casa OutraCasa, jogo Gama x Delta."""

    name = "fake-b"
    snapshot_label = "fake-b-live"

    def __init__(self, events=None, **kwargs) -> None:
        if events is None:
            events = [
                _h2h_event("Gama FC", "Delta FC", price=2.40, bookmaker="OutraCasa")
            ]
        kwargs.setdefault("snapshot_provider", self.snapshot_label)
        super().__init__(events, **kwargs)


class FakeLiveProvider:
    """Fake LEGADO: contrato `live_odds_with_meta` (pre-FASE B).

    Espelha o formato usado por test_operational_pipeline (T-1..T-10):
    quem nao tem `fetch_odds` cai no caminho de hoje.
    """

    def __init__(self, events: list[dict], headers: dict | None = None) -> None:
        self.events = events
        self.headers = headers or {
            "x-requests-last": "2",
            "x-requests-used": "5",
            "x-requests-remaining": "495",
        }

    def live_odds_with_meta(self, sport_key, regions=None, markets=None):
        return list(self.events), dict(self.headers)


def _capture(
    tmp_path: Path,
    providers,
    *,
    sports=(SPORT,),
    now: datetime = NOW,
    markets: str = "h2h",
    regions: str = "eu",
    store: OddsSnapshotStore | None = None,
    cache: OddsHistoryCache | None = None,
    perf=None,
    fixtures=None,
    aliases=None,
) -> tuple[CaptureReport, OddsSnapshotStore]:
    store = store if store is not None else OddsSnapshotStore(tmp_path / "odds.db")
    kwargs: dict = {}
    if perf is not None:
        kwargs["perf"] = perf
    capture = LiveOddsCapture(
        providers,
        cache if cache is not None else OddsHistoryCache(tmp_path / "cache"),
        regions=regions,
        markets=markets,
        store=store,
        fixtures=fixtures,
        aliases=aliases,
        **kwargs,
    )
    report = capture.capture(list(sports), now=now)
    return report, store


# ==========================================================================
# 1-2. A valido / B valido (caminho de contrato)
# ==========================================================================


def test_provider_a_valid_capture(tmp_path):
    report, store = _capture(
        tmp_path, FakeProviderA(), perf=_fake_perf([0.0, 0.25])
    )
    assert report.providers_used == ["fake-a"]
    assert report.errors == []
    assert report.observations_saved == 3
    assert report.observations_received == 3
    assert report.observations_dropped == 0
    assert report.accounting == ()
    assert report.snapshots_saved == 1
    assert report.events == 1
    assert report.events_with_odds == 1

    snapshots = OddsHistoryCache(tmp_path / "cache").load(SPORT)
    assert len(snapshots) == 1
    assert snapshots[0].provider == "fake-a-live"
    assert snapshots[0].timestamp == STAMP

    health = store.load_provider_health()
    assert health["fake-a"]["state"] == "HEALTHY"
    assert health["fake-a"]["total_successes"] == 1
    assert health["fake-a"]["latency_ms"] == pytest.approx(250.0)

    assert report.per_provider["fake-a"] == {
        "quotes": 3,
        "observations_saved": 3,
        "events_matched": 0,
        "events_unmatched": 0,
        "events_ambiguous": 0,
    }


def test_provider_b_valid_capture(tmp_path):
    report, store = _capture(tmp_path, FakeProviderB())
    assert report.providers_used == ["fake-b"]
    assert report.errors == []
    assert report.observations_saved == 3
    key = _event_key("Gama FC", "Delta FC")
    assert len(store.all_observations(key)) == 3
    health = store.load_provider_health()
    assert health["fake-b"]["state"] == "HEALTHY"


def test_provider_without_snapshot_label_saves_no_snapshot(tmp_path):
    """Sem raw_events/snapshot_provider nao ha snapshot — nada e inventado."""
    provider = FakeProviderB(snapshot_provider="")
    report, _ = _capture(tmp_path, provider)
    assert report.snapshots_saved == 0
    assert report.observations_saved == 3  # observacoes seguem para o store


# ==========================================================================
# 3. A falha -> B funciona (isolamento por provider)
# ==========================================================================


def test_failure_of_one_provider_does_not_abort_the_other(tmp_path):
    a = FakeProviderA(
        error=ProviderError("401 Unauthorized", status=401, kind=FAILURE_AUTH)
    )
    b = FakeProviderB()
    report, store = _capture(tmp_path, [a, b])

    assert any("fake-a" in e and "401" in e for e in report.errors)
    assert not any("fake-b" in e for e in report.errors)
    assert report.providers_used == ["fake-a", "fake-b"]
    # B persistiu integralmente
    assert report.observations_saved == 3
    assert len(store.all_observations(_event_key("Gama FC", "Delta FC"))) == 3

    health = store.load_provider_health()
    assert health["fake-a"]["state"] == "UNAVAILABLE"
    assert health["fake-a"]["last_kind"] == FAILURE_AUTH
    assert health["fake-a"]["last_status"] == 401
    assert health["fake-a"]["total_failures"] == 1
    assert health["fake-b"]["state"] == "HEALTHY"
    assert health["fake-b"]["total_successes"] == 1


# ==========================================================================
# 4-5. A sem cobertura / B sem cobertura (health, nao error)
# ==========================================================================


@pytest.mark.parametrize(
    "provider",
    [
        FakeProviderA(no_coverage=True),
        FakeProviderB(no_coverage=True),
    ],
    ids=["provider-a", "provider-b"],
)
def test_no_coverage_is_health_not_error(tmp_path, provider):
    report, store = _capture(tmp_path, provider)
    assert report.errors == []
    assert report.observations_saved == 0
    assert report.snapshots_saved == 0
    health = store.load_provider_health()
    assert health[provider.name]["state"] == "NO_COVERAGE"
    assert health[provider.name]["total_failures"] == 0


# ==========================================================================
# 6. A + B quotes distintas -> ambas persistem
# ==========================================================================


def test_distinct_quotes_from_both_providers_persist(tmp_path):
    report, store = _capture(tmp_path, [FakeProviderA(), FakeProviderB()])
    assert report.providers_used == ["fake-a", "fake-b"]
    assert report.observations_saved == 6
    assert report.accounting == ()
    assert report.observations_dropped == 0
    assert report.per_provider["fake-a"]["observations_saved"] == 3
    assert report.per_provider["fake-b"]["observations_saved"] == 3
    assert len(store.all_observations(_event_key("Alfa FC", "Bravo FC"))) == 3
    assert len(store.all_observations(_event_key("Gama FC", "Delta FC"))) == 3


# ==========================================================================
# 7. A + B mesma chave física -> collision contabilizada
# ==========================================================================


def test_collision_between_providers_is_reported(tmp_path):
    event = _single_outcome_event("Alfa FC", "Bravo FC")
    report, store = _capture(
        tmp_path, [FakeProviderA([event]), FakeProviderB([event])]
    )

    assert len(report.accounting) == 1
    entry = report.accounting[0]
    assert isinstance(entry, CaptureAccounting)
    assert entry.action == "collision"
    assert entry.provider == "fake-b"
    assert entry.kept_by == "fake-a"
    assert entry.match_key == _event_key("Alfa FC", "Bravo FC")
    assert entry.market == MARKET
    assert entry.outcome == "1"
    assert entry.bookmaker == "Pinnacle"
    assert entry.timestamp == STAMP

    # o store fica com UMA linha, dona = primeira ocorrencia (A)
    rows = store.all_observations(_event_key("Alfa FC", "Bravo FC"))
    assert len(rows) == 1
    assert rows[0].provider == "fake-a"
    assert report.observations_saved == 1
    assert report.observations_received == 2
    assert report.observations_dropped == 1
    assert report.per_provider["fake-a"]["observations_saved"] == 1
    assert report.per_provider["fake-b"]["observations_saved"] == 0

    # serializacao natural (asdict): auditoria sai inteira no JSON
    payload = report.to_json()
    assert payload["accounting"][0]["action"] == "collision"
    json.dumps(payload)


# ==========================================================================
# 8. sem dados sinteticos
# ==========================================================================


def test_no_synthetic_quotes_for_unavailable_market(tmp_path):
    """O pedido pede h2h,totals,btts; o evento so tem h2h — nada de
    Total de Gols/Ambas Marcam inventado, e o timestamp e o injetado."""
    provider = FakeProviderA([_h2h_event("Alfa FC", "Bravo FC")])
    report, store = _capture(tmp_path, provider, markets="h2h,totals,btts")

    rows = store.all_observations(_event_key("Alfa FC", "Bravo FC"))
    assert len(rows) == 3
    assert {r.market for r in rows} == {MARKET}
    assert all(r.timestamp == STAMP for r in rows)
    assert all(r.kickoff == utc_key(KICKOFF) for r in rows)


def test_event_without_kickoff_produces_nothing(tmp_path):
    """Evento sem horario publico nao gera observacao — kickoff nunca
    e fabricado pela captura."""
    provider = FakeProviderA([_h2h_event("Alfa FC", "Bravo FC", kickoff="")])
    report, store = _capture(tmp_path, provider)
    assert report.observations_saved == 0
    assert store.stats()["observations"] == 0


# ==========================================================================
# 9. falha nao apaga observacoes anteriores
# ==========================================================================


def test_failure_does_not_delete_previous_observations(tmp_path):
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    report1, _ = _capture(tmp_path, FakeProviderA(), store=store)
    assert report1.observations_saved == 3

    broken = FakeProviderA(
        error=ProviderError("500 Internal", status=500, kind="server")
    )
    report2, _ = _capture(tmp_path, broken, store=store)
    assert report2.observations_saved == 0
    assert report2.errors
    # as 3 observacoes validas da primeira captura seguem intactas
    assert len(store.all_observations(_event_key("Alfa FC", "Bravo FC"))) == 3


# ==========================================================================
# 10. timestamps corretos (point-in-time, canonicos)
# ==========================================================================


def test_timestamps_are_canonical_and_point_in_time(tmp_path):
    """Timestamp da observacao: o injetado (stamp) OU o proprio do evento
    — nunca fabricado; kickoff preservado; observacao pre-kickoff."""
    provider = FakeProviderA(
        [
            _h2h_event("Alfa FC", "Bravo FC"),
            _h2h_event(
                "Eco FC", "Foxtrot FC", timestamp=EVENT_TIMESTAMP
            ),
        ]
    )
    _, store = _capture(tmp_path, provider)

    rows_a = store.all_observations(_event_key("Alfa FC", "Bravo FC"))
    assert all(r.timestamp == STAMP for r in rows_a)

    rows_b = store.all_observations(_event_key("Eco FC", "Foxtrot FC"))
    assert rows_b
    assert all(r.timestamp == utc_key(EVENT_TIMESTAMP) for r in rows_b)

    for row in (*rows_a, *rows_b):
        assert utc_key(row.timestamp) < utc_key(row.kickoff)
        assert row.kickoff == utc_key(KICKOFF)


# ==========================================================================
# 11. provider name preservado nas observacoes e no health
# ==========================================================================


def test_provider_name_preserved_in_observations(tmp_path):
    _, store = _capture(tmp_path, [FakeProviderA(), FakeProviderB()])
    for row in store.all_observations(_event_key("Alfa FC", "Bravo FC")):
        assert row.provider == "fake-a"
    for row in store.all_observations(_event_key("Gama FC", "Delta FC")):
        assert row.provider == "fake-b"


def test_health_and_credits_are_per_provider(tmp_path):
    a = FakeProviderA(credits=CreditUpdate(last=2, used=5, remaining=495))
    b = FakeProviderB(credits=CreditUpdate(used=1, remaining=99))
    report, store = _capture(
        tmp_path, [a, b], perf=_fake_perf([0.0, 0.25, 0.0, 0.125])
    )

    health = store.load_provider_health()
    assert set(health) == {"fake-a", "fake-b"}
    assert health["fake-a"]["latency_ms"] == pytest.approx(250.0)
    assert health["fake-b"]["latency_ms"] == pytest.approx(125.0)
    assert health["fake-a"]["credits_remaining"] == 495
    assert health["fake-b"]["credits_remaining"] == 99

    credits = store.load_provider_credits()
    assert credits["fake-a"]["known_remaining"] == 495
    assert credits["fake-a"]["used"] == 5
    assert credits["fake-b"]["known_remaining"] == 99
    assert credits["fake-b"]["used"] == 1

    assert report.credits_remaining == 99


# ==========================================================================
# 12-13. ignored (re-captura identica) e deduped (mesmo provider no lote)
# ==========================================================================


def test_second_identical_capture_reports_ignored(tmp_path):
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    report1, _ = _capture(tmp_path, FakeProviderA(), store=store)
    assert report1.observations_saved == 3
    assert report1.accounting == ()

    report2, _ = _capture(tmp_path, FakeProviderA(), store=store)
    assert report2.observations_saved == 0
    assert len(report2.accounting) == 3
    assert all(e.action == "ignored" for e in report2.accounting)
    assert all(e.provider == "fake-a" for e in report2.accounting)
    assert all(e.kept_by == "fake-a" for e in report2.accounting)
    assert report2.observations_received == 3
    assert report2.observations_dropped == 3
    # nada foi reenviado ao store
    assert len(store.all_observations(_event_key("Alfa FC", "Bravo FC"))) == 3


def test_same_provider_duplicate_in_batch_is_deduped(tmp_path):
    """Mesmo provider devolvendo o mesmo evento para dois escopos: a
    chave física repetida no lote e `deduped` (nao re-gravada)."""
    provider = FakeProviderA()
    report, store = _capture(
        tmp_path, provider, sports=[SPORT, "soccer_brazil_campeonato"]
    )
    assert len(provider.requests) == 2
    assert report.observations_saved == 3
    assert len(report.accounting) == 3
    assert all(e.action == "deduped" for e in report.accounting)
    assert all(e.provider == "fake-a" for e in report.accounting)
    assert all(e.kept_by == "fake-a" for e in report.accounting)
    assert len(store.all_observations(_event_key("Alfa FC", "Bravo FC"))) == 3


def test_accounting_divergence_is_never_masked(tmp_path):
    """store.add que diverge do enviado vira ERRO no report — nunca
    contagem silenciosa."""

    class _ShortCountStore:
        def __init__(self, real: OddsSnapshotStore) -> None:
            self._real = real

        def add(self, observations):
            self._real.add(observations)
            return max(0, len(observations) - 1)

        def all_observations(self, match_key):
            return self._real.all_observations(match_key)

        def save_provider_health(self, health, credits):
            return self._real.save_provider_health(health, credits)

    real = OddsSnapshotStore(tmp_path / "odds.db")
    capture = LiveOddsCapture(
        FakeProviderA([_single_outcome_event("Alfa FC", "Bravo FC")]),
        OddsHistoryCache(tmp_path / "cache"),
        regions="eu",
        markets="h2h",
        store=_ShortCountStore(real),
    )
    report = capture.capture([SPORT], now=NOW)
    assert report.observations_saved == 0
    assert any("contabiliza" in e.lower() for e in report.errors)


# ==========================================================================
# Escopo do pedido de contrato (divisoes do adapter, rotulos internos)
# ==========================================================================


def test_contract_request_carries_canonical_scope(tmp_path):
    provider = FakeProviderA(divisions=("E0",))
    report, _ = _capture(
        tmp_path, provider, regions="eu,uk", markets="h2h,totals"
    )
    (request,) = provider.requests
    assert isinstance(request, OddsFetchRequest)
    assert request.divisions == divisions_for(provider, SPORT) == ("E0",)
    assert request.markets == ("Resultado Final (1X2)", "Total de Gols")
    assert request.regions == "eu,uk"
    assert request.fetched_at == STAMP


def test_capture_from_fresh_registry_providers(tmp_path):
    """Providers montados por um registry novo entram em ordem de
    prioridade — a ordem decide quem fica dono das chaves físicas."""
    a, b = FakeProviderA(), FakeProviderB()
    registry = OddsProviderRegistry()
    registry.register(ProviderSpec(name="fake-a", factory=lambda: a, priority=2))
    registry.register(ProviderSpec(name="fake-b", factory=lambda: b, priority=1))
    pairs = registry.available_providers()
    assert [name for name, _ in pairs] == ["fake-b", "fake-a"]

    report, _ = _capture(tmp_path, [p for _, p in pairs])
    assert report.providers_used == ["fake-b", "fake-a"]
    assert report.observations_saved == 6


# ==========================================================================
# Construtor: um provider OU muitos (compat total com os call-sites)
# ==========================================================================


def test_constructor_accepts_single_sequence_and_keywords(tmp_path):
    a, b = FakeProviderA(), FakeProviderB()

    single = LiveOddsCapture(a, OddsHistoryCache(tmp_path / "c1"))
    assert single.providers == [a]
    assert single.provider is a

    as_list = LiveOddsCapture([a, b], OddsHistoryCache(tmp_path / "c2"))
    assert as_list.providers == [a, b]

    by_keyword = LiveOddsCapture(provider=a, cache=OddsHistoryCache(tmp_path / "c3"))
    assert by_keyword.providers == [a]

    by_providers = LiveOddsCapture(
        providers=[a, b], cache=OddsHistoryCache(tmp_path / "c4")
    )
    assert by_providers.providers == [a, b]

    with pytest.raises(ValueError, match="um.*OU.*providers"):
        LiveOddsCapture(a, providers=[b])


# ==========================================================================
# Compat: provider LEGADO (live_odds_with_meta) — caminho de hoje
# ==========================================================================


def test_legacy_provider_keeps_current_path(tmp_path):
    events = [_h2h_event("Alfa FC", "Bravo FC")]
    provider = FakeLiveProvider(events)
    report, store = _capture(tmp_path, provider, markets="h2h")

    # headers x-requests-* continuam sendo a fonte de creditos
    assert report.credits_last == 2
    assert report.credits_used == 5
    assert report.credits_remaining == 495
    # label de snapshot legado preservado
    snapshots = OddsHistoryCache(tmp_path / "cache").load(SPORT)
    assert snapshots[0].provider == "the-odds-api-live"
    # health sob o rotulo legado
    health = store.load_provider_health()
    assert health["The Odds API"]["state"] == "HEALTHY"
    assert health["The Odds API"]["credits_remaining"] == 495
    assert report.providers_used == ["The Odds API"]
    # observacoes com o provider legado
    rows = store.all_observations(_event_key("Alfa FC", "Bravo FC"))
    assert len(rows) == 3
    assert all(r.provider == "The Odds API" for r in rows)
    assert report.observations_saved == 3
    assert report.per_provider["The Odds API"]["observations_saved"] == 3


def test_legacy_provider_with_keyword_compatible(tmp_path):
    """Call-site estilo test_operational_pipeline: provider= por keyword."""
    provider = FakeLiveProvider([_h2h_event("Alfa FC", "Bravo FC")])
    capture = LiveOddsCapture(
        provider=provider,
        cache=OddsHistoryCache(tmp_path / "cache"),
        regions="eu",
        markets="h2h",
        store=OddsSnapshotStore(tmp_path / "odds.db"),
    )
    report = capture.capture([SPORT], now=NOW)
    assert report.observations_saved == 3
    assert report.errors == []


def test_legacy_rerun_reports_ignored(tmp_path):
    """A contabilizacao tambem se aplica ao caminho legado (single
    provider: apenas ignored/deduped)."""
    db = tmp_path / "odds.db"
    store = OddsSnapshotStore(db)
    events = [_h2h_event("Alfa FC", "Bravo FC")]
    report1, _ = _capture(tmp_path, FakeLiveProvider(events), store=store, markets="h2h")
    report2, _ = _capture(tmp_path, FakeLiveProvider(events), store=store, markets="h2h")
    assert report1.observations_saved == 3
    assert report2.observations_saved == 0
    assert report2.accounting
    assert all(e.action == "ignored" for e in report2.accounting)


# ==========================================================================
# CLI --capture-odds: providers via registry
# ==========================================================================


def _load_cli_module():
    """Carrega o script raiz betgsn.py como modulo (nao e o pacote)."""
    spec = importlib.util.spec_from_file_location("betgsn_cli_test", _REPO / "betgsn.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_capture_without_configured_provider_keeps_error(monkeypatch, capsys):
    monkeypatch.delenv("BETGSN_ODDS_API_KEY", raising=False)
    monkeypatch.delenv("BETGSN_PARLAY_API_KEY", raising=False)
    monkeypatch.delenv("BETGSN_PARLAY_API_BASE", raising=False)
    # providers novos: o teste afirma "NENHUM provider configurado" — sem
    # limpar, uma chave real no .env da maquina configuraria um provider
    # e invalidaria a premissa (hermeticidade).
    for name in (
        "BETGSN_ODDSPAPI_API_KEY",
        "ODDSPAPI_API_KEY",
        "BETGSN_ODDS_API_IO_KEY",
        "ODDS_API_IO_KEY",
        "BETGSN_OPTICODDS_API_KEY",
        "OPTICODDS_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    cli = _load_cli_module()
    rc = cli.capture_odds_cli([])

    assert rc == 1
    out = capsys.readouterr().out
    assert "ERRO: nenhum provider operacional configurado." in out
    assert "BETGSN_ODDS_API_KEY" in out


def test_cli_capture_uses_registry_providers(monkeypatch, capsys, tmp_path):
    import betgsn.odds_registry as registry_mod
    from betgsn.config import output_root

    monkeypatch.setenv("BETGSN_OUTPUT_DIR", str(tmp_path))
    # kickoff no FUTURO distante: a captura usa o relogio real, e um kickoff
    # fixo ja vencido faria `pre_kickoff` descartar tudo (teste sensivel ao
    # relogio). O ponto do teste e o registry, nao o calendario.
    provider = FakeProviderA(
        events=[_h2h_event("Alfa FC", "Bravo FC",
                           kickoff="2099-01-01T12:00:00Z")]
    )
    registry = OddsProviderRegistry()
    registry.register(
        ProviderSpec(name="fake-a", factory=lambda: provider, priority=1)
    )
    monkeypatch.setattr(
        registry_mod, "default_odds_registry", lambda: registry
    )

    cli = _load_cli_module()
    rc = cli.capture_odds_cli(["--sports=soccer_epl"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "observacoes" in out
    store = OddsSnapshotStore(output_root() / "odds_snapshots.db")
    assert store.stats()["observations"] == 3
    providers = store.stats()["providers"]
    assert providers.get("fake-a") == 3


# ==========================================================================
# Conformance do contrato (Task 1) contra os fakes A e B
# ==========================================================================


def test_fakes_satisfy_provider_contract_conformance():
    for provider, snapshot_label in (
        (FakeProviderA(), "fake-a-live"),
        (FakeProviderB(), "fake-b-live"),
    ):
        result = run_contract_conformance(provider, expect_name=provider.name)
        assert result.quotes, "conformance nao pode ser vacuo"
        assert result.no_coverage is False
        assert result.snapshot_provider == snapshot_label
        assert isinstance(provider, OddsProvider)


# ==========================================================================
# Verificacoes estruturais (estilo FASE A)
# ==========================================================================


_CORE_TARGETS = (
    "betgsn/staking.py",
    "betgsn/strategy.py",
    "betgsn/strategy_runner.py",
    "betgsn/models",
    "betgsn/portfolio",
    "betgsn/odds_snapshots.py",
    "betgsn/features/movement.py",
)

_FORBIDDEN_PROVIDER_MODULES = {"providers", "odds_registry", "odds_provider"}


def _core_files() -> list[Path]:
    files: list[Path] = []
    for target in _CORE_TARGETS:
        path = _REPO / target
        if path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
        else:
            files.append(path)
    return files


def _touches_provider_module(name: str) -> bool:
    parts = name.split(".")
    if parts and parts[0] == "betgsn":
        parts = parts[1:]
    return bool(parts) and parts[0] in _FORBIDDEN_PROVIDER_MODULES


def test_structural_core_does_not_import_provider_modules():
    """O core quantitativo nao conhece os modulos de provider da FASE B.

    Se alguem reintroduzir `providers`/`odds_registry`/`odds_provider`
    em staking, strategy, models, portfolio, odds_snapshots ou
    features/movement, este teste falha.
    """
    offenders: list[str] = []
    for path in _core_files():
        rel = str(path.relative_to(_REPO))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _touches_provider_module(alias.name):
                        offenders.append(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0 and not node.module:
                    for alias in node.names:
                        if _touches_provider_module(alias.name):
                            offenders.append(f"{rel}:{node.lineno}")
                elif node.module and _touches_provider_module(node.module):
                    offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], f"core importando modulos de provider: {offenders}"


def _calls_fetch_odds(func: ast.AST) -> bool:
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "fetch_odds":
            return True
        if isinstance(fn, ast.Name) and fn.id == "fetch_odds":
            return True
    return False


def test_structural_contract_path_avoids_legacy_transport():
    """O caminho de contrato nao le headers x-requests-* nem o mapa
    legado SPORT_KEY_TO_DIVISIONS — divisoes vem do adapter."""
    source = (_REPO / "betgsn" / "backtest_sources.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    contract_funcs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and _calls_fetch_odds(node)
    ]
    assert contract_funcs, "o caminho de contrato deve existir e chamar fetch_odds"

    for func in contract_funcs:
        identifiers = {
            node.id for node in ast.walk(func) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(func) if isinstance(node, ast.Attribute)
        }
        strings = {
            node.value
            for node in ast.walk(func)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert "SPORT_KEY_TO_DIVISIONS" not in identifiers, (
            "divisoes do caminho novo vem do adapter (divisions_for)"
        )
        assert not any(s.startswith("x-requests") for s in strings), (
            "creditos do caminho novo vem do contrato (CreditUpdate), "
            "nao de headers crus"
        )
