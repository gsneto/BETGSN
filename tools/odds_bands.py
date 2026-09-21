"""Analise de faixas de odds sobre dados reais do football-data.co.uk.

Duas perguntas que o senso comum de apostas erra:

1. "Odd baixa e segura." Nao e. Segura seria se o preco fosse justo. Uma
   odd 1.20 sem vantagem perde dinheiro de forma tao confiavel quanto
   qualquer outra — so perde mais devagar, o que a faz parecer segura.

2. "Basta acertar muito." Taxa de acerto alta com preco ruim e prejuizo.
   A comparacao que importa e entre a taxa observada e a taxa IMPLICITA
   no preco pago.

Financeiro aqui e EXPLORATORIO: os CSV trazem precos reais, mas sem
timestamp de publicacao. Nao ha prova de que aquele preco estava
disponivel no momento da decisao.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from betgsn.backtest_data import MatchResult, settle_outcome  # noqa: E402
from betgsn.football_data_uk import FootballDataClient  # noqa: E402

BANDS: tuple[tuple[float, float], ...] = (
    (1.00, 1.10), (1.10, 1.20), (1.20, 1.30), (1.30, 1.40),
    (1.40, 1.60), (1.60, 2.00), (2.00, 3.00), (3.00, 1e9),
)
MIN_BOOKS = 3
MARKET = "Resultado Final (1X2)"


@dataclass
class Band:
    low: float
    high: float
    returns: list[float] = field(default_factory=list)
    wins: int = 0
    odds: list[float] = field(default_factory=list)
    implied: list[float] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.returns)

    @property
    def label(self) -> str:
        return f"{self.low:.2f}-{self.high:.2f}" if self.high < 100 else f"{self.low:.2f}+"

    def summary(self) -> dict:
        if not self.returns:
            return {"band": self.label, "n": 0}
        roi = statistics.fmean(self.returns)
        hit = self.wins / self.n
        implied_hit = statistics.fmean(self.implied)
        stdev = statistics.pstdev(self.returns) if self.n > 1 else 0.0
        se = stdev / math.sqrt(self.n) if self.n > 1 else 0.0
        # Crescimento log com stake fixa de 1% da banca: mede se a faixa
        # sobrevive ao efeito composto, nao so a media aritmetica.
        f = 0.01
        growth = statistics.fmean([
            math.log(max(1e-9, 1.0 + f * r)) for r in self.returns
        ])
        return {
            "band": self.label,
            "n": self.n,
            "hit_rate": round(hit, 4),
            "implied_hit_rate": round(implied_hit, 4),
            "hit_vs_implied": round(hit - implied_hit, 4),
            "avg_odd": round(statistics.fmean(self.odds), 3),
            "roi": round(roi, 4),
            "roi_se": round(se, 4),
            "roi_t_stat": round(roi / se, 2) if se > 0 else None,
            "significant_95": bool(se > 0 and abs(roi / se) > 1.96),
            "log_growth_1pct": round(growth, 8),
        }


def analyse(divisions: list[str], max_matches: int = 0) -> dict:
    client = FootballDataClient()
    matches = client.load_matches(divisions or None)
    if max_matches:
        matches = matches[:max_matches]

    bands = [Band(lo, hi) for lo, hi in BANDS]
    considered = 0
    skipped_no_odds = 0

    for match in matches:
        if match.home_goals is None or match.away_goals is None:
            continue
        book_odds = match.odds_closing.get(MARKET) or match.odds_opening.get(MARKET)
        if not book_odds:
            skipped_no_odds += 1
            continue

        result = MatchResult(
            home_goals=match.home_goals, away_goals=match.away_goals,
            home_corners=None, away_corners=None,
            home_cards=None, away_cards=None,
        )

        by_outcome: dict[str, list[float]] = {}
        for book, outcomes in book_odds.items():
            for outcome, odd in outcomes.items():
                if odd and odd > 1.0:
                    by_outcome.setdefault(outcome, []).append(odd)

        for outcome, values in by_outcome.items():
            if len(values) < MIN_BOOKS:
                continue
            best = max(values)
            settled = settle_outcome(MARKET, outcome, result)
            if settled not in ("win", "loss"):
                continue
            considered += 1
            ret = (best - 1.0) if settled == "win" else -1.0
            for band in bands:
                if band.low <= best < band.high:
                    band.returns.append(ret)
                    band.odds.append(best)
                    band.implied.append(1.0 / best)
                    if settled == "win":
                        band.wins += 1
                    break

    return {
        "kind": "low_odds_band_analysis",
        "market": MARKET,
        "min_books": MIN_BOOKS,
        "divisions": divisions or "ALL",
        "n_matches": len(matches),
        "n_bets_considered": considered,
        "skipped_no_odds": skipped_no_odds,
        "price_used": "best_available_across_books",
        "financial_status": "EXPLORATORY_CSV_UNTIMESTAMPED",
        "bands": [b.summary() for b in bands],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ROI por faixa de odd.")
    parser.add_argument("--divisions", default="E0,SP1,I1,D1,F1")
    parser.add_argument("--max-matches", type=int, default=0)
    parser.add_argument("--output", default="output/engineering/benchmark/odds_bands.json")
    args = parser.parse_args()

    divisions = [d.strip() for d in args.divisions.split(",") if d.strip()]
    report = analyse(divisions, args.max_matches)

    print(f"mercado {MARKET} | {report['n_bets_considered']} apostas | "
          f"{report['n_matches']} partidas | preco: melhor entre casas")
    print("financeiro EXPLORATORIO: CSV sem timestamp de publicacao\n")
    head = (f"{'faixa':<12}{'n':>7}{'acerto':>9}{'implicito':>11}"
            f"{'dif':>8}{'ROI':>9}{'t':>7}{'sig':>5}")
    print(head)
    print("-" * len(head))
    for band in report["bands"]:
        if not band.get("n"):
            continue
        t = band["roi_t_stat"]
        print(
            f"{band['band']:<12}{band['n']:>7}{band['hit_rate']:>9.3f}"
            f"{band['implied_hit_rate']:>11.3f}{band['hit_vs_implied']:>+8.3f}"
            f"{band['roi']:>+9.3%}{(f'{t:.2f}' if t is not None else 'n/d'):>7}"
            f"{('sim' if band['significant_95'] else 'nao'):>5}"
        )

    sig = [b for b in report["bands"] if b.get("significant_95") and b.get("roi", 0) > 0]
    print()
    if sig:
        print("Faixas com ROI positivo e significativo a 95%:")
        for b in sig:
            print(f"  {b['band']}: ROI {b['roi']:+.2%} em {b['n']} apostas (t={b['roi_t_stat']})")
        print("Significancia estatistica nao e prova de lucro futuro:")
        print("estes precos sao a MELHOR odd entre casas, sem custo de execucao,")
        print("sem limite de stake e sem prova de disponibilidade no momento.")
    else:
        print("Nenhuma faixa apresentou ROI positivo significativo a 95%.")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nrelatorio: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
