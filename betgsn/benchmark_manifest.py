"""BETGSN :: benchmark_manifest — referência oficial congelada do benchmark.

POR QUE ESTE MODULO EXISTE
--------------------------
O benchmark da Etapa 19 (490.736 linhas OOS, 24 janelas, embargo 2d)
vive em caches fingerprintados. Um número desses só é evidência se for
possível responder, a qualquer momento:

    - sobre qual corpus foi medido (assinatura + período + partidas);
    - com quais janelas/embargo/seed/bandas/parâmetros;
    - com qual versão de esquema e de código;
    - o cache AINDA é legível para essa configuração (reprodutibilidade)?

Este modulo NUNCA recalcula nada: lê os caches existentes, confere os
fingerprints contra o corpus atual e devolve o manifesto. Cache
ilegível/stale é DECLARADO, nunca servido como se fosse atual.

Nada aqui muda resultados históricos: o manifesto é fotografia, não
edição.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from betgsn import __version__

def _cache_status(payload: dict | None, expected: str | None) -> str:
    """Estado do cache: VALID / STALE / MISSING — nunca silencioso."""
    if payload is None:
        return "MISSING"
    if expected is None:
        return "STALE"
    return "VALID" if payload.get("cache_fingerprint") == expected else "STALE"


def build_benchmark_manifest(output_dir: Path | None = None) -> dict[str, Any]:
    """Fotografa a referência de benchmark SEM recalcular nada.

    Lê cada cache pela via VALIDADA (fingerprint conferido contra o
    corpus atual). O manifesto declara: corpus, período, janelas,
    embargo, seed, bandas, parâmetros, fingerprints, versões de esquema
    e de código — e o estado de reprodutibilidade de cada cache.
    """
    from betgsn.football_data_uk import FootballDataClient
    from betgsn.value_strategy import (
        ODD_BANDS,
        BOOTSTRAP_RESAMPLES,
        BOOTSTRAP_SEED,
        CACHE_SCHEMA_VERSION as VALUE_FULL_SCHEMA,
        MAX_ODD,
        MIN_BOOKS,
    )
    from betgsn.value_walkforward import (
        OOS_CACHE_SCHEMA_VERSION,
        WalkForwardConfig,
        cached_oos_evidence,
    )

    output_dir = output_dir or _default_output_dir()
    config = WalkForwardConfig()
    client = FootballDataClient()
    corpus_signature = client.corpus_signature()

    manifest: dict[str, Any] = {
        "kind": "benchmark_manifest",
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"),
        "code_version": __version__,
        "benchmark_reference": "ETAPA_19",
        "corpus": {
            "signature": corpus_signature,
            "source": "football-data.co.uk (CSVs locais)",
        },
        "protocol": {
            "train_days": config.train_days,
            "test_days": config.test_days,
            "gap_days_embargo": config.gap_days,
            "candidate_max_odds": list(config.candidate_max_odds),
            "min_train_bets": config.min_train_bets,
            "min_test_bets": config.min_test_bets,
            "bootstrap_resamples": config.bootstrap_resamples,
            "bootstrap_seed": config.bootstrap_seed,
            "bootstrap_resamples_full_sample": BOOTSTRAP_RESAMPLES,
            "bootstrap_seed_full_sample": BOOTSTRAP_SEED,
            "rule": {
                "max_odd": MAX_ODD,
                "min_books": MIN_BOOKS,
                "odd_bands": [[lo, hi] for lo, hi in ODD_BANDS],
            },
        },
        "schemas": {
            "value_oos": OOS_CACHE_SCHEMA_VERSION,
            "value_full_sample": VALUE_FULL_SCHEMA,
        },
        "caches": {},
    }

    # ---- corpus: período e contagem a partir do cache de partidas ----
    try:
        inventory = client.inventory()
        manifest["corpus"]["main_files"] = inventory.get("main_files")
        manifest["corpus"]["extra_files"] = inventory.get("extra_files")
        manifest["corpus"]["total_mb"] = inventory.get("total_mb")
    except Exception:  # noqa: BLE001 - inventario e diagnostico, nao evidencia
        manifest["corpus"]["inventory_error"] = "inventario indisponivel"

    # ---- cache OOS da estrategia (leitura VALIDADA) ----
    oos = cached_oos_evidence(config)
    manifest["caches"]["value_validation_oos.json"] = {
        "status": _cache_status(
            _read_cache(output_dir / "value_validation_oos.json"),
            _value_fingerprint(config, corpus_signature),
        ),
        "expected_fingerprint": _value_fingerprint(config, corpus_signature),
        "aggregate": _oos_aggregate(oos),
    }
    if oos is not None:
        manifest["windows"] = {
            "n_windows": oos.n_windows,
            "n_windows_valid": oos.n_windows_valid,
            "embargo_days": oos.embargo_days,
            "n_bets_oos": oos.n_bets_oos,
        }

    # ---- cache OOS de modelo (payload bruto so se legivel) ----
    model_payload = _read_cache(output_dir / "model_validation_oos.json")
    model_expected = _model_fingerprint(config, corpus_signature)
    model_status = _cache_status(model_payload, model_expected)
    manifest["caches"]["model_validation_oos.json"] = {
        "status": model_status,
        "expected_fingerprint": model_expected,
    }
    if model_status == "VALID" and model_payload:
        manifest["caches"]["model_validation_oos.json"]["model"] = (
            model_payload.get("model"))
        comparison = model_payload.get("comparison") or {}
        manifest["caches"]["model_validation_oos.json"]["aggregate"] = {
            k: comparison.get(k) for k in (
                "n_bets_oos", "n_windows_valid", "n_windows",
                "market_raw", "market_fair", "model_raw", "model_calibrated",
            )
        }

    # ---- cache full-sample da regra (leitura VALIDADA) ----
    from betgsn.value_strategy import cached_validation

    full = cached_validation()
    full_payload = _read_cache(output_dir / "value_validation.json")
    manifest["caches"]["value_validation.json"] = {
        "status": (
            "VALID" if full is not None
            else _cache_status(full_payload, None)
        ),
        "expected_fingerprint": _full_fingerprint(corpus_signature),
        "n_bets": full.n_bets if full is not None else None,
        "roi": full.roi if full is not None else None,
    }

    manifest["reproducibility"] = {
        "all_valid": all(
            entry["status"] == "VALID"
            for entry in manifest["caches"].values()
        ),
        "note": (
            "VALID = cache legivel para a configuracao atual sobre o corpus "
            "atual (fingerprint confere). STALE/MISSING = a medicao precisa "
            "ser reexecutada pela tool offline correspondente; o numero "
            "anterior NAO e servido como atual."
        ),
    }
    return manifest


def _default_output_dir() -> Path:
    from betgsn.config import output_root

    return output_root()


def _read_cache(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _value_fingerprint(config: WalkForwardConfig, corpus: str) -> str:
    from betgsn.value_walkforward import oos_cache_fingerprint

    return oos_cache_fingerprint(config=config, corpus_signature=corpus)


def _model_fingerprint(config: WalkForwardConfig, corpus: str) -> str:
    from betgsn.model_walkforward import model_cache_fingerprint

    return model_cache_fingerprint(config=config, corpus_signature=corpus)


def _full_fingerprint(corpus: str) -> str:
    from betgsn.value_strategy import (
        MAX_ODD,
        MIN_BOOKS,
        validation_cache_fingerprint,
    )

    return validation_cache_fingerprint(
        max_odd=MAX_ODD,
        min_books=MIN_BOOKS,
        corpus_signature=corpus,
    )


def _oos_aggregate(oos) -> dict | None:
    if oos is None:
        return None
    agg = dict(oos.aggregate)
    agg.pop("oos_rule_bets", None)
    agg.pop("oos_market_bets", None)
    return agg
