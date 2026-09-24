"""Manifest do benchmark (referência Etapa 19) — fotografia, não edição.

O que esta suíte prova:

  - o manifesto declara corpus, período de protocolo, janelas, embargo,
    seed, bandas, fingerprints, versões de esquema e de código;
  - o estado de cada cache é explícito: VALID / STALE / MISSING;
  - o manifesto NUNCA recalcula (collect_bets não pode ser chamado);
  - resultados históricos não são sobrescritos — o manifesto só lê.
"""

from __future__ import annotations

import json

import pytest

from betgsn import benchmark_manifest as bm
from betgsn.benchmark_manifest import build_benchmark_manifest

CORPUS = "test-corpus-123"


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    """Isola corpus + caminhos de cache em tmp."""
    from betgsn import football_data_uk as fduk
    from betgsn import value_strategy as vs
    from betgsn import value_walkforward as vwf

    monkeypatch.setattr(
        fduk.FootballDataClient, "corpus_signature", lambda self: CORPUS)
    monkeypatch.setattr(
        fduk.FootballDataClient, "inventory",
        lambda self: {"main_files": 10, "extra_files": 2, "total_mb": 99})
    monkeypatch.setattr(
        vwf, "_oos_cache_path", lambda: tmp_path / "value_validation_oos.json")
    monkeypatch.setattr(
        vs, "_validation_cache_path",
        lambda: tmp_path / "value_validation.json")
    return tmp_path


def test_manifest_declares_protocol_and_corpus(isolated):
    manifest = build_benchmark_manifest(output_dir=isolated)
    assert manifest["kind"] == "benchmark_manifest"
    assert manifest["benchmark_reference"] == "ETAPA_19"
    assert manifest["corpus"]["signature"] == CORPUS
    assert manifest["corpus"]["main_files"] == 10
    proto = manifest["protocol"]
    assert proto["train_days"] == 730
    assert proto["test_days"] == 365
    assert proto["gap_days_embargo"] == 2
    assert proto["bootstrap_seed"] == 424242
    assert proto["rule"]["max_odd"] == 1.30
    assert [1.0, 1.15] in proto["rule"]["odd_bands"]
    assert manifest["code_version"]
    assert manifest["schemas"]["value_oos"]


def test_manifest_never_recalculates(isolated, monkeypatch):
    """Fotografar não é medir: collect_bets não pode rodar."""
    from betgsn import value_walkforward as vwf

    def _forbidden(*a, **k):  # pragma: no cover - sempre falha se chamado
        raise AssertionError("manifest nao pode recalcular o corpus")

    monkeypatch.setattr(vwf, "collect_bets", _forbidden)
    build_benchmark_manifest(output_dir=isolated)  # não pode explodir


def test_manifest_missing_caches_are_declared(isolated):
    manifest = build_benchmark_manifest(output_dir=isolated)
    statuses = {name: entry["status"] for name, entry
                in manifest["caches"].items()}
    assert set(statuses.values()) == {"MISSING"}
    assert manifest["reproducibility"]["all_valid"] is False


def test_manifest_stale_cache_is_declared(isolated):
    """Cache com fingerprint divergente é STALE — nunca servido."""
    payload = {
        "schema": 1, "cache_fingerprint": "outro-fingerprint",
        "aggregate": {"roi": 0.5},
    }
    (isolated / "value_validation_oos.json").write_text(
        json.dumps(payload), encoding="utf-8")
    manifest = build_benchmark_manifest(output_dir=isolated)
    assert manifest["caches"]["value_validation_oos.json"]["status"] == "STALE"


def _valid_oos_payload(corpus: str) -> dict:
    from betgsn.value_walkforward import (
        OOS_CACHE_SCHEMA_VERSION,
        WalkForwardConfig,
        oos_cache_fingerprint,
    )

    return {
        "schema": OOS_CACHE_SCHEMA_VERSION,
        "cache_fingerprint": oos_cache_fingerprint(
            config=WalkForwardConfig(), corpus_signature=corpus),
        "aggregate": {
            "n_windows": 24, "n_windows_valid": 24, "n_bets_oos": 100,
            "roi": 0.01, "roi_se": 0.005, "avg_odd": 1.21,
            "max_drawdown": 0.1, "embargo_days": 2,
            "oos_rule_bets": [], "oos_market_bets": [],
        },
        "oos_rule_bets": [], "oos_market_bets": [],
        "calibration_windows": [], "calibration_insufficient": 0,
    }


