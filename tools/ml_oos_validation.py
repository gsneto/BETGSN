"""Validação OOS dos modelos experimentais — MESMAS 24 janelas da Etapa 19.

Submete Elo, XGBoost e LightGBM ao MESMO protocolo do BASELINE_V1
(`run_model_walkforward`): train 730d -> embargo 2d -> test 365d, 24
janelas, treino apenas com partidas anteriores a train_end, avaliação
apenas no TEST, mesmas odd bands, mesmo benchmark de mercado.

Para cada modelo produz:
    - Brier / LogLoss / ECE (model_raw e model_calibrated);
    - comparação pareada vs MARKET_RAW e MARKET_FAIR (block bootstrap);
    - performance por janela e drift;
    - diferença de LogLoss contra MARKET_RAW e MARKET_FAIR.

PRINCIPALMENTE: MODEL vs MARKET — não MODEL vs zero.

    - NÃO gera ranking de modelos.
    - NÃO declara vencedor.
    - NÃO promove nada (o promotion gate segue intocado).
    - NÃO faz tuning: hiperparâmetros DEFAULT de models/*_model.py.
    - Ensemble: PENDENTE neste protocolo (exige stacking OOS dentro de
      cada janela; evidência dele vive em cross_season_gate.json).

Uso:
    python tools/ml_oos_validation.py [--models elo,xgboost,lightgbm]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from betgsn.ml_walkforward import (  # noqa: E402
    ML_MODEL_KINDS,
    MLWindowAdapter,
    build_feature_corpus,
)
from betgsn.model_walkforward import run_model_walkforward  # noqa: E402
from betgsn.value_strategy import collect_bets  # noqa: E402
from betgsn.value_walkforward import WalkForwardConfig  # noqa: E402


def _progress(done: int, total: int, message: str = "") -> None:
    if message:
        print(f"  [{done}/{total}] {message}", flush=True)
    else:
        print(f"  ... {done}/{total}", flush=True)


def _fmt(value) -> str:
    return "n/d" if value is None else f"{value:.4f}"


def _delta(a, b):
    if a is None or b is None:
        return None
    return round(a - b, 6)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models", default="elo,xgboost,lightgbm",
        help="modelos separados por vírgula (elo,xgboost,lightgbm)")
    args = parser.parse_args()
    kinds = [k.strip() for k in args.models.split(",") if k.strip()]
    for kind in kinds:
        if kind not in ML_MODEL_KINDS:
            print(f"modelo desconhecido: {kind!r} (use {ML_MODEL_KINDS})")
            return 2

    config = WalkForwardConfig()
    print("Validação OOS de modelos experimentais (corpus real, sem rede)")
    print(f"  janelas: train={config.train_days}d test={config.test_days}d "
          f"gap/embargo={config.gap_days}d (MESMAS da Etapa 19)")
    print(f"  modelos: {', '.join(kinds)}")
    print("  hiperparâmetros: DEFAULT (nenhum tuning OOS)")

    print("  coletando linhas apostáveis do corpus…")
    bets = collect_bets(99.0, 1, closing=False, progress=_progress)
    print(f"  {len(bets)} linhas apostáveis")

    from betgsn.football_data_uk import FootballDataClient

    print("  carregando corpus histórico…")
    matches = [m.to_historical() for m in FootballDataClient().load_matches()]
    print(f"  {len(matches)} partidas")

    print("  construindo corpus de features point-in-time…")
    corpus = build_feature_corpus(matches, progress=_progress)
    print(f"  features: {corpus.matrix.shape[1]} colunas")

    results = {}
    for kind in kinds:
        print(f"\n=== {kind.upper()} — modelo vs mercado (24 janelas) ===")
        adapter = MLWindowAdapter(kind, corpus)
        comparison = run_model_walkforward(
            bets, matches, config, _progress, model_fn=adapter.fit)

        m_raw = comparison.model_raw
        m_cal = comparison.model_calibrated
        mk_raw = comparison.market_raw
        mk_fair = comparison.market_fair

        print(f"  janelas válidas: {comparison.n_windows_valid}/"
              f"{comparison.n_windows} | linhas OOS: "
              f"{comparison.n_bets_oos}")
        print(f"  MARKET_RAW  Brier {_fmt(mk_raw.brier)} | "
              f"LogLoss {_fmt(mk_raw.logloss)} | ECE {_fmt(mk_raw.ece)}")
        if mk_fair.n:
            print(f"  MARKET_FAIR Brier {_fmt(mk_fair.brier)} | "
                  f"LogLoss {_fmt(mk_fair.logloss)} | ECE {_fmt(mk_fair.ece)}")
        print(f"  MODEL RAW   Brier {_fmt(m_raw.brier)} | "
              f"LogLoss {_fmt(m_raw.logloss)} | ECE {_fmt(m_raw.ece)}")
        print(f"  MODEL CAL   Brier {_fmt(m_cal.brier)} | "
              f"LogLoss {_fmt(m_cal.logloss)} | ECE {_fmt(m_cal.ece)}")

        d_raw = _delta(m_raw.logloss, mk_raw.logloss)
        d_fair = _delta(m_raw.logloss, mk_fair.logloss)
        # logloss MENOR é melhor: delta negativo = modelo pior que o mercado
        print(f"  delta LogLoss (modelo - market_raw):  "
              f"{'n/d' if d_raw is None else f'{d_raw:+.4f}'}"
              f"  ({'pior' if (d_raw or 0) > 0 else 'melhor'} que o mercado)")
        if mk_fair.n:
            print(f"  delta LogLoss (modelo - market_fair): "
                  f"{'n/d' if d_fair is None else f'{d_fair:+.4f}'}")

        for key, label in (
            ("paired_model_vs_raw", "PAREADO vs MARKET_RAW"),
            ("paired_model_vs_fair", "PAREADO vs MARKET_FAIR"),
        ):
            paired = comparison.to_dict().get(key)
            if paired:
                print(f"  {label}: {paired.get('verdict')} "
                      f"(delta {paired.get('delta_mean')})")

        results[kind] = {
            "n_windows": comparison.n_windows,
            "n_windows_valid": comparison.n_windows_valid,
            "n_bets_oos": comparison.n_bets_oos,
            "market_raw": mk_raw.__dict__,
            "market_fair": mk_fair.__dict__,
            "model_raw": m_raw.__dict__,
            "model_calibrated": m_cal.__dict__,
            "delta_logloss_vs_market_raw": d_raw,
            "delta_logloss_vs_market_fair": d_fair,
            "paired_model_vs_raw": comparison.paired_model_vs_raw,
            "paired_model_vs_fair": comparison.paired_model_vs_fair,
            "strategy_model": comparison.strategy_model,
            "drift": comparison.drift,
            "windows": [w.to_dict() for w in comparison.windows],
        }

    payload = {
        "kind": "ml_oos_validation",
        "protocol": {
            "train_days": config.train_days,
            "test_days": config.test_days,
            "gap_days_embargo": config.gap_days,
            "note": (
                "MESMAS 24 janelas walk-forward da Etapa 19 (mesmo "
                "WalkForwardConfig/embargo). Treino apenas com partidas "
                "anteriores a train_end; features point-in-time via "
                "FeatureBuilder; hiperparâmetros DEFAULT (sem tuning OOS)."
            ),
        },
        "models": results,
        "ensemble": {
            "status": "PENDENTE",
            "reason": (
                "Ensemble exige stacking OOS dentro de cada janela "
                "(previsões out-of-sample dos base models no TRAIN da "
                "própria janela). Evidência existente vive em "
                "cross_season_gate.json sob protocolo próprio — sem "
                "mistura com estas 24 janelas."
            ),
        },
        "declarations": [
            "sem ranking de modelos",
            "sem vencedor declarado",
            "nenhuma promoção automática (promotion gate intocado)",
            "benchmark central: MODEL vs MARKET (raw e fair), nunca "
            "model vs zero",
        ],
    }
    first = next(iter(results.values()), None)
    if first:
        payload["protocol"]["n_windows"] = first["n_windows"]
        payload["protocol"]["n_windows_valid"] = first["n_windows_valid"]

    out = ROOT / "output" / "engineering" / "quant" / "ml_oos_validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    print(f"\nrelatório: {out}")
    print("\nNenhuma conclusão de promoção é extraída daqui. "
          "O promotion gate permanece intocado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
