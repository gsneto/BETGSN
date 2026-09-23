"""Validação OOS de MODELO vs MERCADO — execução real sobre o corpus.

Avalia o modelo de PRODUÇÃO (BASELINE_V1: Poisson bivariado + Dixon-Coles
via fit_ratings) nas MESMAS 24 janelas walk-forward da validação da
estratégia, comparando:

    MARKET RAW    p = 1/odd (melhor odd entre casas)
    MARKET FAIR   p = de-vig multiplicativo do consenso de medianas
    MODEL RAW     p = modelo congelado (ratings fit no TRAIN)
    MODEL CAL     calibrador escolhido no TRAIN, congelado no TEST

E mede, separado: STRATEGY (EV>0 com prob do modelo; inclui
line-shopping), robustez por faixa de odd, ablação de componentes e
drift por janela. ML models (Elo/XGBoost/LightGBM/Ensemble) têm
avaliação própria nos benchmarks (janelas declaradas lá) — este
relatório os referencia sem misturar.

Artefatos gerados (mesma estrutura, sem formatos concorrentes):
    model_validation_oos.json     (tudo)
    model_comparison_oos.json     (fontes + pareado)
    calibration_report.json       (método por janela)
    robustness_report.json        (faixas de odd)
    ablation_report.json          (componentes)
    strategy_validation_oos.json  (strategy_model + strategy_market)

Uso:
    python tools/model_validation.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from betgsn.model_walkforward import (  # noqa: E402
    compute_model_validation,
    save_model_validation,
)
from betgsn.value_walkforward import WalkForwardConfig  # noqa: E402


def _progress(done: int, total: int, message: str = "") -> None:
    if message:
        print(f"  [{done}/{total}] {message}", flush=True)
    else:
        print(f"  ... {done}/{total}", flush=True)


def _fmt(value, spec: str = ".4f") -> str:
    return "n/d" if value is None else format(value, spec)


def main() -> int:
    config = WalkForwardConfig()
    print("Validação OOS de modelo vs mercado (corpus real, sem rede)")
    print(f"  janelas: train={config.train_days}d test={config.test_days}d "
          f"gap/embargo={config.gap_days}d (MESMAS da estratégia)")
    print("  modelo: BASELINE_V1 (Poisson bivariado + Dixon-Coles)")

    payload = compute_model_validation(config, progress=_progress)
    path = save_model_validation(payload)
    print(f"\ncache gravado: {path}")

    comparison = payload["comparison"]
    print("\n=== AGREGADO OOS (mesma população de linhas) ===")
    print(f"  janelas válidas: {comparison['n_windows_valid']}/"
          f"{comparison['n_windows']}")
    print(f"  linhas avaliadas: {comparison['n_bets_oos']}")
    for source in ("market_raw", "market_fair", "model_raw",
                   "model_calibrated"):
        m = comparison[source]
        print(f"  {source:17s} Brier {_fmt(m['brier'])} | "
              f"LogLoss {_fmt(m['logloss'])} | ECE {_fmt(m['ece'])} | "
              f"n {m['n']}")

    for key, label in (
        ("paired_model_vs_raw", "MODELO vs MARKET RAW"),
        ("paired_model_vs_fair", "MODELO vs MARKET FAIR"),
    ):
        paired = comparison.get(key)
        print(f"\n=== PAREADO: {label} (block bootstrap por mês) ===")
        if paired is None:
            print("  INSUFFICIENT_DATA (blocos insuficientes)")
            continue
        print(f"  veredito: {paired.get('verdict')}")
        for k in ("delta_mean", "ci_low", "ci_high", "p_value_bootstrap"):
            if k in paired:
                print(f"  {k}: {paired[k]}")

    print("\n=== STRATEGY (separada do modelo) ===")
    sm = comparison.get("strategy_model") or {}
    if sm:
        print(f"  strategy_model (EV>0, INCLUI line-shopping): "
              f"n={sm['n_bets']} roi={sm['roi']:+.4%} "
              f"(se {sm['roi_se']:.4%}, t {sm['roi_t']}) "
              f"bootstrap [{sm['bootstrap_low']:+.4%}, "
              f"{sm['bootstrap_high']:+.4%}]")
    valid = [w for w in comparison["windows"] if w.get("strategy_market_n")]
    if valid:
        rois = [w["strategy_market_roi"] for w in valid
                if w.get("strategy_market_roi") is not None]
        if rois:
            print(f"  strategy_market (favoritos <1.30, mesmas janelas): "
                  f"roi médio {sum(rois) / len(rois):+.4%} em "
                  f"{len(rois)} janela(s)")

    print("\n=== DRIFT (por janela, histórico) ===")
    for source, info in (comparison.get("drift") or {}).items():
        if info.get("drift") == "INSUFFICIENT_DATA":
            print(f"  {source:17s} INSUFFICIENT_DATA")
        else:
            print(f"  {source:17s} 1o terço {info['logloss_first_third']:.4f} "
                  f"-> último {info['logloss_last_third']:.4f} "
                  f"(delta {info['delta']:+.4f}, {info['drift']})")

    print("\n=== ROBUSTEZ (por faixa de odd) ===")
    for s in payload["robustness"]:
        if s.get("status") != "OK":
            print(f"  {s['scenario']:24s} {s.get('status')}")
            continue
        delta = s["delta_logloss_menos_modelo"]
        delta_txt = f"{delta:+.4f}" if delta is not None else "n/d"
        print(f"  {s['scenario']:24s} n={s['n']:6d} "
              f"mkt {_fmt(s['market_logloss'])} vs mdl "
              f"{_fmt(s['model_logloss'])} (delta {delta_txt})")

    print("\n=== ABLAÇÃO (componentes isolados) ===")
    for row in payload["ablation"]:
        if "logloss" in row:
            print(f"  {row['componente']:48s} LogLoss "
                  f"{_fmt(row['logloss'])} (n={row.get('n')})")
        elif "roi" in row:
            print(f"  {row['componente']:48s} ROI "
                  f"{row['roi']:+.4%} (n={row.get('n_bets')})")

    # ---------------- artefatos separados (mesma estrutura) ----------------
    out_dir = ROOT / "output" / "engineering" / "quant"
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "model_comparison_oos.json": {
            "kind": "model_comparison_oos",
            "aggregate": {
                k: comparison[k] for k in
                ("market_raw", "market_fair", "model_raw", "model_calibrated",
                 "paired_model_vs_raw", "paired_model_vs_fair",
                 "n_bets_oos", "n_windows_valid", "n_windows")
            },
            "windows": comparison["windows"],
        },
        "calibration_report.json": {
            "kind": "calibration_report",
            "windows": [
                {"window": w["index"], "method": w["calibration_method"],
                 "n_test_bets": w["n_test_bets"],
                 "model_calibrated": w["model_calibrated"],
                 "model_raw": w["model_raw"]}
                for w in comparison["windows"]
            ],
        },
        "robustness_report.json": {
            "kind": "robustness_report",
            "scenarios": payload["robustness"],
        },
        "ablation_report.json": {
            "kind": "ablation_report",
            "components": payload["ablation"],
        },
        "strategy_validation_oos.json": {
            "kind": "strategy_validation_oos",
            "strategy_model": comparison.get("strategy_model") or {},
            "strategy_market_per_window": [
                {"window": w["index"], "n": w["strategy_market_n"],
                 "roi": w["strategy_market_roi"]}
                for w in comparison["windows"]
            ],
            "note": (
                "strategy_model usa prob do modelo calibrado + line-shopping "
                "(best odd) — performance da ESTRATÉGIA, não do modelo. "
                "strategy_market = regra de favoritos curtos vigente, "
                "medida nas mesmas janelas."
            ),
        },
    }
    for name, body in artifacts.items():
        (out_dir / name).write_text(
            json.dumps(body, indent=2, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
    print(f"\nartefatos: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
