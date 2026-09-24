"""BETGSN :: api.quant_service — observabilidade quantitativa (leitura).

Endpoints sob /api/quant expõem o que o sistema QUANTITATIVO já provou,
pelas vias VALIDADAS (fingerprint) ou pelos artefatos de auditoria:

    GET /api/quant/benchmarks       manifest da referência (Etapa 19)
    GET /api/quant/model-vs-market  modelo vs mercado (cache validado)
    GET /api/quant/line-shopping    auditoria do line-shopping
    GET /api/quant/clv/status       ciclo de vida do CLV prospectivo
    GET /api/quant/ml               modelos experimentais (24 janelas)

REGRAS:
    - nada é recalculado aqui (corpus de 195 mil partidas não roda em
      request HTTP);
    - cache/artefato ausente ou stale é DECLARADO no corpo da resposta
      ("MISSING"/"STALE"), nunca servido como atual;
    - nenhum endpoint decide ou promove: observabilidade, não decisão.
    - production_eligible continua vindo do promotion gate, intocado.
"""

from __future__ import annotations

from typing import Any

from ..benchmark_manifest import build_benchmark_manifest


def benchmarks() -> dict[str, Any]:
    """Manifest da referência oficial (Etapa 19): corpus, protocolo,
    janelas, fingerprints e estado de reprodutibilidade de cada cache."""
    return build_benchmark_manifest()


def model_vs_market() -> dict[str, Any]:
    """Comparação modelo vs mercado (BASELINE_V1 nas 24 janelas).

    Cache validado por fingerprint: ausente/stale -> status explícito,
    nunca números de outra medição.
    """
    # import por módulo (não do nome): a leitura passa pelo atributo no
    # instante da chamada — nada de referência congelada em import.
    from .. import model_walkforward as mw

    payload = mw.cached_model_evidence()
    if payload is None:
        return {
            "status": "NO_VALID_CACHE",
            "detail": (
                "cache de modelo ausente ou stale para o corpus/protocolo "
                "atuais. Rode `python tools/model_validation.py` offline — "
                "nunca recalculado num request."
            ),
        }
    comparison = payload.get("comparison") or {}
    out: dict[str, Any] = {
        "status": "OK",
        "model": payload.get("model"),
        "generated_at": payload.get("generated_at"),
        "cache_fingerprint": payload.get("cache_fingerprint"),
        "n_bets_oos": comparison.get("n_bets_oos"),
        "n_windows_valid": comparison.get("n_windows_valid"),
        "n_windows": comparison.get("n_windows"),
        "market_raw": comparison.get("market_raw"),
        "market_fair": comparison.get("market_fair"),
        "model_raw": comparison.get("model_raw"),
        "model_calibrated": comparison.get("model_calibrated"),
        "paired_model_vs_raw": comparison.get("paired_model_vs_raw"),
        "paired_model_vs_fair": comparison.get("paired_model_vs_fair"),
        "drift": comparison.get("drift"),
        "strategy_model": comparison.get("strategy_model"),
        "note": (
            "model_* = BASELINE_V1 (ratings congelados por janela); "
            "market_* = benchmark de mercado; strategy_model inclui "
            "line-shopping — performance da ESTRATÉGIA, não do modelo."
        ),
    }
    return out


def _read_artifact(name: str) -> dict[str, Any] | None:
    import json

    from ..config import output_root

    path = output_root() / "engineering" / "quant" / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def line_shopping() -> dict[str, Any]:
    """Auditoria do line-shopping (artefato da tool offline)."""
    payload = _read_artifact("line_shopping_audit.json")
    if payload is None:
        return {
            "status": "MISSING",
            "detail": (
                "auditoria ainda não executada. Rode "
                "`python tools/line_shopping_audit.py [--with-model]`."
            ),
        }
    audit = payload.get("audit") or {}
    return {
        "status": "OK",
        "kind": payload.get("kind"),
        "n_lines_total": audit.get("n_lines_total"),
        "n_lines_rule": audit.get("n_lines_rule"),
        "rule_population_by_price": audit.get("rule_population_by_price"),
        "ablation_semantics": audit.get("ablation_semantics"),
        "price_uplift": audit.get("price_uplift"),
        "by_window": audit.get("by_window"),
        "by_band": audit.get("by_band"),
        "by_n_books": audit.get("by_n_books"),
        "answers": audit.get("answers"),
        "limitations": audit.get("limitations"),
        "strategy_model_decomposition": payload.get(
            "strategy_model_decomposition"),
    }


