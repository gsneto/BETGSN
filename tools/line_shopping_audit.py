"""Auditoria do line-shopping — de onde vem o efeito de ~+6,4pp?

Executa sobre o CORPUS HISTÓRICO REAL (football-data.co.uk), sem rede:

    1. UMA passada de collect_bets (todas as linhas, campos enriquecidos:
       best/second/median/mean/worst/disp/n_books);
    2. auditoria da regra de mercado (favoritos < 1.30, n_books >= 3):
       população CONSTANTE, preço variando — isola PREÇO de SELEÇÃO;
    3. semântica da ablação use_median (população muda): reproduz o
       número reportado e expõe o quanto é população diferente;
    4. segmentações: odd band, liga, janela walk-forward, bucket de
       bookmakers; uplift de preço e dispersão entre casas;
    5. com --with-model: decomposição da STRATEGY_MODEL (EV>0) — as
       MESMAS apostas liquidadas ao melhor preço e à mediana.

AUDITORIA, NÃO TUNING: nenhum parâmetro é otimizado. O objetivo é
responder as perguntas A-I do protocolo, com limitações temporais
declaradas (corpus sem timestamps de odds).

Uso:
    python tools/line_shopping_audit.py [--with-model]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from betgsn.line_shopping_audit import (  # noqa: E402
    run_line_shopping_audit,
    strategy_model_decomposition,
)
from betgsn.value_strategy import collect_bets  # noqa: E402
from betgsn.value_walkforward import WalkForwardConfig  # noqa: E402


def _progress(done: int, total: int, message: str = "") -> None:
    if message:
        print(f"  [{done}/{total}] {message}", flush=True)
    else:
        print(f"  ... {done}/{total} partidas", flush=True)


def _roi_txt(value) -> str:
    return "n/d" if value is None else f"{value:+.4%}"


def main() -> int:
    with_model = "--with-model" in sys.argv[1:]
    config = WalkForwardConfig()

    print("Auditoria do line-shopping (corpus real, sem rede)")
    print(f"  janelas: train={config.train_days}d test={config.test_days}d "
          f"gap/embargo={config.gap_days}d (MESMAS do harness OOS)")

    print("  coletando linhas apostáveis do corpus…")
    bets = collect_bets(99.0, 1, closing=False, progress=_progress)
    print(f"  {len(bets)} linhas apostáveis")

    audit = run_line_shopping_audit(bets, config, progress=_progress)

    print("\n=== POPULAÇÃO CONSTANTE (regra: best < 1.30, n_books >= 3) ===")
    for price, stats in audit.rule_population_by_price.items():
        print(f"  preço {price:7s} n={stats['n']:6d} "
              f"roi={_roi_txt(stats['roi'])} "
              f"bootstrap [{_roi_txt(stats['bootstrap_low'])}, "
              f"{_roi_txt(stats['bootstrap_high'])}]")
    best = audit.rule_population_by_price["best"]
    median = audit.rule_population_by_price["median"]
    if best["roi"] is not None and median["roi"] is not None:
        print(f"  DELTA preço (best - median): "
              f"{_roi_txt(best['roi'] - median['roi'])}")

    print("\n=== SEMÂNTICA DA ABLAÇÃO (população muda) ===")
    sem = audit.ablation_semantics
    print(f"  população best:    n={sem['population_best']['n']:6d} "
          f"roi_best={_roi_txt(sem['population_best']['roi_best'])}")
    print(f"  população median:  n={sem['population_median']['n']:6d} "
          f"roi_median={_roi_txt(sem['population_median']['roi_median'])}")
    print(f"  delta reportado:   "
          f"{_roi_txt(sem['delta_reported_semantics'])}")

    print("\n=== UPLIFT DE PREÇO ===")
    uplift = audit.price_uplift
    bvm = uplift["best_vs_median"]
    bvs = uplift["best_vs_second"]
    print(f"  best vs median: mean {bvm['mean']:+.4%} | "
          f"mediana {bvm['median']:+.4%} | p10 {bvm['p10']:+.4%} | "
          f"p90 {bvm['p90']:+.4%}")
    print(f"  best vs second: mean {bvs['mean']:+.4%}")
    print(f"  distribuição de casas: {uplift['n_books_distribution']}")

    print("\n=== POR JANELA (efeito best - median) ===")
    windows = [w for w in audit.by_window]
    n_pos = sum(1 for w in windows if (w["delta_best_minus_median"] or 0) > 0)
    for w in windows:
        print(f"  w{w['window']:02d} test[{w['test_start']}..{w['test_end']}] "
              f"n={w['n']:5d} best={_roi_txt(w['roi_best'])} "
              f"median={_roi_txt(w['roi_median'])} "
              f"delta={_roi_txt(w['delta_best_minus_median'])}")
    print(f"  janelas com delta positivo: {n_pos}/{len(windows)}")

    print("\n=== POR ODD BAND ===")
    for row in audit.by_band:
        print(f"  {row['segment']:12s} n={row['n']:6d} "
              f"best={_roi_txt(row['roi_best'])} "
              f"median={_roi_txt(row['roi_median'])} "
              f"delta={_roi_txt(row['delta_best_minus_median'])}")

    print("\n=== POR BUCKET DE BOOKMAKERS ===")
    for row in audit.by_n_books:
        print(f"  {row['segment']:6s} n={row['n']:6d} "
              f"best={_roi_txt(row['roi_best'])} "
              f"median={_roi_txt(row['roi_median'])} "
              f"delta={_roi_txt(row['delta_best_minus_median'])}")

    payload = {
        "kind": "line_shopping_audit",
        "audit": audit.to_dict(),
    }

    # -------- decomposição da strategy_model (mesma população) --------
    if with_model:
        from betgsn.model_walkforward import run_model_walkforward

        print("\n=== STRATEGY_MODEL (EV>0): decomposição preço/seleção ===")
        print("  rodando modelo vs mercado nas mesmas janelas…")
        matches = None
        from betgsn.football_data_uk import FootballDataClient

        matches = [m.to_historical()
                   for m in FootballDataClient().load_matches()]
        comparison = run_model_walkforward(bets, matches, config, _progress)
        decomp = strategy_model_decomposition(
            comparison.strategy_rows, config)
        payload["strategy_model_decomposition"] = decomp
        print(f"  linhas EV>0: {decomp['n_rows']}")
        print(f"  ROI ao melhor preço:  {_roi_txt(decomp['roi_best'])} "
              f"bootstrap {decomp['roi_best_bootstrap']}")
        print(f"  ROI à mediana:        {_roi_txt(decomp['roi_median'])} "
              f"bootstrap {decomp['roi_median_bootstrap']}")
        print(f"  DELTA puro preço:     {_roi_txt(decomp['delta_price_effect'])}")
        print(f"  janelas com delta positivo: "
              f"{decomp['n_windows_positive']}/{decomp['n_windows']}")
        print("  NOTA: o número reportado (+6,4pp) também troca a população "
              "(EV recalculado à mediana); o delta acima é SÓ preço.")

    out = ROOT / "output" / "engineering" / "quant" / "line_shopping_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    print(f"\nrelatório: {out}")
    print("\nAUDITORIA: nenhuma conclusão de edge é extraída daqui. "
          "As limitações temporais estão no campo 'limitations'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
