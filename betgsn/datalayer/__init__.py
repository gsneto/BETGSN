"""BETGSN :: datalayer — camada de dados multi-fonte, temporalmente correta.

Ponto de entrada único para obter dados de futebol de várias fontes com
fallback, cache com TTL, quota, limite de ritmo, saúde, cobertura,
proveniência e garantia point-in-time.

Exemplo mínimo
--------------
>>> from betgsn.datalayer import MultiSourceLayer, FootballDataUkSource
>>> layer = MultiSourceLayer()
>>> layer.register(FootballDataUkSource(), priority=1, supports_cache=False)
>>> envelope = layer.fetch("fixtures")
>>> envelope.status.value
'OK'  # ou 'STALE' / 'MISSING' / 'NO_COVERAGE', nunca uma mentira

Regra central: nenhum dado é fabricado. Ausência é explícita e datada.
"""

from __future__ import annotations

from .adapters import (
    ApiFootballSource,
    FootballDataOrgSource,
    FootballDataUkSource,
)
from .canonical_bridge import (
    envelope_to_canonical,
    from_api_fixture,
    from_csv_match,
    from_upcoming_fixture,
    odds_to_canonical,
)
from .coverage import CoverageReport, SourceCoverage, build_coverage
from .envelope import (
    DataEnvelope,
    DataStatus,
    DataUnavailable,
    Provenance,
    build_provenance,
)
from .entity import (
    Entity,
    EntityKind,
    EntityMatch,
    EntityRegistry,
    MatchStatus,
    normalize_name,
)
from .errors import (
    ErrorKind,
    SourceError,
    classify_exception,
    classify_message,
    classify_status,
)
from .health import HealthRegistry, ProviderStatus, SourceHealth
from .pointintime import (
    PointInTimeError,
    assert_no_future,
    available_at,
    filter_available_before,
    guard_envelope,
)
from .ratelimit import RateLimiter
from .source import (
    BaseSource,
    Capabilities,
    MultiSourceLayer,
    RawFetch,
    RetryPolicy,
    SourceRegistration,
    TTL_BY_KIND,
)
from .times import age_seconds, humanize_age, utc_stamp
from .xg import ApiFootballXGSource, UnavailableXGSource, xg_from_statistics

__all__ = [
    # orquestração
    "MultiSourceLayer",
    "BaseSource",
    "RawFetch",
    "RetryPolicy",
    "SourceRegistration",
    "Capabilities",
    "TTL_BY_KIND",
    # adapters
    "FootballDataUkSource",
    "ApiFootballSource",
    "FootballDataOrgSource",
    # ponte canônica
    "envelope_to_canonical",
    "from_api_fixture",
    "from_csv_match",
    "from_upcoming_fixture",
    "odds_to_canonical",
    # envelope/proveniência
    "DataEnvelope",
    "DataStatus",
    "DataUnavailable",
    "Provenance",
    "build_provenance",
    # erros/health
    "ErrorKind",
    "SourceError",
    "classify_exception",
    "classify_status",
    "classify_message",
    "HealthRegistry",
    "ProviderStatus",
    "SourceHealth",
    # cache/quota/ritmo
    "RateLimiter",
    # entidades
    "Entity",
    "EntityKind",
    "EntityMatch",
    "EntityRegistry",
    "MatchStatus",
    "normalize_name",
    # point-in-time
    "PointInTimeError",
    "available_at",
    "filter_available_before",
    "assert_no_future",
    "guard_envelope",
    # xG
    "ApiFootballXGSource",
    "UnavailableXGSource",
    "xg_from_statistics",
    # cobertura
    "CoverageReport",
    "SourceCoverage",
    "build_coverage",
    # tempo
    "utc_stamp",
    "age_seconds",
    "humanize_age",
]
