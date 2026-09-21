"""Testes da camada de dados multi-fonte (`betgsn.datalayer`).

Nenhum teste toca a rede nem espera de verdade: fontes são falsas, o
relógio é injetado e o `sleep` é substituído por um no-op. O que se prova
aqui é o comportamento sob falha — 429, timeout, quota, resposta
incompleta, ausência de cobertura — e as garantias temporais.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from betgsn.cache import DiskCache
from betgsn.data_quality import QualityGrade, grade_for_fetch
from betgsn.datalayer import (
    Capabilities,
    DataStatus,
    Entity,
    EntityKind,
    EntityRegistry,
    ErrorKind,
    HealthRegistry,
    MatchStatus,
    MultiSourceLayer,
    ProviderStatus,
    RateLimiter,
    RawFetch,
    RetryPolicy,
    SourceError,
    assert_no_future,
    build_coverage,
    classify_exception,
    classify_message,
    classify_status,
    filter_available_before,
    guard_envelope,
    normalize_name,
    utc_stamp,
)
from betgsn.datalayer.envelope import build_provenance
from betgsn.datalayer.entity import normalize_name as entity_normalize
from betgsn.datalayer.pointintime import PointInTimeError, available_at
from betgsn.datalayer.xg import ApiFootballXGSource, xg_from_statistics
from betgsn.providers import ProviderError
from betgsn.xg_sources import ChainXGSource, XGStatus, UnavailableXGSource

BASE = "2025-05-01T12:00:00Z"


def future(seconds: int) -> str:
    moment = datetime.strptime(BASE, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    from datetime import timedelta

    return utc_stamp(moment + timedelta(seconds=seconds))


class FakeSource:
    """Fonte controlável: sucesso, erro, resposta incompleta ou indisponível."""

    def __init__(
        self,
        name: str,
        capabilities,
        *,
        records=None,
        error: Exception | None = None,
        available: bool = True,
        timestamp: str = "",
        complete: bool = True,
        coverage=(),
    ) -> None:
        self.name = name
        self.capabilities = frozenset(capabilities)
        self.records = list(records or [])
        self.error = error
        self._available = available
        self.timestamp = timestamp
        self.complete = complete
        self.coverage = tuple(coverage)
        self.calls: list[tuple[str, dict]] = []

    def available(self) -> bool:
        return self._available

    def fetch(self, kind: str, **params):
        self.calls.append((kind, params))
        if self.error is not None:
            raise self.error
        return RawFetch(
            records=list(self.records),
            source=self.name,
            source_timestamp=self.timestamp,
            coverage=self.coverage,
            complete=self.complete,
            note=f"{len(self.records)} registros",
        )


def make_layer(tmp_path, *, quota=None):
    return MultiSourceLayer(
        cache_root=tmp_path / "cache",
        quota=quota,
        sleep=lambda _s: None,
        clock=lambda: BASE,
    )


# ==========================================================================
# Classificação de erros
# ==========================================================================


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        (401, ErrorKind.AUTH),
        (403, ErrorKind.FORBIDDEN),
        (404, ErrorKind.NOT_FOUND),
        (429, ErrorKind.RATE_LIMIT),
        (500, ErrorKind.SERVER),
        (503, ErrorKind.SERVER),
        (422, ErrorKind.INCOMPLETE),
    ],
)
def test_classify_status(code, kind):
    assert classify_status(code) == kind


def test_only_transient_kinds_are_retryable():
    assert classify_status(429).value == "RATE_LIMIT"
    err = classify_exception(ProviderError("HTTP 429 em https://x?apiKey=***"), "src")
    assert err.kind == ErrorKind.RATE_LIMIT and err.retryable
    err = classify_exception(ProviderError("HTTP 401 em https://x?apiKey=***"), "src")
    assert err.kind == ErrorKind.AUTH and not err.retryable


def test_classify_timeout_and_connection():
    assert classify_exception(TimeoutError("demorou"), "s").kind == ErrorKind.TIMEOUT
    assert classify_exception(ConnectionError("caiu"), "s").kind == ErrorKind.CONNECTION


def test_classify_message_quota_and_coverage():
    assert classify_message("quota exceeded for today") == ErrorKind.QUOTA
    assert classify_message("Markets not supported by this endpoint") == ErrorKind.NO_COVERAGE
    assert classify_message("HTTP 429 em x") == ErrorKind.RATE_LIMIT


def test_error_message_never_leaks_key():
    err = SourceError(ErrorKind.AUTH, "s", "HTTP 401 em https://x?apiKey=SEGREDO")
    assert "SEGREDO" not in str(err)


# ==========================================================================
# Fallback: 429, timeout, incompleta, indisponível
# ==========================================================================


def test_fallback_uses_second_source_when_first_fails(tmp_path):
    failing = FakeSource("primary", [Capabilities.RESULTS],
                         error=ProviderError("HTTP 500 em https://x"))
    backup = FakeSource("backup", [Capabilities.RESULTS], records=[{"id": 1}])
    layer = make_layer(tmp_path)
    layer.register(failing, priority=1)
    layer.register(backup, priority=2)

    env = layer.fetch(Capabilities.RESULTS, use_cache=False)
    assert env.status == DataStatus.DEGRADED
    assert env.value == [{"id": 1}]
    assert env.source == "backup"
    assert env.errors and "HTTP 500" in env.errors[0]
    # a fonte primária entrou no histórico de saúde
    assert layer.health.status_of("primary") == ProviderStatus.DEGRADED


def test_429_retries_bounded_then_falls_back(tmp_path):
    flaky = FakeSource("flaky", [Capabilities.RESULTS],
                       error=ProviderError("HTTP 429 em https://x"))
    backup = FakeSource("backup", [Capabilities.RESULTS], records=[{"id": 2}])
    layer = make_layer(tmp_path)
    layer.register(flaky, priority=1, retry=RetryPolicy(max_attempts=3))
    layer.register(backup, priority=2)

    env = layer.fetch(Capabilities.RESULTS, use_cache=False)
    assert env.source == "backup"
    assert len(flaky.calls) == 3, "retry deve ser limitado a max_attempts"
    assert layer.health.status_of("flaky") == ProviderStatus.DEGRADED


def test_timeout_is_retried_then_next_source(tmp_path):
    slow = FakeSource("slow", [Capabilities.FIXTURES], error=TimeoutError("timeout"))
    fast = FakeSource("fast", [Capabilities.FIXTURES], records=[{"m": 1}])
    layer = make_layer(tmp_path)
    layer.register(slow, priority=1, retry=RetryPolicy(max_attempts=2))
    layer.register(fast, priority=2)

    env = layer.fetch(Capabilities.FIXTURES, use_cache=False)
    assert len(slow.calls) == 2
    assert env.source == "fast"


def test_incomplete_response_falls_back(tmp_path):
    partial = FakeSource("partial", [Capabilities.RESULTS],
                         records=[{"id": 1}], complete=False)
    full = FakeSource("full", [Capabilities.RESULTS], records=[{"id": 1}, {"id": 2}])
    layer = make_layer(tmp_path)
    layer.register(partial, priority=1)
    layer.register(full, priority=2)

    env = layer.fetch(Capabilities.RESULTS, use_cache=False)
    assert env.source == "full"
    assert env.status == DataStatus.DEGRADED
    assert any("incompleta" in e.lower() or "INCOMPLETE" in e for e in env.errors)


def test_unavailable_source_is_skipped(tmp_path):
    no_key = FakeSource("no_key", [Capabilities.RESULTS], available=False)
    backup = FakeSource("backup", [Capabilities.RESULTS], records=[{"id": 1}])
    layer = make_layer(tmp_path)
    layer.register(no_key, priority=1)
    layer.register(backup, priority=2)

    env = layer.fetch(Capabilities.RESULTS, use_cache=False)
    assert env.source == "backup"
    assert no_key.calls == []


def test_no_source_covers_kind(tmp_path):
    layer = make_layer(tmp_path)
    layer.register(FakeSource("csv", [Capabilities.RESULTS]), priority=1)
    env = layer.fetch(Capabilities.XG, use_cache=False)
    assert env.status == DataStatus.NO_COVERAGE
    assert not env.available


def test_all_sources_fail_is_missing_not_fake_success(tmp_path):
    a = FakeSource("a", [Capabilities.RESULTS], error=ProviderError("HTTP 503"))
    b = FakeSource("b", [Capabilities.RESULTS], error=ProviderError("HTTP 401"))
    layer = make_layer(tmp_path)
    layer.register(a, priority=1)
    layer.register(b, priority=2)

    env = layer.fetch(Capabilities.RESULTS, use_cache=False)
    assert env.status == DataStatus.MISSING
    assert env.value is None
    with pytest.raises(Exception):
        env.require()


# ==========================================================================
# Quota e rate limit
# ==========================================================================


def test_quota_exhaustion_degrades_to_next_source(tmp_path):
    from betgsn.quota import QuotaManager

    quota = QuotaManager()
    quota.register("limited", daily_limit=1)
    limited = FakeSource("limited", [Capabilities.RESULTS], records=[{"id": 1}])
    backup = FakeSource("backup", [Capabilities.RESULTS], records=[{"id": 2}])
    layer = make_layer(tmp_path, quota=quota)
    layer.register(limited, priority=1, quota_provider="limited")
    layer.register(backup, priority=2, quota_provider="backup")

    first = layer.fetch(Capabilities.RESULTS, use_cache=False)
    assert first.source == "limited"
    assert len(limited.calls) == 1

    second = layer.fetch(Capabilities.RESULTS, use_cache=False)
    assert second.source == "backup"
    assert len(limited.calls) == 1, "quota esgotada: nao pode chamar de novo"
    assert any("QUOTA" in e for e in second.errors)
    assert layer.health.health("limited").in_cooldown(BASE)


def test_rate_limiter_waits_and_records():
    slept: list[float] = []
    limiter = RateLimiter(
        min_interval_seconds=10,
        clock=lambda: 100.0,
        sleeper=slept.append,
    )
    limiter.record(now=100.0)
    assert limiter.delay_for(now=104.0) == pytest.approx(6.0)
    waited = limiter.acquire(now=104.0)
    assert waited == pytest.approx(6.0)
    assert slept == [pytest.approx(6.0)]


# ==========================================================================
# Cache, TTL e stale
# ==========================================================================


def test_cache_hit_avoids_second_fetch(tmp_path):
    source = FakeSource("s", [Capabilities.FIXTURES], records=[{"a": 1}],
                        timestamp=BASE)
    layer = make_layer(tmp_path)
    layer.register(source, priority=1)

    first = layer.fetch(Capabilities.FIXTURES, now=BASE)
    assert first.source == "s" and not first.provenance.from_cache
    source.records = [{"a": 999}]
    second = layer.fetch(Capabilities.FIXTURES, now=future(60))
    assert second.value == [{"a": 1}], "cache fresco deve vencer"
    assert second.provenance.from_cache
    # a idade é a do dado da fonte (BASE), não a da gravação no cache
    assert second.provenance.data_age_seconds == pytest.approx(60.0)
    assert len(source.calls) == 1


def test_cache_ttl_expiry_refetches(tmp_path):
    source = FakeSource("s", [Capabilities.FIXTURES], records=[{"a": 1}])
    layer = make_layer(tmp_path)
    layer.register(source, priority=1, ttl=100)

    layer.fetch(Capabilities.FIXTURES, now=BASE)
    source.records = [{"a": 2}]
    refreshed = layer.fetch(Capabilities.FIXTURES, now=future(200))
    assert refreshed.value == [{"a": 2}]
    assert len(source.calls) == 2


def test_stale_cache_is_served_explicitly_when_sources_fail(tmp_path):
    source = FakeSource("s", [Capabilities.FIXTURES], records=[{"a": 1}],
                        timestamp=BASE)
    layer = make_layer(tmp_path)
    layer.register(source, priority=1, ttl=100)

    layer.fetch(Capabilities.FIXTURES, now=BASE)
    source.error = ProviderError("HTTP 503 em https://x")

    stale = layer.fetch(Capabilities.FIXTURES, now=future(200), allow_stale=True)
    assert stale.status == DataStatus.STALE
    assert stale.is_stale
    assert stale.value == [{"a": 1}]
    assert stale.provenance.data_age_seconds is not None
    assert "cache antigo" in stale.note


def test_stale_beyond_window_is_refused(tmp_path):
    source = FakeSource("s", [Capabilities.FIXTURES], records=[{"a": 1}])
    layer = make_layer(tmp_path)
    layer.register(source, priority=1, ttl=100)

    layer.fetch(Capabilities.FIXTURES, now=BASE)
    source.error = ProviderError("HTTP 503 em https://x")

    # 10 dias depois, com janela de stale de 1h: recusado
    refused = layer.fetch(
        Capabilities.FIXTURES, now=future(10 * 86400),
        allow_stale=True, max_stale_seconds=3600,
    )
    assert refused.status == DataStatus.MISSING


def test_cache_never_serves_fresh_without_provenance(tmp_path):
    source = FakeSource("s", [Capabilities.RESULTS], records=[{"a": 1}],
                        timestamp=BASE)
    layer = make_layer(tmp_path)
    layer.register(source, priority=1)
    layer.fetch(Capabilities.RESULTS, now=BASE)
    cached = layer.fetch(Capabilities.RESULTS, now=future(30))
    assert cached.provenance is not None
    assert cached.provenance.fetched_at
    assert cached.provenance.source == "s"


# ==========================================================================
# Health
# ==========================================================================


def test_health_transitions_degraded_then_unavailable():
    registry = HealthRegistry(degraded_after=2, unavailable_after=4)
    err = SourceError(ErrorKind.SERVER, "s", "HTTP 503")
    registry.record_failure("s", err, at=BASE)
    assert registry.status_of("s") == ProviderStatus.DEGRADED
    registry.record_failure("s", err, at=BASE)
    assert registry.status_of("s") == ProviderStatus.DEGRADED
    for _ in range(2):
        registry.record_failure("s", err, at=BASE)
    assert registry.status_of("s") == ProviderStatus.UNAVAILABLE
    registry.record_success("s", at=BASE)
    assert registry.status_of("s") == ProviderStatus.HEALTHY
    assert registry.health("s").reliability_pct == pytest.approx(20.0)


def test_no_coverage_is_not_a_failure():
    registry = HealthRegistry()
    err = SourceError(ErrorKind.NO_COVERAGE, "org", "sem xG")
    registry.record_failure("org", err, at=BASE)
    health = registry.health("org")
    assert health.status == ProviderStatus.NO_COVERAGE
    assert health.total_failures == 0
    assert registry.usable("org")


# ==========================================================================
# Entity matching
# ==========================================================================


def test_normalize_name_matches_variants():
    assert entity_normalize("São Paulo") == entity_normalize("Sao Paulo FC")
    assert entity_normalize("CR Flamengo") == entity_normalize("Flamengo")
    assert normalize_name("Botafogo FR") == "botafogo"


def test_registry_resolves_exact_and_alias():
    registry = EntityRegistry()
    registry.add(Entity(key="sao-paulo", name="São Paulo", country="Brazil",
                        aliases=("Sao Paulo FC", "SPFC")))
    match = registry.resolve("Sao Paulo FC", kind=EntityKind.TEAM)
    assert match.ok and match.entity.key == "sao-paulo"


def test_registry_marks_ambiguity_instead_of_guessing():
    registry = EntityRegistry()
    registry.add(Entity(key="nacional-uru", name="Nacional", country="Uruguay"))
    registry.add(Entity(key="nacional-madeira", name="Nacional", country="Portugal"))
    match = registry.resolve("Nacional", kind=EntityKind.TEAM)
    assert match.status == MatchStatus.AMBIGUOUS
    assert {c.key for c in match.candidates} == {"nacional-uru", "nacional-madeira"}


def test_registry_country_disambiguates():
    registry = EntityRegistry()
    registry.add(Entity(key="nacional-uru", name="Nacional", country="Uruguay"))
    registry.add(Entity(key="nacional-madeira", name="Nacional", country="Portugal"))
    match = registry.resolve("Nacional", kind=EntityKind.TEAM, country="Uruguay")
    assert match.ok and match.entity.key == "nacional-uru"


def test_registry_unknown_never_invents():
    registry = EntityRegistry()
    registry.add(Entity(key="flamengo", name="Flamengo"))
    match = registry.resolve("Time Inexistente")
    assert match.status == MatchStatus.UNKNOWN and match.entity is None


def test_fuzzy_requires_unique_winner():
    registry = EntityRegistry()
    registry.add(Entity(key="palmeiras", name="Palmeiras"))
    registry.add(Entity(key="palmieras", name="Palmieras"))
    # "Palmeiras" casa exato com um; o fuzzy só entra quando não há exato
    assert registry.resolve("Palmeiras").ok
    tie = registry.resolve_fuzzy("Palmeyras", threshold=0.8)
    assert tie.status in (MatchStatus.MATCHED, MatchStatus.AMBIGUOUS)


def test_registry_external_ids():
    registry = EntityRegistry()
    registry.add(Entity(key="flamengo", name="Flamengo",
                        external_ids=(("api_football", "127"),)))
    assert registry.by_external("api_football", "127").key == "flamengo"
    assert registry.by_external("api_football", "999") is None


# ==========================================================================
# Point-in-time
# ==========================================================================


def test_filter_available_before_excludes_future_results():
    past = {"kickoff": "2025-04-20T15:00:00Z", "home_goals": 1}
    future_match = {"kickoff": "2025-05-10T15:00:00Z", "home_goals": 2}
    kept = filter_available_before([past, future_match], BASE, kind="result")
    assert past in kept and future_match not in kept


def test_assert_no_future_raises_on_leak():
    leak = {"kickoff": "2025-06-01T15:00:00Z"}
    with pytest.raises(PointInTimeError):
        assert_no_future([leak], BASE, kind="result", label="corpus")


def test_fixture_availability_is_kickoff():
    fixture = {"kickoff": "2025-05-20T18:00:00Z"}
    assert available_at(fixture, kind="fixture") == "2025-05-20T18:00:00Z"
    assert available_at(fixture, kind="fixture") > BASE


def test_guard_envelope_rejects_future_source():
    from betgsn.datalayer import DataEnvelope

    provenance = build_provenance(
        source="s", source_timestamp="2025-06-01T00:00:00Z", fetched_at=BASE
    )
    envelope = DataEnvelope(value=[1], provenance=provenance, status=DataStatus.OK)
    guarded = guard_envelope(envelope, BASE)
    assert guarded.status == DataStatus.MISSING
    assert "posterior ao corte" in guarded.note


def test_guard_envelope_allows_older_source():
    from betgsn.datalayer import DataEnvelope

    provenance = build_provenance(
        source="s", source_timestamp="2025-04-01T00:00:00Z", fetched_at=BASE
    )
    envelope = DataEnvelope(value=[1], provenance=provenance, status=DataStatus.OK)
    assert guard_envelope(envelope, BASE).status == DataStatus.OK


# ==========================================================================
# xG — ausência explícita
# ==========================================================================


def test_xg_absent_is_none_not_zero():
    payload = [
        {"team": {"name": "A"}, "statistics": [{"type": "Total Shots", "value": 10}]},
        {"team": {"name": "B"}, "statistics": [{"type": "Total Shots", "value": 8}]},
    ]
    obs = xg_from_statistics(payload, "A", "B")
    assert obs.status == XGStatus.UNAVAILABLE
    assert obs.home_xg is None and obs.away_xg is None
    assert "sem xG" in obs.note or "sem xg" in obs.note.lower()


def test_xg_real_keeps_source_and_orientation():
    payload = [
        {"team": {"name": "A"}, "statistics": [{"type": "expected_goals", "value": "1.9"}]},
        {"team": {"name": "B"}, "statistics": [{"type": "expected_goals", "value": "0.7"}]},
    ]
    obs = xg_from_statistics(payload, "A", "B", source="API-Football")
    assert obs.status == XGStatus.REAL
    assert obs.home_xg == pytest.approx(1.9)
    assert obs.away_xg == pytest.approx(0.7)
    assert obs.home_xg_against == pytest.approx(0.7)
    assert obs.source == "API-Football"


def test_xg_incomplete_payload_is_unavailable():
    assert xg_from_statistics([], "A", "B").status == XGStatus.UNAVAILABLE
    assert xg_from_statistics(None, "A", "B").status == XGStatus.UNAVAILABLE


def test_xg_unknown_orientation_is_unavailable():
    payload = [
        {"team": {"name": "X"}, "statistics": [{"type": "expected_goals", "value": "1.0"}]},
        {"team": {"name": "Y"}, "statistics": [{"type": "expected_goals", "value": "2.0"}]},
    ]
    obs = xg_from_statistics(payload, "A", "B")
    assert obs.status == XGStatus.UNAVAILABLE


def test_api_football_xg_source_without_context():
    class Provider:
        def fixture_statistics(self, fixture_id):
            raise AssertionError("nao deveria ser chamado sem contexto de mando")

    source = ApiFootballXGSource(Provider(), team_names=None)
    obs = source.fetch("123")
    assert obs.status == XGStatus.UNAVAILABLE
    assert "contexto" in obs.note


def test_chain_xg_source_reports_reason_when_all_unavailable():
    chain = ChainXGSource([
        UnavailableXGSource("fonte A sem liga"),
        UnavailableXGSource("fonte B sem chave"),
    ])
    obs = chain.fetch("1")
    assert obs.status == XGStatus.UNAVAILABLE
    assert "fonte A" in obs.note and "fonte B" in obs.note


# ==========================================================================
# Cobertura e qualidade
# ==========================================================================


def test_build_coverage_maps_features_to_sources(tmp_path):
    csv = FakeSource("football_data_uk", [Capabilities.RESULTS, Capabilities.ODDS])
    org = FakeSource("football_data_org", [Capabilities.FIXTURES, Capabilities.RESULTS])
    api = FakeSource("api_football", [Capabilities.RESULTS, Capabilities.XG])
    layer = make_layer(tmp_path)
    for source in (csv, org, api):
        layer.register(source, priority=1)

    report = build_coverage(layer.registrations(), layer.health)
    assert set(report.for_feature(Capabilities.RESULTS)) == {
        "football_data_uk", "football_data_org", "api_football"
    }
    assert report.for_feature(Capabilities.XG) == ("api_football",)
    assert report.for_feature(Capabilities.ODDS) == ("football_data_uk",)
    assert report.gaps([Capabilities.RESULTS]) == {}
    assert report.gaps([Capabilities.INJURIES]) == {Capabilities.INJURIES: ()}
    assert "providers_by_feature" in report.summary()


def test_quality_grades_are_conservative():
    assert grade_for_fetch(primary=True, from_cache=False) == QualityGrade.HIGH
    assert grade_for_fetch(primary=True, from_cache=True) == QualityGrade.MEDIUM
    assert grade_for_fetch(primary=True, from_cache=True, stale=True) == QualityGrade.LOW
    assert grade_for_fetch(primary=True, complete=False) == QualityGrade.LOW


def test_cache_entry_exposes_age(tmp_path):
    cache = DiskCache(tmp_path / "c")
    cache.put("ns", "k", {"v": 1}, ttl=100, source="s", created_at=10.0)
    meta = cache.get_with_meta("ns", "k", allow_stale=True, now=50.0)
    assert meta is not None
    data, info = meta
    assert data == {"v": 1}
    assert info["age_seconds"] == pytest.approx(40.0)
    assert info["stale"] is False

    # além do TTL, mas dentro da janela de stale (ttl 100 + 60)
    stale = cache.get_with_meta("ns", "k", allow_stale=True, max_stale_seconds=60, now=150.0)
    assert stale is not None and stale[1]["stale"] is True

    # além da janela: recusado
    assert cache.get_with_meta("ns", "k", allow_stale=True, max_stale_seconds=60, now=1000.0) is None


# ==========================================================================
# Adapters (sem rede)
# ==========================================================================


def test_api_football_source_filters_finished_and_reports_coverage():
    from betgsn.datalayer import ApiFootballSource

    class Provider:
        def fixtures_by_season(self, league, season):
            return [
                {"fixture": {"id": 1, "date": "2024-04-14T15:00:00-03:00",
                             "status": {"short": "FT"}},
                 "goals": {"home": 2, "away": 1}, "teams": {}},
                {"fixture": {"id": 2, "date": "2024-04-21T15:00:00-03:00",
                             "status": {"short": "NS"}},
                 "goals": {"home": None, "away": None}, "teams": {}},
            ]

    source = ApiFootballSource(Provider())
    raw = source.fetch(Capabilities.RESULTS, league=71, season=2024)
    assert len(raw.records) == 1
    assert raw.coverage == ("league:71:season:2024",)


def test_api_football_source_without_key_is_unavailable():
    from betgsn.datalayer import ApiFootballSource

    source = ApiFootballSource(None)
    assert not source.available()
    raw = source.fetch(Capabilities.RESULTS, league=71, season=2024)
    assert not raw.complete


def test_football_data_org_does_not_claim_xg_or_odds():
    from betgsn.datalayer import FootballDataOrgSource

    caps = FootballDataOrgSource(None).capabilities
    assert Capabilities.FIXTURES in caps
    assert Capabilities.XG not in caps
    assert Capabilities.ODDS not in caps


def test_football_data_uk_source_available_reflects_cache(tmp_path):
    from betgsn.datalayer import FootballDataUkSource
    from betgsn.football_data_uk import FootballDataClient

    source = FootballDataUkSource(FootballDataClient(root=tmp_path))
    assert not source.available()
    (tmp_path / "main").mkdir(parents=True, exist_ok=True)
    (tmp_path / "main" / "2425_E0.csv").write_text("Div,Date\n", encoding="utf-8")
    assert source.available()
    assert Capabilities.XG not in source.capabilities
    assert Capabilities.ODDS in source.capabilities


def test_football_data_uk_source_flows_through_layer(tmp_path):
    """Integração real: CSV local -> adapter -> camada -> envelope."""
    from betgsn.datalayer import FootballDataUkSource
    from betgsn.football_data_uk import FootballDataClient

    header = ("Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,"
              "PSH,PSD,PSA,PSCH,PSCD,PSCA")
    row = ("E0,16/08/2024,20:00,Man United,Fulham,1,0,"
           "1.63,4.38,5.30,1.65,4.23,5.28")
    (tmp_path / "main").mkdir(parents=True, exist_ok=True)
    (tmp_path / "main" / "2425_E0.csv").write_text(f"{header}\n{row}\n", encoding="utf-8")

    layer = MultiSourceLayer(
        cache_root=tmp_path / "cache", sleep=lambda _s: None, clock=lambda: BASE
    )
    layer.register(
        FootballDataUkSource(FootballDataClient(root=tmp_path)),
        priority=1, supports_cache=False,
    )
    env = layer.fetch(Capabilities.RESULTS, now=BASE)
    assert env.status == DataStatus.OK
    assert env.source == "football_data_uk"
    assert len(env.value) == 1 and env.value[0].home == "Man United"
    assert env.provenance.source_timestamp, "mtime do CSV vira carimbo da fonte"

    odds = layer.fetch(Capabilities.ODDS, now=BASE)
    assert odds.available and odds.value[0].odds_closing


# ==========================================================================
# Ponte canônica
# ==========================================================================


def test_canonical_bridge_from_csv_match():
    from betgsn.datalayer import DataEnvelope, from_csv_match, envelope_to_canonical
    from betgsn.football_data_uk import parse_main_row

    row = {
        "Div": "E0", "Date": "16/08/2024", "Time": "20:00",
        "HomeTeam": "Man United", "AwayTeam": "Fulham",
        "FTHG": "1", "FTAG": "0", "HS": "14", "AS": "10", "HST": "5", "AST": "2",
        "HC": "7", "AC": "8",
        "PSH": "1.63", "PSD": "4.38", "PSA": "5.30",
        "PSCH": "1.65", "PSCD": "4.23", "PSCA": "5.28",
    }
    match = parse_main_row(row, "E0")
    assert match is not None

    canonical = from_csv_match(match)
    assert canonical.finished
    assert canonical.home == "Man United" and canonical.home_goals == 1
    assert canonical.xg.status == "UNAVAILABLE", "CSV não publica xG: ausência explícita"
    assert canonical.odds is not None
    one_x_two = canonical.odds.by_market()["Resultado Final (1X2)"]
    assert one_x_two["Pinnacle"]["1"] == pytest.approx(1.65)
    # odds sem carimbo não são consideradas disponíveis antes de nenhum corte
    assert canonical.odds.best_before("2030-01-01T00:00:00Z").lines == ()

    envelope = DataEnvelope(value=[match], status=DataStatus.OK, kind=Capabilities.RESULTS)
    converted = envelope_to_canonical(envelope, Capabilities.RESULTS)
    assert len(converted) == 1 and converted[0].away == "Fulham"


def test_canonical_bridge_api_fixture_requires_score():
    from betgsn.datalayer import from_api_fixture

    incomplete = {"fixture": {"date": "2024-01-01T00:00:00Z"}, "teams": {}, "goals": {}}
    assert from_api_fixture(incomplete) is None

    payload = {
        "fixture": {"date": "2024-04-14T15:00:00-03:00"},
        "league": {"name": "Serie A", "season": 2024},
        "teams": {"home": {"name": "Palmeiras"}, "away": {"name": "Flamengo"}},
        "goals": {"home": 2, "away": 1},
    }
    canonical = from_api_fixture(payload)
    assert canonical is not None
    assert canonical.home_goals == 2 and canonical.season == "2024"


# ==========================================================================
# Resolução do .env a partir de worktree
# ==========================================================================


def test_env_candidates_include_linked_worktree_main(tmp_path):
    from betgsn import envconfig

    main = tmp_path / "main"
    worktree = tmp_path / "worktree"
    gitdir = main / ".git" / "worktrees" / "worktree"
    gitdir.mkdir(parents=True)
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / ".git").write_text(f"gitdir: {gitdir}", encoding="utf-8")
    (gitdir / "commondir").write_text("../..", encoding="utf-8")
    (main / ".env").write_text("BETGSN_APIFOOTBALL_KEY=segredo\n", encoding="utf-8")

    candidates = envconfig.env_file_candidates(worktree)
    assert (main / ".env").resolve() in [c.resolve() for c in candidates]
    assert envconfig.resolve_env_file(worktree) == (main / ".env")


def test_load_env_file_from_explicit_path(tmp_path, monkeypatch):
    from betgsn.providers import load_env_file

    env = tmp_path / ".env"
    env.write_text("BETGSN_TEST_KEY=valor\n", encoding="utf-8")
    monkeypatch.delenv("BETGSN_TEST_KEY", raising=False)
    assert load_env_file(env) == 1
    assert __import__("os").environ["BETGSN_TEST_KEY"] == "valor"
