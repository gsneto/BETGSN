"""Relatorio de validacao quantitativa sobre a evidencia ja existente.

Nao roda benchmark (que exige os CSVs do football-data.co.uk): le os
relatorios multi-liga ja persistidos, reconstroi os segmentos (liga x
temporada) e aplica o GATE FORTALECIDO, que agora exige:

- efeito acima do ruido (t entre segmentos >= 2, ou IC pareado excluindo 0);
- amostra total minima;
- nenhum ajuste no conjunto de teste;
- janelas walk-forward declaradas (quando informadas);
- CLV e drawdown (quando informados).

Alem disso, separa duas coisas que estavam misturadas:

- VALIDATED: a evidencia estatistica existe;
- PRODUCTION_ELIGIBLE: o efeito excede a margem de erro medida (~5%).

O veredito final e explicito sobre NAO apostar quando nada sustenta a
aposta. Resultado negativo e resultado.

Uso:
    python tools/quant_validation_report.py
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from betgsn.models import MEASURED_ERROR_MARGIN  # noqa: E402
from betgsn.models.promotion import (  # noqa: E402
    ModelStatus,
    SegmentResult,
    evaluate_promotion,
)

#: Metricas comparadas. Financeiro fica FORA: as odds dos CSV nao tem
#: timestamp de publicacao, entao ROI ali e cenario, nao evidencia.
METRICS = ("logloss", "brier", "rps", "ece")
BASELINE = "BASELINE_V1"

DEFAULT_REPORTS = (
    "output/engineering/benchmark/multi_league_2024/report.json",
    "output/engineering/benchmark/multi_league_2025.json/report.json",
)


def load(path: Path) -> dict:
    if path.is_dir():
        path = path / "report.json"
    return json.loads(path.read_text(encoding="utf-8"))


def collect_segments(reports: list[dict]) -> dict[str, list[SegmentResult]]:
    """Segmento por (liga x temporada), com o baseline do mesmo mercado."""
    segments: dict[str, list[SegmentResult]] = {}
    for report in reports:
        season = str(report["test_year"])
        for league in report.get("leagues", []):
            division = league["division"]
            n_test = int(league.get("n_test", 0))
            models = league.get("models", {})
            baselines: dict[str, dict[str, float]] = {}
            for key, values in models.items():
                if isinstance(values, dict) and key.endswith("|" + BASELINE):
                    baselines[key.split("|", 1)[0]] = {
                        m: values[m] for m in METRICS if m in values
                    }
            for key, values in models.items():
                if not isinstance(values, dict) or "|" not in key:
                    continue
                market, variant = key.split("|", 1)
                if variant == BASELINE:
                    continue
                base = baselines.get(market)
                if not base:
                    continue
                own = {m: values[m] for m in METRICS if m in values}
                if not own:
                    continue
                segments.setdefault(key, []).append(SegmentResult(
                    league=division, season=season, n_matches=n_test,
                    metrics=own, baseline_metrics=base,
                ))
    return segments


def evaluate_all(segments: dict[str, list[SegmentResult]]) -> list[dict]:
    out = []
    for key, segs in sorted(segments.items()):
        decision = evaluate_promotion(
            model=key, segments=segs, current_status=ModelStatus.EXPERIMENTAL,
            primary_metric="logloss",
        )
        out.append(decision.to_dict())
    out.sort(key=lambda d: (-(d["mean_improvement"] or -9), -d["consistency"]))
    return out


def betting_evidence(odds_bands_path: Path, value_path: Path) -> dict:
    """Resume a evidencia financeira e classifica o que ela permite concluir."""
    bands = load(odds_bands_path)
    positive_significant = [
        b for b in bands["bands"]
        if b.get("significant_95") and b.get("roi", 0) > 0
    ]
    negative_significant = [
        b for b in bands["bands"]
        if b.get("significant_95") and b.get("roi", 0) < 0
    ]
    value = load(value_path) if value_path.exists() else None
    return {
        "odds_bands": {
            "n_bets_considered": bands.get("n_bets_considered"),
            "financial_status": bands.get("financial_status"),
            "bands_with_positive_significant_roi": [
                b["band"] for b in positive_significant
            ],
            "bands_with_negative_significant_roi": [
                b["band"] for b in negative_significant
            ],
            "verdict": (
                "nenhuma faixa de odd com ROI positivo significativo"
                if not positive_significant
                else "ha faixa com ROI positivo significativo — investigar antes de apostar"
            ),
        },
        "value_validation": (
            {
                "roi": value.get("roi"),
                "se": value.get("se"),
                "ci_low": value.get("ci_low"),
                "ci_high": value.get("ci_high"),
                "n_bets": value.get("n_bets"),
                "status": "EXPLORATORY_CSV_UNTIMESTAMPED",
                "note": (
                    "preco real de fecho, mas sem timestamp de publicacao: "
                    "nao prova disponibilidade no momento da decisao"
                ),
            }
            if value else None
        ),
    }


def build_report(report_paths: list[Path], odds_bands_path: Path,
                 value_path: Path) -> dict:
    reports = [load(p) for p in report_paths]
    segments = collect_segments(reports)
    decisions = evaluate_all(segments)

    validated = [d["model"] for d in decisions if d["recommended_status"] == "VALIDATED"]
    eligible = [d["model"] for d in decisions if d["production_eligible"]]
    evidence = betting_evidence(odds_bands_path, value_path)

    no_bet = not eligible and not evidence["odds_bands"]["bands_with_positive_significant_roi"]

    return {
        "kind": "quant_validation",
        "seasons": sorted({str(r["test_year"]) for r in reports}),
        "divisions": sorted({d for r in reports for d in r.get("divisions_used", [])}),
        "error_margin": MEASURED_ERROR_MARGIN,
        "gate": "promotion_v2 (ruido + margem + OOS + tuning)",
        "decisions": decisions,
        "validated": validated,
        "production_eligible": eligible,
        "betting_evidence": evidence,
        "conclusion": {
            "action": "NO_BET" if no_bet else "REVIEW",
            "validated_but_not_production_eligible": [
                d["model"] for d in decisions
                if d["recommended_status"] == "VALIDATED" and not d["production_eligible"]
            ],
            "statement": (
                "Nenhum modelo e elegivel a producao: os efeitos ficam abaixo "
                f"da margem de erro medida ({MEASURED_ERROR_MARGIN:.0%}). "
                "A evidencia de aposta real nao mostra vantagem positiva "
                "significativa em nenhuma faixa de odd. NO BET e o resultado "
                "honesto; o resultado negativo fica preservado."
                if no_bet else
                "Ha evidencia que justifica revisao humana antes de qualquer "
                "decisao de producao."
            ),
        },
    }


def print_summary(report: dict) -> None:
    print(f"temporadas: {', '.join(report['seasons'])}")
    print(f"ligas: {', '.join(report['divisions'])}")
    print(f"margem de erro medida: {report['error_margin']:.0%}\n")
    header = (f"{'modelo':<34}{'seg':>5}{'melhora':>10}{'t':>8}"
              f"{'consist':>9}  status")
    print(header)
    print("-" * (len(header) + 18))
    for d in report["decisions"]:
        imp = d["mean_improvement"]
        imp_txt = f"{imp:+.3%}" if imp is not None else "  n/d"
        t = d["improvement_t_stat"]
        if d.get("improvement_t_stat_infinite"):
            t_txt = "inf"
        else:
            t_txt = f"{t:.2f}" if t is not None else "n/d"
        status = d["recommended_status"]
        if status == "VALIDATED" and not d["production_eligible"]:
            status = "VALIDATED (abaixo da margem)"
        elif status == "EXPERIMENTAL" and d["blocking_failures"]:
            status = f"EXPERIMENTAL ({d['blocking_failures'][0]})"
        print(f"{d['model']:<34}{d['n_segments']:>5}{imp_txt:>10}{t_txt:>8}"
              f"{d['consistency']:>9.2f}  {status}")
    print()
    print(report["conclusion"]["statement"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Validacao quantitativa honesta.")
    parser.add_argument("--reports", nargs="*", default=list(DEFAULT_REPORTS))
    parser.add_argument("--odds-bands",
                        default="output/engineering/benchmark/odds_bands.json")
    parser.add_argument("--value-validation", default="output/value_validation.json")
    parser.add_argument("--output", default="output/engineering/quant/quant_validation.json")
    args = parser.parse_args()

    report = build_report(
        [Path(p) for p in args.reports], Path(args.odds_bands),
        Path(args.value_validation),
    )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False),
                   encoding="utf-8")
    print_summary(report)
    print(f"\nrelatorio: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
