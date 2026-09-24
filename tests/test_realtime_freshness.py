"""Freshness do terminal em tempo real: idade real, sem maquiagem.

Regra testada: o estado de frescor vem do TIMESTAMP DA OBSERVACAO,
nunca do momento da captura ou de valor default. Sem carimbo:
UNKNOWN — nunca 0, nunca "agora".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from betgsn.realtime.freshness import (
    FreshnessState,
    FreshnessThresholds,
    age_seconds,
    freshness_state,
)

NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


def test_thresholds_must_be_crescent():
    with pytest.raises(ValueError):
        FreshnessThresholds(fresh_seconds=900, recent_seconds=300).validate()


def test_fresh_recent_stale_by_age():
    thresholds = FreshnessThresholds(
        fresh_seconds=120, recent_seconds=600, stale_seconds=3600
    )
    fresh, age = freshness_state("2026-09-24T11:59:30Z", thresholds, NOW)
    assert fresh is FreshnessState.FRESH
    assert age == 30.0

    recent, _ = freshness_state("2026-09-24T11:55:00Z", thresholds, NOW)
    assert recent is FreshnessState.RECENT

    stale, _ = freshness_state("2026-09-24T10:00:00Z", thresholds, NOW)
    assert stale is FreshnessState.STALE


def test_missing_stamp_is_unknown_never_fresh():
    state, age = freshness_state("", FreshnessThresholds(), NOW)
    assert state is FreshnessState.UNKNOWN
    assert age is None


def test_future_stamp_is_unknown_never_fresh():
    """Carimbo no futuro e dado suspeito — nunca apresentado como FRESH."""
    state, age = freshness_state("2026-09-24T12:30:00Z", FreshnessThresholds(), NOW)
    assert state is FreshnessState.UNKNOWN
    assert age is not None and age < 0


def test_age_seconds_uses_observation_stamp():
    stamp = (NOW - timedelta(minutes=5)).isoformat()
    assert age_seconds(stamp, NOW) == 300.0


def test_age_of_invalid_stamp_is_none():
    assert age_seconds("not-a-stamp", NOW) is None
