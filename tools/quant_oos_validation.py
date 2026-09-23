"""Validação OOS real da estratégia de valor — gera o cache do promotion gate.

Executa sobre o CORPUS HISTÓRICO REAL (football-data.co.uk, ~195 mil
partidas), sem chamar nenhuma API externa:

    1. UMA passada de collect_bets (todas as linhas, todas as odds);
    2. walk-forward OOS (seleção de banda no TRAIN, medida no TEST,
       gap/embargo entre blocos);
    3. robustez (variações de max_odd/min_books, OOS);
    4. ablação de componentes (line-shopping, consenso mínimo, OOS);
    5. promotion gate da estratégia com: segmentos OOS, n_windows,
       CLV prospectivo REAL do store, drawdown OOS;
    6. grava o cache fingerprintado (value_validation_oos.json) que o
       caminho de decisão lê com validação de fingerprint.

O relatório final separa HISTÓRICO / OOS / PROSPECTIVO / PRODUÇÃO e o
veredito pode perfeitamente ser NO BET — resultado negativo é resultado.

Uso:
    python tools/quant_oos_validation.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from betgsn.models.promotion import ModelStatus  # noqa: E402
from betgsn.strategy_runner import evaluate_strategy_promotion  # noqa: E402
from betgsn.value_strategy import STRATEGY_NAME  # noqa: E402
from betgsn.value_walkforward import (  # noqa: E402
    WalkForwardConfig,
    cached_oos_evidence,
    compute_oos_validation,
    prospective_clv_evidence,
    save_oos_validation,
)


def _progress(done: int, total: int, message: str = "") -> None:
    if message:
        print(f"  [{done}/{total}] {message}", flush=True)
    else:
        print(f"  ... {done}/{total} partidas", flush=True)


def _previous_aggregate() -> dict | None:
    """Agregado do cache ANTERIOR (ANTES), se legível — para comparação.

    Cache de schema antigo ou corrompido devolve None: o 'antes' só
    existe como histórico quando é legível.
    """
    import json

    from betgsn.value_walkforward import _oos_cache_path

    try:
        payload = json.loads(_oos_cache_path().read_text(encoding="utf-8"))
        return payload.get("aggregate") or None
    except (OSError, json.JSONDecodeError):
        return None


def main() -> int:
    config = WalkForwardConfig()
    print("Validação OOS da estratégia de valor (corpus real, sem rede)")
    print(f"  janelas: train={config.train_days}d test={config.test_days}d "
          f"gap/embargo={config.gap_days}d")
    print(f"  bandas candidatas: {config.candidate_max_odds}")

    previous = _previous_aggregate()
    payload = compute_oos_validation(config, progress=_progress)
    path = save_oos_validation(payload)
    print(f"\ncache OOS gravado: {path}")

    agg = payload["aggregate"]
    print("\n=== AGREGADO OOS ===")
    print(f"  janelas: {agg['n_windows_valid']}/{agg['n_windows']} válidas")
    print(f"  apostas OOS: {agg['n_bets_oos']}")
    print(f"  ROI: {agg['roi']:+.4%} (se {agg['roi_se']:.4%}, "
          f"t {agg['roi_t']})")
    print(f"  Wilson (win rate): [{agg['wilson_low']:.4f}, "
          f"{agg['wilson_high']:.4f}]")
    print(f"  bootstrap 95% ROI: [{agg['bootstrap_low']:+.4%}, "
          f"{agg['bootstrap_high']:+.4%}]")
    # ORIGEM DAS PROBABILIDADES: market_* = baseline de mercado (p=1/odd,
    # sem modelo); calibrated_* = calibrador por janela, frozen no TEST.
    print(f"  MARKET baseline: Brier {agg['market_brier']:.4f} | "
          f"LogLoss {agg['market_logloss']:.4f} | ECE {agg['market_ece']:.4f}")
    print(f"  CALIBRADO (frozen): Brier {agg['calibrated_brier']:.4f} | "
          f"LogLoss {agg['calibrated_logloss']:.4f} | "
          f"ECE {agg['calibrated_ece']:.4f}")
    print(f"  drawdown máximo (stake 1%): {agg['max_drawdown']:.2%}")
    print(f"  embargo aplicado: {agg['embargo_days']}d")

    if previous:
        print("\n=== COMPARAÇÃO (ANTES -> DEPOIS) ===")
        for key, fmt in (
            ("roi", "{:+.4%}"), ("n_bets_oos", "{}"),
            ("market_ece", "{:.4f}"), ("calibrated_ece", "{:.4f}"),
            ("market_brier", "{:.4f}"), ("calibrated_brier", "{:.4f}"),
        ):
            old, new = previous.get(key), agg.get(key)
            if old is not None and new is not None:
                print(f"  {key:18s} {fmt.format(old)} -> {fmt.format(new)}")
            elif new is not None:
                print(f"  {key:18s} n/d (campo novo) -> {fmt.format(new)}")

    print("\n=== CALIBRAÇÃO POR JANELA ===")
    for w in payload["calibration_windows"]:
        print(f"  w{w['window']:02d} método={w['method']:8s} n={w['n_test']:4d} "
              f"ECE calibrado {w['calibrated_ece']:.4f} "
              f"(mercado {w['market_ece']:.4f})")
    if payload["calibration_insufficient"]:
        print(f"  {payload['calibration_insufficient']} janela(s) "
              f"INSUFFICIENT_DATA (amostra < MIN_ECE_SAMPLE) — excluídas")

    print("\n=== POR JANELA ===")
    for w in payload["windows"]:
        band = w["selected_max_odd"]
        roi = f"{w['roi']:+.4%}" if w["roi"] is not None else "n/d"
        print(f"  w{w['index']:02d} train[{w['train_start']}..{w['train_end']}] "
              f"test[{w['test_start']}..{w['test_end']}] banda={band} "
              f"n_test={w['n_test_bets']:4d} roi={roi} calib={w['calibration_method']}")

    print("\n=== ROBUSTEZ (OOS) ===")
    for s in payload["robustness"]:
        deg = s["degradation_vs_baseline"]
        deg_txt = f"{deg:+.4%}" if deg is not None else "n/d"
        cal = s.get("calibrated_ece")
        cal_txt = f"{cal:.4f}" if cal is not None else "n/d"
        print(f"  {s['scenario']:16s} n={s['n_bets_oos']:5d} "
              f"roi={s['roi']:+.4%} ECEcal={cal_txt} degradação={deg_txt}")

    print("\n=== ABLAÇÃO (OOS) ===")
    for s in payload["ablation"]:
        delta = s["delta_vs_baseline"]
        delta_txt = f"{delta:+.4%}" if delta is not None else "n/d"
        print(f"  {s['scenario']:52s} n={s['n_bets_oos']:5d} "
              f"roi={s['roi']:+.4%} delta={delta_txt}")

    # ---------------- promotion gate com evidência REAL ----------------
    evidence = cached_oos_evidence(config)
    assert evidence is not None, "cache recém-gravado precisa ler"
    clv = prospective_clv_evidence()
    print("\n=== CLV PROSPECTIVO (store operacional) ===")
    print(f"  n={clv['n']} (mínimo do gate: 30) | mean={clv['mean']:+.4%} | "
          f"prospective={clv['prospective']}")

    decision = evaluate_strategy_promotion(
        STRATEGY_NAME,
        segments=evidence.promotion_segments(),
        current_status=ModelStatus.EXPERIMENTAL,
        n_windows=evidence.n_windows_valid,
        clv=clv,
        max_drawdown=evidence.max_drawdown,
        calibration=evidence.calibration_channel(),
    )
    print("\n=== PROMOTION GATE ===")
    print(decision.summary())

    report = {
        "kind": "quant_oos_validation",
        "strategy": STRATEGY_NAME,
        "aggregate": agg,
        "calibration_windows": payload["calibration_windows"],
        "calibration_insufficient": payload["calibration_insufficient"],
        "previous_aggregate": previous,
        "robustness": payload["robustness"],
        "ablation": payload["ablation"],
        "clv_prospective": clv,
        "promotion": decision.to_dict(),
        "sections": {
            # SEPARAÇÃO EXPLÍCITA (ETAPA 1): nunca misturar origens
            "modelo": (
                "NÃO AVALIADO nesta validação: a estratégia de valor usa "
                "o MERCADO como fonte de probabilidade (p=1/odd), sem "
                "modelo BETGSN. Performance de modelo vive nos relatórios "
                "de benchmark (output/engineering/benchmark), separada."
            ),
            "mercado": {
                "baseline": "probabilidades implícitas p=1/odd (MARKET)",
                "market_brier": agg["market_brier"],
                "market_logloss": agg["market_logloss"],
                "market_ece": agg["market_ece"],
            },
            "estrategia": {
                "calibrado_por_janela": {
                    "metodo_por_janela": sorted({
                        w["method"] for w in payload["calibration_windows"]
                    }),
                    "calibrated_brier": agg["calibrated_brier"],
                    "calibrated_logloss": agg["calibrated_logloss"],
                    "calibrated_ece": agg["calibrated_ece"],
                    "n_janelas_suficientes": len(payload["calibration_windows"]),
                    "n_janelas_insuficientes": payload["calibration_insufficient"],
                },
                "oos": agg,
            },
            "historico": (
                "validação full-sample em cache separado "
                "(value_validation.json) — medição histórica, não OOS"
            ),
            "oos": agg,
            "prospectivo": clv,
            "producao": {
                "production_eligible": decision.production_eligible,
                "evidence_status_max": (
                    "exploratory (odds FDUK sem timestamp de publicação)"
                ),
                "action": (
                    "NO_BET" if not decision.production_eligible else "REVIEW"
                ),
            },
        },
    }
    out = ROOT / "output" / "engineering" / "quant" / "quant_oos_validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    print(f"\nrelatório: {out}")
    print("\nVEREDITO:", "NO_BET" if not decision.production_eligible
          else "REVIEW (elegível — decisão humana)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
