"""Testes de cache, quota e data quality."""
import pytest
import time
from pathlib import Path

from betgsn.cache import DiskCache, TTL_FIXTURES, TTL_HISTORICAL
from betgsn.quota import QuotaManager, PRIORITY_CRITICAL
from betgsn.data_quality import (
    FieldComparison, MatchQualityReport, compare_match_data, ProviderHealth,
)


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def test_cache_put_and_get(tmp_path):
    cache = DiskCache(tmp_path / "cache")
    cache.put("test", "key1", {"hello": "world"})
    result = cache.get("test", "key1")
    assert result == {"hello": "world"}

def test_cache_miss(tmp_path):
    cache = DiskCache(tmp_path / "cache")
    assert cache.get("test", "nonexistent") is None

def test_cache_ttl_expired(tmp_path):
    cache = DiskCache(tmp_path / "cache")
    cache.put("test", "key1", "data", ttl=0.01)  # 10ms TTL
    time.sleep(0.02)
    assert cache.get("test", "key1") is None

def test_cache_infinite_ttl(tmp_path):
    cache = DiskCache(tmp_path / "cache")
    cache.put("test", "key1", "data", ttl=TTL_HISTORICAL)  # infinite
    assert cache.get("test", "key1") == "data"

def test_cache_invalidate(tmp_path):
    cache = DiskCache(tmp_path / "cache")
    cache.put("test", "key1", "data")
    assert cache.invalidate("test", "key1")
    assert cache.get("test", "key1") is None

def test_cache_clear_namespace(tmp_path):
    cache = DiskCache(tmp_path / "cache")
    cache.put("ns1", "a", 1)
    cache.put("ns1", "b", 2)
    cache.put("ns2", "c", 3)
    assert cache.clear_namespace("ns1") == 2
    assert cache.get("ns2", "c") == 3

def test_cache_stats(tmp_path):
    cache = DiskCache(tmp_path / "cache")
    cache.put("test", "k", "v")
    cache.get("test", "k")
    cache.get("test", "miss")
    s = cache.stats()
    assert s["writes"] == 1
    assert s["hits"] == 1
    assert s["misses"] == 1

def test_cache_inventory(tmp_path):
    cache = DiskCache(tmp_path / "cache")
    cache.put("fixtures", "a", 1)
    cache.put("fixtures", "b", 2)
    cache.put("odds", "c", 3)
    inv = cache.inventory()
    assert inv["fixtures"] == 2
    assert inv["odds"] == 1


# --------------------------------------------------------------------------
# Quota
# --------------------------------------------------------------------------

def test_quota_basic():
    qm = QuotaManager()
    qm.register("api-football", daily_limit=100, minute_limit=10)
    assert qm.can_request("api-football")
    qm.record_request("api-football")
    status = qm.status("api-football")
    assert status["requests_today"] == 1
    assert status["daily_remaining"] == 99

def test_quota_exhausted():
    qm = QuotaManager()
    qm.register("test", daily_limit=2)
    qm.record_request("test")
    qm.record_request("test")
    assert not qm.can_request("test")

def test_quota_unregistered_provider():
    qm = QuotaManager()
    assert qm.can_request("unknown")

def test_quota_error_tracking():
    qm = QuotaManager()
    qm.register("test", daily_limit=100)
    qm.record_error("test")
    assert qm.status("test")["errors_today"] == 1


# --------------------------------------------------------------------------
# Data Quality
# --------------------------------------------------------------------------

def test_field_comparison_consistent():
    fc = FieldComparison.compare("goals", {"api": 2, "csv": 2})
    assert fc.status == "CONSISTENT"
    assert fc.consensus == 2

def test_field_comparison_conflict():
    fc = FieldComparison.compare("goals", {"api": 2, "csv": 3})
    assert fc.status == "CONFLICT"
    assert fc.consensus is None

def test_field_comparison_single_source():
    fc = FieldComparison.compare("xg", {"api": 1.5})
    assert fc.status == "SINGLE_SOURCE"

def test_field_comparison_missing():
    fc = FieldComparison.compare("xg", {"api": None, "csv": None})
    assert fc.status == "MISSING"

def test_compare_match_data():
    data = {
        "api_football": {"goals_home": 2, "goals_away": 1, "corners": 10},
        "csv": {"goals_home": 2, "goals_away": 1},
    }
    report = compare_match_data("2026-01-01|Arsenal|Chelsea", data)
    assert not report.has_conflicts
    summary = report.summary()
    assert summary["consistent"] == 2  # goals_home and goals_away
    assert summary["single_source"] == 1  # corners

def test_compare_match_data_with_conflict():
    data = {
        "source_a": {"goals_home": 2},
        "source_b": {"goals_home": 3},
    }
    report = compare_match_data("key", data)
    assert report.has_conflicts
    assert "goals_home" in report.conflict_fields