def _valid_model_payload(corpus: str) -> dict:
    from betgsn.model_walkforward import (
        MODEL_CACHE_SCHEMA_VERSION,
        model_cache_fingerprint,
    )
    from betgsn.value_walkforward import WalkForwardConfig

    return {
        "schema": MODEL_CACHE_SCHEMA_VERSION,
        "cache_fingerprint": model_cache_fingerprint(
            config=WalkForwardConfig(), corpus_signature=corpus),
        "model": "BASELINE_V1 (Poisson bivariado + Dixon-Coles, fit_ratings)",
        "comparison": {
            "n_bets_oos": 100, "n_windows_valid": 24, "n_windows": 24,
            "market_raw": {"brier": 0.2}, "market_fair": {"brier": 0.2},
            "model_raw": {"brier": 0.21}, "model_calibrated": {"brier": 0.21},
        },
    }


def _valid_full_payload(corpus: str) -> dict:
    from betgsn.value_strategy import (
        BOOTSTRAP_RESAMPLES,
        BOOTSTRAP_SEED,
        MAX_ODD,
        MIN_BOOKS,
        ODD_BANDS,
        validation_cache_fingerprint,
    )

    return {
        "max_odd": MAX_ODD, "min_books": MIN_BOOKS, "n_bets": 7000,
        "roi": 0.016, "se": 0.005, "t": 3.0, "ci_low": 0.005,
        "ci_high": 0.027, "avg_odd": 1.21, "positive_years": 20,
        "total_years": 27, "positive_leagues": 10, "total_leagues": 15,
        "by_year": {}, "by_league": {}, "by_band": {}, "band_counts": {},
        "generated_at": "2026-09-23 00:00:00",
        "cache_fingerprint": validation_cache_fingerprint(
            max_odd=MAX_ODD, min_books=MIN_BOOKS, corpus_signature=corpus,
            bootstrap_resamples=BOOTSTRAP_RESAMPLES,
            bootstrap_seed=BOOTSTRAP_SEED, odd_bands=ODD_BANDS,
        ),
    }


def test_manifest_valid_caches_report_windows(isolated):
    (isolated / "value_validation_oos.json").write_text(
        json.dumps(_valid_oos_payload(CORPUS)), encoding="utf-8")
    (isolated / "model_validation_oos.json").write_text(
        json.dumps(_valid_model_payload(CORPUS)), encoding="utf-8")
    (isolated / "value_validation.json").write_text(
        json.dumps(_valid_full_payload(CORPUS)), encoding="utf-8")

    manifest = build_benchmark_manifest(output_dir=isolated)
    statuses = {name: entry["status"] for name, entry
                in manifest["caches"].items()}
    assert set(statuses.values()) == {"VALID"}
    assert manifest["reproducibility"]["all_valid"] is True
    assert manifest["windows"]["n_windows_valid"] == 24
    assert manifest["windows"]["embargo_days"] == 2
    model_entry = manifest["caches"]["model_validation_oos.json"]
    assert model_entry["model"].startswith("BASELINE_V1")
    assert model_entry["aggregate"]["n_bets_oos"] == 100
    # histórico não sobrescrito: o aggregate é leitura, não recomputo
    assert manifest["caches"]["value_validation_oos.json"]["aggregate"][
        "n_bets_oos"] == 100


def test_manifest_stale_when_corpus_changes(isolated):
    """Mesmo cache, corpus novo: STALE — a referência declara a ruptura."""
    (isolated / "value_validation_oos.json").write_text(
        json.dumps(_valid_oos_payload(CORPUS)), encoding="utf-8")
    manifest_a = build_benchmark_manifest(output_dir=isolated)
    assert manifest_a["caches"]["value_validation_oos.json"][
        "status"] == "VALID"

    from betgsn import football_data_uk as fduk

    isolated_env = fduk.FootballDataClient
    # corpus mudou (CSV novo chegou)
    import betgsn.benchmark_manifest as bm_mod

    original = bm_mod.build_benchmark_manifest

    def _with_new_corpus():
        import unittest.mock as mock

        with mock.patch.object(
                isolated_env, "corpus_signature",
                lambda self: "corpus-NOVO", create=True):
            return original(output_dir=isolated)

    manifest_b = _with_new_corpus()
    assert manifest_b["corpus"]["signature"] == "corpus-NOVO"
    assert manifest_b["caches"]["value_validation_oos.json"][
        "status"] == "STALE"
    assert manifest_b["reproducibility"]["all_valid"] is False
