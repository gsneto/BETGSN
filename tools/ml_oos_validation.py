"""Validação OOS dos modelos experimentais — MESMAS 24 janelas da Etapa 19.

Submete Elo, XGBoost, LightGBM e o ENSEMBLE (stacking OOS por janela)
ao MESMO protocolo do BASELINE_V1 (`run_model_walkforward`): train 730d
-> embargo 2d -> test 365d, 24 janelas, treino apenas com partidas
anteriores a train_end, avaliação apenas no TEST, mesmas odd bands,
mesmo benchmark de mercado.

Para cada modelo produz:
    - Brier / LogLoss / ECE (model_raw e model_calibrated);
    - comparação pareada vs MARKET_RAW e MARKET_FAIR (block bootstrap);
    - performance por janela e drift;
    - diferença de LogLoss contra MARKET_RAW e MARKET_FAIR.

ENSEMBLE (stacking SEM leakage, por janela):
    - o TRAIN de cada janela é dividido em chunks temporais;
    - cada fold é previsto por bases ajustadas APENAS nos chunks
      anteriores (rolling origin) — previsões out-of-sample;
    - o meta-modelo é treinado SOMENTE nessas previsões OOS;
    - as bases finais (TRAIN completo, congeladas) preveem o TEST e o
      meta combina. Nada de bases treinadas no corpus inteiro.

PRINCIPALMENTE: MODEL vs MARKET — não MODEL vs zero.

    - NÃO gera ranking de modelos.
    - NÃO declara vencedor.
    - NÃO promove nada (o promotion gate segue intocado).
    - NÃO faz tuning: hiperparâmetros DEFAULT de models/*_model.py.

Uso:
    python tools/ml_oos_validation.py [--models elo,xgboost,lightgbm,ensemble]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from betgsn.ml_walkforward import (  # noqa: E402
    ENSEMBLE_BASE_KINDS,
    ENSEMBLE_FOLDS,
    ENSEMBLE_KIND,
    ML_MODEL_KINDS,
    EnsembleWindowAdapter,
    MLWindowAdapter,
    build_feature_corpus,
)
from betgsn.model_walkforward import run_model_walkforward  # noqa: E402
from betgsn.value_strategy import collect_bets  # noqa: E402
from betgsn.value_walkforward import WalkForwardConfig  # noqa: E402

#: Kinds aceitos: os modelos individuais + o ensemble (stacking).
VALIDATION_KINDS = ML_MODEL_KINDS + (ENSEMBLE_KIND,)


def _progress(done: int, total: int, message: str = "") -> None:
    if message:
        print(f"  [{done}/{total}] {message}", flush=True)
    else:
        print(f"  ... {done}/{total}", flush=True)


def _cached_feature_corpus(matches, corpus_signature: str):
    """Corpus de features com cache pickle (build de ~15min por corpus).

    O cache e chaveado pela assinatura do corpus: CSV novo = rebuild.
    Fica em output/ (artefato reproduzivel, nunca versionado)."""
    import pickle
    import re

    from betgsn import __version__
    from betgsn.config import output_root

    safe_sig = re.sub(r"[^A-Za-z0-9_.-]", "_", corpus_signature)
    path = (output_root() / "engineering" / "quant"
            / f"ml_feature_corpus_{safe_sig}_{__version__}.pkl")
    if path.exists():
        try:
            with path.open("rb") as fh:
                return pickle.load(fh)
        except Exception:  # noqa: BLE001 - cache corrompido = rebuild
            pass
    corpus = build_feature_corpus(matches, _progress)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        pickle.dump(corpus, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return corpus


def _fmt(value) -> str:
    return "n/d" if value is None else f"{value:.4f}"


def _delta(a, b):
    if a is None or b is None:
        return None
    return round(a - b, 6)


#: Bandas de odd para o breakdown do ensemble (mesmas faixas da
#: robustez de modelo em model_walkforward.model_robustness).
_ODDS_BANDS: list[tuple[str, float, float]] = [
    ("< 1.40", 0.0, 1.40), ("1.40-2.00", 1.40, 2.00),
    ("2.00-3.00", 2.00, 3.00), (">= 3.00", 3.00, 99.0),
]

#: Amostra mínima por banda para reportar métricas (abaixo disso a
#: banda é INSUFFICIENT_DATA — declarado, nunca número frágil).
_MIN_BAND_ROWS = 200


def odds_band_breakdown(rows: list[tuple]) -> list[dict]:
    """Breakdown por odd band sobre `prediction_rows` do harness.

    `rows`: (window, odd, p_raw, p_fair, p_model, p_cal, y) — a
    população EXATA da avaliação OOS. Métricas idênticas às do harness
    (mesma `_metrics_from_pairs`), banda por banda, sem re-rodar
    janelas. Sem amostra suficiente: INSUFFICIENT_DATA com o n
    declarado — nada fabricado.
    """
    from betgsn.value_walkforward import _metrics_from_pairs

    out: list[dict] = []
    for label, lo, hi in _ODDS_BANDS:
        sel = [row for row in rows if lo <= row[1] < hi]
        if len(sel) < _MIN_BAND_ROWS:
            out.append({
                "band": label, "n": len(sel),
                "status": "INSUFFICIENT_DATA",
            })
            continue

        def _source(idx: int) -> dict:
            pairs = [
                (row[idx], row[6]) for row in sel
                if row[idx] is not None
            ]
            ps = [p for p, _y in pairs]
            ys = [y for _p, y in pairs]
            m = _metrics_from_pairs(ps, ys)
            return {
                "brier": m["brier"], "logloss": m["logloss"],
                "ece": m["ece"], "n": m["n_prob"],
            }

        entry: dict = {
            "band": label, "n": len(sel), "status": "OK",
            "model_raw": _source(4),
            "model_calibrated": _source(5),
            "market_raw": _source(2),
            "market_fair": _source(3),
        }
        model_ll = entry["model_raw"]["logloss"]
        market_ll = entry["market_raw"]["logloss"]
        fair_ll = entry["market_fair"]["logloss"]
        entry["delta_logloss_model_vs_market_raw"] = (
            None if model_ll is None or market_ll is None
            else round(model_ll - market_ll, 6))
        entry["delta_logloss_model_vs_market_fair"] = (
            None if model_ll is None or fair_ll is None
            else round(model_ll - fair_ll, 6))
        out.append(entry)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models", default="elo,xgboost,lightgbm",
        help="modelos separados por vírgula "
             f"({','.join(VALIDATION_KINDS)})")
    args = parser.parse_args()
    kinds = [k.strip() for k in args.models.split(",") if k.strip()]
    for kind in kinds:
        if kind not in VALIDATION_KINDS:
            print(f"modelo desconhecido: {kind!r} (use {VALIDATION_KINDS})")
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
    client = FootballDataClient()
    corpus_signature = client.corpus_signature()
    matches = [m.to_historical() for m in client.load_matches()]
    print(f"  {len(matches)} partidas")

    print("  construindo corpus de features point-in-time…")
    corpus = _cached_feature_corpus(matches, corpus_signature)
    print(f"  features: {corpus.matrix.shape[1]} colunas")

    single_kinds = [k for k in kinds if k != ENSEMBLE_KIND]
    run_ensemble = ENSEMBLE_KIND in kinds

    results = {}
    for kind in single_kinds:
        print(f"\n=== {kind.upper()} — modelo vs mercado (24 janelas) ===")
        adapter = MLWindowAdapter(kind, corpus)
        results[kind] = _evaluate_adapter(adapter, bets, matches, config)

    ensemble_result = None
    if run_ensemble:
        print(f"\n=== ENSEMBLE (stacking OOS por janela) — "
              f"modelo vs mercado (24 janelas) ===")
        print(f"  bases: {', '.join(ENSEMBLE_BASE_KINDS)} | "
              f"folds rolling-origin: {ENSEMBLE_FOLDS} dentro do TRAIN")
        adapter = EnsembleWindowAdapter(corpus, progress=_progress)
        ensemble_result = _evaluate_adapter(
            adapter, bets, matches, config, collect_rows=True)
        # breakdown por odd band sobre a população EXATA da avaliação
        bands = odds_band_breakdown(ensemble_result.pop("prediction_rows"))
        ensemble_result["odds_bands"] = bands
        print("  odd bands:")
        for band in bands:
            if band.get("status") != "OK":
                print(f"    {band['band']:<10} n={band['n']} "
                      f"{band['status']}")
                continue
            d = band["delta_logloss_model_vs_market_raw"]
            print(f"    {band['band']:<10} n={band['n']} | "
                  f"logloss modelo {band['model_raw']['logloss']:.4f} vs "
                  f"mercado {band['market_raw']['logloss']:.4f} "
                  f"(delta {'n/d' if d is None else f'{d:+.4f}'})")

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
        "ensemble": (
            _ensemble_payload(ensemble_result, config)
            if ensemble_result is not None else
            {
                "status": "PENDENTE",
                "reason": (
                    "Ensemble exige stacking OOS dentro de cada janela — "
                    "rode com --models "
                    f"{ENSEMBLE_KIND} para produzi-lo neste protocolo."
                ),
            }
        ),
        "declarations": [
            "sem ranking de modelos",
            "sem vencedor declarado",
            "nenhuma promoção automática (promotion gate intocado)",
            "benchmark central: MODEL vs MARKET (raw e fair), nunca "
            "model vs zero",
        ],
    }
    first = next(iter(results.values()), ensemble_result)
    if first:
        payload["protocol"]["n_windows"] = first["n_windows"]
        payload["protocol"]["n_windows_valid"] = first["n_windows_valid"]

    from betgsn.config import output_root

    out = output_root() / "engineering" / "quant" / "ml_oos_validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    print(f"\nrelatório: {out}")
    print("\nNenhuma conclusão de promoção é extraída daqui. "
          "O promotion gate permanece intocado.")
    return 0


def _evaluate_adapter(adapter, bets, matches, config,
                      collect_rows: bool = False) -> dict:
    """Roda o adapter no MESMO harness e devolve o resultado serializado."""
    comparison = run_model_walkforward(
        bets, matches, config, _progress, model_fn=adapter.fit,
        collect_rows=collect_rows)

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

    return {
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


def _ensemble_payload(ensemble_result: dict, config) -> dict:
    """Seção do ensemble: resultado + protocolo de stacking declarado."""
    out = dict(ensemble_result)
    out["status"] = "OK"
    out["model"] = "ENSEMBLE_STACK_V1 (stacking OOS por janela)"
    out["stacking_protocol"] = {
        "base_models": list(ENSEMBLE_BASE_KINDS),
        "n_folds": ENSEMBLE_FOLDS,
        "meta_model": "LogisticRegression multinomial (default, sem tuning)",
        "note": (
            "Por janela: TRAIN dividido em chunks temporais; cada fold "
            "previsto por bases ajustadas APENAS nos chunks anteriores "
            "(rolling origin); meta treinado SOMENTE nessas previsões "
            "OOS; bases finais no TRAIN completo, congeladas, preveem o "
            "TEST e o meta combina. Bases NUNCA treinam no corpus "
            "inteiro para depois 'avaliar' nas janelas — isso seria "
            "leakage."
        ),
    }
    return out


if __name__ == "__main__":
    raise SystemExit(main())
