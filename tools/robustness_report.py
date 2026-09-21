"""Teste de robustez: e se as probabilidades do modelo estiverem erradas?

Um portfolio com EV positivo no papel pode perder dinheiro na pratica por
duas razoes que nao aparecem no backtest padrao:

1. O modelo e superconfiante. Ele diz 55%, a realidade e 53%.
2. O preco executado e pior que o preco visto.

Este script mede as duas coisas.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from betgsn.portfolio.simulation import (  # noqa: E402
    PortfolioBet,
    model_error_simulation,
    slippage_scenarios,
)


def main() -> int:
    # Portfolio deliberadamente realista: EV declarado modesto (+4.5%),
    # 25 apostas em jogos distintos, stake de 1% da banca cada.
    bets = [
        PortfolioBet(f"bet{i}", 0.55, 1.90, 10.0, match=f"m{i}", league="E0")
        for i in range(25)
    ]
    declared_ev = bets[0].ev

    print("Portfolio: 25 apostas, p=0.55, odd=1.90, stake 1% da banca")
    print(f"EV declarado por aposta: {declared_ev:+.2%}")
    print("Banca inicial 1000. 8000 trajetorias por cenario.\n")

    report = model_error_simulation(bets, initial_bankroll=1000.0, runs=8000)

    print("=== ERRO DE MODELO (probability haircut) ===")
    header = (f"{'haircut':>8}{'p efetiva':>11}{'EV real':>10}"
              f"{'log growth':>13}{'mediana':>10}{'P(lucro)':>10}{'P(ruina)':>10}")
    print(header)
    print("-" * len(header))
    for s in report.scenarios:
        p_eff = 0.55 * (1 - s.haircut)
        ev_real = p_eff * 1.90 - 1.0
        print(
            f"{s.haircut:>8.0%}{p_eff:>11.4f}{ev_real:>+10.2%}"
            f"{s.expected_log_growth:>13.6f}{s.median_final_bankroll:>10.1f}"
            f"{s.probability_of_profit:>10.3f}{s.probability_of_ruin:>10.3f}"
        )

    print()
    print(f"ROBUSTNESS_SCORE: {report.robustness_score:.2f}")
    print("  definicao exata: fracao dos haircuts testados em que")
    print("  expected_log_growth permaneceu > 0. Sem ponderacao oculta.")
    breakeven = report.breakeven_haircut
    if breakeven is None:
        print("  breakeven_haircut: nenhum (sobreviveu a todos os cortes)")
    else:
        print(f"  breakeven_haircut: {breakeven:.0%}")
        print(f"  Ou seja: se o modelo superestimar em {breakeven:.0%},")
        print("  a vantagem desaparece. Essa e a margem de erro tolerada.")

    print()
    print("=== DEGRADACAO DE PRECO NA EXECUCAO (slippage) ===")
    slip = slippage_scenarios(bets, slippages=(0.0, 0.01, 0.02, 0.05), runs=6000)
    header2 = (f"{'slippage':>9}{'EV medio':>11}{'log growth':>13}"
               f"{'mediana':>10}{'P(lucro)':>10}")
    print(header2)
    print("-" * len(header2))
    for r in slip:
        print(
            f"{r['slippage']:>9.0%}{r['mean_ev']:>+11.4f}"
            f"{r['expected_log_growth']:>13.6f}"
            f"{r['median_final_bankroll']:>10.1f}{r['probability_of_profit']:>10.3f}"
        )

    negative = [r for r in slip if r["mean_ev"] <= 0]
    print()
    if negative:
        first = negative[0]
        print(f"A {first['slippage']:.0%} de slippage o EV ja e nao-positivo.")
        print("Uma vantagem que nao sobrevive a execucao real nao e vantagem.")
    else:
        print("O EV permaneceu positivo em todos os niveis de slippage testados.")

    out = Path("output/engineering/benchmark/robustness.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "kind": "robustness_and_slippage",
        "portfolio": {
            "n_bets": len(bets),
            "probability": 0.55,
            "odd": 1.90,
            "stake_each": 10.0,
            "declared_ev": round(declared_ev, 6),
        },
        "model_error": report.to_dict(),
        "slippage": slip,
    }, indent=2), encoding="utf-8")
    print(f"\nrelatorio: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