def ml_models() -> dict[str, Any]:
    """Modelos experimentais nas 24 janelas (artefato da tool offline)."""
    payload = _read_artifact("ml_oos_validation.json")
    if payload is None:
        return {
            "status": "MISSING",
            "detail": (
                "validação ML ainda não executada. Rode "
                "`python tools/ml_oos_validation.py`."
            ),
        }
    return {
        "status": "OK",
        "protocol": payload.get("protocol"),
        "models": payload.get("models"),
        "ensemble": payload.get("ensemble"),
        "declarations": payload.get("declarations"),
    }


def clv_status() -> dict[str, Any]:
    """Ciclo de vida do CLV prospectivo (store operacional, leitura).

    Observabilidade completa do pipeline CLV: lifecycle, estatísticas
    nulas-seguras (n=0 => mean/median None), taxa de fechamento,
    resolução de identidade, última captura e providers com problema —
    tudo observado, nada recalculado ou fabricado.
    """
    from .. import value_walkforward as vwf
    from ..models.promotion import MIN_CLV_SAMPLE
    from ..odds_snapshots import (
        OddsSnapshotStore,
        clv_statistics,
        sweep_close_rate,
        sweep_resolve_rate,
    )

    store = OddsSnapshotStore()
    sweep = store.clv_lifecycle_sweep()
    evidence = vwf.prospective_clv_evidence(store)
    oos = vwf.cached_oos_evidence()
    n_clv = int(evidence.get("n", 0))
    blocked = n_clv < MIN_CLV_SAMPLE

    stats = store.stats()
    health = store.load_provider_health()
    provider_health = {
        name: {
            "state": record.get("state", ""),
            "consecutive_failures": record.get("consecutive_failures", 0),
            "last_success_at": record.get("last_success_at", ""),
            "last_error": record.get("last_error", ""),
        }
        for name, record in sorted(health.items())
    }
    provider_issues = sorted(
        name for name, record in health.items()
        if record.get("state", "") != "HEALTHY"
    )

    return {
        "status": "OK",
        "lifecycle": sweep.by_state,
        "n_entries": sweep.n_entries,
        "clv_prospective": evidence,
        "clv_statistics": clv_statistics(sweep),
        # fechamentos encontrados entre entradas elegíveis (kickoff já
        # passado); None quando nada foi medido
        "close_rate": sweep_close_rate(sweep),
        # entradas que resolvem a observações do store (identidade casada)
        "resolve_rate": sweep_resolve_rate(sweep),
        "capture": {
            "last_observation_timestamp": stats.get("last_timestamp") or None,
            "n_observations": stats.get("observations", 0),
            "n_matches": stats.get("matches", 0),
            "providers": stats.get("providers", {}),
        },
        "provider_health": provider_health,
        "provider_issues": provider_issues,
        "promotion_gate_note": (
            f"o gate consome CLV prospectivo com n >= {MIN_CLV_SAMPLE} "
            f"(MIN_CLV_SAMPLE); atualmente n={n_clv} — "
            + ("promoção segue bloqueada por amostra insuficiente."
               if blocked
               else "amostra suficiente para o critério de CLV; a decisão "
                    "continua sendo do promotion gate, nunca automática.")
        ),
        "oos_windows_valid": oos.n_windows_valid if oos else 0,
        "lifecycle_note": (
            "PENDING = kickoff no futuro; NO_CLOSE = kickoff passou sem "
            "fechamento válido; CLOSED = CLV calculado; INVALID = dado "
            "inconsistente; MISMATCH = entrada sem observação "
            "correspondente. Ausência de fechamento nunca vira CLV=0."
        ),
        "evaluated_at": sweep.evaluated_at,
    }
