"""Une rodadas multi-liga de temporadas diferentes e aplica o gate.

Uma rodada do multi_benchmark cobre UM ano de teste. O gate de promocao
exige evidencia em mais de uma temporada, e com razao: um modelo pode
vencer em 2025 porque 2025 teve muitos favoritos confirmando, e perder em
2024. Largura (ligas) nao substitui profundidade (temporadas).

Este script le varios relatorios, monta um SegmentResult por
(liga x temporada) e roda o gate sobre o conjunto completo.

Uso:
    python tools/cross_season_gate.py relatorio_a.json relatorio_b.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from betgsn.models.promotion import (  # noqa: E402
    ModelStatus,
    SegmentResult,
    evaluate_promotion,
)

#: Metricas comparadas. Financeiro fica de fora: as odds dos CSV nao tem
#: timestamp de publicacao, entao ROI ali e cenario, nao evidencia.
METRICS = ("logloss", "brier", "rps", "ece")


def load_report(path: Path) -> dict:
    if path.is_dir():
        path = path / "report.json"
    return json.loads(path.read_text(encoding="utf-8"))


def collect_segments(reports: list[dict]) -> dict[str, list[SegmentResult]]:
    """Monta segmentos (liga x temporada) por modelo.

    A chave do modelo e "mercado|variante": comparar logloss de 1x2 (tres
    classes) com o de btts (duas) nao significa nada.
    """
    segments: dict[str, list[SegmentResult]] = {}

    for report in reports:
        season = str(report["test_year"])
        for league in report["leagues"]:
            division = league["division"]
            n_test = league.get("n_test", 0)
            models = league.get("models", {})

            # As chaves ja vem como "mercado|variante". O baseline de cada
            # mercado e o unico comparativo valido para aquele mercado.
            baselines: dict[str, dict[str, float]] = {}
            for key, values in models.items():
                if not isinstance(values, dict) or "|" not in key:
                    continue
                market, variant = key.split("|", 1)
                if variant == "BASELINE_V1":
                    baselines[market] = {
                        m: values[m] for m in METRICS
                        if isinstance(values.get(m), (int, float))
                    }

            for key, values in models.items():
                if not isinstance(values, dict) or "|" not in key:
                    continue
                market, variant = key.split("|", 1)
                if variant == "BASELINE_V1":
                    continue
                base_metrics = baselines.get(market)
                if not base_metrics:
                    continue
                own = {
                    m: values[m] for m in METRICS
                    if isinstance(values.get(m), (int, float))
                }
                if not own:
                    continue
                segments.setdefault(key, []).append(
                    SegmentResult(
                        league=division,
                        season=season,
                        n_matches=n_test,
                        metrics=own,
                        baseline_metrics=base_metrics,
                    )
                )
    return segments


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gate de promocao sobre multiplas temporadas."
    )
    parser.add_argument("reports", nargs="+", help="relatorios multi-liga (json ou pasta)")
    parser.add_argument("--output", default="output/engineering/benchmark/cross_season_gate.json")
    parser.add_argument("--primary", default="logloss")
    args = parser.parse_args()

    reports = [load_report(Path(p)) for p in args.reports]
    seasons = sorted({str(r["test_year"]) for r in reports})
    divisions = sorted({d for r in reports for d in r["divisions_used"]})

    print(f"temporadas: {', '.join(seasons)}")
    print(f"ligas: {', '.join(divisions)}")
    print(f"segmentos por modelo: ate {len(seasons) * len(divisions)}\n")

    segments = collect_segments(reports)
    decisions = []

    for key, segs in sorted(segments.items()):
        decision = evaluate_promotion(
            model=key,
            segments=segs,
            current_status=ModelStatus.EXPERIMENTAL,
            primary_metric=args.primary,
        )
        improvements = [
            s.improvement(args.primary) for s in segs
            if s.improvement(args.primary) is not None
        ]
        decisions.append({
            "model": key,
            "n_segments": len(segs),
            "n_seasons": len({s.season for s in segs}),
            "n_leagues": len({s.league for s in segs}),
            "mean_improvement": (
                statistics.fmean(improvements) if improvements else None
            ),
            "consistency": decision.consistency,
            "recommended_status": decision.recommended_status.value,
            "blocking_failures": decision.blocking_failures,
            "decision": decision.to_dict(),
        })

    promoted = [d for d in decisions if d["recommended_status"] == "VALIDATED"]
    decisions.sort(key=lambda d: (-(d["mean_improvement"] or -9), -d["consistency"]))

    header = f"{'modelo':<34}{'seg':>5}{'temp':>6}{'ligas':>7}{'melhora':>10}{'consist':>9}  status"
    print(header)
    print("-" * len(header))
    for d in decisions:
        imp = d["mean_improvement"]
        imp_txt = f"{imp:+.3%}" if imp is not None else "  n/d"
        status = d["recommended_status"]
        if status == "EXPERIMENTAL" and d["blocking_failures"]:
            status = f"EXPERIMENTAL ({d['blocking_failures'][0]})"
        print(
            f"{d['model']:<34}{d['n_segments']:>5}{d['n_seasons']:>6}"
            f"{d['n_leagues']:>7}{imp_txt:>10}{d['consistency']:>9.2f}  {status}"
        )

    print()
    if promoted:
        print(f"APROVADOS NO GATE ({len(promoted)}):")
        for d in promoted:
            print(f"  {d['model']}: melhora {d['mean_improvement']:+.3%}, "
                  f"consistencia {d['consistency']:.0%}, "
                  f"{d['n_seasons']} temporadas x {d['n_leagues']} ligas")
        print()
        print("Aprovado no gate significa VALIDATED, nao PRODUCTION.")
        print("A troca do modelo de producao e decisao explicita, nao automatica.")
    else:
        print("Nenhum modelo passou no gate.")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "kind": "cross_season_promotion_gate",
        "seasons": seasons,
        "divisions": divisions,
        "primary_metric": args.primary,
        "n_models_evaluated": len(decisions),
        "n_promoted": len(promoted),
        "decisions": decisions,
    }, indent=2), encoding="utf-8")
    print(f"\nrelatorio: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
